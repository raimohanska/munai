"""Synthetic human teaching, provenance, undo and Ingress mutation tests."""
import io
import json
from email.message import Message
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from PIL import Image

from ha_service.captures_web import render as captures_page
from ha_service.journal import Journal
from ha_service.journal_web import JournalHandler, render as journal_page
from ha_service.options import AppError, validate
from ha_service.reviews import Reviews
from ha_service.storage import Conflict, Store, digest

STAMP = '20000101T120000_000000Z'
SOURCE = 'captures/unknown/' + STAMP + '.jpg'
FRAME = STAMP + '_' + 'a' * 32


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.store = Store(Path(self.temp.name) / 'data', validate({'box_split': 'vertical'}))
        self.journal = Journal(self.store)
        self.validator = Mock()
        self.reviews = Reviews(self.journal, self.validator)
        image = Image.new('RGB', (256, 128), (80, 90, 100))
        for x in range(128, 256):
            for y in range(128):
                image.putpixel((x, y), (170, 180, 190))
        output = io.BytesIO()
        image.save(output, 'JPEG')
        self.image = output.getvalue()
        path = self.store.root / SOURCE
        path.parent.mkdir(parents=True)
        path.write_bytes(self.image)
        self.result = self.store.root / 'results' / (STAMP + '.json')
        self.result.write_text(json.dumps(dict(timestamp=STAMP, raw_prediction={
            'zones': {'left': {'label': 'unknown'}, 'right': {'label': 'unknown'}}})))

    def tearDown(self):
        self.temp.cleanup()

    def request(self, label='hen_a', region='right', action='save', source=SOURCE, **overrides):
        context = self.reviews.context(source)
        value = {key: context[key] for key in ('image', 'layout', 'revision', 'references')}
        value.update(source=source, label=label, region=region, action=action)
        value.update(overrides)
        return value

    def save(self, **kwargs):
        return self.reviews.save(self.request(**kwargs))

    def test_regions_follow_spatial_order(self):
        self.assertEqual(self.reviews.context(SOURCE)['regions'],
                         ['whole', 'left', 'center', 'right'])
        self.result.write_text(json.dumps(dict(timestamp=STAMP, raw_prediction={'zones': {'top': {}}})))
        self.assertEqual(self.reviews.context(SOURCE)['regions'],
                         ['whole', 'top', 'center', 'bottom'])

    def test_promote_correct_crop_validate_activate_and_undo(self):
        original_result = self.result.read_bytes()
        value = self.save()
        record = value['records']['vertical-right']
        self.assertEqual(record['status'], 'active')
        self.assertTrue(record['created'])
        self.assertIn('hen_a', value['identities'])
        self.assertTrue(record['reference'].endswith('-right.jpg'))
        with Image.open(io.BytesIO(self.store.read('references', record['reference']))) as crop:
            self.assertEqual(crop.size, (128, 128))
            self.assertGreater(crop.getpixel((64, 64))[0], 150)
        self.validator.assert_called_once()
        revision = self.store.revision
        self.save()
        self.assertEqual(revision, self.store.revision)
        self.assertEqual(len(self.store.files('references')), 1)
        self.save(action='undo')
        self.assertEqual(self.store.files('references'), [])
        self.assertEqual(self.result.read_bytes(), original_result)
        self.assertEqual((self.store.root / SOURCE).read_bytes(), self.image)

    def test_obscured_partial_empty_and_whole_hen_are_annotation_only(self):
        for label, region in (('obscured', 'left'), ('empty', 'right'), ('hen_a', 'whole')):
            value = self.save(label=label, region=region)
            self.assertEqual(value['records']['vertical-' + region]['status'], 'annotation')
        self.assertEqual(self.store.files('references'), [])
        self.validator.assert_not_called()

    def test_whole_empty_keeps_original_full_frame(self):
        value = self.save(label='empty', region='whole')
        record = value['records']['vertical-whole']
        self.assertEqual(self.store.read('references', record['reference']), self.image)

    def test_no_layout_fallback_and_no_cross_layout_training(self):
        self.result.unlink()
        context = self.reviews.context(SOURCE)
        self.assertEqual(context['layout'], 'unknown')
        self.assertEqual(context['regions'], ['whole'])
        self.assertEqual(self.save(label='empty', region='whole')['records']['unknown-whole']['status'], 'annotation')
        self.store.options['box_split'] = 'horizontal'
        self.result.write_text(json.dumps(dict(timestamp=STAMP, raw_prediction={'zones': {'left': {}}})))
        self.assertEqual(self.save()['records']['vertical-right']['status'], 'annotation')

    def test_whole_mode_and_horizontal_center_crop(self):
        self.store.options['box_split'] = 'none'
        self.result.write_text(json.dumps(dict(timestamp=STAMP, raw_prediction={'occupant': 'unknown'})))
        value = self.save(region='whole')
        self.assertEqual(value['records']['none-whole']['status'], 'active')
        self.save(region='whole', action='undo')
        self.store.options['box_split'] = 'horizontal'
        self.result.write_text(json.dumps(dict(timestamp=STAMP, raw_prediction={'zones': {'top': {}}})))
        value = self.save(region='center')
        with Image.open(io.BytesIO(self.store.read('references', value['records']['horizontal-center']['reference']))) as image:
            self.assertEqual(image.size, (256, 64))

    def journal_image(self):
        (self.journal.root / 'images' / (FRAME + '.jpg')).write_bytes(self.image)
        entry = dict(id=FRAME, text='Synthetic event', zone='right', layout='vertical',
                     evidence=[dict(role='Identity confirmation', frames=[
                         dict(id=FRAME, label='unknown', egg_count=None, zone='right')])])
        path = self.journal.root / 'entries' / (FRAME + '.json')
        path.write_text(json.dumps(entry))
        return path

    def test_shared_journal_capture_review_and_component(self):
        path = self.journal_image()
        original = path.read_bytes()
        capture = self.save(label='obscured')
        source = 'images/' + FRAME + '.jpg'
        context = self.reviews.context(source)
        self.assertEqual(capture['image'], context['image'])
        self.assertEqual(capture['records'], context['records'])
        self.save(action='undo', source=source)
        self.assertFalse(self.reviews.context(SOURCE)['records'])
        self.assertEqual(path.read_bytes(), original)
        for page in (captures_page(self.store), journal_page(self.journal, 0)):
            self.assertIn(b'data-review-source=', page)
            self.assertEqual(page.count(b'id="review-dialog"'), 1)
            self.assertIn(b'src="review.js"', page)
            self.assertNotIn(b'review-confirm', page)
            self.assertNotIn(b'I checked this view', page)
        self.assertIn(b'data-review-region="right"', journal_page(self.journal, 0))

    def test_validation_failure_does_not_activate_or_mark_success(self):
        self.validator.side_effect = AppError('Synthetic invalid reference.')
        with self.assertRaises(AppError):
            self.save()
        self.assertEqual(self.store.files('references'), [])
        self.assertFalse(self.reviews.context(SOURCE)['records'])

    def test_interrupted_activation_has_durable_intent_and_can_be_undone(self):
        upload = self.store.upload
        def interrupted(*args, **kwargs):
            upload(*args, **kwargs)
            raise OSError('Synthetic interruption after activation')
        with patch.object(self.store, 'upload', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.save()
        record = self.reviews.context(SOURCE)['records']['vertical-right']
        self.assertEqual(record['status'], 'pending')
        self.assertEqual(len(self.store.files('references')), 1)
        restarted = Reviews(self.journal, self.validator)
        self.assertEqual(restarted.context(SOURCE)['records']['vertical-right']['status'], 'pending')
        self.save(action='undo')
        self.assertEqual(self.store.files('references'), [])

    def test_duplicate_shared_reference_must_undo_dependents_first(self):
        output = io.BytesIO()
        Image.new('RGB', (256, 128), (80, 90, 100)).save(output, 'JPEG')
        (self.store.root / SOURCE).write_bytes(output.getvalue())
        first = self.save()['records']['vertical-right']
        second = self.save(region='left')['records']['vertical-left']
        self.assertEqual(first['reference'], second['reference'])
        self.assertFalse(second['created'])
        with self.assertRaises(Conflict):
            self.save(action='undo')
        self.save(region='left', action='undo')
        self.assertEqual(len(self.store.files('references')), 1)
        self.save(action='undo')
        self.assertFalse(self.store.files('references'))

    def test_deleted_source_and_low_space_do_not_create_reviews(self):
        with patch.object(self.store, 'require_space', side_effect=AppError('Synthetic low storage')):
            with self.assertRaises(AppError):
                self.save(label='obscured')
        self.assertFalse(self.reviews.context(SOURCE)['records'])
        (self.store.root / SOURCE).unlink()
        with self.assertRaises(FileNotFoundError):
            self.reviews.context(SOURCE)

    def test_conflicts_invalid_labels_regions_and_source(self):
        for value in ('empty/../../private', '<script>', 'unknown', 'obscured_hen!' , 'reviewed/name'):
            with self.assertRaises(AppError):
                self.save(label=value)
        for kwargs in ({'region': 'top'}, {'source': 'references/empty/test.jpg'}):
            with self.assertRaises(AppError):
                self.reviews.save(self.request(**kwargs))
        for field in ('image', 'layout', 'revision', 'references'):
            with self.assertRaises(Conflict):
                self.reviews.save(self.request(**{field: 'stale'}))
        self.save(label='obscured')
        with self.assertRaises(Conflict):
            self.save(label='hen_a')

    def test_undo_does_not_delete_external_replacement_or_existing_duplicate(self):
        record = self.save(label='empty', region='whole')['records']['vertical-whole']
        path = self.store.reference_root() / record['reference']
        path.write_bytes(b'changed elsewhere')
        with self.assertRaises(Conflict):
            self.save(region='whole', action='undo')
        self.assertEqual(path.read_bytes(), b'changed elsewhere')
        path.write_bytes(self.image)
        self.save(region='whole', action='undo')
        relative = 'empty/' + STAMP + '.jpg'
        self.store.upload(relative, self.image, digest(self.image), self.store.revision, False, self.validator)
        record = self.save(label='empty', region='whole')['records']['vertical-whole']
        self.assertFalse(record['created'])
        self.save(region='whole', action='undo')
        self.assertEqual(self.store.read('references', relative), self.image)

    def test_desktop_replacement_cannot_silently_drop_ui_reference(self):
        self.save(label='empty', region='whole')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for label in ('hen_a', 'hen_b', 'empty'):
                archive.writestr(label + '/' + STAMP + '.jpg', self.image)
        with self.assertRaises(Conflict):
            self.store.replace_references(output, digest(output.getvalue()), self.store.revision, self.validator)

    def handler(self, body, token=True, address='172.30.32.2', headers=None):
        handler = JournalHandler.__new__(JournalHandler)
        handler.path = '/review'
        handler.client_address = (address, 12345)
        handler.server = Mock(journal=self.journal, reviews=self.reviews)
        handler.headers = Message()
        for key, value in (headers or {'Content-Type': 'application/json', 'Content-Length': str(len(body))}).items():
            handler.headers[key] = value
        if token:
            handler.headers['X-Munai-Review'] = self.reviews.token
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_ingress_csrf_content_type_size_and_conflicts(self):
        body = json.dumps(self.request(label='obscured')).encode()
        for kwargs in ({'token': False}, {'address': '127.0.0.1'}):
            handler = self.handler(body, **kwargs)
            handler.do_POST()
            self.assertEqual(handler.send_response.call_args.args[0], 403)
        for headers in ({'Content-Type': 'text/plain', 'Content-Length': str(len(body))},
                        {'Content-Type': 'application/json', 'Content-Length': '99999'},
                        {'Content-Type': 'application/json', 'Transfer-Encoding': 'chunked'}):
            handler = self.handler(body, headers=headers)
            handler.do_POST()
            self.assertEqual(handler.send_response.call_args.args[0], 400)
        handler = self.handler(body)
        handler.do_POST()
        self.assertEqual(handler.send_response.call_args.args[0], 200)
        handler = self.handler(body)
        handler.do_POST()
        self.assertEqual(handler.send_response.call_args.args[0], 409)

    def test_symlinks_not_followed(self):
        path = self.store.root / SOURCE
        path.unlink()
        path.symlink_to(self.store.root / 'private.jpg')
        with self.assertRaises(AppError):
            self.reviews.context(SOURCE)
