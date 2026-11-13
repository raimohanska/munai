"""Synthetic service/API/CLI tests. No .env, camera capture, HA, or broker access."""
from contextlib import redirect_stdout
import io
import json
import logging
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import time
import unittest
import zipfile
import warnings
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from ha_service.api import TransferServer, tls_context
from ha_service.mqtt import MQTT, daily_discovery, discovery, occupant
from ha_service.options import AppError, validate
from ha_service.runtime import EventLog, Runtime, next_deadline
from ha_service.storage import Conflict, Store, digest, image_valid, safe_path
from munai_remote import Remote, RemoteError, download_path, endpoint, local_references, main, pull, push

NAME = '20000101T120000_000000Z.jpg'
SECOND = '20000101T120100_000000Z.jpg'
DUMMY_TOKEN = 'synthetic-offline-token-not-a-real-secret'


def synthetic_jpeg(brightness=40):
    rng = np.random.default_rng(42)
    pixels = rng.integers(70, 180, size=(360, 640), dtype=np.uint8)
    pixels[90:250, 220:410] = brightness
    stream = io.BytesIO()
    Image.fromarray(pixels).save(stream, format='JPEG', quality=95)
    return stream.getvalue()


def synthetic_empty():
    pixels = np.random.default_rng(7).integers(70, 180, size=(360, 640), dtype=np.uint8)
    stream = io.BytesIO()
    Image.fromarray(pixels).save(stream, format='JPEG', quality=95)
    return stream.getvalue()


def reference_archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as bundle:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            for relative, data in entries:
                bundle.writestr(relative, data)
    return stream.getvalue()


class OptionsTests(unittest.TestCase):
    def test_egg_area_validation(self):
        self.assertEqual(validate({})['egg_min_area'], 950)
        self.assertEqual(validate({'egg_min_area': 700, 'egg_max_area': 2800})['egg_min_area'], 700)
        for options in ({'egg_min_area': True}, {'egg_min_area': 0}, {'egg_max_area': float('nan')},
                        {'egg_min_area': 3000, 'egg_max_area': 2000}):
            with self.assertRaises(AppError):
                validate(options)

    def test_safe_defaults_and_no_implicit_profile_read(self):
        options = validate({})
        self.assertFalse(options['capture_enabled'])
        self.assertFalse(options['mqtt_enabled'])
        self.assertNotIn('camera_profile', options)

    def test_invalid_options_do_not_expose_values(self):
        for extra in ({'camera_url': 'secret-invalid', 'capture_enabled': True},
                      {'mqtt_port': 'secret-invalid'}, {'device_id': 'secret-invalid/'},
                      {'log_level': 'secret-invalid'}, {'capture_timeout': True},
                      {'capture_interval': 180},
                      {'transfer_tokens': [{'name': 'test', 'sha256': 'secret-invalid', 'scope': 'write'}]}):
            with self.assertRaises(AppError) as caught:
                validate(extra)
            self.assertNotIn('secret-invalid', str(caught.exception))

    def test_empty_optional_lists_can_be_omitted_by_ha_form(self):
        options = validate({})
        self.assertEqual(options['transfer_tokens'], [])
        self.assertEqual(options['transfer_token_sha256'], '')

    def test_primary_token_uses_scalar_form_fields(self):
        sha = 'a' * 64  # Synthetic digest, not a credential.
        options = validate({'transfer_token_sha256': sha, 'transfer_token_name': 'test-client',
                            'transfer_token_scope': 'read'})
        self.assertEqual(options['transfer_tokens'], [{'name': 'test-client', 'scope': 'read', 'sha256': sha}])
        with self.assertRaises(AppError):
            validate({'transfer_token_sha256': 'secret-invalid'})

    def test_additional_token_rows_and_legacy_runtime_entries(self):
        sha = 'b' * 64
        expected = [{'name': 'second', 'scope': 'write', 'sha256': sha}]
        self.assertEqual(validate({'transfer_tokens': ['second|write|' + sha]})['transfer_tokens'], expected)
        self.assertEqual(validate({'transfer_tokens': expected})['transfer_tokens'], expected)

    def test_retired_masks_are_ignored_on_upgrade(self):
        self.assertEqual(validate({'horizontal_bounds': [10, 630], 'horizontal_left': 0,
                                   'horizontal_right': 640, 'excluded_rectangles': ['1,2,3,4']}), validate({}))

    def test_metadata_options_and_schema_agree(self):
        from ha_service import VERSION
        config = json.loads((Path(__file__).resolve().parent.parent / 'config.json').read_text())
        self.assertEqual(config['version'], VERSION)
        dockerfile = (Path(__file__).resolve().parent.parent / 'Dockerfile').read_text()
        self.assertIn('io.hass.version="{}"'.format(VERSION), dockerfile)
        self.assertLessEqual(set(config['options']), set(config['schema']))
        missing = set(config['schema']) - set(config['options'])
        self.assertEqual(missing, {'transfer_token_sha256', 'transfer_tokens'})
        for key in missing:
            typ = config['schema'][key]
            if isinstance(typ, list):
                typ = typ[0]
            self.assertIsInstance(typ, str)
            self.assertTrue(typ.endswith('?'))  # Supervisor's missing-option rule.
        self.assertNotIn('horizontal_bounds', config['schema'])
        self.assertNotIn('horizontal_left', config['schema'])
        self.assertNotIn('horizontal_right', config['schema'])
        self.assertNotIn('excluded_rectangles', config['schema'])
        # Empty list-of-object fields are optional in UiOptions but required by
        # AppOptions: prohibit that mismatch in our installable metadata.
        self.assertTrue(all(not isinstance(value, list) or not isinstance(value[0], dict)
                            for value in config['schema'].values()))
        self.assertIn('aarch64', config['arch'])
        self.assertNotIn('image', config)  # Build on the Green, like Paine.
        for permission in ('privileged', 'host_network', 'docker_api', 'homeassistant_api', 'hassio_api', 'map'):
            self.assertNotIn(permission, config)


