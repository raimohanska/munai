"""Synthetic stored-photo browsing tests, not camera capture tests."""
import io
import json
import os
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ha_service.captures_web import inventory, render
from ha_service.confirmations import Confirmations
from ha_service.journal import Journal
from ha_service.journal_web import JournalHandler
from ha_service.options import validate
from ha_service.storage import Store


class CapturesGalleryTests(unittest.TestCase):
    def setUp(self):
        self.timezone = patch.dict(os.environ, {'TZ': 'UTC'})
        self.timezone.start()
        time.tzset()
        self.addCleanup(time.tzset)
        self.addCleanup(self.timezone.stop)
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
        handler.server = Mock(journal=self.journal, confirmations=Confirmations(self.journal))
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
        for removed in (b'reviewed identities', b'Frame only:', b'Deduplication and retention'):
            self.assertNotIn(removed, page)

    def test_single_local_day_and_navigation_skip_empty_days(self):
        self.photo('empty/20000101T235959_999999Z.jpg')
        self.photo('hen_a/unreviewed/20000103T000000_000000Z.jpg')
        self.photo('unknown/20000103T120000_000000Z.jpg')
        status, page, _ = self.request('/captures')
        self.assertEqual(status, 200)
        self.assertIn(b'<h2>2000-01-03</h2>', page)
        self.assertNotIn(b'Refresh', page)
        self.assertIn(b'<h3><time datetime="2000-01-03T12:00:00+00:00">12:00:00</time></h3>', page)
        self.assertIn(b'2 stored photos', page)
        self.assertEqual(page.count(b'<figure>'), 2)
        self.assertNotIn(b'20000101T', page)
        self.assertIn(b'?date=2000-01-01', page)
        self.assertIn(b'Previous day', page)
        self.assertNotIn(b'Next day', page)
        self.assertLess(page.index(b'unknown/20000103T'), page.index(b'hen_a/unreviewed/20000103T'))
        status, page, _ = self.request('/captures?date=2000-01-01')
        self.assertEqual(status, 200)
        self.assertEqual(page.count(b'<figure>'), 1)
        self.assertIn(b'?date=2000-01-03', page)
        self.assertIn(b'Next day', page)
        self.assertNotIn(b'Previous day', page)
        status, page, _ = self.request('/captures?date=2000-01-02')
        self.assertEqual(status, 200)
        self.assertIn(b'No stored captures for this day', page)
        self.assertIn(b'?date=2000-01-01', page)
        self.assertIn(b'?date=2000-01-03', page)

    def test_local_day_boundary_and_links_surround_day(self):
        with patch.dict(os.environ, {'TZ': 'Europe/Helsinki'}):
            time.tzset()
            self.photo('empty/20000101T210000_000000Z.jpg')
            self.photo('empty/20000101T230000_000000Z.jpg')
            self.photo('empty/20000102T230000_000000Z.jpg')
            page = render(self.store, day='2000-01-02')
            self.assertEqual(page.count(b'<figure>'), 1)
            self.assertIn(b'>01:00:00</time></h3>', page)
            self.assertLess(page.index(b'?date=2000-01-01'), page.index(b'<h2>2000-01-02'))
            self.assertLess(page.index(b'<h2>2000-01-02'), page.index(b'?date=2000-01-03'))
        time.tzset()

    def test_all_capture_categories_and_ingress_relative_links(self):
        for category in ('empty', 'unknown', 'pending', 'eggs', 'hen_a/unreviewed'):
            self.photo(category + '/20000101T120000_000000Z.jpg')
        status, page, _ = self.request('/captures/')
        self.assertEqual(status, 200)
        self.assertEqual(page.count(b'loading="lazy"'), 5)
        self.assertIn(b'href="../journal.css"', page)
        self.assertIn(b'href="../captures/hen_a/unreviewed/', page)
        self.assertNotIn(b'Full-size photo', page)
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

    def result(self, stamp='20000101T120000_000000Z', **fields):
        value = dict(timestamp=stamp, occupant='empty', egg_count=2,
                     raw_prediction={'occupant': 'empty', 'egg_count': 3})
        value.update(fields)
        path = self.store.root / 'results' / (stamp + '.json')
        path.write_text(json.dumps(value))
        return path

    def test_compact_paired_interpretation_and_filename_only(self):
        self.photo('unknown/20000101T120000_000000Z.jpg')
        self.result()
        _, page, _ = self.request('/captures')
        caption = page.split(b'<figcaption>')[1].split(b'</figcaption>')[0]
        self.assertIn(b'empty (3 eggs)', caption)
        self.assertNotIn(b'Confirmed', caption)
        self.assertNotIn(b'Prediction:', caption)
        self.assertIn(b'<code>20000101T120000_000000Z.jpg</code>', caption)
        for removed in (b'unknown/', b'KiB', b'Full-size', b'12:00:', b'UTC'):
            self.assertNotIn(removed, caption)
        self.assertEqual(caption.count(b'<br>'), 1)

    def test_split_summary_and_hidden_raw_eggs(self):
        self.photo('unknown/20000101T120000_000000Z.jpg')
        self.result(zones={'left': {'occupant': 'hen_a', 'egg_count': 2},
                           'right': {'occupant': 'empty', 'egg_count': 1}},
                    raw_prediction={'egg_count': 0, 'zones': {
                         'left': {'label': 'hen_b', 'egg_count': 99},
                         'center': {'label': 'no_chicken', 'egg_count': 0},
                         'right': {'label': 'no_chicken', 'egg_count': 1}}})
        page = render(self.store)
        self.assertIn(b'hen_b | empty | empty (0 eggs)', page)
        self.assertNotIn(b'hen_a', page)
        self.assertNotIn(b'last confirmed', page)
        self.assertNotIn(b'99 eggs', page)

    def test_obscured_split_summary_has_no_egg_total(self):
        self.photo('unknown/20000101T120000_000000Z.jpg')
        for item in ({'label': 'obscured'}, {'label': 'no_chicken', 'obscured': True}):
            self.result(raw_prediction={'egg_count': 0, 'zones': {
                'left': item, 'center': {'label': 'unknown'}, 'right': {'label': 'no_chicken'}}})
            self.assertNotIn(b'0 eggs', render(self.store))

    def test_requested_spatial_order_and_only_frame_egg_count(self):
        self.photo('unknown/20000101T120000_000000Z.jpg')
        self.result(egg_count=88, raw_prediction={'egg_count': 0, 'zones': {
            'center': {'label': 'no_chicken', 'egg_count': 0},
            'left': {'label': 'unknown', 'obscured': True},
            'right': {'label': 'hen_a'}}})
        self.assertIn(b'uncertain | empty | hen_a', render(self.store))
        self.assertNotIn(b'0 eggs', render(self.store))
        self.assertNotIn(b'88 eggs', render(self.store))
        self.result(raw_prediction={'egg_count': None, 'zones': {
            'bottom': {'label': 'hen_b'}, 'top': {'label': 'no_chicken'},
            'center': {'label': 'unknown'}}})
        self.assertIn(b'empty | uncertain | hen_b', render(self.store))
        self.assertNotIn(b'(? eggs)', render(self.store))

    def test_missing_corrupt_or_mismatched_results_do_not_guess_from_folder(self):
        self.photo('hen_a/unreviewed/20000101T120000_000000Z.jpg')
        self.assertIn(b'Interpretation unavailable', render(self.store))
        path = self.result()
        path.write_text('{invalid')
        self.assertIn(b'Interpretation unavailable', render(self.store))
        self.result(timestamp='20000102T120000_000000Z')
        self.assertIn(b'Interpretation unavailable', render(self.store))
        path.unlink()
        path.symlink_to(self.store.root / 'private.json')
        self.assertIn(b'Interpretation unavailable', render(self.store))

    def test_unknown_obscured_and_html_escaping(self):
        self.photo('unknown/20000101T120000_000000Z.jpg')
        self.result(occupant=None, egg_count=None,
                    raw_prediction={'occupant': 'unknown', 'egg_count': 0})
        self.assertIn(b'uncertain (0 eggs)', render(self.store))
        self.assertNotIn(b'Not yet confirmed', render(self.store))
        self.result(raw_prediction={'occupant': 'empty', 'obscured': True, 'egg_count': 99})
        self.assertIn(b'uncertain', render(self.store))
        self.assertNotIn(b'99 eggs', render(self.store))
        self.result(raw_prediction={'occupant': '<script>', 'egg_count': 0})
        self.assertIn(b'&lt;script&gt; (0 eggs)', render(self.store))
        self.assertNotIn(b'<script>', render(self.store))
