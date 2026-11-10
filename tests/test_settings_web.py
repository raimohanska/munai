"""State reset, durable recovery and Ingress controls; no camera access."""
import copy
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from count_eggs import detect_eggs
from ha_service import state_reset
from ha_service.journal_web import JournalHandler
from ha_service.options import AppError, validate
from ha_service.runtime import Runtime
from ha_service.settings_web import render
from ha_service.storage import Conflict, Store, atomic_json
from tests.test_count_eggs import nest


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.root = Path(self.temp.name)
        self.options = validate({'box_split': 'vertical'})
        self.store = Store(self.root, self.options)
        self.runtime = Runtime(self.options, self.store)
        self.daily = self.runtime.settings_snapshot()
        self.day = self.daily['date']
        for zone, state in self.runtime.nest.regions.items():
            saved = copy.deepcopy(state.state)
            saved.update(hen='hen_a', confirmed=True, eggs=1, egg_high_water=1,
                         baseline=0, latest_event='Hen_a arrived')
            saved['daily_eggs'] = {'date': self.day, 'counts': {'unknown': 4, 'hen_a': 1}}
            state._commit(saved)
        self.server = Mock(runtime=self.runtime, journal=self.runtime.journal,
                           settings_token='synthetic-settings-token')

    def tearDown(self):
        self.temp.cleanup()

    def request(self, path='/settings', body=None, token='synthetic-settings-token', address='172.30.32.2', headers=None):
        handler = JournalHandler.__new__(JournalHandler)
        handler.path = path
        handler.client_address = (address, 12345)
        handler.server = self.server
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.headers = Message()
        if body is None:
            handler.do_GET()
        else:
            data = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
            handler.rfile = io.BytesIO(data)
            handler.headers['Content-Length'] = str(len(data))
            handler.headers['Content-Type'] = 'application/json'
            if token is not None:
                handler.headers['X-Munai-Settings'] = token
            for key, value in (headers or {}).items():
                handler.headers[key] = value
            handler.do_POST()
        return handler.send_response.call_args[0][0], handler.wfile.getvalue(), handler

    def reset(self):
        return self.runtime.reset_state(self.day)

    def test_page_navigation_relative_paths_and_no_get_side_effects(self):
        before = self.runtime.nest.daily_snapshot()
        for path, base in (('/settings', ''), ('/settings/', '../')):
            status, page, handler = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(b'aria-current="page">Settings', page)
            self.assertIn(('data-endpoint="{}settings/reset"'.format(base)).encode(), page)
            self.assertIn(b'Keeps references, photos and journal', page)
            self.assertIn(('href="{}journal"'.format(base)).encode(), page)
            handler.send_header.assert_any_call('Cache-Control', 'no-store')
        self.assertEqual(self.runtime.nest.daily_snapshot(), before)
        self.assertEqual(self.request('/settings.js')[0], 200)
        self.assertEqual(self.request('/settings/reset')[0], 404)

    def test_page_escapes_token_and_identity(self):
        page = render({'date': self.day, 'counts': {'<script>': 0}}, '"<unsafe>')
        self.assertIn(b'&lt;script&gt;', page)
        self.assertIn(b'&quot;&lt;unsafe&gt;', page)
        self.assertNotIn(b'<script>', page)

    def test_reset_requires_ingress_secret_header_and_explicit_confirmation(self):
        body = {'confirm': True, 'date': self.day}
        before = self.runtime.nest.daily_snapshot()
        self.assertEqual(self.request('/settings', address='127.0.0.1')[0], 403)
        self.assertEqual(self.request('/settings/reset', body, address='127.0.0.1')[0], 403)
        for token in (None, 'incorrect'):
            self.assertEqual(self.request('/settings/reset', body, token=token)[0], 403)
        for bad in ({'date': self.day}, {'confirm': False, 'date': self.day},
                    {'confirm': 1, 'date': self.day}, dict(body, extra=True), [], b'{broken'):
            self.assertEqual(self.request('/settings/reset', bad)[0], 400)
        self.assertEqual(self.request('/settings/reset', body,
                                      headers={'X-Munai-Settings': 'synthetic-settings-token'})[0], 403)
        self.assertEqual(self.runtime.nest.daily_snapshot(), before)

    def test_reset_rejects_stale_day_and_query_string(self):
        before = self.runtime.nest.daily_snapshot()
        self.assertEqual(self.request('/settings/reset', {'confirm': True, 'date': '2000-01-01'})[0], 409)
        self.assertEqual(self.request('/settings/reset?x=1', {'confirm': True, 'date': self.day})[0], 400)
        self.assertEqual(self.runtime.nest.daily_snapshot(), before)

    def test_post_resets_and_consumes_token(self):
        body = {'confirm': True, 'date': self.day}
        status, data, unused = self.request('/settings/reset', body)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(data)['reset'])
        self.assertNotEqual(self.server.settings_token, 'synthetic-settings-token')
        self.assertEqual(self.request('/settings/reset', body)[0], 403)

    def test_split_reset_clears_carry_legacy_and_live_state_but_preserves_files(self):
        metadata = dict(self.runtime.nest.metadata, carry={'date': self.day, 'counts': {'unknown': 3, 'hen_b': 2}})
        self.runtime.nest.metadata = metadata
        atomic_json(self.root / 'nest-layout.json', metadata)
        protected = []
        for relative in ('references/hen_a/example.jpg', 'captures/empty/example.jpg',
                         'journal/entries/example.json', 'training/annotations/example.json'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic-preserved-data')
            protected.append(path)
        self.runtime.last_observation = {'timestamp': 'synthetic'}
        self.runtime.web_images = {'synthetic': b'synthetic-image'}
        result = self.reset()
        self.assertEqual(result['daily_eggs'], {'date': self.day, 'counts': {'unknown': 0, 'hen_a': 0, 'hen_b': 0}})
        for state in self.runtime.nest.regions.values():
            self.assertIsNone(state.state['hen'])
            self.assertIsNone(state.state['eggs'])
            self.assertIsNone(state.state['latest_event'])
            self.assertFalse(state.state['confirmed'])
        self.assertIsNone(self.runtime.last_observation)
        self.assertEqual(self.runtime.web_images, {})
        for path in protected:
            self.assertEqual(path.read_bytes(), b'synthetic-preserved-data')
        legacy = json.loads((self.root / 'nest-state.json').read_text())
        self.assertFalse(any(legacy['daily_eggs']['counts'].values()))
        restarted = Runtime(self.options, self.store)
        self.assertEqual(restarted.nest.daily_snapshot(), result['daily_eggs'])
        self.assertIsNone(restarted.nest.state['latest_event'])

    def test_reset_publishes_zero_totals_and_unknown_live_state_immediately(self):
        publisher = Mock()
        self.runtime.publisher = publisher
        result = self.reset()
        publisher.publish_daily.assert_called_once_with(result['daily_eggs'])
        publisher.publish.assert_called_once()
        state = publisher.publish.call_args[0][0]
        self.assertIsNone(state['egg_count'])
        self.assertIsNone(state['latest_event'])
        self.assertFalse(result['mqtt_pending'])

    def test_horizontal_reset_uses_top_and_bottom_regions(self):
        options = validate({'box_split': 'horizontal'})
        store = Store(self.root / 'horizontal-data', options)
        runtime = Runtime(options, store)
        day = runtime.settings_snapshot()['date']
        result = runtime.reset_state(day)
        self.assertEqual(set(runtime.nest.regions), {'top', 'bottom', 'center'})
        for zone in ('top', 'bottom', 'center'):
            self.assertTrue((store.root / 'nests' / 'horizontal' / zone / 'nest-state.json').is_file())
        self.assertFalse(any(result['daily_eggs']['counts'].values()))
        self.assertEqual(Runtime(options, store).nest.daily_snapshot(), result['daily_eggs'])

    def test_mqtt_outage_does_not_undo_saved_reset(self):
        publisher = Mock()
        publisher.publish_daily.side_effect = AppError('Synthetic MQTT outage')
        publisher.publish.side_effect = AppError('Synthetic MQTT outage')
        self.runtime.publisher = publisher
        self.assertTrue(self.reset()['mqtt_pending'])
        self.assertFalse(any(Runtime(self.options, self.store).nest.daily_snapshot()['counts'].values()))
        publisher.publish_daily.side_effect = None
        publisher.publish.side_effect = None
        self.runtime._cycle()
        self.assertNotIn('daily_mqtt', self.runtime.events.failures)
        self.assertNotIn('state_mqtt', self.runtime.events.failures)
        self.assertEqual(publisher.publish.call_count, 2)

    def test_reset_refuses_inflight_processing(self):
        ready, release = threading.Event(), threading.Event()
        def hold():
            with self.runtime.state_lock:
                ready.set()
                release.wait(5)
        worker = threading.Thread(target=hold)
        worker.start()
        ready.wait(5)
        try:
            with self.assertRaises(Conflict):
                self.reset()
        finally:
            release.set()
            worker.join(5)

    def test_interrupted_split_reset_replays_before_old_transition_on_restart(self):
        original_states = {z: copy.deepcopy(n.state) for z, n in self.runtime.nest.regions.items()}
        atomic_json(self.root / 'nest-transition.json', {'layout': 'vertical', 'regions': original_states})
        original = state_reset.atomic_json
        calls = []
        def failing(path, value):
            calls.append(path)
            if len(calls) == 4:
                raise OSError('Synthetic interrupted write')
            original(path, value)
        with patch('ha_service.state_reset.atomic_json', side_effect=failing):
            with self.assertRaises(OSError):
                self.reset()
        self.assertTrue(self.runtime.reset_pending)
        self.assertTrue((self.root / 'state-reset.json').exists())
        restarted = Runtime(self.options, self.store)
        self.assertFalse(any(restarted.nest.daily_snapshot()['counts'].values()))
        self.assertFalse((self.root / 'state-reset.json').exists())
        self.assertFalse((self.root / 'nest-transition.json').exists())

    def test_reset_pending_does_not_write_old_state_when_recovery_fails(self):
        self.runtime.reset_pending = True
        with patch.object(self.runtime, '_load_nest', side_effect=AppError('Synthetic recovery failure')), \
                patch.object(self.runtime.nest, 'reset_confirmation') as reset:
            self.runtime._cycle()
            reset.assert_not_called()

    def test_committed_transition_outbox_is_archived_before_reset(self):
        states = {z: copy.deepcopy(n.state) for z, n in self.runtime.nest.regions.items()}
        entry = {'id': '20000101T000000_000000Z_' + 'a' * 32,
                 'text': 'Left: Hen_a arrived', 'evidence': [], 'zone': 'left', 'layout': 'vertical'}
        states['left']['journal']['outbox'] = [entry]
        atomic_json(self.root / 'nest-transition.json', {'layout': 'vertical', 'regions': states})
        self.reset()
        saved = self.root / 'journal' / 'entries' / (entry['id'] + '.json')
        self.assertEqual(json.loads(saved.read_text()), entry)
        self.assertIsNone(self.runtime.nest.state['latest_event'])

    def test_invalid_reset_intent_fails_closed(self):
        for payload in ({'version': 1, 'layout': 'horizontal', 'daily': self.daily},
                        {'version': 1, 'layout': 'vertical', 'daily': {'date': self.day, 'counts': {'unknown': 1}}}):
            atomic_json(self.root / 'state-reset.json', payload)
            with self.assertRaises(AppError):
                Runtime(self.options, self.store)


class WholeResetTests(unittest.TestCase):
    def test_existing_egg_after_reset_is_baseline_not_new_production_even_after_restart(self):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as folder:
            options = validate({})
            store = Store(Path(folder), options)
            runtime = Runtime(options, store)
            day = runtime.settings_snapshot()['date']
            runtime.reset_state(day)
            runtime = Runtime(options, store)
            image = nest([(180, 180)])
            detections = detect_eggs(image)
            for unused in range(5):
                runtime.nest.observe('no_chicken', 1, egg_image=image, egg_detections=detections)
            self.assertEqual(runtime.nest.state['eggs'], 1)
            self.assertEqual(runtime.nest.daily_snapshot()['counts']['unknown'], 0)
            for unused in range(5):
                runtime.nest.observe('no_chicken', 0, egg_image=nest(), egg_detections=[])
            for unused in range(5):
                runtime.nest.observe('no_chicken', 1, egg_image=image, egg_detections=detections)
            self.assertEqual(runtime.nest.daily_snapshot()['counts']['unknown'], 1)
