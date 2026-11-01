"""Serial recognition rounds around the unchanged appearance-based algorithm."""
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import tempfile
import time

from capture import CaptureError, capture
from recognize_chickens import identify, load_references, measure

from . import VERSION
from .mqtt import occupant
from .options import AppError
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
        self.events = EventLog()

    def measurement(self, path):
        return measure(path, self.options['chicken_area'], self.options['texture_threshold'],
                       self.options['camera_profile'])

    def validate_reference(self, path):
        try:
            if self.measurement(path)['features'] is None:
                raise ValueError()
        except Exception:
            raise AppError('Reference has no measurable hen; review the image and camera masks/thresholds.', 'invalid_reference') from None

    def status(self):
        with self.store.lock:
            references = self.store.files('references')
            identities = len({item['path'].split('/')[0] for item in references})
            return {'version': VERSION, 'capture_enabled': self.options['capture_enabled'],
                    'mqtt_enabled': self.options['mqtt_enabled'],
                    'mqtt_connected': bool(self.publisher and self.publisher.connected.is_set()),
                    'reference_revision': self.store.revision, 'reference_count': len(references),
                    'identity_count': identities, 'references_ready': identities >= 2,
                    'last_observation': self.last_observation}

    def _references(self):
        root = self.store.reference_root()
        if root is None or len([p for p in root.iterdir() if p.is_dir()]) < 2:
            raise AppError('Upload reviewed reference JPEGs for at least two identities before enabling capture.')
        if self.reference_revision != self.store.revision:
            try:
                self.references = load_references(root, self.options['chicken_area'],
                                                  self.options['texture_threshold'], self.options['camera_profile'])
            except Exception:
                raise AppError('References are not usable with these settings; review references and camera masks.') from None
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

            def process(path):
                source = Path(path)
                if source.stat().st_size > MAX_FILE_BYTES:
                    raise AppError('Captured image exceeds 8 MiB; lower the camera resolution.')
                image_valid(source.read_bytes())
                prediction = identify(self.measurement(source), references,
                                      self.options['max_distance'], self.options['min_margin'])
                predictions.append(prediction)
                if self.options['store_captures']:
                    label = prediction['label']
                    category = ('empty' if label == 'no_chicken' else 'chicken/unknown' if label == 'unknown'
                                else 'chicken/' + label + '/unreviewed')
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
            prediction = predictions[0]
            observation = {'timestamp': stamp, 'occupant': occupant(prediction),
                           'distance': prediction['distance'], 'margin': prediction['margin']}
            atomic_json(self.store.root / 'results' / (stamp + '.json'), observation)
            self.last_observation = observation
            if self.last_occupant != observation['occupant']:
                LOG.info('Nest occupant: %s.', observation['occupant'])
                self.last_occupant = observation['occupant']
            if self.publisher:
                self.publisher.publish(prediction)

    def run(self, stop):
        deadline = time.monotonic()
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.store.retain()
                if self.options['capture_enabled']:
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
                # Enforce the cap after this round too, not just before the next.
                try:
                    self.store.retain()
                    self.events.recovered('retention', 'Capture retention recovered.')
                except Exception:
                    self.events.failed('retention', 'Capture retention failed; check storage permissions.')
            LOG.debug('Scheduled round finished in %.2f seconds.', time.monotonic() - started)
            deadline = next_deadline(deadline, time.monotonic(), self.options['capture_interval'])
            stop.wait(max(0, deadline - time.monotonic()))