class StorageFixture(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='munai-service-test-')
        self.root = Path(self.scratch.name)
        self.options = validate({})
        self.store = Store(self.root / 'data', self.options)
        self.runtime = Runtime(self.options, self.store)
        self.image = synthetic_jpeg()

    def tearDown(self):
        self.scratch.cleanup()

    def upload(self, relative='hen_a/' + NAME, data=None, overwrite=False):
        data = self.image if data is None else data
        return self.store.upload(relative, data, digest(data), self.store.revision, overwrite,
                                 self.runtime.validate_reference)


class StorageTests(StorageFixture):
    def replacement(self, entries=None, revision=None, validator=None):
        entries = entries if entries is not None else [
            ('hen_b/' + NAME, synthetic_jpeg(200)),
            ('hen_c/' + NAME, self.image), ('empty/' + NAME, synthetic_empty())]
        data = reference_archive(entries)
        return self.store.replace_references(io.BytesIO(data), digest(data),
                                             self.store.revision if revision is None else revision,
                                             validator or self.runtime.validate_reference)

    def test_replace_removes_old_labels_and_is_idempotent_without_history(self):
        self.upload()
        checksum = self.replacement()
        self.assertEqual({item['path'] for item in self.store.files('references')},
                         {'hen_b/' + NAME, 'hen_c/' + NAME, 'empty/' + NAME})
        self.assertEqual(self.replacement(), checksum)
        self.assertFalse((self.store.reference_root() / 'versions').exists())
        self.assertEqual(list((self.store.root / 'scratch').iterdir()), [])
        self.assertEqual(Store(self.store.root, self.options).revision, checksum)

    def test_replacement_failure_and_stale_checksum_preserve_original(self):
        original = self.upload()
        for entries in ([], [('hen_b/' + NAME, self.image)],
                        [('../escape.jpg', self.image)],
                        [('hen_a/' + NAME, self.image)] * 2,
                        [('hen_b/' + NAME, b'invalid'), ('hen_c/' + NAME, self.image),
                         ('empty/' + NAME, synthetic_empty())]):
            with self.assertRaises(AppError):
                self.replacement(entries)
            self.assertEqual(self.store.revision, original)
        with self.assertRaises(Conflict):
            self.replacement(revision='none')
        with self.assertRaises(AppError):
            self.store.replace_references(io.BytesIO(b'invalid'), '0' * 64, original, lambda p: None)
        with self.assertRaises(AppError):
            self.store.replace_references(io.BytesIO(b'invalid'), digest(b'invalid'), original, lambda p: None)
        with self.assertRaises(AppError):
            self.replacement(validator=Mock(side_effect=AppError('Synthetic validation failure')))
        with patch('ha_service.storage.exchange_directories', side_effect=AppError('Synthetic exchange failure')):
            with self.assertRaises(AppError):
                self.replacement()
        self.assertEqual(self.store.revision, original)
        self.assertEqual(self.store.read('references', 'hen_a/' + NAME), self.image)

    def test_replacement_limits_and_disk_reserve(self):
        original = self.upload()
        for setting in ('MAX_REFERENCES', 'MAX_REFERENCE_BYTES', 'MAX_ARCHIVE_BYTES'):
            with patch('ha_service.storage.' + setting, 1):
                with self.assertRaises(AppError):
                    self.replacement()
        usage = shutil.disk_usage(str(self.root))
        with patch('ha_service.storage.shutil.disk_usage', return_value=usage._replace(free=0)):
            with self.assertRaises(AppError):
                self.replacement()
        self.assertEqual(self.store.revision, original)

    def test_replacement_rejects_compression_links_and_path_traversal(self):
        original = self.upload()
        for kind in ('compressed', 'symlink', 'traversal'):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as bundle:
                for identity in ('empty', 'hen_b', 'hen_c'):
                    entry = zipfile.ZipInfo(identity + '/' + NAME)
                    if identity == 'hen_c':
                        if kind == 'compressed':
                            entry.compress_type = zipfile.ZIP_DEFLATED
                        elif kind == 'symlink':
                            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                        else:
                            entry.filename = '../hen_c/' + NAME
                    bundle.writestr(entry, self.image)
            data = stream.getvalue()
            with self.assertRaises(AppError):
                self.store.replace_references(io.BytesIO(data), digest(data), original, lambda path: None)
            self.assertEqual(self.store.revision, original)

    def test_replacement_waits_for_recognition_and_uses_complete_staged_background(self):
        errors = []
        entered = threading.Event()

        def replace():
            entered.set()
            try:
                self.replacement()
            except Exception as error:
                errors.append(error)

        with self.store.job():
            thread = threading.Thread(target=replace)
            thread.start()
            self.assertTrue(entered.wait(2))
            time.sleep(0.03)
            self.assertEqual(self.store.revision, 'none')
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(self.runtime.status()['references_ready'])

    def test_restart_cleans_discarded_swap_folder_without_touching_current_set(self):
        self.upload()
        stage = self.store.root / 'scratch' / 'job-interrupted' / 'references'
        (stage / 'hen_old').mkdir(parents=True)
        (stage / 'hen_old' / NAME).write_bytes(self.image)
        checksum = self.store.revision
        restarted = Store(self.store.root, self.options)
        self.assertEqual(restarted.revision, checksum)
        self.assertFalse(stage.exists())
        self.assertEqual(restarted.read('references', 'hen_a/' + NAME), self.image)

    def test_migrate_legacy_active_images_and_discard_version_history(self):
        legacy = self.root / 'legacy'
        active, previous = 'a' * 32, 'b' * 32
        for version, identity in ((active, 'hen_a'), (previous, 'hen_old')):
            folder = legacy / 'references' / 'versions' / version / identity
            folder.mkdir(parents=True)
            (folder / NAME).write_bytes(self.image)
        (legacy / 'references' / 'state.json').write_text(json.dumps({'active': active, 'previous': previous}))
        migrated = Store(legacy, self.options)
        self.assertEqual([item['path'] for item in migrated.files('references')], ['hen_a/' + NAME])
        self.assertFalse((legacy / 'references' / 'versions').exists())
        self.assertFalse((legacy / 'references' / 'state.json').exists())
        self.assertEqual(Store(legacy, self.options).revision, migrated.revision)

    def test_predict_counts_eggs_only_without_a_hen(self):
        from recognize_chickens import References
        path = self.root / NAME
        path.write_bytes(synthetic_empty())
        references = References(np.zeros((360, 640), bool))
        self.options.update({'egg_min_area': 700, 'egg_max_area': 2800})
        with patch('ha_service.runtime.identify') as identify, patch('ha_service.runtime.detect_eggs') as detect:
            for label, count in (('hen_a', 0), ('unknown', 0), ('no_chicken', 2)):
                identify.return_value = {'label': label}
                detect.reset_mock()
                detect.return_value = [{}, {}]
                prediction = self.runtime.predict(path, references)
                self.assertEqual(prediction['egg_count'], count)
                if label == 'no_chicken':
                    self.assertEqual(detect.call_args.args[1:], (8, 700, 2800))
                else:
                    detect.assert_not_called()

    def test_egg_detection_failure_does_not_become_zero(self):
        from recognize_chickens import References
        path = self.root / NAME
        path.write_bytes(synthetic_empty())
        references = References(np.zeros((360, 640), bool))
        with patch('ha_service.runtime.identify', return_value={'label': 'no_chicken'}), \
                patch('ha_service.runtime.detect_eggs', side_effect=ValueError('synthetic-private-detail')):
            with self.assertRaises(AppError) as caught:
                self.runtime.predict(path, references)
        self.assertNotIn('synthetic-private-detail', str(caught.exception))

    def test_predict_refuses_uncalibrated_references(self):
        self.upload()
        self.upload('hen_b/' + NAME, synthetic_jpeg(200))
        with self.assertRaisesRegex(AppError, 'empty-nest'):
            self.runtime.predict(self.store.reference_root() / 'hen_a' / NAME)

    def test_empty_background_never_counts_as_a_second_hen(self):
        self.upload('empty/' + NAME, synthetic_empty())
        self.upload()
        self.assertFalse(self.runtime.status()['references_ready'])
        self.assertEqual(self.runtime.status()['identity_count'], 1)
        with self.assertRaises(AppError):
            self.runtime._references()

    def test_featureless_empty_reference_is_rejected(self):
        stream = io.BytesIO()
        Image.new('L', (640, 360), 100).save(stream, format='JPEG')
        with self.assertRaises(AppError):
            self.upload('empty/' + NAME, stream.getvalue())
        self.assertEqual(self.store.revision, 'none')

    def test_upload_restart_and_reference_readiness(self):
        self.upload()
        self.assertFalse(self.runtime.status()['references_ready'])
        self.upload('hen_b/' + NAME, synthetic_jpeg(200))
        self.assertFalse(self.runtime.status()['references_ready'])
        self.upload('empty/' + NAME, synthetic_empty())
        self.assertTrue(self.runtime.status()['references_ready'])
        self.assertEqual(self.runtime.status()['identity_count'], 2)
        self.assertEqual(self.runtime.status()['empty_reference_count'], 1)
        self.assertEqual(len(self.runtime._references()), 2)
        restart = Store(self.store.root, self.options)
        self.assertEqual(restart.revision, self.store.revision)
        self.assertEqual(restart.read('references', 'hen_a/' + NAME), self.image)

    def test_unknown_and_unreviewed_are_never_references(self):
        for path in ('unknown/' + NAME, 'unreviewed/' + NAME, 'hen_a/unreviewed/' + NAME,
                      'empty/unreviewed/' + NAME, '../hen_a/' + NAME, '/hen_a/' + NAME, 'hen_a/not-a-timestamp.jpg'):
            with self.assertRaises(AppError):
                self.upload(path)
        self.assertEqual(self.store.revision, 'none')

    def test_checksums_and_partial_jpeg_never_activate(self):
        with self.assertRaises(AppError):
            self.store.upload('hen_a/' + NAME, self.image, '0' * 64, 'none', False,
                              self.runtime.validate_reference)
        for data in (b'', self.image[:40], b'not an image'):
            with self.assertRaises(AppError):
                self.upload(data=data)
        self.assertEqual(self.store.revision, 'none')
        self.assertEqual(list(self.store.reference_root().iterdir()), [])

    def test_image_dimensions_are_checked_before_decode(self):
        with patch('ha_service.storage.MAX_PIXELS', 100):
            with self.assertRaises(AppError):
                image_valid(self.image)

    def test_no_measurable_hen_cannot_activate(self):
        stream = io.BytesIO()
        Image.new('L', (640, 360), 128).save(stream, 'JPEG')
        with self.assertRaises(AppError):
            self.upload(data=stream.getvalue())
        self.assertEqual(self.store.revision, 'none')

    def test_no_silent_overwrite_and_no_history(self):
        first = self.upload()
        with self.assertRaises(Conflict):
            self.upload(data=synthetic_jpeg(200))
        second = self.upload(data=synthetic_jpeg(200), overwrite=True)
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.read('references', 'hen_a/' + NAME), synthetic_jpeg(200))
        self.assertFalse((self.store.reference_root() / 'versions').exists())
        self.assertFalse(hasattr(self.store, 'rollback'))

    def test_stale_revision_rejected_and_identical_upload_is_idempotent(self):
        revision = self.upload()
        self.assertEqual(self.upload(), revision)
        with self.assertRaises(Conflict):
            self.store.upload('hen_b/' + NAME, self.image, digest(self.image), 'none', False,
                              self.runtime.validate_reference)

    def test_only_one_flat_reference_folder_is_retained(self):
        self.upload()
        self.upload('hen_b/' + NAME, synthetic_jpeg(200))
        self.upload('hen_b/' + SECOND, synthetic_jpeg(190))
        self.assertEqual(len(self.store.files('references')), 3)
        self.assertEqual(list((self.store.root / 'scratch').iterdir()), [])
        self.assertFalse((self.store.reference_root() / 'state.json').exists())

    def test_limit_and_disk_reserve_fail_without_activation(self):
        with patch('ha_service.storage.MAX_REFERENCES', 0):
            with self.assertRaises(AppError):
                self.upload()
        usage = shutil.disk_usage(str(self.root))
        with patch('ha_service.storage.shutil.disk_usage', return_value=usage._replace(free=0)):
            with self.assertRaises(AppError):
                self.upload()
        self.assertEqual(self.store.revision, 'none')

    def test_activation_waits_for_job_boundary(self):
        entered = threading.Event()
        errors = []

        def upload():
            entered.set()
            try:
                self.upload()
            except Exception as error:
                errors.append(error)

        with self.store.job():
            thread = threading.Thread(target=upload)
            thread.start()
            self.assertTrue(entered.wait(2))
            time.sleep(0.03)
            self.assertEqual(self.store.revision, 'none')
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertNotEqual(self.store.revision, 'none')

    def test_symlink_and_path_escape_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.store.root / 'captures' / 'empty').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AppError):
            self.store.read('captures', 'empty/' + NAME)
        for relative in ('../outside', '/outside', 'a//b', 'a/../../b'):
            with self.assertRaises(AppError):
                safe_path(self.store.root, relative)

    def test_retention_does_not_touch_reviewed_references(self):
        self.upload()
        old = self.store.root / 'results' / '20000101T120000_000000Z.json'
        old.write_text('{}')
        import os
        os.utime(str(old), (0, 0))
        self.assertEqual(self.store.retain(), 1)
        self.assertFalse(old.exists())
        self.assertEqual(len(self.store.files('references')), 1)

    def test_restart_cleans_only_known_incomplete_files(self):
        self.upload()
        capture_dir = self.store.root / 'captures' / 'empty'
        capture_dir.mkdir()
        pending = capture_dir / '.pending-abcdefgh'
        pending.write_bytes(b'incomplete synthetic image')
        result_pending = self.store.root / 'results' / '.pending-12345678'
        result_pending.write_bytes(b'{')
        state_pending = self.store.root / 'references' / '.pending-abcd1234'
        state_pending.write_bytes(b'{')
        restarted = Store(self.store.root, self.options)
        self.assertFalse(pending.exists())
        self.assertFalse(result_pending.exists())
        self.assertFalse(state_pending.exists())
        self.assertEqual(restarted.read('references', 'hen_a/' + NAME), self.image)

    def test_failed_activation_preserves_previous_state_on_disk(self):
        revision = self.upload()
        with patch('ha_service.storage.exchange_directories', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                self.upload('hen_b/' + NAME, synthetic_jpeg(200))
        self.assertEqual(self.store.revision, revision)
        restarted = Store(self.store.root, self.options)
        self.assertEqual(restarted.revision, revision)
        self.assertEqual(len(restarted.files('references')), 1)

    def test_retention_enforces_file_count_limit(self):
        from data_layout import MAX_HISTORY_FILES
        for index in range(MAX_HISTORY_FILES + 1):
            (self.store.root / 'results' / ('20000101T120000_%06dZ.json' % index)).write_text('{}')
        self.assertEqual(self.store.retain(), 1)
        self.assertEqual(len(self.store.files('results')), MAX_HISTORY_FILES)

    def test_result_day_survives_independently_of_thousand_photo_ceiling(self):
        folder = self.store.root / 'captures' / 'empty'
        folder.mkdir()
        # Synthetic storage entries only: no camera, image decode or capture.
        for index in range(1440):
            name = '20000101T120000_%06dZ' % index
            (folder / (name + '.jpg')).write_bytes(b'synthetic history')
            (self.store.root / 'results' / (name + '.json')).write_text('{}')
        self.assertEqual(self.store.retain(), 440)
        self.assertEqual(len(self.store.files('captures')), 1000)
        self.assertEqual(len(self.store.files('results')), 1440)

    def test_retention_enforces_combined_byte_cap(self):
        self.upload()
        self.options['capture_storage_mb'] = 16
        folder = self.store.root / 'captures' / 'empty'
        folder.mkdir()
        for index in range(3):
            path = folder / ('20000101T120000_%06dZ.jpg' % index)
            with path.open('wb') as handle:
                handle.truncate(6 * 1024 * 1024)  # Synthetic sparse files, no image decode/capture.
        self.assertEqual(self.store.retain(), 1)
        self.assertEqual(sum(path.stat().st_size for path in folder.iterdir()), 12 * 1024 * 1024)
        self.assertEqual(len(self.store.files('references')), 1)

    def test_status_contains_no_camera_credentials(self):
        self.options['camera_url'] = 'rtsp://synthetic-user:synthetic-password@camera.invalid/path'
        self.options['mqtt_password'] = 'synthetic-broker-password'
        status = json.dumps(self.runtime.status())
        self.assertNotIn('synthetic-', status)
        self.assertNotIn('camera_url', status)


class SchedulingAndMQTTTests(unittest.TestCase):
    def test_schedule_skips_missed_rounds(self):
        self.assertEqual(next_deadline(0, 10, 60), 60)
        self.assertEqual(next_deadline(0, 120, 60), 180)
        self.assertEqual(next_deadline(0, 60, 60), 120)

    def test_log_suppression_and_recovery(self):
        stream = io.StringIO()
        logger = logging.Logger('synthetic-test')
        logger.addHandler(logging.StreamHandler(stream))
        clock = [0]
        events = EventLog(logger, lambda: clock[0])
        events.failed('broker', 'Broker unreachable.')
        clock[0] = 10
        events.failed('broker', 'Broker unreachable.')
        self.assertEqual(stream.getvalue().count('Broker unreachable.'), 1)
        clock[0] = 901
        events.failed('broker', 'Broker unreachable.')
        events.recovered('broker', 'Broker recovered.')
        events.recovered('broker', 'Broker recovered.')
        self.assertEqual(stream.getvalue().count('Broker unreachable.'), 2)
        self.assertEqual(stream.getvalue().count('Broker recovered.'), 1)

    def test_occupant_states_and_discovery(self):
        for label, expected in (('no_chicken', 'empty'), ('unknown', 'unknown'), ('hen_a', 'Hen_a')):
            self.assertEqual(occupant({'label': label}), expected)
        config = discovery(validate({}))
        self.assertEqual(config['expire_after'], 180)
        self.assertIn('availability_topic', config)
        self.assertEqual(config['device']['identifiers'], ['munai_ha_nest'])
        self.assertNotIn('device_class', config)
        eggs = discovery(validate({}), 'egg_count')
        self.assertEqual(eggs['name'], 'Egg count')
        self.assertEqual(eggs['unique_id'], 'munai_ha_nest_egg_count')
        self.assertIn("else 'unknown'", eggs['value_template'])
        self.assertEqual(eggs['state_class'], 'measurement')
        for key in ('device', 'expire_after', 'availability_topic', 'state_topic'):
            self.assertEqual(config[key], eggs[key])

    def test_latest_event_survives_app_availability_changes(self):
        config = discovery(validate({'device_id': 'synthetic'}), 'latest_event')
        self.assertNotIn('availability_topic', config)
        self.assertNotIn('expire_after', config)
        publisher = MQTT.__new__(MQTT)
        publisher.base = 'munai/synthetic'
        publisher.connected = threading.Event()
        publisher.connected.set()
        publisher.send = Mock()
        state = {'occupant': 'empty', 'egg_count': 0,
                 'latest_event': '1 egg collected'}
        publisher.publish(state)
        publisher.offline()
        publisher.publish(state)
        event_calls = [call for call in publisher.send.call_args_list
                       if call.args[0] == config['state_topic']]
        self.assertEqual(len(event_calls), 2)
        self.assertTrue(all(call.args[1:] == ('1 egg collected', True)
                            for call in event_calls))

    def test_new_states_not_retained_and_availability_retained(self):
        mqtt = MQTT.__new__(MQTT)
        mqtt.base = 'munai/synthetic'
        sent = []
        mqtt.send = lambda *args: sent.append(args)
        mqtt.publish({'occupant': 'Hen_a', 'egg_count': 2, 'latest_event': None})
        self.assertEqual(sent[0][1:], ('No events yet', True))
        self.assertEqual(json.loads(sent[1][1]), {'occupant': 'Hen_a', 'egg_count': 2})
        self.assertFalse(sent[1][2])
        self.assertEqual(sent[2][0], 'munai/synthetic/presence')
        self.assertEqual(sent[2][1:], ('{}', False))
        self.assertEqual(sent[3][1:], ('online', True))

    def test_confirmed_egg_states_including_occupied_and_unestablished(self):
        publisher = MQTT.__new__(MQTT)
        publisher.base = 'munai/synthetic'
        publisher.send = Mock()
        for occupant_value, eggs in (('empty', 2), ('empty', 0), ('unknown', 3),
                                     ('Hen_a', 3), (None, None), ('Hen_a', None)):
            publisher.send.reset_mock()
            publisher.publish({'occupant': occupant_value, 'egg_count': eggs,
                               'latest_event': None})
            state = publisher.send.call_args_list[1].args
            self.assertEqual(json.loads(state[1])['egg_count'], eggs)
            self.assertFalse(state[2])
        for value in (-1, True, 1.5):
            publisher.send.reset_mock()
            with self.assertRaises(AppError):
                publisher.publish({'occupant': 'empty', 'egg_count': value, 'latest_event': None})
            publisher.send.assert_not_called()

    def test_reconnect_and_ha_birth_rediscover_without_replaying_state(self):
        options = validate({})
        publisher = MQTT.__new__(MQTT)
        publisher.connected = threading.Event()
        publisher.base = 'munai/synthetic'
        publisher.discovery_topic = 'homeassistant/sensor/synthetic/occupant/config'
        publisher.config = discovery(options)
        publisher.egg_discovery_topic = 'homeassistant/sensor/synthetic/egg_count/config'
        publisher.egg_config = discovery(options, 'egg_count')
        publisher.event_discovery_topic = 'homeassistant/sensor/synthetic/latest_event/config'
        publisher.event_config = discovery(options, 'latest_event')
        publisher.options = options
        publisher.daily_configs = {'daily_eggs': daily_discovery(options)}
        publisher.zone_configs = {}
        client = Mock()
        publisher.on_connect(client, None, None, 0, None)
        self.assertTrue(publisher.connected.is_set())
        self.assertEqual(client.publish.call_count, 5)
        self.assertEqual(client.publish.call_args_list[0].args[0], publisher.discovery_topic)
        self.assertEqual(client.publish.call_args_list[1].args[0], publisher.egg_discovery_topic)
        self.assertEqual(json.loads(client.publish.call_args_list[1].args[1]), publisher.egg_config)
        self.assertEqual(client.publish.call_args_list[2].args[0], publisher.event_discovery_topic)
        self.assertEqual(client.publish.call_args_list[4].args, ('munai/synthetic/availability', 'offline'))
        self.assertTrue(client.publish.call_args_list[1].kwargs['retain'])
        client.subscribe.assert_called_once_with('homeassistant/status', qos=1)
        message = Mock(topic='homeassistant/status', payload=b'online')
        publisher.on_message(client, None, message)
        self.assertEqual(client.publish.call_count, 9)
        self.assertTrue(all(call.kwargs['retain'] for call in client.publish.call_args_list))
        self.assertTrue(all(call.args[0] != publisher.base + '/state' for call in client.publish.call_args_list))
        publisher.on_disconnect(client, None, None, 0, None)
        self.assertFalse(publisher.connected.is_set())

    def test_publish_errors_do_not_expose_library_exception_text(self):
        publisher = MQTT.__new__(MQTT)
        publisher.connected = threading.Event()
        publisher.connected.set()
        publisher.client = Mock()
        publisher.client.publish.return_value.wait_for_publish.side_effect = RuntimeError('synthetic-secret-error')
        with self.assertRaises(AppError) as caught:
            publisher.send('munai/synthetic/state', '{}', False)
        self.assertNotIn('synthetic-secret-error', str(caught.exception))


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL required for local HTTPS fixtures')
class APITests(StorageFixture):
    def setUp(self):
        super().setUp()
        context, pin = tls_context(self.store.root)
        self.profile = {'url': '', 'fingerprint': pin}
        self.tokens = [{'name': 'synthetic', 'sha256': digest(DUMMY_TOKEN.encode()), 'scope': 'write'}]
        self.server = TransferServer(('127.0.0.1', 0), context, self.store, self.tokens,
                                     self.runtime.validate_reference, self.runtime.status)
        self.profile['url'] = 'https://127.0.0.1:' + str(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.remote = Remote(self.profile, DUMMY_TOKEN)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        super().tearDown()

    def test_real_https_cli_upload_download_and_export_roundtrip(self):
        source = self.root / 'local' / 'hen_a'
        source.mkdir(parents=True)
        (source / NAME).write_bytes(self.image)
        second = source.parent / 'hen_b'
        second.mkdir()
        (second / NAME).write_bytes(synthetic_jpeg(200))
        empty = source.parent / 'empty'
        empty.mkdir()
        (empty / NAME).write_bytes(synthetic_empty())
        with redirect_stdout(io.StringIO()):
            push(self.remote, source.parent, dry_run=True)
            self.assertEqual(self.store.revision, 'none')
            push(self.remote, source.parent)
            destination = self.root / 'download'
            pull(self.remote, 'references', destination, dry_run=True)
            self.assertFalse(destination.exists())
            pull(self.remote, 'references', destination)
        self.assertEqual((destination / 'hen_a' / NAME).read_bytes(), self.image)
        self.assertTrue(self.remote.request('GET', '/v1/status')['references_ready'])

    def test_https_replace_exact_set_preview_confirmation_and_no_rollback(self):
        self.upload()
        source = self.root / 'corrected'
        for identity, data in (('hen_b', synthetic_jpeg(200)), ('hen_c', self.image),
                               ('empty', synthetic_empty())):
            folder = source / identity
            folder.mkdir(parents=True)
            (folder / NAME).write_bytes(data)
        original = self.store.revision
        with redirect_stdout(io.StringIO()):
            push(self.remote, source, dry_run=True, replace=True)
            self.assertEqual(self.store.revision, original)
            with self.assertRaises(RemoteError):
                push(self.remote, source, replace=True)
            push(self.remote, source, replace=True, yes=True)
        self.assertEqual(len(self.store.files('references')), 3)
        self.assertFalse((self.store.reference_root() / 'hen_a').exists())
        self.assertTrue(self.runtime.status()['references_ready'])
        with self.assertRaises(RemoteError):
            self.remote.request('POST', '/v1/references/rollback', b'')
        self.tokens[0]['scope'] = 'read'
        data = reference_archive([])
        with self.assertRaises(RemoteError):
            self.remote.request('POST', '/v1/references/replace', data,
                                headers={'X-Content-SHA256': digest(data), 'If-Match': self.store.revision})

    def test_replace_rejects_bad_checksum_and_unbounded_body(self):
        data = reference_archive([('hen_a/' + NAME, self.image)])
        for headers in ({'X-Content-SHA256': '0' * 64, 'If-Match': 'none'},
                        {'Content-Length': '0'}, {'Transfer-Encoding': 'chunked'},
                        {'Content-Length': str(130 * 1024 * 1024)}):
            with self.assertRaises(RemoteError):
                self.remote.request('POST', '/v1/references/replace', data, headers=headers)
        self.assertEqual(self.store.revision, 'none')

    def test_download_conflict_requires_explicit_overwrite(self):
        self.upload()
        destination = self.root / 'download' / 'hen_a'
        destination.mkdir(parents=True)
        (destination / NAME).write_bytes(b'keep this synthetic local file')
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(RemoteError):
                pull(self.remote, 'references', destination.parent)
            self.assertEqual((destination / NAME).read_bytes(), b'keep this synthetic local file')
            pull(self.remote, 'references', destination.parent, overwrite=True)
        self.assertEqual((destination / NAME).read_bytes(), self.image)

    def test_no_access_logs_or_token_echo(self):
        import contextlib
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream), redirect_stdout(stream):
            with self.assertRaises(RemoteError):
                Remote(self.profile, 'incorrect-synthetic-token-not-a-secret').request('GET', '/v1/status')
            self.remote.request('GET', '/v1/status')
        self.assertEqual(stream.getvalue(), '')

    def test_capture_and_result_downloads_use_only_managed_roots(self):
        folder = self.store.root / 'captures' / 'empty'
        folder.mkdir()
        (folder / NAME).write_bytes(self.image)
        result_name = NAME.replace('.jpg', '.json')
        (self.store.root / 'results' / result_name).write_text('{"occupant":"empty"}')
        with redirect_stdout(io.StringIO()):
            pull(self.remote, 'captures', self.root / 'download-captures')
            pull(self.remote, 'results', self.root / 'download-results')
        self.assertEqual((self.root / 'download-captures' / 'empty' / NAME).read_bytes(), self.image)
        self.assertEqual(json.loads((self.root / 'download-results' / result_name).read_text()), {'occupant': 'empty'})

    def test_https_training_download_prune_and_write_scope(self):
        from html import unescape
        from ha_service.confirmations import Confirmations
        from munai_remote import pull_all
        source = 'captures/empty/' + NAME
        path = self.store.root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.image)
        confirmations = Confirmations(self.runtime.journal)
        button = confirmations.button(source, {'label': 'no_chicken', 'egg_count': 2})
        request = json.loads(unescape(button.split('data-confirm="', 1)[1].split('"', 1)[0]))
        confirmations.save(request)
        self.tokens[0]['scope'] = 'read'
        self.assertEqual(len(self.remote.request('GET', '/v1/files/training')['files']), 2)
        root = self.root / 'download-training'
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(RemoteError):
                pull_all(self.remote, root, remove_transferred=True, yes=True)
        self.assertEqual(len(self.server.training.files()), 2)
        self.tokens[0]['scope'] = 'write'
        with redirect_stdout(io.StringIO()):
            pull_all(self.remote, root, remove_transferred=True, yes=True)
        self.assertFalse(self.server.training.files())
        self.assertEqual((root / source).read_bytes(), self.image)
        self.assertTrue(path.exists())

    def test_wrong_or_revoked_token_cannot_read_status(self):
        for token in (None, 'incorrect-synthetic-token-not-a-secret'):
            with self.assertRaises(RemoteError):
                Remote(self.profile, token).request('GET', '/v1/status')
        self.server.tokens = []
        with self.assertRaises(RemoteError):
            self.remote.request('GET', '/v1/status')

    def test_read_scope_cannot_upload(self):
        self.tokens[0]['scope'] = 'read'
        self.assertEqual(self.remote.request('GET', '/v1/status')['reference_count'], 0)
        with self.assertRaises(RemoteError):
            self.remote.request('PUT', '/v1/references/hen_a/' + NAME, self.image,
                                headers={'X-Content-SHA256': digest(self.image), 'If-Match': 'none'})
        self.assertEqual(self.store.revision, 'none')

    def test_fingerprint_mismatch_fails_before_http_or_token_is_sent(self):
        from ha_service.api import Handler
        with patch.object(Handler, 'authorized', side_effect=AssertionError('HTTP must not be sent')) as auth:
            with self.assertRaises(RemoteError) as caught:
                Remote(dict(self.profile, fingerprint='0' * 64), DUMMY_TOKEN).request('GET', '/v1/status')
            self.assertIn('No token was sent', str(caught.exception))
            self.assertEqual(auth.call_count, 0)

    def test_https_paths_upload_bounds_and_missing_headers(self):
        for path in ('/v1/files/references/../outside.jpg', '/v1/files/references/%2e%2e/outside.jpg',
                     '/v1/files/references/hen_a%2f..%2foutside.jpg', '/v1/files/references?path=outside'):
            with self.assertRaises(RemoteError):
                self.remote.request('GET', path)
        for headers in ({'Content-Length': '0'}, {'Content-Length': str(9 * 1024 * 1024)},
                        {'Transfer-Encoding': 'chunked'}, {}):
            with self.assertRaises(RemoteError):
                self.remote.request('PUT', '/v1/references/hen_a/' + NAME, b'x', headers=headers)
        self.assertEqual(self.store.revision, 'none')

    def test_certificate_persists_across_restart(self):
        unused, pin = tls_context(self.store.root)
        self.assertEqual(pin, self.profile['fingerprint'])

    def test_pairing_stores_token_in_private_file_without_printing_it(self):
        output = io.StringIO()
        config = self.root / 'profiles.json'
        args = ['--config', str(config), 'remote', 'pair', self.profile['url'],
                '--fingerprint', self.profile['fingerprint']]
        with redirect_stdout(output):
            self.assertEqual(main(args), 0)
        profiles = json.loads(config.read_text())  # Synthetic local HTTPS fixture only.
        token = profiles['green']['token']
        self.assertNotIn(token, output.getvalue())
        self.assertIn(digest(token.encode()), output.getvalue())
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        self.server.tokens = [{'name': 'synthetic-file-client', 'scope': 'write', 'sha256': digest(token.encode())}]
        status_output = io.StringIO()
        with redirect_stdout(status_output):
            self.assertEqual(main(['--config', str(config), 'remote', 'status']), 0)
        self.assertNotIn(token, status_output.getvalue())
        self.assertIn('reference_count', status_output.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--config', str(config), 'remote', 'logout']), 0)
        self.assertEqual(json.loads(config.read_text()), {})


class CLISafetyTests(unittest.TestCase):
    def test_https_origins_only(self):
        for url in ('http://example.invalid', 'https://user:password@example.invalid',
                    'https://example.invalid/path', 'https://example.invalid?token=test'):
            with self.assertRaises(RemoteError):
                endpoint(url)

    def test_unsafe_download_paths_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory(prefix='munai-cli-test-') as directory:
            root = Path(directory)
            (root / 'linked').symlink_to(root / 'outside', target_is_directory=True)
            for path in ('../outside', '/outside', 'linked/file.jpg', 'a//file.jpg'):
                with self.assertRaises(RemoteError):
                    download_path(root, path)

    def test_local_reference_collection_is_nonrecursive_and_excludes_unknown(self):
        with tempfile.TemporaryDirectory(prefix='munai-cli-test-') as directory:
            root = Path(directory)
            for folder in ('hen_a', 'hen_a/unreviewed', 'unknown'):
                destination = root / folder
                destination.mkdir(parents=True, exist_ok=True)
                (destination / NAME).write_bytes(b'synthetic-data-not-uploaded')
            self.assertEqual([item[0] for item in local_references(root)], ['hen_a/' + NAME])


if __name__ == '__main__':
    unittest.main()
