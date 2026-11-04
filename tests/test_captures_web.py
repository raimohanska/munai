"""Synthetic stored-photo browsing tests, not camera capture tests."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ha_service.captures_web import inventory, render
from ha_service.journal import Journal
from ha_service.journal_web import JournalHandler
from ha_service.options import validate
from ha_service.storage import Store


class CapturesGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.store = Store(Path(self.temp.name) / 'data', validate({}))
        self.journal = Journal(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def photo(self, relative):
        path = self.store.root / 'captures' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic stored JPEG')
        return path

    def request(self, path, address='172.30.32.2'):
        handler = JournalHandler.__new__(JournalHandler)
        handler.path = path
        handler.client_address = (address, 12345)
        handler.server = Mock(journal=self.journal)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue(), handler

    def test_empty_and_disabled_storage(self):
        status, page, _ = self.request('/captures')
        self.assertEqual(status, 200)
        self.assertIn(b'No stored captures', page)
        self.assertIn(b'capture storage is disabled', page)
        self.assertIn(b'href="captures" aria-current="page"', page)
        self.assertNotIn(b'?date=', page)

    def test_single_utc_day_and_navigation_skip_empty_days(self):
        self.photo('empty/20000101T235959_999999Z.jpg')
        self.photo('hen_a/unreviewed/20000103T000000_000000Z.jpg')
        self.photo('unknown/20000103T120000_000000Z.jpg')
        status, page, _ = self.request('/captures')
        self.assertEqual(status, 200)
        self.assertIn(b'2000-01-03 (UTC)', page)
        self.assertIn(b'2 stored photos', page)
        self.assertEqual(page.count(b'<figure>'), 2)
        self.assertNotIn(b'20000101T', page)
        self.assertIn(b'?date=2000-01-01', page)
        self.assertNotIn(b'Newer day', page)
        self.assertLess(page.index(b'unknown/20000103T'), page.index(b'hen_a/unreviewed/20000103T'))
        status, page, _ = self.request('/captures?date=2000-01-01')
        self.assertEqual(status, 200)
        self.assertEqual(page.count(b'<figure>'), 1)
        self.assertIn(b'?date=2000-01-03', page)
        self.assertNotIn(b'Older day', page)
        status, page, _ = self.request('/captures?date=2000-01-02')
        self.assertEqual(status, 200)
        self.assertIn(b'No stored captures for this day', page)
        self.assertIn(b'?date=2000-01-01', page)
        self.assertIn(b'?date=2000-01-03', page)

    def test_all_capture_categories_and_ingress_relative_links(self):
        for category in ('empty', 'unknown', 'pending', 'eggs', 'hen_a/unreviewed'):
            self.photo(category + '/20000101T120000_000000Z.jpg')
        status, page, _ = self.request('/captures/')
        self.assertEqual(status, 200)
        self.assertEqual(page.count(b'loading="lazy"'), 5)
        self.assertIn(b'href="../journal.css"', page)
        self.assertIn(b'href="../captures/hen_a/unreviewed/', page)
        self.assertIn(b'Full-size photo', page)
        self.assertNotIn(b'src="/', page)

    def test_original_photo_and_cleanup_404(self):
        relative = 'unknown/20000101T120000_000000Z.jpg'
        path = self.photo(relative)
        status, data, handler = self.request('/captures/' + relative)
        self.assertEqual((status, data), (200, path.read_bytes()))
        handler.send_header.assert_any_call('Content-Type', 'image/jpeg')
        handler.send_header.assert_any_call('Cache-Control', 'no-store')
        handler.send_header.assert_any_call('X-Content-Type-Options', 'nosniff')
        path.unlink()
        self.assertEqual(self.request('/captures/' + relative)[0], 404)

    def test_ingress_only_and_invalid_requests(self):
        for url in ('/captures', '/captures/', '/captures/unknown/20000101T120000_000000Z.jpg'):
            self.assertEqual(self.request(url, '127.0.0.1')[0], 403)
        for url in ('/captures?date=', '/captures?date=2000-02-30',
                    '/captures?date=20000101', '/captures?date=2000-1-1',
                    '/captures?date=2000-01-01&date=2000-01-02', '/captures?page=1',
                    '/captures/../options.json', '/captures/%2e%2e/options.json',
                    '/captures/hen_a/reviewed/20000101T120000_000000Z.jpg'):
            self.assertEqual(self.request(url)[0], 400, url)

    def test_metadata_only_no_photo_reads_or_other_archives(self):
        self.photo('unknown/20000101T120000_000000Z.jpg')
        self.photo('.pending-abcdefgh')
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('No photo reads')):
            self.assertEqual(len(inventory(self.store)), 1)
            self.assertEqual(render(self.store).count(b'<figure>'), 1)

    def test_symlinks_not_exposed(self):
        relative = 'unknown/20000101T120000_000000Z.jpg'
        path = self.photo(relative)
        path.unlink()
        path.symlink_to(self.store.root / 'config.json')
        self.assertEqual(self.request('/captures')[0], 500)
        self.assertEqual(self.request('/captures/' + relative)[0], 500)
