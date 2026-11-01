"""Offline image review tests; no camera capture, .env, HA or broker access."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ha_service.options import AppError, validate
from ha_service.storage import Store, digest
from test_ha_service import NAME, synthetic_jpeg
from tools.review_images import review


class OfflineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='munai-review-test-')
        self.root = Path(self.temporary.name)
        self.store = Store(self.root, validate({}))
        for label, brightness, name in (('hen_a', 40, '20000101T120500_000000Z.jpg'),
                                        ('hen_b', 200, '20000101T120600_000000Z.jpg')):
            data = synthetic_jpeg(brightness)
            self.store.upload(label + '/' + name, data, digest(data), self.store.revision, False, lambda path: None)
        self.source = self.root / 'captures' / 'pending' / NAME
        self.source.parent.mkdir()
        self.data = synthetic_jpeg(41)
        self.source.write_bytes(self.data)
        self.profile = patch('tools.review_images.camera_bounds', return_value=(0, 640, []))
        self.profile.start()

    def tearDown(self):
        self.profile.stop()
        self.temporary.cleanup()

    def test_preview_predicts_without_relocating_images(self):
        with redirect_stdout(io.StringIO()):
            result = review(self.root)
        self.assertEqual(result['images'], 3)
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root / 'reports').exists())

    def test_review_preserves_every_image_and_never_runs_retention(self):
        before = self.store.files('references')
        with patch('ha_service.storage.Store.retain', side_effect=AssertionError('Never purge review data')):
            with redirect_stdout(io.StringIO()):
                result = review(self.root, apply=True)
        self.assertTrue(result['complete'])
        self.assertEqual(result['images'], 3)
        self.assertFalse(self.source.exists())
        destination = self.root / 'captures' / 'hen_a' / 'unreviewed' / NAME
        self.assertEqual(destination.read_bytes(), self.data)
        self.assertEqual(self.store.files('references'), before)
        self.assertEqual(len(self.store.files('captures')), 3)
        self.assertEqual(len(self.store.files('results')), 3)
        manifest = json.loads(next((self.root / 'reports').glob('review-*/manifest.json')).read_text())
        checks = [item for item in manifest['images'] if item['reference_original']]
        self.assertEqual(len(checks), 2)
        for item in checks:
            self.assertTrue(item['reference_check'])
            self.assertFalse(item['self_check_testable'])  # One reference per identity.
            self.assertEqual(item['prediction']['label'], 'unknown')
            self.assertFalse(os.path.samefile(str(self.root / item['source']), str(self.root / item['destination'])))

    def test_conflict_preflight_preserves_pending_source(self):
        destination = self.root / 'captures' / 'hen_a' / 'unreviewed' / NAME
        destination.parent.mkdir(parents=True)
        destination.write_bytes(synthetic_jpeg(90))
        with redirect_stdout(io.StringIO()), self.assertRaises(AppError):
            review(self.root, apply=True)
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertFalse((self.root / 'reports').exists())

    def test_reference_features_are_not_modified_or_promoted(self):
        version = self.store.revision
        with redirect_stdout(io.StringIO()):
            review(self.root, apply=True)
        self.assertEqual(Store(self.root, validate({})).revision, version)
        self.assertEqual(len(self.store.files('references')), 2)


if __name__ == '__main__':
    unittest.main()
