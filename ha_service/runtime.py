"""Serial recognition rounds around the unchanged appearance-based algorithm."""
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import tempfile
import time

import cv2

from capture import CaptureError, capture
from count_eggs import detect_eggs
from recognize_chickens import background_mask, identify, load_references, measure
from data_layout import RESERVED, prediction_category

from . import VERSION
from .mqtt import occupant
from .nest import NestState, valid_hen
from .journal import Journal
from .options import AppError
from .schedule import capture_paused
from .storage import MAX_FILE_BYTES, atomic_json, image_valid, safe_path

LOG = logging.getLogger('munai')


def next_deadline(previous, now, interval):
    next_time = previous + interval
    return next_time if next_time > now else now + interval


class EventLog:
    def __init__(self, logger=LOG, clock=time.monotonic):
        self.logger = logger
        self.clock = clock
        self.failures = {}

    def failed(self, key, message):
        now = self.clock()
        previous = self.failures.get(key)
        if previous is None or previous[0] != message or now - previous[1] >= 900:
            self.logger.warning('%s Repeated failures are suppressed for 15 minutes.', message)
            self.failures[key] = (message, now)

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
        self.last_occupant = None
        # Prediction-only callers may omit storage; they never advance events.
        self.nest = NestState(store.root) if store is not None else None
        self.journal = Journal(store) if store is not None else None
        if self.journal:
            self.journal.flush(self.nest)
        self.events = EventLog()

    def measurement(self, path, mask=None):
        return measure(path, self.options['chicken_area'], self.options['texture_threshold'],
                       mask)

    def validate_reference(self, path):
        try:
            # Empty images are explicitly reviewed background, not hen identities.
            if Path(path).parent.name == 'empty':
                background_mask(Path(path).parent.parent, self.options['texture_threshold'])
                return
            mask = background_mask(Path(path).parent.parent, self.options['texture_threshold'])
            if self.measurement(path, mask)['features'] is None:
                raise ValueError()
        except Exception:
            raise AppError('Reference is not measurable; review hen/empty images and recognition thresholds.', 'invalid_reference') from None

    def predict(self, path, references=None):
        """The same bounded image/appearance pipeline for HA and offline review."""
        path = Path(path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise AppError('Image exceeds 8 MiB; lower the camera resolution.')
        image_valid(path.read_bytes())
        references = self._references() if references is None else references
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
                prediction['egg_count'] = len(detect_eggs(
                    gray, self.options['texture_threshold'],
                    self.options['egg_min_area'], self.options['egg_max_area']))
            except Exception:
                raise AppError('Egg detection failed; check the image and egg size settings.') from None
        return prediction

    def status(self):
        with self.store.lock:
            references = self.store.files('references')
            identities = len({item['path'].split('/')[0] for item in references
                              if item['path'].split('/')[0] not in RESERVED})
            empty_count = sum(item['path'].startswith('empty/') for item in references)
            return {'version': VERSION, 'capture_enabled': self.options['capture_enabled'],
                    'mqtt_enabled': self.options['mqtt_enabled'],
                    'mqtt_connected': bool(self.publisher and self.publisher.connected.is_set()),
                    'reference_revision': self.store.revision, 'reference_count': len(references),
                    'identity_count': identities, 'empty_reference_count': empty_count,
                    'references_ready': identities >= 2 and empty_count > 0,
                    'last_observation': self.last_observation,
                    'latest_event': self.nest.state['latest_event'],
                    'daily_eggs': self.nest.daily_snapshot()}

    def _references(self):
        root = self.store.reference_root()
        if root is None or len([p for p in root.iterdir() if p.is_dir() and p.name not in RESERVED]) < 2:
            raise AppError('Upload reviewed reference JPEGs for at least two identities before enabling capture.')
        if self.reference_revision != self.store.revision:
            try:
                self.references = load_references(root, self.options['chicken_area'],
                                                  self.options['texture_threshold'])
            except Exception:
                raise AppError('References are not usable with these settings; review hen and empty references.') from None
            self.reference_revision = self.store.revision
            LOG.info('Loaded %d reviewed references for %d identities.', len(self.references),
                     len({item[0] for item in self.references}))
        return self.references

    def round(self):
        with self.store.job() as scratch:
            references = self._references()
            self.store.require_space(MAX_FILE_BYTES * 2)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
            predictions = []
            evidence = []

            def process(path):
                source = Path(path)
                prediction = self.predict(source, references)
                predictions.append(prediction)
                evidence.append(self.journal.save_frame(source, prediction))
                if self.options['store_captures']:
                    category = '/'.join(prediction_category(prediction['label']))
                    destination = safe_path(self.store.root / 'captures', category + '/' + stamp + '.jpg')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=str(destination.parent))
                    try:
                        with os.fdopen(descriptor, 'wb') as handle, source.open('rb') as incoming:
                            shutil.copyfileobj(incoming, handle)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(temporary, str(destination))
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)

            try:
                capture(self.options['camera_url'], str(scratch), '/usr/bin/ffmpeg',
                        self.options['capture_timeout'], 15, 0, on_capture=process, store=False, quiet=True)
            except CaptureError as error:
                raise AppError(str(error)) from None  # CaptureError contains only fixed summaries.
            if not predictions:
                raise AppError('Capture did not complete; check the camera and overlap lock.')
            self.observe(stamp, predictions[0], evidence[0])

    def observe(self, stamp, prediction, evidence=None):
        """Commit and publish the same confirmed state for a successful prediction."""
        self.journal.flush(self.nest)
        for event in self.nest.observe(prediction['label'], prediction['egg_count'], evidence):
            LOG.info('Latest event: %s.', event)
        self.journal.flush(self.nest)
        self.journal.cleanup_pending(self.nest)
        state = self.nest.snapshot()
        observation = dict(state, timestamp=stamp,
                           distance=prediction['distance'], margin=prediction['margin'],
                           raw_prediction={'occupant': occupant(prediction),
                                           'egg_count': prediction['egg_count']})
        atomic_json(self.store.root / 'results' / (stamp + '.json'), observation)
        self.last_observation = observation
        if self.last_occupant != observation['occupant']:
            LOG.info('Nest occupant: %s.', observation['occupant'])
            self.last_occupant = observation['occupant']
        if self.publisher:
            self.publish_daily()
            self.publisher.publish(state)

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
        was_paused = None
        while not stop.is_set():
            started = time.monotonic()
            previous_observation = self.last_observation
            try:
                self.store.retain()
                self.journal.retain(self.nest)
                self.update_daily()
                self.publish_daily()
                paused = capture_paused(self.options)
                if self.options['capture_enabled'] and paused != was_paused:
                    LOG.info('Scheduled camera capture %s.', 'paused for sleep time' if paused else 'active')
                    was_paused = paused
                if self.options['capture_enabled'] and not paused:
                    self.round()
                    self.events.recovered('round', 'Capture, recognition and publication recovered.')
                if self.publisher and not self.publisher.connected.is_set():
                    self.events.failed('mqtt', 'MQTT disconnected; check broker settings, credentials and TLS.')
                else:
                    self.events.recovered('mqtt', 'MQTT connected.')
            except AppError as error:
                self.events.failed('round', str(error))
            except Exception:
                self.events.failed('round', 'Round failed; check dependencies, reviewed references and storage permissions.')
            finally:
                if self.last_observation is previous_observation:
                    self.nest.reset_confirmation()
                # Enforce the cap after this round too, not just before the next.
                try:
                    self.store.retain()
                    self.events.recovered('retention', 'Capture retention recovered.')
                except Exception:
                    self.events.failed('retention', 'Capture retention failed; check storage permissions.')
            LOG.debug('Scheduled round finished in %.2f seconds.', time.monotonic() - started)
            deadline = next_deadline(deadline, time.monotonic(), self.options['capture_interval'])
            stop.wait(max(0, deadline - time.monotonic()))
