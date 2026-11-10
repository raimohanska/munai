"""Synthetic state observations only: no camera, credentials, or network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ha_service.mqtt import MQTT, discovery
from ha_service.nest import NestState
from ha_service.options import AppError, validate
from ha_service.runtime import Runtime


class NestTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.nest = NestState(self.root)

    def tearDown(self):
        self.scratch.cleanup()

    def shots(self, label, eggs=0, n=None):
        if n is None:
            n = 5 if label == 'no_chicken' and self.nest.state['hen'] is not None else 3
        events = []
        for _ in range(n):
            events.extend(self.nest.observe(label, eggs))
        return events

    def test_visit_laying_and_collection(self):
        self.shots('no_chicken', 2)
        self.assertEqual(self.shots('hen_a', n=2), [])
        self.assertEqual(self.shots('hen_a', n=1), ['Hen_a arrived'])
        self.assertEqual(self.nest.state['baseline'], 2)
        self.shots('hen_a', 0)
        self.assertEqual(self.shots('no_chicken', 3), ['Hen_a laid an Egg'])
        self.assertEqual(self.shots('no_chicken', 0, 2), [])
        self.assertEqual(self.shots('no_chicken', 0, 1), ['3 eggs collected'])
        self.assertEqual(self.shots('no_chicken', 0), [])

    def test_leave_without_egg_and_initial_unknown_baseline(self):
        self.shots('hen_a')
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_a left'])
        self.shots('hen_b')
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_b left'])

    def test_unknown_preserves_identity_and_breaks_switch_confirmation(self):
        self.shots('no_chicken', 1)
        self.shots('hen_a')
        self.assertEqual(self.shots('unknown'), [])
        self.shots('hen_b', n=2)
        self.shots('unknown', n=1)
        self.assertEqual(self.shots('hen_b', n=2), [])
        self.assertEqual(self.shots('hen_b', n=1), ['Hen_a left', 'Hen_b arrived'])
        self.assertIsNone(self.nest.state['baseline'])
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_b left'])

    def test_unknown_visit_can_acquire_identity_without_losing_baseline(self):
        self.shots('no_chicken', 1)
        self.assertEqual(self.shots('unknown'), ['Unknown arrived'])
        self.assertEqual(self.shots('hen_a'), ['Hen_a arrived'])
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_a laid an Egg'])

    def test_single_identity_among_unknown_arrival_frames(self):
        self.shots('no_chicken', 1)
        self.assertEqual(self.shots('hen_a', n=1), [])
        self.assertEqual(self.shots('unknown', n=2), ['Hen_a arrived'])
        self.assertEqual(self.nest.state['baseline'], 1)
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_a laid an Egg'])

    def test_empty_breaks_tentative_arrival_identity(self):
        self.shots('hen_a', n=1)
        self.shots('no_chicken', n=1)
        self.assertEqual(self.shots('unknown'), ['Unknown arrived'])

    def test_competing_identity_replaces_tentative_arrival(self):
        self.shots('hen_a', n=1)
        self.shots('hen_b', n=1)
        self.assertEqual(self.shots('unknown', n=2), ['Hen_b arrived'])

    def test_noise_and_occupied_zeros_are_not_collection(self):
        self.shots('no_chicken', 2)
        self.shots('no_chicken', 0, 2)
        self.shots('hen_a')
        self.assertEqual(self.nest.state['baseline'], 2)
        self.shots('no_chicken', 0, 2)
        self.shots('unknown')
        self.assertEqual(self.nest.state['hen'], 'hen_a')
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_a left'])
        self.shots('no_chicken', 0, 2)
        self.shots('no_chicken', 2, 1)
        self.assertEqual(self.shots('no_chicken', 0, 2), [])
        self.assertEqual(self.shots('no_chicken', 0, 1), ['2 eggs collected'])

    def test_restart_keeps_visit_and_event_but_not_streaks(self):
        self.shots('no_chicken', 1)
        self.shots('hen_a')
        self.shots('no_chicken', 2, 2)
        self.nest = NestState(self.root)
        self.assertEqual(self.nest.state['latest_event'], 'Hen_a arrived')
        self.assertEqual(self.shots('no_chicken', 2, 2), [])
        self.assertEqual(self.shots('no_chicken', 2, 2), [])
        self.assertEqual(self.shots('no_chicken', 2, 1), ['Hen_a laid an Egg'])
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 2), [])

    def test_confirmation_reset_on_missing_observation(self):
        self.shots('hen_a', n=2)
        self.nest.reset_confirmation()
        self.assertEqual(self.shots('hen_a', n=2), [])
        self.assertEqual(self.shots('hen_a', n=1), ['Hen_a arrived'])

    def test_departure_requires_five_empty_frames_even_when_eggs_are_stable(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.assertEqual(self.shots('no_chicken', 1, 4), [])
        self.assertEqual(self.nest.snapshot()['occupant'], 'Hen_a')
        self.assertEqual(self.nest.snapshot()['egg_count'], 0)
        self.assertEqual(self.shots('no_chicken', 1, 1), ['Hen_a laid an Egg'])

    def test_hen_frame_and_failed_round_both_restart_departure_confirmation(self):
        self.shots('hen_a')
        self.shots('no_chicken', 0, 4)
        self.shots('unknown', n=1)
        self.assertEqual(self.shots('no_chicken', 0, 4), [])
        self.nest.reset_confirmation()
        self.assertEqual(self.shots('no_chicken', 0, 4), [])
        self.assertEqual(self.shots('no_chicken', 0, 1), ['Hen_a left'])

    def test_delayed_laying_after_settled_zero_survives_restart(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.assertEqual(self.shots('no_chicken', 0), ['Hen_a left'])
        self.shots('no_chicken', 0, 12)
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 1, 2), [])
        self.assertEqual(self.shots('no_chicken', 1, 1), ['Hen_a laid an Egg'])
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 1)
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 1, 8), [])
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 1)

    def test_missed_second_egg_is_attributed_without_recounting_first(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.assertEqual(self.shots('no_chicken', 1), ['Hen_a laid an Egg'])
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_a laid an Egg'])
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 2)

    def test_new_confirmed_visit_replaces_delayed_attribution(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 0)
        self.shots('hen_b')
        self.assertEqual(self.shots('no_chicken', 0), ['Hen_b left'])
        self.assertEqual(self.shots('no_chicken', 1), ['Hen_b laid an Egg'])
        counts = self.nest.daily_snapshot()['counts']
        self.assertEqual((counts['hen_a'], counts['hen_b']), (0, 1))

    def test_unknown_visit_replaces_known_delayed_attribution(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 0)
        self.shots('unknown')
        self.shots('no_chicken', 0)
        self.assertEqual(self.shots('no_chicken', 1), ['Unknown laid an Egg'])
        counts = self.nest.daily_snapshot()['counts']
        self.assertEqual((counts['hen_a'], counts['unknown']), (0, 1))

    def test_collection_ends_attribution_but_an_initial_zero_does_not(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 1)
        self.assertEqual(self.shots('no_chicken', 0), ['1 egg collected'])
        self.assertEqual(self.shots('no_chicken', 1), [])
        counts = self.nest.daily_snapshot()['counts']
        self.assertEqual((counts['hen_a'], counts['unknown']), (1, 1))

    def test_partial_count_dips_do_not_double_count_on_rebound_after_restart(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 2)
        self.shots('no_chicken', 1)
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 2), [])
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 2)
        self.assertEqual(self.shots('no_chicken', 3), ['Hen_a laid an Egg'])
        self.assertEqual(self.nest.daily_snapshot()['counts']['hen_a'], 3)

    def test_missing_baseline_does_not_invent_a_laying_attribution(self):
        self.shots('hen_a')
        self.assertEqual(self.shots('no_chicken', 1), ['Hen_a left'])
        self.assertEqual(self.shots('no_chicken', 2), [])
        counts = self.nest.daily_snapshot()['counts']
        self.assertEqual((counts['hen_a'], counts['unknown']), (0, 2))

    def test_failed_delayed_laying_commit_does_not_consume_attribution(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 0)
        self.shots('no_chicken', 1, 2)
        before = self.nest.daily_snapshot()
        with patch('ha_service.nest.atomic_json', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.shots('no_chicken', 1, 1)
        self.assertEqual(self.nest.daily_snapshot(), before)
        self.assertEqual(self.shots('no_chicken', 1, 1), ['Hen_a laid an Egg'])

    def test_v4_migration_preserves_pending_visit_and_does_not_parse_event_text(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 0)
        old = dict(self.nest.state)
        for key in ('pending_settled', 'egg_high_water', 'egg_tracking'):
            del old[key]
        old['version'] = 4
        self.nest.path.write_text(json.dumps(old))
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 1), ['Hen_a laid an Egg'])
        old.update(pending_hen=None, pending_baseline=None, eggs=1,
                   latest_event='Hen_b left')
        self.nest.path.write_text(json.dumps(old))
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 2), [])
        self.assertEqual(self.nest.daily_snapshot()['counts']['unknown'], 1)

    def test_collection_confirmation_restarts_and_event_is_not_repeated(self):
        self.shots('no_chicken', 1)
        self.shots('no_chicken', 0, 2)
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 0, 2), [])
        self.assertEqual(self.shots('no_chicken', 0, 1), ['1 egg collected'])
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 0), [])
        self.assertEqual(self.nest.state['latest_event'], '1 egg collected')

    def test_empty_flicker_and_other_hens_break_arrival_confirmation(self):
        self.shots('no_chicken', 1)
        self.shots('hen_a', n=2)
        self.shots('hen_b', n=1)
        self.shots('hen_a', n=2)
        self.shots('no_chicken', 1, 1)
        self.assertIsNone(self.nest.state['hen'])
        self.assertEqual(self.shots('hen_a', n=2), [])
        self.assertEqual(self.shots('hen_a', n=1), ['Hen_a arrived'])

    def test_invalid_observations_do_not_mutate_state(self):
        before = dict(self.nest.state)
        for label, eggs in [('empty', 0), ('no_chicken', True),
                            ('no_chicken', -1), ('no_chicken', None)]:
            with self.assertRaises(AppError):
                self.nest.observe(label, eggs)
            self.assertEqual(self.nest.state, before)

    def test_write_failure_does_not_consume_event(self):
        self.shots('hen_a', n=2)
        before = dict(self.nest.state)
        with patch('ha_service.nest.atomic_json', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.nest.observe('hen_a', 0)
        self.assertEqual(self.nest.state, before)
        self.assertEqual(self.shots('hen_a', n=1), ['Hen_a arrived'])
        self.assertEqual(NestState(self.root).state, self.nest.state)

    def test_invalid_persistence_fails_closed_without_echoing_contents(self):
        self.shots('hen_a')
        good = dict(self.nest.state)
        bad = ['synthetic-private-detail', [], dict(good, version=True),
               dict(good, version=99), dict(good, eggs=True),
               dict(good, baseline=-1), dict(good, hen='empty'),
               dict(good, confirmed=1), dict(good, confirmed=False),
               dict(good, pending_hen='empty'), dict(good, pending_baseline=1),
               dict(good, pending_hen='hen_b'),
               dict(good, pending_settled=1), dict(good, pending_settled=True),
               dict(good, egg_high_water=True), dict(good, egg_high_water=-1),
               dict(good, latest_event='bad\ntext'), dict(good, extra=1)]
        for state in bad:
            self.nest.path.write_text(json.dumps(state))
            with self.assertRaises(AppError) as error:
                NestState(self.root)
            self.assertNotIn('synthetic-private-detail', str(error.exception))
        self.nest.path.write_text('{truncated')
        with self.assertRaises(AppError):
            NestState(self.root)

    def test_symlink_state_rejected(self):
        self.nest.path.symlink_to(self.root / 'elsewhere')
        with self.assertRaises(AppError):
            NestState(self.root)

    def test_retained_event_sensor_separate_from_live_observation(self):
        config = discovery(validate({}), 'latest_event')
        self.assertEqual(config['name'], 'Latest event')
        self.assertNotIn('expire_after', config)
        self.assertNotIn('value_template', config)
        publisher = MQTT.__new__(MQTT)
        publisher.base = 'munai/synthetic'
        publisher.send = Mock()
        publisher.publish({'occupant': 'Hen_a', 'egg_count': None,
                           'latest_event': 'Hen_a arrived'})
        publisher.send.assert_any_call('munai/synthetic/latest_event', 'Hen_a arrived', True)
        self.assertFalse(publisher.send.call_args_list[1].args[2])
        self.assertEqual(self.nest.state['latest_event'], None)

    def test_snapshot_tracks_confirmed_occupant_and_freezes_eggs(self):
        self.assertEqual(self.nest.snapshot(), {'occupant': None, 'egg_count': None,
                                               'latest_event': None})
        self.shots('no_chicken', 2, 2)
        self.assertIsNone(self.nest.snapshot()['occupant'])
        self.shots('no_chicken', 2, 1)
        self.assertEqual(self.nest.snapshot()['occupant'], 'empty')
        self.shots('hen_a', n=2)
        self.assertEqual(self.nest.snapshot()['occupant'], 'empty')
        self.shots('hen_a', n=1)
        self.shots('unknown')
        self.assertEqual(self.nest.snapshot(), {'occupant': 'Hen_a', 'egg_count': 2,
                                               'latest_event': 'Hen_a arrived'})
        self.shots('no_chicken', 0, 2)
        self.assertEqual(self.nest.snapshot()['occupant'], 'Hen_a')
        self.assertEqual(self.nest.snapshot()['egg_count'], 2)
        self.shots('hen_b')
        self.assertEqual(self.nest.snapshot()['occupant'], 'Hen_b')
        self.assertEqual(self.nest.snapshot()['egg_count'], 2)

    def test_all_egg_changes_require_matching_empty_observations(self):
        self.shots('no_chicken', 2)
        for eggs in (3, 3, 4, 4, 3, 0, 0, 2):
            self.shots('no_chicken', eggs, 1)
            self.assertEqual(self.nest.snapshot()['egg_count'], 2)
        self.shots('no_chicken', 4, 2)
        self.nest.reset_confirmation()
        self.shots('no_chicken', 4, 2)
        self.assertEqual(self.nest.snapshot()['egg_count'], 2)
        self.shots('no_chicken', 4, 1)
        self.assertEqual(self.nest.snapshot()['egg_count'], 4)

    def test_departure_and_laying_confirmation_can_be_separate_and_survive_restart(self):
        self.shots('no_chicken', 2)
        self.shots('hen_a')
        for eggs in (3, 4, 3, 4):
            self.shots('no_chicken', eggs, 1)
        self.assertEqual(self.shots('no_chicken', 3, 1), ['Hen_a left'])
        self.assertEqual(self.nest.snapshot()['occupant'], 'empty')
        self.assertEqual(self.nest.snapshot()['egg_count'], 2)
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 3, 2), [])
        self.assertEqual(self.shots('no_chicken', 3, 1), ['Hen_a laid an Egg'])
        self.assertEqual(self.nest.snapshot()['egg_count'], 3)
        self.assertEqual(self.shots('no_chicken', 3), [])

    def test_unsettled_departure_cannot_be_attributed_to_next_hen(self):
        self.shots('no_chicken', 2)
        self.shots('hen_a')
        for eggs in (3, 4, 3, 4, 3):
            self.shots('no_chicken', eggs, 1)
        self.shots('hen_b')
        self.assertIsNone(self.nest.state['baseline'])
        self.assertEqual(self.shots('no_chicken', 4), ['Hen_b left'])
        self.assertEqual(self.nest.snapshot()['egg_count'], 4)

    def test_first_visit_has_unknown_eggs_not_zero(self):
        self.shots('unknown')
        self.assertEqual(self.nest.snapshot()['occupant'], 'unknown')
        self.assertIsNone(self.nest.snapshot()['egg_count'])
        self.shots('no_chicken', 1)
        self.assertEqual(self.nest.snapshot()['egg_count'], 1)

    def test_legacy_state_migrates_without_inventing_counts_or_streaks(self):
        for hen, eggs in ((None, None), (None, 2), ('hen_a', None), ('hen_a', 2)):
            self.nest.path.write_text(json.dumps({'version': 1, 'hen': hen,
                                                'eggs': eggs, 'baseline': None,
                                                'latest_event': None}))
            self.nest = NestState(self.root)
            self.assertEqual(self.nest.snapshot()['egg_count'], eggs)
            self.assertEqual(self.nest.snapshot()['occupant'],
                             'Hen_a' if hen else 'empty' if eggs is not None else None)
            self.shots('no_chicken', 4, 2)
            self.assertEqual(self.nest.snapshot()['egg_count'], eggs)

    def test_failed_write_does_not_advance_egg_confirmation(self):
        self.shots('no_chicken', 2)
        self.shots('no_chicken', 3, 2)
        before = self.nest.snapshot()
        with patch('ha_service.nest.atomic_json', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.nest.observe('no_chicken', 3)
        self.assertEqual(self.nest.snapshot(), before)
        self.shots('no_chicken', 3, 1)
        self.assertEqual(self.nest.snapshot()['egg_count'], 3)

    def test_runtime_results_logs_and_mqtt_share_committed_snapshot(self):
        publisher = Mock()
        (self.root / 'results').mkdir()
        runtime = Runtime(validate({}), Mock(root=self.root), publisher)
        for index, label in enumerate(['no_chicken'] * 3 + ['hen_a'] * 3 +
                                      ['unknown', 'no_chicken', 'hen_a']):
            prediction = {'label': label, 'egg_count': 2 if label == 'no_chicken' else 0,
                          'distance': None, 'margin': None}
            stamp = 'synthetic-{}'.format(index)
            runtime.observe(stamp, prediction)
            snapshot = runtime.nest.snapshot()
            publisher.publish.assert_called_with(snapshot)
            for key, value in snapshot.items():
                self.assertEqual(runtime.last_observation[key], value)
            self.assertEqual(runtime.last_occupant, snapshot['occupant'])
            saved = json.loads((self.root / 'results' / (stamp + '.json')).read_text())
            self.assertEqual(saved, runtime.last_observation)
        self.assertEqual(runtime.last_observation['occupant'], 'Hen_a')
        self.assertEqual(runtime.last_observation['egg_count'], 2)
        self.assertEqual(runtime.last_observation['raw_prediction']['egg_count'], 0)


if __name__ == '__main__':
    unittest.main()
