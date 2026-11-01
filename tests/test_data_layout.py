"""Storage/path/migration checks only, never camera capture functionality."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_layout import prediction_category, reference_directory
from ha_service.options import validate
from ha_service.storage import AppError, Store, digest
from munai_remote import local_references
from recognize_chickens import load_references
from test_ha_service import NAME, synthetic_jpeg
from tools.migrate_layout import MigrationError, inventory, migrate
from tools.import_references import import_references


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


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='munai-migration-test-')
        self.root = Path(self.temporary.name)
        self.image = synthetic_jpeg()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, data=None):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.image if data is None else data)
        return path

    def test_dry_run_does_not_create_state_or_move_data(self):
        source = self.write('captures/chicken/hen_a/' + NAME)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(migrate(self.root), (1, 0))
        self.assertTrue(source.exists())
        self.assertFalse((self.root / 'references').exists())

    def test_all_known_legacy_locations_migrate_losslessly_and_idempotently(self):
        paths = {
            'captures/' + NAME: 'captures/pending/' + NAME,
            'captures/chicken/' + NAME: 'captures/unknown/' + NAME,
            'captures/chicken/hen_a/unreviewed/' + NAME: 'captures/hen_a/unreviewed/' + NAME,
            'captures/empty/' + NAME: 'captures/empty/' + NAME,
            'captures/eggs/' + NAME: 'captures/eggs/' + NAME,
            'captures/sort-123-456.csv': 'reports/legacy/sort-123-456.csv',
        }
        for source in paths:
            self.write(source, b'synthetic report' if source.endswith('.csv') else self.image)
        self.write('captures/chicken/hen_a/' + NAME)
        self.write('captures/chicken/hen_b/' + NAME, synthetic_jpeg(200))
        self.write('captures/.DS_Store', b'preserve hidden metadata')
        with redirect_stdout(io.StringIO()):
            migrate(self.root, apply=True)
            self.assertEqual(migrate(self.root, apply=True), (0, 0))
        for old, new in paths.items():
            self.assertTrue((self.root / new).exists())
            if old != new:
                self.assertFalse((self.root / old).exists())
        store = Store(self.root, validate({}))
        self.assertEqual(store.read('references', 'hen_a/' + NAME), self.image)
        self.assertEqual(len(store.files('references')), 2)
        self.assertEqual(len(store.files('captures')), 5)
        self.assertTrue((self.root / 'captures' / '.DS_Store').exists())

    def test_conflict_preflight_does_not_move_or_activate_anything(self):
        source = self.write('captures/' + NAME)
        self.write('captures/pending/' + NAME, b'a different original')
        with self.assertRaises(MigrationError):
            migrate(self.root, apply=True)
        self.assertTrue(source.exists())
        self.assertFalse((self.root / 'references').exists())

    def test_failed_validation_preserves_reviewed_originals(self):
        source = self.write('captures/chicken/hen_a/' + NAME)
        with patch('tools.migrate_layout.Runtime.validate_reference', side_effect=AppError('Synthetic invalid reference.')):
            with redirect_stdout(io.StringIO()), self.assertRaises(AppError):
                migrate(self.root, apply=True)
        self.assertEqual(source.read_bytes(), self.image)

    def test_symlinks_are_rejected(self):
        folder = self.root / 'captures'
        folder.mkdir()
        (folder / NAME).symlink_to(self.root / 'unrelated')
        with self.assertRaises(MigrationError):
            inventory(self.root)

    def test_reviewed_import_preserves_sources_and_activates_snapshot(self):
        source = self.write('local-data/reviewed/hen_a/' + NAME)
        with redirect_stdout(io.StringIO()):
            import_references(source.parent.parent, self.root)
            self.assertFalse((self.root / 'references').exists())
            import_references(source.parent.parent, self.root, apply=True)
        self.assertEqual(source.read_bytes(), self.image)
        store = Store(self.root, validate({}))
        self.assertEqual(store.read('references', 'hen_a/' + NAME), self.image)

    def test_reviewed_import_conflict_requires_explicit_overwrite(self):
        source = self.write('local-data/reviewed/hen_a/' + NAME)
        with redirect_stdout(io.StringIO()):
            import_references(source.parent.parent, self.root, apply=True)
            first = Store(self.root, validate({})).revision
            source.write_bytes(synthetic_jpeg(200))
            with self.assertRaises(AppError):
                import_references(source.parent.parent, self.root, apply=True)
            self.assertEqual(Store(self.root, validate({})).revision, first)
            import_references(source.parent.parent, self.root, apply=True, overwrite=True)
        self.assertEqual(Store(self.root, validate({})).read('references', 'hen_a/' + NAME), source.read_bytes())


if __name__ == '__main__':
    unittest.main()
