"""Synthetic offline journal evidence, persistence, retention and HTTP tests.

No camera capture calls, live HA, credentials or real images.
"""
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from ha_service.journal import Journal, frame_ids
from ha_service.journal_web import JournalHandler, render
from ha_service.nest import NestState
from ha_service.options import AppError, validate
from ha_service.runtime import Runtime
from ha_service.storage import Store, atomic_json


class JournalTests(unittest.TestCase):
    def setUp(self):
        scratch = '/tmp/opencode' if Path('/tmp/opencode').is_dir() else None
        self.temp = tempfile.TemporaryDirectory(prefix='munai-journal-', dir=scratch)
        self.root = Path(self.temp.name)
        self.options = validate({})
        self.store = Store(self.root / 'data', self.options)
        self.nest = NestState(self.store.root)
        self.journal = Journal(self.store)
        self.journal.retain(self.nest, force=True)
        self.source = self.root / 'synthetic.jpg'
        Image.new('RGB', (64, 64), (80, 90, 100)).save(str(self.source))

    def tearDown(self):
        self.temp.cleanup()

    def shot(self, label, eggs=0, flush=True):
        prediction = {'label': label, 'egg_count': eggs}
        frame = self.journal.save_frame(self.source, prediction)
        events = self.nest.observe(label, eggs, frame)
        if flush:
            self.journal.flush(self.nest)
            self.journal.cleanup_pending(self.nest)
        return frame, events

    def shots(self, label, eggs=0, n=None):
        if n is None:
            n = 5 if label == 'no_chicken' and self.nest.state['hen'] is not None else 3
        return [self.shot(label, eggs)[0] for _ in range(n)]

    def entries(self):
        return [self.journal.read_entry(path) for path in self.journal.entries()]

    def test_visit_retains_exact_baseline_identity_and_after_frames(self):
        baseline = self.shots('no_chicken', 1)
        identity = self.shots('hen_a')
        self.shots('hen_a', n=5)
        after = self.shots('no_chicken', 2)
        entries = self.entries()
        self.assertEqual([entry['text'] for entry in entries], ['Hen_a laid an Egg', 'Hen_a arrived'])
        self.assertEqual(set(frame_ids(entries[0])), {f['id'] for f in baseline + identity + after})
        self.assertEqual(len(entries[0]['evidence']), 4)
        for identifier in frame_ids(entries):
            self.assertEqual((self.journal.root / 'images' / (identifier + '.jpg')).read_bytes(), self.source.read_bytes())
        self.assertEqual(list((self.store.root / 'captures').rglob('*.jpg')), [])
        self.store.retain()
        self.assertEqual(len(self.entries()), 2)

    def test_delayed_count_and_visit_evidence_survive_restart(self):
        baseline = self.shots('no_chicken', 1)
        identity = self.shots('hen_a')
        departure = [self.shot('no_chicken', eggs)[0] for eggs in (2, 3, 2, 3, 2)]
        self.assertEqual(self.entries()[0]['text'], 'Hen_a left')
        self.nest = NestState(self.store.root)
        self.journal = Journal(self.store)
        self.journal.retain(self.nest, force=True)
        self.shots('no_chicken', 2, n=2)
        self.assertEqual(self.entries()[0]['text'], 'Hen_a left')
        last, events = self.shot('no_chicken', 2)
        self.assertEqual(events, ['Hen_a laid an Egg'])
        entry = self.entries()[0]
        self.assertEqual(len(entry['evidence']), 4)
        self.assertTrue({f['id'] for f in baseline + identity + departure + [last]} <= set(frame_ids(entry)))

    def test_noise_is_not_in_confirmation_evidence(self):
        rejected = self.shots('hen_a', n=2)
        self.shots('hen_b', n=1)
        actual = self.shots('hen_a')
        entry = self.entries()[0]
        self.assertEqual(set(frame_ids(entry)), {f['id'] for f in actual})
        for frame in rejected:
            self.assertFalse((self.journal.root / 'images' / (frame['id'] + '.jpg')).exists())

    def test_settled_missed_egg_keeps_visit_photos_for_later_attribution(self):
        baseline = self.shots('no_chicken', 0)
        identity = self.shots('hen_a')
        departure = self.shots('no_chicken', 0, n=5)
        entry = self.entries()[0]
        self.assertEqual(entry['text'], 'Hen_a left')
        self.assertEqual(entry['evidence'][-1]['frames'], departure)
        self.shots('no_chicken', 0, n=8)
        self.nest = NestState(self.store.root)
        self.journal = Journal(self.store)
        self.journal.retain(self.nest, force=True)
        after = self.shots('no_chicken', 1, n=3)
        entry = self.entries()[0]
        self.assertEqual(entry['text'], 'Hen_a laid an Egg')
        self.assertEqual(len(entry['evidence']), 4)
        self.assertEqual(set(frame_ids(entry)),
                         {f['id'] for f in baseline + identity + departure + after})
        for identifier in frame_ids(entry):
            self.assertTrue((self.journal.root / 'images' / (identifier + '.jpg')).is_file())

    def test_restart_resets_streak_frames_but_not_visit(self):
        old = self.shots('hen_a', n=2)
        self.nest = NestState(self.store.root)
        actual = self.shots('hen_a')
        self.assertEqual(set(frame_ids(self.entries()[0])), {f['id'] for f in actual})
        self.assertFalse(set(f['id'] for f in old) & set(frame_ids(self.entries())))

    def test_collection_shares_before_count_and_has_three_zero_frames(self):
        baseline = self.shots('no_chicken', 2)
        after = self.shots('no_chicken', 0)
        entry = self.entries()[0]
        self.assertEqual(entry['text'], '2 eggs collected')
        self.assertEqual(set(frame_ids(entry)), {f['id'] for f in baseline + after})

    def test_swap_order_and_no_fabricated_baseline(self):
        self.shots('no_chicken', 1)
        self.shots('hen_a')
        swap = self.shots('hen_b')
        entries = self.entries()
        self.assertEqual([e['text'] for e in entries[:2]], ['Hen_b arrived', 'Hen_a left'])
        self.assertEqual(entries[0]['evidence'][0]['frames'], [])
        self.assertTrue({f['id'] for f in swap} <= set(frame_ids(entries[1])))

    def test_unknown_upgraded_identity_keeps_before_visit_evidence(self):
        baseline = self.shots('no_chicken', 1)
        self.shots('unknown')
        identity = self.shots('hen_a')
        after = self.shots('no_chicken', 2)
        self.assertEqual(set(frame_ids(self.entries()[0])), {f['id'] for f in baseline + identity + after})

    def test_outbox_recovery_after_entry_write_failure(self):
        self.shots('hen_a', n=2)
        frame, events = self.shot('hen_a', flush=False)
        self.assertEqual(events, ['Hen_a arrived'])
        original = copy.deepcopy(self.nest.state['journal']['outbox'])
        with patch('ha_service.journal.atomic_json', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.journal.flush(self.nest)
        self.nest = NestState(self.store.root)
        self.journal.flush(self.nest)
        self.assertEqual(self.entries(), original)
        self.journal.flush(self.nest)
        self.assertEqual(len(self.entries()), 1)
        self.assertIn(frame['id'], set(frame_ids(self.entries())))

    def test_outbox_replay_after_entry_written_but_ack_failed(self):
        self.shots('hen_a', n=2)
        self.shot('hen_a', flush=False)
        with patch('ha_service.nest.atomic_json', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.journal.flush(self.nest)
        self.assertEqual(len(self.entries()), 1)
        self.nest = NestState(self.store.root)
        self.journal.flush(self.nest)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.nest.state['journal']['outbox'], [])

    def test_failed_state_write_does_not_consume_transition_or_evidence(self):
        self.shots('hen_a', n=2)
        before = copy.deepcopy(self.nest.state)
        with patch('ha_service.nest.atomic_json', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.shot('hen_a')
        self.assertEqual(self.nest.state, before)
        frame, events = self.shot('hen_a')
        self.assertEqual(events, ['Hen_a arrived'])
        self.assertIn(frame['id'], set(frame_ids(self.entries()[0])))

    def test_expiry_removes_only_unreferenced_images_and_preserves_shared_ones(self):
        self.shots('no_chicken', 1)
        self.shots('hen_a')
        self.shots('no_chicken', 2)
        arrival = self.entries()[1]
        old_id = '20000101T000000_000000Z_' + 'a' * 32
        old_path = self.journal.root / 'entries' / (old_id + '.json')
        original = self.journal.root / 'entries' / (arrival['id'] + '.json')
        arrival['id'] = old_id
        atomic_json(old_path, arrival)
        original.unlink()
        self.journal.retain(self.nest, force=True)
        self.assertFalse(old_path.exists())
        self.assertEqual(len(self.entries()), 1)
        for identifier in frame_ids(self.entries()):
            self.assertTrue((self.journal.root / 'images' / (identifier + '.jpg')).exists())
        with patch('ha_service.journal.time.time', return_value=time.time() + 366 * 86400):
            self.journal.retain(self.nest, force=True)
        self.assertEqual(self.entries(), [])
        current = set(frame_ids(self.nest.state['journal']))
        self.assertEqual({p.stem for p in (self.journal.root / 'images').glob('*.jpg')}, current)

    def test_low_space_does_not_consume_event_or_delete_younger_archive(self):
        self.shots('hen_a')
        before = copy.deepcopy(self.nest.state)
        with patch.object(self.store, 'require_space', side_effect=AppError('Synthetic low storage.')):
            with self.assertRaises(AppError):
                self.shot('no_chicken')
        self.assertEqual(self.nest.state, before)
        self.assertEqual(len(self.entries()), 1)

    def test_v3_migration_preserves_state_without_inventing_evidence(self):
        self.shots('hen_a')
        old = dict(self.nest.state)
        del old['journal']
        del old['pending_settled']
        del old['egg_high_water']
        old['version'] = 3
        atomic_json(self.nest.path, old)
        migrated = NestState(self.store.root)
        self.assertEqual(migrated.state['hen'], 'hen_a')
        self.assertEqual(list(frame_ids(migrated.state['journal'])), [])

    def test_invalid_evidence_and_symlinks_are_rejected(self):
        self.shots('hen_a')
        state = copy.deepcopy(self.nest.state)
        state['journal']['visit'][1]['frames'][0]['id'] = '../private'
        atomic_json(self.nest.path, state)
        with self.assertRaises(AppError):
            NestState(self.store.root)
        identifier = next(frame_ids(self.entries()[0]))
        path = self.journal.root / 'images' / (identifier + '.jpg')
        path.unlink()
        path.symlink_to(self.source)
        with self.assertRaises(AppError):
            self.journal.retain(self.nest, force=True)

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

    def test_ingress_access_paths_headers_and_original_images(self):
        self.shots('hen_a')
        identifier = next(frame_ids(self.entries()[0]))
        status, data, handler = self.request('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Hen_a arrived', data)
        self.assertIn(b'loading="lazy"', data)
        self.assertNotIn(b'src="/', data)  # All assets survive an ingress prefix.
        handler.send_header.assert_any_call('X-Content-Type-Options', 'nosniff')
        status, data, _ = self.request('/images/' + identifier + '.jpg')
        self.assertEqual((status, data), (200, self.source.read_bytes()))
        for path in ('/', '/journal.js', '/journal.css', '/images/' + identifier + '.jpg'):
            self.assertEqual(self.request(path, '127.0.0.1')[0], 403)
        for path in ('/journal.js', '/journal.css', '/?page=0'):
            self.assertEqual(self.request(path)[0], 200)
        for path in ('/images/../nest-state.json', '/?page=-1', '/?page=1&page=2', '/?x=1'):
            self.assertEqual(self.request(path)[0], 400)
        self.assertEqual(self.request('/options.json')[0], 404)

    def test_page_escapes_text_paginates_and_discloses_missing_evidence(self):
        self.shots('hen_a')
        original = self.entries()[0]
        for index in range(23):
            entry = copy.deepcopy(original)
            entry['id'] = entry['id'][:24] + format(index, '032x')
            entry['text'] = '<script>alert("synthetic")</script>'
            atomic_json(self.journal.root / 'entries' / (entry['id'] + '.json'), entry)
        html = render(self.journal, 0).decode('utf-8')
        self.assertEqual(html.count('<article>'), 20)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>alert', html)
        self.assertIn('Older', html)
        self.assertEqual(render(self.journal, 1).count(b'<article>'), 4)
        self.assertIn(b'Newer', render(self.journal, 1))
        self.assertEqual(self.request('/?page=99999999')[0], 200)

    def test_runtime_commits_evidence_with_mqtt_and_results(self):
        runtime = Runtime(self.options, self.store, Mock())
        for index in range(3):
            prediction = {'label': 'hen_a', 'egg_count': 0, 'distance': .1, 'margin': .5}
            frame = runtime.journal.save_frame(self.source, prediction)
            runtime.observe('synthetic-' + str(index), prediction, frame)
        self.assertEqual(self.entries()[0]['text'], runtime.nest.state['latest_event'])
        self.assertEqual(len(set(frame_ids(self.entries()[0]))), 3)
        runtime.publisher.publish.assert_called_with(runtime.nest.snapshot())

    def test_retention_option_validation_and_ingress_metadata(self):
        self.assertEqual(self.options['journal_retention_days'], 365)
        for value in (0, True, 3651, '365'):
            with self.assertRaises(AppError):
                validate({'journal_retention_days': value})
        metadata = json.loads((Path(__file__).resolve().parent.parent / 'config.json').read_text())
        self.assertTrue(metadata['ingress'])
        self.assertEqual(metadata['ingress_port'], 8099)
        self.assertNotIn('8099/tcp', metadata['ports'])


if __name__ == '__main__':
    unittest.main()
