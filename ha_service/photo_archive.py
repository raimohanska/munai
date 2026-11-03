"""Deduplicate ordinary photo storage, never recognition or journal evidence."""
import logging
import os
import shutil
import tempfile
import time

import cv2
import numpy as np

from data_layout import prediction_category
from .storage import MAX_FILE_BYTES, safe_path, sync_directory

LOG = logging.getLogger('munai')


def comparison_pixels(source):
    image = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Photo comparison unavailable')
    return cv2.resize(image, (320, 180), interpolation=cv2.INTER_AREA)


def similar(old, new):
    changed = (cv2.absdiff(old, new) > 15).astype(np.uint8)
    # Suppress isolated sensor noise and thin timestamp strokes, without any
    # camera-specific coordinates or exclusion of an entire nest edge.
    changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return float(np.mean(changed != 0)) * 100 < 0.1


def signature(prediction):
    zones = prediction.get('zones')
    if zones is not None:
        return tuple((zone, item['label'], item.get('egg_count'), item.get('obscured', False))
                     for zone, item in sorted(zones.items()))
    return (prediction['label'], prediction['egg_count'])


class PhotoArchive:
    def __init__(self, store, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.previous = None
        self.previous_signature = None
        self.saved_at = None
        self.destination = None

    def save(self, source, stamp, prediction):
        if not self.store.options['store_captures']:
            return False
        now = self.clock()
        current_signature = signature(prediction)
        try:
            pixels = comparison_pixels(source)
        except Exception:
            pixels = None
            LOG.warning('Photo comparison unavailable; retaining this photo.')
        if (self.store.options['capture_deduplicate'] and pixels is not None
                and self.previous is not None and current_signature == self.previous_signature
                and self.destination is not None and self.destination.is_file()
                and now - self.saved_at < self.store.options['capture_heartbeat_seconds']
                and similar(self.previous, pixels)):
            LOG.debug('Similar ordinary photo omitted; observation and journal evidence are unaffected.')
            return False
        category = '/'.join(prediction_category(prediction['label']))
        destination = safe_path(self.store.root / 'captures', category + '/' + stamp + '.jpg')
        self.store.require_space(MAX_FILE_BYTES)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=str(destination.parent))
        try:
            with os.fdopen(descriptor, 'wb') as handle, source.open('rb') as incoming:
                shutil.copyfileobj(incoming, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, str(destination))
            sync_directory(destination.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        # Failed writes must not move the comparison baseline or heartbeat.
        self.previous, self.previous_signature = pixels, current_signature
        self.saved_at, self.destination = now, destination
        return True
