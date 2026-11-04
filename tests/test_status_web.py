"""Synthetic status/Ingress tests; never connect to or exercise camera capture."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from ha_service.journal_web import JournalHandler
from ha_service.options import validate
from ha_service.runtime import Runtime
from ha_service.status_web import render
from ha_service.storage import Store, MAX_FILE_BYTES


class StatusWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.options = validate({'capture_enabled': True, 'camera_url': 'rtsp://camera.invalid/test'})
        self.runtime = Runtime(self.options, Store(Path(self.temp.name), self.options))
        output = io.BytesIO()
        Image.new('RGB', (64, 36), (80, 90, 100)).save(output, 'JPEG')
        self.image = output.getvalue()

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, index=0, image=None):
        stamp = '20000101T120000_{:06d}Z'.format(index)
        self.runtime.observe(stamp, {'label': 'no_chicken', 'egg_count': 2,
                                    'distance': .1, 'margin': .5},
                             image=self.image if image is None else image)
        return stamp

    def request(self, path, address='172.30.32.2'):
        handler = JournalHandler.__new__(JournalHandler)
        handler.path = path
        handler.client_address = (address, 12345)
        handler.server = Mock(journal=self.runtime.journal, runtime=self.runtime)
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue(), handler

    def test_default_status_navigation_and_empty_startup(self):
        for path in ('/', '/index.html', '/status', '/status/'):
            status, page, _ = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(b'Waiting for a capture', page)
            self.assertIn(b'class="brand">MunAI', page)
            self.assertIn(b'aria-current="page">Status', page)
            self.assertNotIn(b'src="/', page)
        for path, active in (('/journal', b'Journal'), ('/references', b'References')):
            status, page, _ = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(b'aria-current="page">' + active, page)
        page = self.request('/status/')[1]
        self.assertIn(b'href="../journal"', page)
        self.assertIn(b'href="../references"', page)

    def test_image_is_paired_pinned_bounded_and_independent_of_archives(self):
        first = self.observe()
        status, page, handler = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(('src="latest/' + first + '.jpg"').encode(), page)
        self.assertIn(b'This image', page)
        self.assertIn(b'Confirmed state', page)
        self.assertIn(b'2 eggs', page)
        self.assertIn(b'Stale image', page)
        self.assertFalse(self.options['store_captures'])
        handler.send_header.assert_any_call('Cache-Control', 'no-store')
        handler.send_header.assert_any_call('Content-Security-Policy',
            "default-src 'none'; img-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
        self.observe(1, self.image + b'synthetic-second')
        status, data, handler = self.request('/latest/' + first + '.jpg')
        self.assertEqual((status, data), (200, self.image))
        handler.send_header.assert_any_call('Content-Type', 'image/jpeg')
        self.observe(2)
        self.assertEqual(self.request('/latest/' + first + '.jpg')[0], 404)
        self.assertEqual(len(self.runtime.web_images), 2)
        self.assertLessEqual(sum(map(len, self.runtime.web_images.values())), MAX_FILE_BYTES * 2)
        restarted = Runtime(self.options, self.runtime.store)
        self.assertIsNone(restarted.web_status()['observation'])
        self.assertIsNone(restarted.web_image(first))

    def test_snapshot_is_detached_and_does_not_reload_references(self):
        self.observe()
        with patch.object(self.runtime, '_references', side_effect=AssertionError('No recognition in UI')):
            snapshot = self.runtime.web_status()
            snapshot['observation']['raw_prediction']['occupant'] = 'changed'
            self.assertEqual(self.runtime.web_status()['observation']['raw_prediction']['occupant'], 'empty')

    def test_split_raw_uncertainty_distinct_from_confirmed_and_escaped(self):
        self.observe()
        snapshot = self.runtime.web_status()
        observation = snapshot['observation']
        observation['zones'] = {'left': {'occupant': 'hen_a', 'egg_count': 0},
                                'right': {'occupant': 'empty', 'egg_count': 2},
                                'center': {'occupant': 'unknown', 'egg_count': None}}
        observation['raw_prediction']['zones'] = {
            'left': {'label': 'unknown', 'obscured': True, 'egg_count': None},
            'right': {'label': 'no_chicken', 'egg_count': 2},
            'center': {'label': '<script>synthetic</script>', 'egg_count': None}}
        page = render(snapshot)
        self.assertIn(b'Obscured / uncertain', page)
        self.assertIn(b'Unknown / uncertain', page)
        self.assertIn(b'hen_a', page)
        self.assertIn(b'&lt;script&gt;', page)
        self.assertNotIn(b'<script>synthetic', page)
        for removed in (b'does not establish that a hen is present',
                        b'not human-reviewed', b'after confirmation rules',
                        b'retain an earlier confirmed value'):
            self.assertNotIn(removed, page)
        self.assertIn(b'Egg count unavailable', page)

    def test_split_sections_follow_spatial_order(self):
        self.observe()
        for names, expected in ((('right', 'left', 'center'), ('Left', 'Center', 'Right')),
                                (('bottom', 'top', 'center'), ('Top', 'Center', 'Bottom'))):
            snapshot = self.runtime.web_status()
            for state in (snapshot['observation'], snapshot['observation']['raw_prediction']):
                state['zones'] = {name: {'occupant': 'empty', 'label': 'empty'} for name in names}
            page = render(snapshot)
            headings = [part.split(b'</dt>', 1)[0] for part in page.split(b'<dt>')[1:]]
            self.assertEqual(headings, [name.encode() for name in expected] * 2)

    def test_health_states_and_sanitized_errors(self):
        with patch('ha_service.runtime.capture_paused', return_value=False):
            self.assertEqual(self.runtime.web_status()['capture_state'], 'Active')
            self.runtime.events.failed('round', 'Synthetic recognition failure.')
            self.assertEqual(self.runtime.web_status()['capture_state'], 'Failed')
            self.assertIn(b'Synthetic recognition failure.', self.request('/')[1])
            self.runtime.events.recovered('round', 'Recovered.')
            self.assertEqual(self.runtime.web_status()['errors'], [])
        with patch('ha_service.runtime.capture_paused', return_value=True):
            self.assertEqual(self.runtime.web_status()['capture_state'], 'Paused for sleep time')
        self.options['capture_enabled'] = False
        self.assertEqual(self.runtime.web_status()['capture_state'], 'Disabled')
        self.options['mqtt_enabled'] = True
        self.assertEqual(self.runtime.web_status()['mqtt_state'], 'Disconnected')
        self.runtime.publisher = Mock()
        self.runtime.publisher.connected.is_set.return_value = True
        self.assertEqual(self.runtime.web_status()['mqtt_state'], 'Connected')

    def test_ingress_only_path_validation_and_trailing_slashes(self):
        stamp = self.observe()
        for path in ('/', '/status', '/journal', '/references', '/latest/' + stamp + '.jpg'):
            self.assertEqual(self.request(path, '127.0.0.1')[0], 403)
        for path in ('/latest/../options.json', '/latest/not-a-stamp.jpg', '/?unexpected=1',
                     '/status?page=-1', '/journal?page=1&page=2'):
            self.assertEqual(self.request(path)[0], 400)
        for path in ('/journal/', '/status/'):
            self.assertIn(b'href="../journal.css"', self.request(path)[1])
        self.assertIn(('src="../latest/' + stamp + '.jpg"').encode(), self.request('/status/')[1])
        self.assertEqual(self.request('/latest/' + stamp + '.jpg?x=1')[0], 404)

    def test_release_versions_match(self):
        from ha_service import VERSION
        root = Path(__file__).resolve().parent.parent
        self.assertEqual(json.loads((root / 'config.json').read_text())['version'], VERSION)
        self.assertIn('## ' + VERSION, (root / 'CHANGELOG.md').read_text())
