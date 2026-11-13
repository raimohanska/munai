"""Synthetic Ingress gallery tests. No HA or real reference images."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from ha_service.journal import Journal
from ha_service.confirmations import Confirmations
from ha_service.journal_web import JournalHandler, render as render_journal
from ha_service.options import validate
from ha_service.references_web import inventory, render
from ha_service.reviews import Reviews
from ha_service.storage import Store, digest

NAME = '20000101T120000_000000Z-left.jpg'


class ReferenceGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.store = Store(Path(self.temp.name) / 'data', validate({'box_split': 'vertical'}))
        self.journal = Journal(self.store)
        stream = io.BytesIO()
        Image.new('RGB', (64, 64), (80, 90, 100)).save(stream, 'JPEG')
        self.image = stream.getvalue()

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, relative='hen_a/' + NAME, data=None):
        data = self.image if data is None else data
        return self.store.upload(relative, data, digest(data), self.store.revision,
                                 True, lambda path: None)

    def request(self, path, address='172.30.32.2'):
        handler = JournalHandler.__new__(JournalHandler)
        handler.path = path
        handler.client_address = (address, 12345)
        handler.server = Mock(journal=self.journal, confirmations=Confirmations(self.journal),
                              reviews=Reviews(self.journal, lambda path: None))
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue(), handler

    def test_empty_page_and_navigation(self):
        status, page, _ = self.request('/references')
        self.assertEqual(status, 200)
        self.assertIn(b'No active reference photos', page)
        self.assertIn(b'href="journal">Journal', page)
        self.assertIn(b'href="references">References', render_journal(self.journal, 0))

    def test_grouping_counts_filenames_layout_and_original_image(self):
        self.upload()
        self.upload('hen_b/20000101T120000_000001Z-center.jpg')
        revision = self.upload('empty/20000101T120000_000002Z.jpg')
        status, page, handler = self.request('/references')
        self.assertEqual(status, 200)
        self.assertIn(revision.encode(), page)
        self.assertIn(b'2 identity photos across 2 hens', page)
        self.assertIn(b'1 full-frame empty calibration photos', page)
        self.assertIn(b'Empty-nest background calibration', page)
        self.assertIn(b'<strong>vertical</strong>', page)
        self.assertIn(b'hen_a/' + NAME.encode(), page)
        self.assertIn(b'loading="lazy"', page)
        self.assertNotIn(b'src="/', page)
        self.assertLess(page.index(b'<h2>Hen_a'), page.index(b'<h2>Empty-nest'))
        for removed in (b'not local candidates', b'validate recognition accuracy',
                        b'not a hen identity', b'provenance'):
            self.assertNotIn(removed, page)
        url = '/references/' + revision + '/hen_a/' + NAME
        status, data, handler = self.request(url)
        self.assertEqual((status, data), (200, self.image))
        handler.send_header.assert_any_call('Content-Type', 'image/jpeg')
        handler.send_header.assert_any_call('Cache-Control', 'no-store')

    def test_reference_cards_hide_confirmation_size_and_full_size_link(self):
        self.upload()
        self.upload('empty/20000101T120000_000002Z.jpg')
        confirmations = Mock()
        page = render(self.store, confirmations=confirmations, token='test-token')
        confirmations.button.assert_not_called()
        for hidden in (b'Confirm', b'KiB', b'Full-size photo'):
            self.assertNotIn(hidden, page)
        self.assertEqual(page.count(b'target="_blank"'), 2)
        self.assertEqual(page.count(b'class="reference-remove"'), 2)

    def test_trailing_slash_keeps_ingress_relative_links(self):
        revision = self.upload()
        status, page, _ = self.request('/references/')
        self.assertEqual(status, 200)
        self.assertIn(b'href="../journal.css"', page)
        self.assertIn(b'href="../journal">Journal', page)
        self.assertIn(('../references/' + revision + '/hen_a/').encode(), page)
        self.assertIn(b'src="../references.js" data-api="../references/remove"', page)
        self.assertIn(b'aria-label="Remove reference hen_a/', page)
        self.assertIn(b'class="reference-remove"', page)

    def test_only_current_folder_no_staging_or_predictions(self):
        first = self.upload()
        second = self.upload('hen_b/20000101T120000_000001Z.jpg')
        stage = self.store.root / 'scratch' / 'job-test' / 'hen_staged'
        stage.mkdir(parents=True)
        (stage / NAME).write_bytes(self.image)
        predicted = self.store.root / 'captures' / 'hen_predicted' / 'unreviewed'
        predicted.mkdir(parents=True)
        (predicted / NAME.replace('-left', '')).write_bytes(self.image)
        page = render(self.store)
        self.assertIn(second.encode(), page)
        for excluded in (first.encode(), b'hen_staged', b'hen_predicted'):
            self.assertNotIn(excluded, page)
        self.assertEqual(self.request('/references/' + first + '/hen_a/' + NAME)[0], 409)

    def test_content_changes_never_silently_replace_a_displayed_photo(self):
        old = self.upload()
        stream = io.BytesIO()
        Image.new('RGB', (64, 64), (150, 100, 80)).save(stream, 'JPEG')
        new = self.upload(data=stream.getvalue())
        self.assertNotEqual(old, new)
        status, message, _ = self.request('/references/' + old + '/hen_a/' + NAME)
        self.assertEqual(status, 409)
        self.assertIn(b'refresh', message)
        self.assertEqual(self.request('/references/' + new + '/hen_a/' + NAME)[1], stream.getvalue())

    def test_ingress_only_and_reject_unsafe_paths_and_queries(self):
        revision = self.upload()
        for path in ('/references', '/references/', '/references/' + revision + '/hen_a/' + NAME):
            self.assertEqual(self.request(path, '127.0.0.1')[0], 403)
        for path in ('/references?x=1', '/references?page=-1', '/references?page=1&page=2',
                     '/references/../options.json', '/references/not-a-version/hen_a/' + NAME,
                     '/references/' + revision + '/../../options.json',
                     '/references/' + revision + '/hen_a/%2e%2e%2foptions.json'):
            self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request('/references/' + revision + '/hen_a/20000101T120000_999999Z.jpg')[0], 404)

    def test_gallery_metadata_does_not_hash_or_decode_all_photos(self):
        self.upload()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('No photo reads needed')):
            revision, items = inventory(self.store)
            self.assertEqual(len(items), 1)
            self.assertEqual(revision, self.store.revision)
            self.assertIn(b'Hen_a', render(self.store))

    def test_all_references_on_one_page_including_old_paging_links(self):
        for index in range(23):
            self.upload('hen_a/20000101T120000_{:06d}Z.jpg'.format(index))
        self.upload('hen_b/20000101T120000_000023Z.jpg')
        self.upload('empty/20000101T120000_000024Z.jpg')
        for path in ('/references', '/references/', '/references?page=1',
                     '/references?page=99999999'):
            status, page, _ = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(page.count(b'<figure>'), 25)
            self.assertEqual(page.count(b'loading="lazy"'), 25)
            self.assertEqual(page.count(b'<h2>Hen_a</h2>'), 1)
            self.assertLess(page.index(b'<h2>Hen_a'), page.index(b'<h2>Hen_b'))
            self.assertLess(page.index(b'<h2>Hen_b'), page.index(b'<h2>Empty-nest'))
            self.assertNotIn(b'?page=', page)
            self.assertNotIn(b'Page 1 of', page)
            self.assertNotIn(b'Previous', page)
            self.assertNotIn(b'Next', page)

    def test_symlinks_are_not_exposed(self):
        self.upload()
        path = self.store.reference_root() / 'hen_a' / NAME
        original = self.store.root / 'synthetic-original.jpg'
        original.write_bytes(self.image)
        path.unlink()
        path.symlink_to(original)
        self.assertEqual(self.request('/references')[0], 500)
        self.assertEqual(self.request('/references/' + self.store.revision + '/hen_a/' + NAME)[0], 500)
