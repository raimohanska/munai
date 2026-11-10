"""Serial recognition rounds around the unchanged appearance-based algorithm."""
from datetime import datetime, timezone
import copy
import logging
from pathlib import Path
import time
import threading

import cv2
import numpy as np

from capture import CaptureError, capture
from count_eggs import detect_eggs
from recognize_chickens import background_mask, identify, load_references, measure
from data_layout import RESERVED

from . import VERSION
from .diagnostics import exception_details
from .mqtt import occupant
from .nest import NestState, valid_hen
from .journal import Journal
from .identity_retry import IdentityRetry, MAX_EXTRA_SHOTS
from .options import AppError
from .photo_archive import PhotoArchive
from .split_nest import SplitNest
from . import split_recognition
from .storage import MAX_FILE_BYTES, atomic_json, image_valid, safe_path
from .web_time import event_text

LOG = logging.getLogger('munai')


def next_deadline(previous, now, interval):
    next_time = previous + interval
    return next_time if next_time > now else now + interval


class EventLog:
    def __init__(self, logger=LOG, clock=time.monotonic):
        self.logger = logger
        self.clock = clock
        self.failures = {}

    def failed(self, key, message, diagnostic=None):
        now = self.clock()
        previous = self.failures.get(key)
        if (previous is None or previous[0] != message or previous[2] != diagnostic
                or now - previous[1] >= 900):
            if diagnostic:
                self.logger.warning('%s Diagnostic: %s. Repeated failures are suppressed for 15 minutes.',
                                    message, diagnostic)
            else:
                self.logger.warning('%s Repeated failures are suppressed for 15 minutes.', message)
            self.failures[key] = (message, now, diagnostic)

    def recovered(self, key, message):
        if key in self.failures:
            self.logger.info('%s', message)
            del self.failures[key]


