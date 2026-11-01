"""Synthetic state observations only: no camera, credentials, or network."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ha_service.mqtt import MQTT, discovery
from ha_service.nest import NestState
from ha_service.options import AppError, validate


class NestTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.nest = NestState(self.root)

    def tearDown(self):
        self.scratch.cleanup()

    def shots(self, label, eggs=0, n=3):
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
        self.assertEqual(self.shots('no_chicken', 2, 1), ['Hen_a laid an Egg'])
        self.nest = NestState(self.root)
        self.assertEqual(self.shots('no_chicken', 2), [])

    def test_confirmation_reset_on_missing_observation(self):
        self.shots('hen_a', n=2)
        self.nest.reset_confirmation()
        self.assertEqual(self.shots('hen_a', n=2), [])
        self.assertEqual(self.shots('hen_a', n=1), ['Hen_a arrived'])

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
        publisher.publish({'label': 'hen_a'}, 'Hen_a arrived')
        publisher.send.assert_any_call('munai/synthetic/latest_event', 'Hen_a arrived', True)
        self.assertFalse(publisher.send.call_args_list[1].args[2])
        self.assertEqual(self.nest.state['latest_event'], None)


if __name__ == '__main__':
    unittest.main()
