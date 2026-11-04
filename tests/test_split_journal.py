"""Synthetic split decisions/journal evidence; no camera or real hen data."""
from datetime import date
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from ha_service.journal import Journal, frame_ids, valid_entry
from ha_service.journal_web import render
from ha_service.options import AppError, validate
from ha_service.split_nest import SplitNest
from ha_service.storage import Store, atomic_json
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

    def test_move_to_center_releases_old_position_and_retains_real_evidence(self):
        self.shots()
        self.shots(right=observation('hen_a'))
        hidden = observation('unknown', eggs=None, obscured=True)
        frames = self.shots(n=2, left=hidden, right=hidden, center=observation('hen_a'))
        self.assertEqual(self.nest.regions['right'].state['hen'], 'hen_a')
        self.assertIsNone(self.nest.regions['center'].state['hen'])
        last, events = self.shot(left=hidden, right=hidden, center=observation('hen_a'))
        self.assertEqual(events, ['Hen_a moved to center'])
        self.assertIsNone(self.nest.regions['right'].state['hen'])
        self.assertEqual(self.nest.regions['center'].state['hen'], 'hen_a')
        self.assertEqual(self.nest.snapshot()['zones']['right']['occupant'], 'obscured')
        self.assertEqual(self.nest.regions['right'].state['eggs'], 0)
        self.assertIsNone(self.nest.regions['right'].state['pending_hen'])
        entry = next(e for e in self.entries() if ' moved to ' in e['text'])
        self.assertEqual(entry['text'], 'Hen_a moved to center')
        self.assertEqual(entry['zone'], 'center')
        self.assertTrue(valid_entry(entry))
        self.assertEqual(set(frame_ids(entry)), {f['id'] for f in frames + [last]})
        self.assertTrue(all(f['zone'] == 'center' for g in entry['evidence'] for f in g['frames']))
        self.assertNotIn('Some evidence is unavailable.', render(self.journal, 0).decode())
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        self.shots(n=5)
        self.assertEqual({e['text'] for e in self.entries()},
                         {'Right: Hen_a arrived', 'Hen_a moved to center', 'Center: Hen_a left'})
        self.assertEqual(sum(self.nest.daily_snapshot()['counts'].values()), 0)

    def test_move_to_either_side_does_not_reuse_hidden_egg_baselines(self):
        for side in ('left', 'right'):
            with self.subTest(side=side):
                self.shots()
                self.shots(center=observation('hen_a'))
                self.shots(**{side: observation('hen_a')})
                self.assertEqual(self.nest.regions[side].state['hen'], 'hen_a')
                self.assertIsNone(self.nest.regions['center'].state['hen'])
                self.assertIsNone(self.nest.regions[side].state['baseline'])
                self.assertIn('Hen_a moved to ' + side, {e['text'] for e in self.entries()})
                self.shots(n=5, **{side: observation(eggs=1)})
        self.assertFalse(any('laid an Egg' in e['text'] for e in self.entries()))

    def test_side_to_side_move_does_not_emit_old_zone_departure(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.shots(n=5, right=observation('hen_a'))
        self.assertEqual({e['text'] for e in self.entries()},
                         {'Left: Hen_a arrived', 'Hen_a moved to right'})
        self.assertIsNone(self.nest.regions['left'].state['hen'])

    def test_simultaneous_swap_is_two_moves_not_departures(self):
        self.shots()
        self.shots(left=observation('hen_a'), right=observation('hen_b'))
        self.shots(left=observation('hen_b'), right=observation('hen_a'))
        self.assertEqual({e['text'] for e in self.entries()},
                         {'Left: Hen_a arrived', 'Right: Hen_b arrived',
                          'Hen_a moved to right', 'Hen_b moved to left'})
        self.assertEqual(self.nest.regions['left'].state['hen'], 'hen_b')
        self.assertEqual(self.nest.regions['right'].state['hen'], 'hen_a')

    def test_duplicate_observed_identity_does_not_choose_a_larger_hen(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.shots(n=6, left=observation('hen_a'), right=observation('hen_a'))
        self.assertEqual({e['text'] for e in self.entries()}, {'Left: Hen_a arrived'})
        self.assertIsNone(self.nest.regions['right'].state['hen'])
        self.assertEqual(self.nest.blocked, {'left', 'right'})

    def test_unknown_new_position_does_not_steal_known_identity(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.shots(n=5, left=observation('unknown', obscured=True), right=observation('unknown'))
        self.assertEqual(self.nest.regions['left'].state['hen'], 'hen_a')
        self.assertEqual(self.nest.regions['right'].state['hen'], 'unknown')
        self.assertFalse(any(' moved to ' in e['text'] for e in self.entries()))

    def test_restart_requires_fresh_move_confirmation(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        hidden = observation('unknown', obscured=True)
        self.shots(n=2, left=hidden, center=observation('hen_a'))
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        self.shots(n=2, left=hidden, center=observation('hen_a'))
        self.assertFalse(any(' moved to ' in e['text'] for e in self.entries()))
        self.shot(left=hidden, center=observation('hen_a'))
        self.assertIsNone(self.nest.regions['left'].state['hen'])
        self.assertEqual(self.nest.regions['center'].state['hen'], 'hen_a')

    def test_failed_transaction_prepare_does_not_change_live_positions(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        hidden = observation('unknown', obscured=True)
        self.shots(n=2, left=hidden, center=observation('hen_a'))
        before = {z: copy.deepcopy(n.state) for z, n in self.nest.regions.items()}
        with patch('ha_service.split_nest.atomic_json', side_effect=OSError()):
            with self.assertRaises(OSError):
                self.shot(left=hidden, center=observation('hen_a'))
        self.assertEqual({z: n.state for z, n in self.nest.regions.items()}, before)
        self.assertFalse(self.nest.transition_path.exists())
        self.shot(left=hidden, center=observation('hen_a'))
        self.assertEqual(sum(' moved to ' in e['text'] for e in self.entries()), 1)

    def test_partial_multi_region_write_replays_move_exactly_once(self):
        self.shots()
        self.shots(right=observation('hen_a'))
        hidden = observation('unknown', obscured=True)
        self.shots(n=2, right=hidden, center=observation('hen_a'))
        def fail_center(path, value):
            if path.parent.name == 'center':
                raise OSError()
            atomic_json(path, value)
        with patch('ha_service.split_nest.atomic_json', side_effect=fail_center):
            with self.assertRaises(OSError):
                self.shot(right=hidden, center=observation('hen_a'))
        self.assertTrue(self.nest.transition_path.exists())
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        self.assertFalse(self.nest.transition_path.exists())
        self.assertIsNone(self.nest.regions['right'].state['hen'])
        self.assertEqual(self.nest.regions['center'].state['hen'], 'hen_a')
        self.journal.flush(self.nest)
        self.journal.flush(self.nest)
        self.assertEqual(sum(' moved to ' in e['text'] for e in self.entries()), 1)
        self.shots(n=5)
        self.assertEqual(sum(e['text'].endswith(' left') for e in self.entries()), 1)

    def test_invalid_transaction_fails_closed(self):
        self.nest.transition_path.write_text(json.dumps({'layout': 'vertical', 'regions': {}}))
        with self.assertRaises(AppError):
            SplitNest(self.store.root, 'vertical', today=self.today)

    def test_confirmation_repairs_legacy_duplicate_positions(self):
        self.shots()
        self.shots(right=observation('hen_a'))
        # Simulate the old independent trackers retaining the same identity in
        # two positions, including after an app upgrade/restart.
        center = self.nest.regions['center']
        state = copy.deepcopy(center.state)
        state.update(hen='hen_a', confirmed=True)
        center._commit(state)
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        hidden = observation('unknown', obscured=True)
        self.shots(right=hidden, center=observation('hen_a'))
        self.assertIsNone(self.nest.regions['right'].state['hen'])
        self.assertEqual(self.nest.regions['center'].state['hen'], 'hen_a')
        self.assertEqual(sum(' moved to ' in e['text'] for e in self.entries()), 1)

    def test_horizontal_movement_uses_same_single_position_rule(self):
        root = self.root / 'horizontal-move'
        root.mkdir()
        nest = SplitNest(root, 'horizontal', today=self.today)
        def shots(top, bottom, center, n=3):
            events = []
            for _ in range(n):
                events.extend(nest.observe_zones({'top': top, 'bottom': bottom, 'center': center}))
            return events
        shots(observation('hen_a'), observation(), observation())
        hidden = observation('unknown', obscured=True)
        self.assertEqual(shots(hidden, hidden, observation('hen_a')), ['Hen_a moved to center'])
        self.assertIsNone(nest.regions['top'].state['hen'])
        self.assertEqual(shots(observation(), observation('hen_a'), observation()),
                         ['Hen_a moved to bottom'])
        self.assertIsNone(nest.regions['center'].state['hen'])
        self.assertEqual(nest.regions['bottom'].state['hen'], 'hen_a')

    def test_clear_side_counts_while_other_hen_stays(self):
        self.shots()
        self.shots(left=observation('hen_a'), right=observation('hen_b'))
        after = self.shots(n=5, left=observation(eggs=1), right=observation('hen_b'))
        self.assertEqual(self.nest.regions['left'].state['eggs'], 1)
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 1)
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_b'], 0)
        self.assertEqual(self.nest.snapshot()['zones']['right']['occupant'], 'Hen_b')
        entry = next(e for e in self.entries() if e['text'] == 'Left: Hen_a laid an Egg')
        count_frames = next(g['frames'] for g in entry['evidence'] if g['role'] == 'Egg count confirmation')
        self.assertTrue(set(frame_ids(count_frames)) <= {f['id'] for f in after})
        self.shots(n=5, left=observation(eggs=1), right=observation(eggs=1))
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_b'], 1)
        self.assertEqual(self.nest.snapshot()['egg_count'], 2)

    def test_unknown_presence_keeps_visit_and_laying_is_the_departure_event(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.shot(left=observation('unknown', eggs=None))
        self.assertEqual(self.nest.regions['left'].state['baseline'], 0)
        self.shots(n=4, left=observation(eggs=1))
        self.assertEqual([e['text'] for e in self.entries()], ['Left: Hen_a arrived'])
        self.shot(left=observation(eggs=1))
        self.assertEqual({e['text'] for e in self.entries()},
                         {'Left: Hen_a arrived', 'Left: Hen_a laid an Egg'})
        # Laying is the departure event, not a second, separate "left" entry.
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 1)

    def test_obscured_frames_cannot_complete_departure_confirmation(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        hidden = observation('unknown', eggs=None, obscured=True)
        self.shots(n=8, left=hidden)
        self.assertEqual([e['text'] for e in self.entries()], ['Left: Hen_a arrived'])
        self.shots(n=4, left=observation(eggs=1))
        self.shot(left=hidden)
        # Obscured pauses at four: it is not a fifth shot, nor a reset to zero.
        self.assertEqual([e['text'] for e in self.entries()], ['Left: Hen_a arrived'])
        self.shot(left=observation(eggs=1))
        self.assertIn('Left: Hen_a left', {e['text'] for e in self.entries()})
        self.assertEqual(self.nest.daily_snapshot()['counts']['unknown'], 1)

    def test_obscured_does_not_consume_or_reset_arrival_shots(self):
        self.shots()
        before = self.shots(n=2, left=observation('hen_a'))
        skipped = self.shots(n=6, left=observation('unknown', eggs=None, obscured=True))
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.nest.regions['left'].streak, 2)
        last = self.shot(left=observation('hen_a'))[0]
        entry = self.entries()[0]
        self.assertEqual(entry['text'], 'Left: Hen_a arrived')
        arrival = next(g['frames'] for g in entry['evidence'] if g['role'] == 'Identity confirmation')
        self.assertEqual(set(frame_ids(arrival)), {f['id'] for f in before + [last]})
        self.assertTrue(set(frame_ids(entry)).isdisjoint({f['id'] for f in skipped}))

    def test_small_fragment_only_pauses_visit_and_keeps_clean_count(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        small = dict(observation('unknown', eggs=None, obscured=True), obscured_reason='small_body')
        self.shots(n=6, left=small)
        self.assertEqual(self.nest.regions['left'].state['baseline'], 0)
        self.assertEqual(self.nest.regions['left'].state['eggs'], 0)
        self.assertEqual([e['text'] for e in self.entries()], ['Left: Hen_a arrived'])
        self.shots(n=5, left=observation(eggs=1))
        self.assertIn('Left: Hen_a laid an Egg', {e['text'] for e in self.entries()})
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 1)

    def test_obscured_shots_do_not_enter_egg_count_confirmation_evidence(self):
        self.shots()
        clean = self.shots(n=2, left=observation(eggs=1))
        small = dict(observation('unknown', eggs=None, obscured=True), obscured_reason='small_body')
        skipped = self.shots(n=4, left=small)
        self.assertEqual(self.nest.regions['left'].egg_streak, 2)
        self.assertEqual(self.nest.regions['left'].state['eggs'], 0)
        last = self.shot(left=observation(eggs=1))[0]
        self.assertEqual(self.nest.regions['left'].state['eggs'], 1)
        evidence = self.nest.regions['left'].state['journal']['baseline']
        self.assertEqual(set(frame_ids(evidence)), {f['id'] for f in clean + [last]})
        self.assertTrue(set(frame_ids(evidence)).isdisjoint({f['id'] for f in skipped}))

    def test_center_never_counts_even_if_supplied_egg_counts(self):
        self.shots(center=observation(eggs=2))
        self.shots(center=observation('hen_a'))
        self.shots(n=5, center=observation(eggs=3))
        self.shots(center=observation(eggs=0))
        self.assertIsNone(self.nest.snapshot()['zones']['center']['egg_count'])
        self.assertEqual(sum(self.nest.daily_snapshot()['counts'].values()), 0)
        self.assertEqual({e['text'] for e in self.entries()},
                         {'Center: Hen_a arrived', 'Center: Hen_a left'})

    def test_same_hen_can_score_from_either_side_without_recount_after_restart(self):
        self.shots()
        self.shots(left=observation('hen_a'))
        self.shots(n=5, left=observation(eggs=1))
        self.shots(left=observation(eggs=1), right=observation('hen_a'))
        self.shots(n=5, left=observation(eggs=1), right=observation(eggs=1))
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 2)
        self.nest = SplitNest(self.store.root, 'vertical', today=self.today)
        self.shots(n=6, left=observation(eggs=1), right=observation(eggs=1))
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 2)

    def test_local_obstruction_does_not_freeze_other_side(self):
        self.shots()
        hidden = observation('unknown', eggs=None, obscured=True)
        self.shots(left=hidden, right=observation(eggs=1))
        self.assertEqual(self.nest.snapshot()['zones']['right']['egg_count'], 1)
        self.assertEqual(self.nest.daily_snapshot()['counts']['unknown'], 1)

    def test_visit_breaks_pre_visit_egg_confirmation(self):
        self.shots()
        self.shots(n=2, left=observation(eggs=1))
        self.shots(left=observation('hen_a'))
        self.shot(left=observation(eggs=1))
        self.assertEqual(self.nest.regions['left'].egg_streak, 1)
        self.assertEqual(self.nest.regions['left'].state['eggs'], 0)

    def test_horizontal_sides_count_independently_too(self):
        root = self.root / 'horizontal-counts'
        root.mkdir()
        nest = SplitNest(root, 'horizontal', today=self.today)
        def shots(top, bottom, n=3):
            for _ in range(n):
                nest.observe_zones({'top': top, 'bottom': bottom, 'center': observation()})
        shots(observation(), observation())
        shots(observation('hen_a'), observation('hen_b'))
        shots(observation(eggs=1), observation('hen_b'), n=5)
        self.assertEqual(nest.daily_snapshot()['counts']['hen_a'], 1)
        self.assertEqual(nest.snapshot()['zones']['bottom']['occupant'], 'Hen_b')
        shots(observation(eggs=1), observation(eggs=1), n=5)
        self.assertEqual(nest.daily_snapshot()['counts']['hen_b'], 1)
        self.assertEqual(nest.snapshot()['egg_count'], 2)

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