class Runtime:
    def __init__(self, options, store, publisher=None):
        self.options = options
        self.store = store
        self.publisher = publisher
        self.reference_revision = None
        self.references = []
        self.last_observation = None
        self.web_lock = threading.Lock()
        self.web_images = {}
        self.last_occupant = None
        # Prediction-only callers may omit storage; they never advance events.
        self.nest = None
        self.journal = Journal(store) if store is not None else None
        if store is not None:
            if options['box_split'] == 'none':
                if safe_path(store.root, 'nest-layout.json').exists():
                    raise AppError('Split state exists; restore a matching whole-box backup before disabling split mode.')
                self.nest = NestState(store.root)
            else:
                legacy = NestState(store.root)
                self.journal.flush(legacy)
                self.nest = SplitNest(store.root, options['box_split'], carry=legacy.daily_snapshot())
        if self.journal:
            self.journal.flush(self.nest)
        self.events = EventLog()
        self.photos = PhotoArchive(store) if store is not None else None

    def measurement(self, path, mask=None):
        return measure(path, self.options['chicken_area'], self.options['texture_threshold'],
                       mask, split_recognition.measurement_size(self.options['box_split'])
                       if self.options['box_split'] != 'none' else (640, 360))

    def validate_reference(self, path):
        try:
            # Empty images are explicitly reviewed background, not hen identities.
            if Path(path).parent.name == 'empty':
                if self.options['box_split'] != 'none':
                    split_recognition.validate_empty(path)
                background_mask(Path(path).parent.parent, self.options['texture_threshold'])
                return
            if self.options['box_split'] != 'none':
                root = Path(path).parent.parent
                split_recognition.validate_half(path, root, self.options['box_split'])
                measured = split_recognition.measure_reference(path, root, self.options)
            else:
                mask = background_mask(Path(path).parent.parent, self.options['texture_threshold'])
                measured = self.measurement(path, mask)
            if measured['features'] is None:
                raise AppError('Reference is not measurable; review hen/empty images and recognition thresholds.',
                               'invalid_reference')
        except AppError as error:
            raise AppError(str(error), 'invalid_reference', error.diagnostic) from None
        except Exception as error:
            diagnostic = exception_details(error)
            LOG.warning('Reference validation failed. Diagnostic: %s', diagnostic)
            raise AppError('Reference validation failed; check app logs.', 'invalid_reference', diagnostic) from None

    def predict(self, path, references=None):
        """The same bounded image/appearance pipeline for HA and offline review."""
        path = Path(path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise AppError('Image exceeds 8 MiB; lower the camera resolution.')
        image_valid(path.read_bytes())
        references = self._references() if references is None else references
        if self.options['box_split'] != 'none':
            return split_recognition.predict(path, references, self.options)
        mask = getattr(references, 'background_mask', None)
        if mask is None:
            raise AppError('Upload reviewed empty-nest references before recognition.')
        prediction = identify(self.measurement(path, mask), references,
                              self.options['max_distance'], self.options['min_margin'])
        # Known and unknown hens suppress egg detection. This raw placeholder
        # never replaces the confirmed egg count in live state.
        prediction['egg_count'] = 0
        if prediction['label'] == 'no_chicken':
            try:
                gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if gray is None:
                    raise ValueError()
                prediction['egg_detections'] = detect_eggs(
                    gray, self.options['texture_threshold'],
                    self.options['egg_min_area'], self.options['egg_max_area'])
                prediction['egg_count'] = len(prediction['egg_detections'])
            except Exception as error:
                raise AppError('Egg detection failed; check app logs.',
                               diagnostic=exception_details(error)) from None
        return prediction

    def status(self):
        with self.store.lock:
            references = self.store.files('references')
            identities = len({item['path'].split('/')[0] for item in references
                              if item['path'].split('/')[0] not in RESERVED})
            empty_count = sum(item['path'].startswith('empty/') for item in references)
            compatible = True
            if self.options['box_split'] != 'none':
                try:
                    self._references()
                except AppError as error:
                    self.events.failed('references', str(error), error.diagnostic)
                    compatible = False
            return {'version': VERSION, 'capture_enabled': self.options['capture_enabled'],
                    'mqtt_enabled': self.options['mqtt_enabled'],
                    'mqtt_connected': bool(self.publisher and self.publisher.connected.is_set()),
                    'reference_revision': self.store.revision, 'reference_count': len(references),
                    'identity_count': identities, 'empty_reference_count': empty_count,
                    'references_ready': identities >= 2 and empty_count > 0 and compatible,
                    'reference_layout_compatible': compatible,
                    'last_observation': self.last_observation,
                    'latest_event': self.latest_display_event(),
                    'box_split': self.options['box_split'],
                    'zones': self.nest.snapshot().get('zones', {}),
                    'daily_eggs': self.nest.daily_snapshot()}

    def web_status(self):
        """Cheap UI snapshot: no reference loading, image decoding or disk scans."""
        with self.web_lock:
            observation = copy.deepcopy(self.last_observation)
            has_image = bool(observation and observation['timestamp'] in self.web_images)
        capture_state = ('Disabled' if not self.options['capture_enabled'] else
                         'Failed' if 'round' in self.events.failures else 'Active')
        return {'observation': observation, 'has_image': has_image,
                'latest_event': self.latest_display_event(),
                'capture_state': capture_state,
                'mqtt_state': ('Disabled' if not self.options['mqtt_enabled'] else
                               'Connected' if self.publisher and self.publisher.connected.is_set() else 'Disconnected'),
                'stale_after': max(self.options['expire_after'], self.options['capture_interval'] * 2),
                'errors': [item[0] for item in self.events.failures.copy().values()]}

    def latest_display_event(self):
        """Display the latest durable event without time-based resets."""
        value = self.nest.state['latest_event']
        return event_text(value) if value else 'No events yet'

    def web_image(self, stamp):
        with self.web_lock:
            return self.web_images.get(stamp)

    def _references(self):
        root = self.store.reference_root()
        if root is None or len([p for p in root.iterdir() if p.is_dir() and p.name not in RESERVED]) < 2:
            raise AppError('Upload reviewed reference JPEGs for at least two identities before enabling capture.')
        if self.reference_revision != self.store.revision:
            try:
                self.references = (split_recognition.load(root, self.options)
                                   if self.options['box_split'] != 'none' else
                                   load_references(root, self.options['chicken_area'],
                                                   self.options['texture_threshold']))
            except AppError:
                raise
            except Exception as error:
                raise AppError('Reference loading failed; check app logs.',
                               diagnostic=exception_details(error)) from None
            self.reference_revision = self.store.revision
            self.events.recovered('references', 'Reference loading recovered.')
            LOG.info('Loaded %d reviewed references for %d identities.', len(self.references),
                     len({item[0] for item in self.references}))
        return self.references

    def round(self):
        with self.store.job() as scratch:
            references = self._references()
            self.store.require_space(MAX_FILE_BYTES * 2)
            selection = IdentityRetry()
            latest = {}
            deadline = time.monotonic() + self.options['capture_timeout']

            def process(path):
                source = Path(path)
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
                prediction = self.predict(source, references)
                frame = self.journal.save_frame(source, prediction, stamp=stamp)
                selection.add(prediction, frame)
                latest.update(stamp=stamp, image=source.read_bytes())
                self.photos.save(source, stamp, prediction)

            for unused in range(MAX_EXTRA_SHOTS + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                previous_stamp = latest.get('stamp')
                try:
                    capture(self.options['camera_url'], str(scratch), '/usr/bin/ffmpeg',
                            remaining, 15, 0, on_capture=process, store=False, quiet=True)
                except CaptureError as error:
                    if not latest:
                        raise AppError(str(error)) from None
                    # Keep successful evidence if a later retry times out/fails.
                    break
                if latest.get('stamp') == previous_stamp or not selection.needs_more():
                    break
            if not latest:
                raise AppError('Capture did not complete; check the camera and overlap lock.')
            prediction, evidence, zone_evidence = selection.result()
            self.observe(latest['stamp'], prediction, evidence, image=latest['image'],
                         zone_evidence=zone_evidence)

    def observe(self, stamp, prediction, evidence=None, image=None, zone_evidence=None):
        """Commit and publish the same confirmed state for a successful prediction."""
        if image is not None and len(image) > MAX_FILE_BYTES:
            raise AppError('Image exceeds 8 MiB; lower the camera resolution.')
        self.journal.flush(self.nest)
        egg_images = {}
        items = prediction.get('zones', {'whole': prediction})
        if image is not None and any('egg_detections' in item for item in items.values()):
            gray = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None:
                raise AppError('Egg tracking image could not be decoded.')
            gray = cv2.resize(gray, (640, 360), interpolation=cv2.INTER_AREA)
            egg_images = {zone: (gray if zone == 'whole' else
                                split_recognition.crop(gray, zone, self.options['box_split']))
                          for zone, item in items.items() if 'egg_detections' in item}
        events = (self.nest.observe_zones(prediction['zones'], evidence,
                                         zone_evidence=zone_evidence, egg_images=egg_images,
                                         texture_threshold=self.options['texture_threshold'])
                  if 'zones' in prediction else
                   self.nest.observe(prediction['label'], prediction['egg_count'], evidence,
                                     egg_image=egg_images.get('whole'),
                                     egg_detections=prediction.get('egg_detections'),
                                     texture_threshold=self.options['texture_threshold']))
        for event in events:
            LOG.info('Latest event: %s.', event)
        self.journal.flush(self.nest)
        self.journal.cleanup_pending(self.nest)
        state = self.nest.snapshot()
        observation = dict(state, timestamp=stamp,
                           distance=prediction['distance'], margin=prediction['margin'],
                            raw_prediction={'occupant': occupant(prediction),
                                             'egg_count': prediction['egg_count']})
        observation['egg_tracking'] = ({zone: nest.state.get('egg_tracking')
                                        for zone, nest in self.nest.regions.items() if zone != 'center'}
                                       if 'zones' in prediction else self.nest.state.get('egg_tracking'))
        if 'zones' in prediction:
            observation['raw_prediction']['zones'] = prediction['zones']
            observation['raw_prediction']['ambiguous'] = prediction['ambiguous']
        atomic_json(self.store.root / 'results' / (stamp + '.json'), observation)
        with self.web_lock:
            # Retain two bounded full frames, independent of optional photo storage
            # and journal cleanup. A new capture must never replace an older URL.
            if image is not None:
                previous = self.last_observation
                self.web_images = ({previous['timestamp']: self.web_images[previous['timestamp']]}
                                   if previous and previous['timestamp'] in self.web_images else {})
                self.web_images[stamp] = image
            self.last_observation = observation
        if self.last_occupant != observation['occupant']:
            LOG.info('Nest occupant: %s.', observation['occupant'])
            self.last_occupant = observation['occupant']
        if self.publisher:
            self.publish_daily()
            latest = self.latest_display_event()
            self.publisher.publish(dict(state, latest_event=None if latest == 'No events yet' else latest))

    def update_daily(self):
        # Only reviewed identity directories, never predictions or nest positions.
        with self.store.lock:
            root = self.store.reference_root()
            identities = [] if root is None else [path.name for path in root.iterdir()
                if valid_hen(path.name) and path.name not in RESERVED
                and safe_path(root, path.name).is_dir()]
        self.nest.update_day(identities)

    def publish_daily(self):
        if self.publisher:
            try:
                self.publisher.publish_daily(self.nest.daily_snapshot())
                self.events.recovered('daily_mqtt', 'Daily egg publication recovered.')
            except AppError:
                # Transport outages must not stop recognition/local counting.
                self.events.failed('daily_mqtt', 'Daily egg MQTT publication failed; check broker connectivity and permissions.')

    def run(self, stop):
        deadline = time.monotonic()
        while not stop.is_set():
            started = time.monotonic()
            previous_observation = self.last_observation
            try:
                self.store.retain()
                self.journal.retain(self.nest)
                self.update_daily()
                self.publish_daily()
                if self.publisher:
                    try:
                        self.publisher.publish_event(self.latest_display_event())
                        self.events.recovered('event_mqtt', 'Latest event publication recovered.')
                    except AppError:
                        self.events.failed('event_mqtt', 'Latest event MQTT publication failed; check broker connectivity and permissions.')
                if self.options['capture_enabled']:
                    self.round()
                    self.events.recovered('round', 'Capture, recognition and publication recovered.')
                if self.publisher and not self.publisher.connected.is_set():
                    self.events.failed('mqtt', 'MQTT disconnected; check broker settings, credentials and TLS.')
                else:
                    self.events.recovered('mqtt', 'MQTT connected.')
            except AppError as error:
                self.events.failed('round', str(error), error.diagnostic)
            except Exception as error:
                self.events.failed('round', 'Round failed; check app logs.', exception_details(error))
            finally:
                if self.last_observation is previous_observation:
                    self.nest.reset_confirmation()
                # Enforce the cap after this round too, not just before the next.
                try:
                    self.store.retain()
                    self.events.recovered('retention', 'Capture retention recovered.')
                except AppError as error:
                    self.events.failed('retention', str(error), error.diagnostic)
                except Exception as error:
                    self.events.failed('retention', 'Capture retention failed; check storage permissions.',
                                       exception_details(error))
            LOG.debug('Scheduled round finished in %.2f seconds.', time.monotonic() - started)
            deadline = next_deadline(deadline, time.monotonic(), self.options['capture_interval'])
            stop.wait(max(0, deadline - time.monotonic()))
