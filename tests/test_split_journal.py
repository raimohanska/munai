"""Synthetic split decisions/journal evidence; no camera or real hen data."""
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from ha_service.journal import Journal, frame_ids, valid_entry
from ha_service.journal_web import render
from ha_service.options import AppError, validate
from ha_service.split_nest import SplitNest
from ha_service.storage import Store
from ha_service.runtime import Runtime


def observation(label='no_chicken', eggs=0, obscured=False):
    return {'label': label, 'egg_count': eggs, 'obscured': obscured}


class SplitJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data', validate({'box_split': 'vertical'}))
        self.today = lambda: date(2000, 1, 1)
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        self.journal = Journal(self.store)
        self.journal.retain(self.nest, force=True)
        self.source = self.root / 'synthetic.jpg'
        Image.new('RGB', (640, 360), (80, 90, 100)).save(str(self.source))

    def tearDown(self):
        self.temp.cleanup()

    def shot(self, left=None, right=None, center=None, flush=True):
        zones = {'left': left or observation(), 'right': right or observation(),
                 'center': center or observation()}
        frame = self.journal.save_frame(self.source, {'label': 'unknown', 'egg_count': None})
        events = self.nest.observe_zones(zones, frame)
        if flush:
            self.journal.flush(self.nest)
            self.journal.cleanup_pending(self.nest)
        return frame, events

    def shots(self, n=3, **zones):
        return [self.shot(**zones)[0] for _ in range(n)]

    def entries(self):
        return [self.journal.read_entry(p) for p in self.journal.entries()]

    def test_two_hens_have_independent_arrival_evidence_and_shared_photos(self):
        baseline = self.shots()
        frames = self.shots(left=observation('hen_a'), right=observation('hen_b'))
        entries = self.entries()
        self.assertEqual({e['text'] for e in entries}, {'Left: Hen_a arrived', 'Right: Hen_b arrived'})
        self.assertEqual({e['zone'] for e in entries}, {'left', 'right'})
        self.assertTrue(all(valid_entry(e) for e in entries))
        self.assertTrue(all(set(frame_ids(e)) == {f['id'] for f in baseline + frames} for e in entries))
        for e in entries:
            self.assertTrue(all(f['zone'] == e['zone'] for g in e['evidence'] for f in g['frames']))
        self.assertEqual(len({*frame_ids(entries)}), 6)
        page = render(self.journal, 0).decode()
        self.assertIn('vertical-left', page)
        self.assertIn('vertical-right', page)
        self.assertIn('Photo ID:', page)
        self.assertIn(frames[0]['id'], page)

    def test_one_nest_lays_while_other_remains_occupied(self):
        baseline = self.shots()
        visit = self.shots(left=observation('hen_a'), right=observation('hen_b'))
        after = self.shots(n=5, left=observation(eggs=1), right=observation('hen_b'))
        entry = next(e for e in self.entries() if e['text'] == 'Left: Hen_a laid an Egg')
        self.assertEqual(set(frame_ids(entry)), {f['id'] for f in baseline + visit + after})
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 1)
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_b'], 0)
        self.assertEqual(self.nest.snapshot()['zones']['right']['occupant'], 'Hen_b')

    def test_center_hen_never_implies_laying_or_collection_on_hidden_sides(self):
        self.shots(left=observation(eggs=1))
        before = self.nest.daily_snapshot()
        hidden = observation('unknown', eggs=None, obscured=True)
        self.shots(n=6, left=hidden, right=hidden, center=observation('hen_a', eggs=None))
        self.assertEqual([e['text'] for e in self.entries()], ['Center: Hen_a arrived'])
        self.assertEqual(self.nest.snapshot()['zones']['left']['occupant'], 'obscured')
        self.assertEqual(self.nest.regions['left'].state['eggs'], 1)
        self.assertEqual(self.nest.daily_snapshot(), before)

    def test_occlusion_invalidates_old_laying_attribution(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.shot(left=observation('unknown', eggs=None, obscured=True))
        self.shots(n=5, left=observation(eggs=1))
        self.assertFalse(any('laid an Egg' in e['text'] for e in self.entries()))
        self.assertEqual(self.nest.daily_snapshot()['counts']['unknown'], 1)

    def test_outbox_replay_after_restart_and_failed_archive_write(self):
        self.shots()
        self.shots(n=2, left=observation('hen_a'), right=observation('hen_b'))
        self.shot(left=observation('hen_a'), right=observation('hen_b'), flush=False)
        expected = {e['id'] for e in self.nest.state['journal']['outbox']}
        with patch('ha_service.journal.atomic_json', side_effect=OSError()):
            with self.assertRaises(OSError):
                self.journal.flush(self.nest)
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        self.journal.flush(self.nest)
        self.journal.flush(self.nest)
        self.assertEqual({e['id'] for e in self.entries()}, expected)
        self.assertEqual(self.nest.state['journal']['outbox'], [])

    def test_restart_preserves_active_evidence_and_resets_streaks(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        self.journal.retain(self.nest, force=True)
        self.shots(n=5, left=observation(eggs=1))
        self.assertTrue(any(e['text'] == 'Left: Hen_a laid an Egg' for e in self.entries()))
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 1)

    def test_layout_change_fails_closed(self):
        with self.assertRaises(AppError):
            SplitNest(self.store.root, 'horizontal', today=self.today)

    def test_initial_split_counts_are_baselines_not_fabricated_production(self):
        self.shots(left=observation(eggs=2), right=observation(eggs=1))
        self.assertEqual(self.nest.snapshot()['egg_count'], 3)
        self.assertEqual(self.nest.daily_snapshot()['counts']['unknown'], 0)
        self.assertEqual(self.entries(), [])

    def test_failed_observation_gap_invalidates_attribution(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.nest.reset_confirmation()
        self.shots(n=5, left=observation(eggs=1))
        self.assertFalse(any('laid an Egg' in e['text'] for e in self.entries()))
        self.assertEqual(self.nest.daily_snapshot()['counts']['unknown'], 1)

    def test_daily_whole_box_carry_is_not_counted_twice(self):
        other = self.root / 'carry'
        other.mkdir()
        carry = {'date': '2000-01-01', 'counts': {'unknown': 1, 'hen_a': 2}}
        nest = SplitNest(other, 'vertical', carry=carry, today=self.today)
        self.assertEqual(nest.daily_snapshot()['counts']['hen_a'], 2)
        restarted = SplitNest(other, 'vertical', carry=carry, today=self.today)
        self.assertEqual(restarted.daily_snapshot(), nest.daily_snapshot())

    def test_horizontal_journal_has_correct_zones(self):
        self.store = Store(self.root / 'horizontal', validate({'box_split': 'horizontal'}))
        self.nest = SplitNest(self.store.root, 'horizontal', today=self.today)
        self.journal = Journal(self.store)
        for i in range(3):
            frame = self.journal.save_frame(self.source, {'label': 'unknown', 'egg_count': None})
            self.nest.observe_zones({'top': observation('hen_a'), 'bottom': observation('hen_b'),
                                     'center': observation()}, frame)
            self.journal.flush(self.nest)
        self.assertEqual({e['zone'] for e in self.entries()}, {'top', 'bottom'})
        page = render(self.journal, 0).decode()
        self.assertIn('horizontal-top', page)
        self.assertIn('horizontal-bottom', page)

    def test_photo_dedup_and_rotation_do_not_drop_split_journal_evidence(self):
        self.store.options['store_captures'] = True
        runtime = Runtime(self.store.options, self.store)
        ids = []
        for i in range(6):
            stamp = '20000101T120000_{:06d}Z'.format(i)
            zones = {'left': observation('hen_a') if i >= 3 else observation(),
                     'right': observation(), 'center': observation()}
            prediction = {'label': 'hen_a' if i >= 3 else 'no_chicken', 'egg_count': 0,
                          'distance': None, 'margin': None, 'zones': zones, 'ambiguous': False}
            frame = runtime.journal.save_frame(self.source, prediction)
            ids.append(frame['id'])
            runtime.photos.save(self.source, stamp, prediction)
            runtime.observe(stamp, prediction, frame)
        photos = list((self.store.root / 'captures').rglob('*.jpg'))
        self.assertEqual(len(photos), 2)
        for photo in photos:
            photo.unlink()  # Simulated ordinary-photo rotation, not camera capture.
        entry = runtime.journal.read_entry(runtime.journal.entries()[0])
        self.assertEqual(entry['zone'], 'left')
        self.assertEqual(set(frame_ids(entry)), set(ids))
        self.assertTrue(all((runtime.journal.root / 'images' / (i + '.jpg')).is_file() for i in ids))
