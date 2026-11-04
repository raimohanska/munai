"""Synthetic Web UI training confirmations; never exercise camera capture."""
import copy
from contextlib import redirect_stdout
from email.message import Message
from html import unescape
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from ha_service.captures_web import render as captures_page
from ha_service.confirmations import Confirmations, MAX_REQUEST_BYTES
from ha_service.journal import Journal
from ha_service.journal_web import JournalHandler, render as journal_page
from ha_service.options import AppError, validate
from ha_service.references_web import render as references_page
from ha_service.status_web import render as status_page
from ha_service.storage import Conflict, Store, digest
from ha_service.training import TrainingArchive
from munai_remote import RemoteError, parser, pull_all, pull_training

STAMP = '20000101T120000_000000Z'
SOURCE = 'captures/unknown/' + STAMP + '.jpg'
FRAME = STAMP + '_' + 'a' * 32


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.store = Store(Path(self.temp.name), validate({'box_split': 'vertical'}))
        self.journal = Journal(self.store)
        self.runtime = Mock()
        output = io.BytesIO()
        Image.new('RGB', (128, 128), (80, 90, 100)).save(output, 'JPEG')
        self.image = output.getvalue()
        self.runtime.web_image.return_value = self.image
        self.confirmations = Confirmations(self.journal, self.runtime)
        path = self.store.root / SOURCE
        path.parent.mkdir(parents=True)
        path.write_bytes(self.image)
        self.raw = {'occupant': 'unknown', 'egg_count': None, 'ambiguous': False, 'zones': {
            'left': {'label': 'hen_a', 'egg_count': None, 'obscured': False},
            'center': {'label': 'no_chicken', 'egg_count': None, 'obscured': False},
            'right': {'label': 'no_chicken', 'egg_count': 2, 'obscured': False}}}
        self.result = self.store.root / 'results' / (STAMP + '.json')
        self.result.write_text(json.dumps(dict(timestamp=STAMP, raw_prediction=self.raw)))

    def tearDown(self):
        self.temp.cleanup()

    def requests(self, page):
        if isinstance(page, bytes):
            page = page.decode('utf-8')
        return [json.loads(unescape(value)) for value in re.findall(r'data-confirm="([^"]+)"', page)]

    def request(self, source=SOURCE, **kwargs):
        return self.requests(self.confirmations.button(source, self.raw, 'vertical', **kwargs))[0]

    def records(self):
        return [json.loads(path.read_text()) for path in
                (self.confirmations.root / 'annotations').glob('*.json')]

    def test_retains_original_and_complete_zones_without_affecting_recognition(self):
        original_result = self.result.read_bytes()
        revision = self.store.revision
        request = self.request()
        self.confirmations.save(request)
        record = self.records()[0]
        self.assertEqual(record['interpretation'], self.raw)
        self.assertEqual(record['layout'], 'vertical')
        self.assertEqual(record['region'], 'whole')
        self.assertTrue(record['human_confirmed'])
        self.assertEqual(record['schema'], 1)
        self.assertIn('confirmed_at', record)
        self.assertIn('app_version', record)
        self.assertEqual(record['sha256'], digest(self.image))
        retained = self.confirmations.root / record['image']
        self.assertEqual(retained.read_bytes(), self.image)
        self.assertEqual(self.store.revision, revision)
        self.assertFalse(self.store.files('references'))
        self.assertEqual(self.result.read_bytes(), original_result)
        # The training copy has its own lifetime, not a retention exemption.
        (self.store.root / SOURCE).unlink()
        self.result.unlink()
        self.assertEqual(retained.read_bytes(), self.image)
        self.assertEqual(self.confirmations.save(request), {'confirmed': True})

    def test_idempotence_image_dedup_and_persistent_button_state(self):
        request = self.request()
        self.confirmations.save(request)
        self.confirmations.save(request)
        self.assertEqual(len(self.records()), 1)
        restarted = Confirmations(self.journal)
        button = restarted.button(SOURCE, self.raw, 'vertical')
        self.assertIn('disabled', button)
        self.assertIn('>Confirmed<', button)
        # Another scope preserves a distinct annotation but not another JPEG.
        self.confirmations.save(self.request(region='right', view='journal'))
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(len(list((self.confirmations.root / 'images').glob('*.jpg'))), 1)

    def test_displayed_snapshot_is_not_replaced_by_newer_results(self):
        request = self.request()
        self.result.write_text(json.dumps(dict(timestamp=STAMP, raw_prediction={'label': 'hen_b'})))
        self.confirmations.save(request)
        self.assertEqual(self.records()[0]['interpretation'], self.raw)
        changed = copy.deepcopy(request)
        changed['snapshot']['interpretation']['zones']['left']['label'] = 'hen_b'
        with self.assertRaises(Conflict):
            self.confirmations.save(changed)
        with self.assertRaises(Conflict):
            Confirmations(self.journal).save(request)

    def test_replaced_deleted_or_expired_images_are_not_silently_substituted(self):
        request = self.request()
        (self.store.root / SOURCE).write_bytes(self.image + b'changed')
        with self.assertRaises(Conflict):
            self.confirmations.save(request)
        (self.store.root / SOURCE).unlink()
        with self.assertRaises(FileNotFoundError):
            self.confirmations.save(request)
        self.assertEqual(self.confirmations.button(SOURCE, self.raw), '')
        latest = self.request(source='latest/' + STAMP + '.jpg')
        self.runtime.web_image.return_value = None
        with self.assertRaises(FileNotFoundError):
            self.confirmations.save(latest)
        self.assertFalse(self.records())

    def test_uncertainty_and_counts_are_preserved_without_fabricated_labels(self):
        self.raw['zones']['right'].update(label='unknown', obscured=True, egg_count=None)
        self.confirmations.save(self.request())
        self.assertEqual(self.records()[0]['interpretation'], self.raw)

    def test_low_storage_limit_and_failed_metadata_write_are_retryable(self):
        request = self.request()
        with patch.object(self.store, 'require_space', side_effect=AppError('Low storage')):
            with self.assertRaises(AppError):
                self.confirmations.save(request)
        with patch('ha_service.confirmations.MAX_CONFIRMATIONS', 0):
            with self.assertRaises(AppError):
                self.confirmations.save(request)
        with patch('ha_service.confirmations.atomic_json', side_effect=OSError('Synthetic interruption')):
            with self.assertRaises(OSError):
                self.confirmations.save(request)
        self.assertFalse(self.records())
        self.confirmations.save(request)
        self.assertEqual(len(self.records()), 1)

    def test_invalid_sources_symlinks_and_oversized_payloads(self):
        for source in ('../private.jpg', 'latest/invalid.jpg', 'images/../private.jpg',
                       'captures/../../private.jpg', 'references/none/../private.jpg'):
            with self.assertRaises(AppError):
                self.confirmations.button(source, self.raw)
        path = self.store.root / SOURCE
        path.unlink()
        path.symlink_to(self.result)
        with self.assertRaises(AppError):
            self.confirmations.button(SOURCE, self.raw)
        with self.assertRaises(AppError):
            self.confirmations.button('latest/' + STAMP + '.jpg', {'label': 'x' * MAX_REQUEST_BYTES})

    def test_all_image_pages_have_scoped_confirmations_and_relative_script_urls(self):
        captures = captures_page(self.store, base='../', confirmations=self.confirmations)
        self.assertEqual(self.requests(captures)[0]['snapshot']['interpretation'], self.raw)
        self.assertIn(b'src="../confirm.js" data-confirm-api="../confirm"', captures)
        self.result.unlink()
        self.assertFalse(self.requests(captures_page(self.store, confirmations=self.confirmations)))
        snapshot = dict(observation=dict(timestamp=STAMP, raw_prediction=self.raw,
                        occupant='hen_b', latest_event=None), has_image=True,
                        capture_state='Active', mqtt_state='Disabled', stale_after=180)
        status = status_page(snapshot, confirmations=self.confirmations)
        request = self.requests(status)[0]
        self.assertEqual(request['snapshot']['interpretation']['zones'], self.raw['zones'])
        self.assertNotIn('latest_event', request['snapshot']['interpretation'])
        self.confirmations.save(request)
        (self.journal.root / 'images' / (FRAME + '.jpg')).write_bytes(self.image)
        entry = dict(id=FRAME, text='Synthetic event', zone='right', layout='vertical', evidence=[
            dict(role='Egg count confirmation', frames=[dict(id=FRAME, label='no_chicken', egg_count=2)])])
        (self.journal.root / 'entries' / (FRAME + '.json')).write_text(json.dumps(entry))
        journal = journal_page(self.journal, 0, confirmations=self.confirmations)
        scoped = self.requests(journal)[0]['snapshot']
        self.assertEqual(scoped['region'], 'right')
        self.assertEqual(scoped['interpretation'], {'label': 'no_chicken', 'egg_count': 2})
        relative = 'hen_a/' + STAMP + '.jpg'
        self.store.upload(relative, self.image, digest(self.image), self.store.revision, False, lambda path: None)
        references = references_page(self.store, confirmations=self.confirmations)
        reference = self.requests(references)[0]
        self.assertEqual(reference['snapshot']['interpretation'], {'label': 'hen_a'})
        self.assertEqual(reference['snapshot']['region'], 'crop')
        self.confirmations.save(reference)

    def post(self, request, address='172.30.32.2', **headers):
        handler = JournalHandler.__new__(JournalHandler)
        handler.path = '/confirm'
        handler.client_address = (address, 12345)
        handler.server = Mock(confirmations=self.confirmations)
        data = json.dumps(request).encode('utf-8')
        handler.headers = Message()
        for key, value in dict({'Content-Length': str(len(data)),
                                'Content-Type': 'application/json'}, **headers).items():
            handler.headers[key] = value
        handler.rfile = io.BytesIO(data)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_POST()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()

    def test_ingress_signed_bounded_post_and_error_responses(self):
        request = self.request()
        self.assertEqual(self.post(request, address='127.0.0.1')[0], 403)
        self.assertEqual(self.post(request, **{'Content-Type': 'text/plain'})[0], 400)
        self.assertEqual(self.post(request, **{'Content-Length': str(MAX_REQUEST_BYTES + 1)})[0], 400)
        changed = copy.deepcopy(request)
        changed['signature'] = 'invalid'
        self.assertEqual(self.post(changed)[0], 409)
        self.assertEqual(self.post({})[0], 400)
        self.assertFalse(self.records())
        self.assertEqual(self.post(request)[0], 200)
        self.assertEqual(self.post(request)[0], 200)
        self.assertEqual(len(self.records()), 1)

    def transfer_remote(self):
        archive = TrainingArchive(self.store)
        def request(method, path, data=None, **kwargs):
            if path == '/v1/training/prune':
                return archive.prune(json.loads(data.decode('utf-8'))['files'])
            if path == '/v1/files/training':
                return {'files': archive.files()}
            if path.startswith('/v1/files/training/'):
                return archive.read(path[len('/v1/files/training/'):])
            if path in ('/v1/files/captures', '/v1/files/results'):
                return {'files': self.store.files(path.rsplit('/', 1)[-1])}
            kind, relative = path[len('/v1/files/'):].split('/', 1)
            return self.store.read(kind, relative)
        return Mock(request=Mock(side_effect=request))

    def test_unified_pull_removes_only_verified_training_and_is_repeatable(self):
        self.confirmations.save(self.request())
        root = Path(self.temp.name) / 'local'
        remote = self.transfer_remote()
        with redirect_stdout(io.StringIO()):
            pull_all(remote, root, dry_run=True, remove_transferred=True)
            self.assertFalse(root.exists())
            with self.assertRaises(RemoteError):
                pull_all(remote, root, remove_transferred=True)
            pull_all(remote, root, remove_transferred=True, yes=True)
            pull_all(remote, root, remove_transferred=True, yes=True)
        self.assertEqual((root / SOURCE).read_bytes(), self.image)
        self.assertEqual((root / 'results' / self.result.name).read_bytes(), self.result.read_bytes())
        annotations = list((root / 'training' / 'annotations').glob('*.json'))
        self.assertEqual(len(annotations), 1)
        record = json.loads(annotations[0].read_text())
        self.assertEqual((root / 'training' / record['image']).read_bytes(), self.image)
        self.assertFalse(TrainingArchive(self.store).files())
        self.assertTrue((self.store.root / SOURCE).exists())
        self.assertTrue(self.result.exists())
        parsed = parser().parse_args(['pull', 'local-data/production', '--remove-transferred', '--yes'])
        self.assertIsNone(parsed.directory)
        self.assertEqual(parsed.kind, 'local-data/production')

    def test_transfer_failures_or_local_conflicts_never_acknowledge(self):
        self.confirmations.save(self.request())
        remote = self.transfer_remote()
        actual = remote.request.side_effect
        def corrupt(method, path, **kwargs):
            value = actual(method, path, **kwargs)
            return b'corrupt' if '/training/images/' in path else value
        remote.request.side_effect = corrupt
        root = Path(self.temp.name) / 'local'
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(RemoteError):
                pull_training(remote, root, remove_transferred=True, yes=True)
        self.assertFalse(any(call.args[0] == 'POST' for call in remote.request.call_args_list))
        self.assertEqual(len(self.records()), 1)
        remote.request.side_effect = actual
        with redirect_stdout(io.StringIO()):
            pull_training(remote, root)
        image = next((root / 'images').glob('*.jpg'))
        image.write_bytes(b'keep local changes')
        remote.request.reset_mock()
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(RemoteError):
                pull_training(remote, root, remove_transferred=True, yes=True)
        self.assertEqual(image.read_bytes(), b'keep local changes')
        self.assertFalse(any(call.args[0] == 'POST' for call in remote.request.call_args_list))

    def test_prune_rejects_changed_hashes_partial_examples_and_unsafe_paths(self):
        self.confirmations.save(self.request())
        archive = TrainingArchive(self.store)
        manifest = archive.files()
        changed = copy.deepcopy(manifest)
        changed[0]['sha256'] = '0' * 64
        with self.assertRaises(Conflict):
            archive.prune(changed)
        annotation = [item for item in manifest if item['path'].startswith('annotations/')]
        with self.assertRaises(AppError):
            archive.prune(annotation)
        with self.assertRaises(AppError):
            archive.prune([dict(path='../private.json', bytes=1, sha256='a' * 64)])
        self.assertEqual(archive.files(), manifest)
        self.assertEqual(archive.prune(manifest)['removed'], 2)
        self.assertEqual(archive.prune(manifest)['removed'], 0)

    def test_prune_keeps_shared_images_for_new_confirmations(self):
        self.confirmations.save(self.request())
        archive = TrainingArchive(self.store)
        manifest = archive.files()
        self.confirmations.save(self.request(region='right'))
        self.assertEqual(archive.prune(manifest)['removed'], 1)
        self.assertEqual(len(self.records()), 1)
        remaining = archive.files()
        self.assertEqual(len(remaining), 2)
        self.assertEqual(archive.prune(remaining)['removed'], 2)

    def test_training_byte_cap_blocks_new_saves_but_never_deletes_existing(self):
        self.confirmations.save(self.request())
        files = TrainingArchive(self.store).files()
        self.store.options['training_storage_mb'] = 0
        with self.assertRaises(AppError) as caught:
            self.confirmations.save(self.request(region='right'))
        self.assertEqual(caught.exception.code, 'training_limit')
        self.assertEqual(TrainingArchive(self.store).files(), files)
        for invalid in (0, 15, 4097, True, '512'):
            with self.assertRaises(AppError):
                validate({'training_storage_mb': invalid})


if __name__ == '__main__':
    unittest.main()
