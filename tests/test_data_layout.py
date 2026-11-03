"""Shared storage/path checks only, never camera capture functionality."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_layout import prediction_category, reference_directory
from ha_service.options import validate
from ha_service.storage import Store, digest
from munai_remote import local_references
from recognize_chickens import load_references
from test_ha_service import NAME, synthetic_jpeg


class DataLayoutTests(unittest.TestCase):
    def test_shared_prediction_categories(self):
        self.assertEqual(prediction_category('no_chicken'), ('empty',))
        self.assertEqual(prediction_category('unknown'), ('unknown',))
        self.assertEqual(prediction_category('hen_a'), ('hen_a', 'unreviewed'))
        for label in ('eggs', 'empty', 'pending', 'unreviewed', '../outside', 'hen_a\n', ''):
            with self.assertRaises(ValueError):
                prediction_category(label)

    def test_capture_manifest_accepts_only_shared_locations(self):
        for category in ('empty', 'unknown', 'pending', 'eggs', 'hen_a/unreviewed'):
            self.assertTrue(Store.valid_file('captures', category + '/' + NAME))
        for category in ('chicken/unknown', 'chicken/hen_a/unreviewed', 'hen_a',
                         'unknown/unreviewed', 'pending/unreviewed', '..'):
            self.assertFalse(Store.valid_file('captures', category + '/' + NAME))

    def test_managed_references_resolve_for_cli_and_legacy_loader(self):
        with tempfile.TemporaryDirectory(prefix='munai-layout-test-') as temporary:
            root = Path(temporary)
            store = Store(root, validate({}))
            for label in ('hen_a', 'hen_b'):
                data = synthetic_jpeg(40 if label == 'hen_a' else 200)
                store.upload(label + '/' + NAME, data, digest(data), store.revision, False, lambda path: None)
            self.assertEqual(reference_directory(root / 'references'), store.reference_root())
            self.assertEqual(len(local_references(root / 'references')), 2)
            # Feature extraction is mocked: this tests layout resolution, not images.
            with patch('recognize_chickens.measure', return_value={'features': [0] * 7}):
                self.assertEqual(len(load_references(root / 'references')), 2)

    def test_missing_or_symlink_version_is_never_used_as_flat_import(self):
        with tempfile.TemporaryDirectory(prefix='munai-layout-test-') as temporary:
            root = Path(temporary)
            (root / 'versions').mkdir()
            with self.assertRaises(ValueError):
                reference_directory(root)
            version = 'a' * 32
            (root / 'state.json').write_text(json.dumps({'active': version, 'previous': None}))
            (root / 'versions' / version).symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(ValueError):
                reference_directory(root)


if __name__ == '__main__':
    unittest.main()
