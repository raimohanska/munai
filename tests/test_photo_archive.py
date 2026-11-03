"""Synthetic offline storage policy tests; never invoke camera capture."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from ha_service.options import validate
from ha_service.photo_archive import PhotoArchive, similar
from ha_service.storage import Store


class PhotoArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.root = Path(self.temp.name)
        self.options = validate({'store_captures': True})
        self.store = Store(self.root / 'data', self.options)
        self.now = 0
        self.archive = PhotoArchive(self.store, clock=lambda: self.now)
        self.source = self.root / 'synthetic.jpg'
        cv2.imwrite(str(self.source), np.full((180, 320), 100, np.uint8))
        self.number = 0

    def tearDown(self):
        self.temp.cleanup()

    def save(self, label='no_chicken', eggs=0, **extra):
        self.number += 1
        stamp = '20000101T120000_{:06d}Z'.format(self.number)
        return self.archive.save(self.source, stamp, dict(label=label, egg_count=eggs, **extra))

    def test_identical_photos_skipped_until_heartbeat(self):
        self.assertTrue(self.save())
        self.now = 899
        self.assertFalse(self.save())
        self.now = 900
        self.assertTrue(self.save())
        self.assertEqual(len(list((self.store.root / 'captures').rglob('*.jpg'))), 2)

    def test_changes_in_identity_count_and_return_from_other_category_save(self):
        self.assertTrue(self.save())
        self.assertTrue(self.save(eggs=1))
        self.assertTrue(self.save('hen_a'))
        self.assertFalse(self.save('hen_a'))
        self.assertTrue(self.save('hen_b'))
        self.assertTrue(self.save())

    def test_view_changes_save_even_if_overall_label_unchanged(self):
        self.assertTrue(self.save('unknown', zones={'left': {'label': 'hen_a', 'egg_count': None}}))
        self.assertFalse(self.save('unknown', zones={'left': {'label': 'hen_a', 'egg_count': None}}))
        self.assertTrue(self.save('unknown', zones={'left': {'label': 'hen_b', 'egg_count': None}}))

    def test_changed_image_and_cumulative_drift_compare_against_last_saved(self):
        self.assertTrue(self.save())
        cv2.imwrite(str(self.source), np.full((180, 320), 110, np.uint8))
        self.assertFalse(self.save())
        cv2.imwrite(str(self.source), np.full((180, 320), 120, np.uint8))
        self.assertTrue(self.save())

    def test_tiny_overlay_noise_suppressed_without_location_mask(self):
        old = np.full((180, 320), 100, np.uint8)
        new = old.copy()
        new[2:30, 2] = 250
        self.assertTrue(similar(old, new))
        new[50:80, 50:80] = 250
        self.assertFalse(similar(old, new))

    def test_comparison_failure_retains_photo(self):
        self.assertTrue(self.save())
        with patch('ha_service.photo_archive.comparison_pixels', side_effect=ValueError()):
            self.assertTrue(self.save())

    def test_failed_write_does_not_advance_baseline(self):
        with patch('ha_service.photo_archive.os.replace', side_effect=OSError()):
            with self.assertRaises(OSError):
                self.save()
        self.assertIsNone(self.archive.previous)
        self.assertTrue(self.save())

    def test_removed_baseline_restart_disabled_and_storage_off(self):
        self.assertTrue(self.save())
        self.archive.destination.unlink()
        self.assertTrue(self.save())
        self.archive = PhotoArchive(self.store)
        self.assertTrue(self.save())
        self.options['capture_deduplicate'] = False
        self.assertTrue(self.save())
        self.options['store_captures'] = False
        self.assertFalse(self.save())
