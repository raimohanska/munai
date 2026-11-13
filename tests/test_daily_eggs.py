"""Synthetic dated observations and offline MQTT calls; never camera capture."""
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

from ha_service.mqtt import MQTT, daily_discovery
from ha_service.nest import NestState
from ha_service.options import AppError, validate
from ha_service.runtime import Runtime


class DailyEggTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.root = Path(self.scratch.name)
        self.day = date(2026, 11, 2)
        self.nest = NestState(self.root, today=lambda: self.day)
        self.nest.update_day(['hen_a', 'hen_b'])

    def tearDown(self):
        self.scratch.cleanup()

    def shots(self, label, eggs=0, n=None):
        if n is None:
            n = 5 if label == 'no_chicken' and self.nest.state['hen'] is not None else 3
        events = []
        for _ in range(n):
            events.extend(self.nest.observe(label, eggs))
        return events

    def counts(self):
        return self.nest.daily_snapshot()['counts']

    def restart(self):
        self.nest = NestState(self.root, today=lambda: self.day)

    def test_first_observed_eggs_and_unattributed_increases_go_to_unknown(self):
        self.shots('no_chicken', 2, 2)
        self.assertEqual(self.counts(), {'hen_a': 0, 'hen_b': 0, 'unknown': 0})
        self.shots('no_chicken', 2, 1)
        self.assertEqual(self.counts()['unknown'], 2)
        self.shots('no_chicken', 4)
        self.assertEqual(self.counts()['unknown'], 4)
        self.shots('no_chicken', 4)
        self.assertEqual(sum(self.counts().values()), 4)

    def test_known_laying_increments_by_delta_collection_does_not_subtract(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('unknown')
        self.shots('no_chicken', 2, 2)
        self.assertEqual(self.counts()['hen_a'], 0)
        self.assertEqual(self.shots('no_chicken', 2, 2), [])
        self.assertEqual(self.shots('no_chicken', 2, 1), ['Hen_a laid an Egg'])
        self.assertEqual(self.counts(), {'hen_a': 2, 'hen_b': 0, 'unknown': 0})
        self.shots('no_chicken', 0)
        self.shots('hen_b')
        self.shots('no_chicken', 1)
        self.assertEqual(self.counts(), {'hen_a': 2, 'hen_b': 1, 'unknown': 0})
        self.restart()
        self.shots('no_chicken', 1)
        self.assertEqual(sum(self.counts().values()), 3)

    def test_unknown_visit_and_hen_swap_do_not_lose_eggs(self):
        self.shots('no_chicken', 0)
        self.shots('unknown')
        self.shots('no_chicken', 1)
        self.shots('hen_a')
        self.shots('hen_b')
        self.shots('no_chicken', 2)
        self.assertEqual(self.counts(), {'hen_a': 0, 'hen_b': 0, 'unknown': 2})

    def test_unknown_visit_can_acquire_identity_before_counting(self):
        self.shots('no_chicken', 0)
        self.shots('unknown')
        self.shots('hen_a')
        self.shots('no_chicken', 1)
        self.assertEqual(self.counts(), {'hen_a': 1, 'hen_b': 0, 'unknown': 0})

    def test_delayed_attribution_is_atomic_and_not_repeated_after_restart(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        for eggs in (1, 2, 1, 2, 1):
            self.shots('no_chicken', eggs, 1)
        self.assertEqual(self.counts()['hen_a'], 0)
        self.restart()
        self.shots('no_chicken', 1)
        self.assertEqual(self.counts()['hen_a'], 1)
        self.restart()
        self.shots('no_chicken', 1)
        self.assertEqual(self.counts()['hen_a'], 1)

    def test_interrupted_pending_visit_counts_increase_as_unknown(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        for eggs in (1, 2, 1, 2, 1):
            self.shots('no_chicken', eggs, 1)
        self.shots('hen_b')
        self.shots('no_chicken', 2)
        self.assertEqual(self.counts(), {'hen_a': 0, 'hen_b': 0, 'unknown': 2})

    def test_failed_commit_cannot_advance_counters_or_consume_laying(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 1, 4)
        before = self.nest.daily_snapshot()
        with patch('ha_service.nest.atomic_json', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError):
                self.shots('no_chicken', 1, 1)
        self.assertEqual(self.nest.daily_snapshot(), before)
        self.assertEqual(NestState(self.root).daily_snapshot(), before)
        self.shots('no_chicken', 1, 1)
        self.assertEqual(self.counts()['hen_a'], 1)

    def test_rollover_keeps_visit_baseline_and_counts_on_confirmation_date(self):
        self.shots('no_chicken', 1)
        self.shots('hen_a')
        self.day = date(2026, 11, 3)
        self.nest.update_day(['hen_a', 'hen_b', 'hen_c'])
        self.assertEqual(sum(self.counts().values()), 0)
        self.assertEqual(self.nest.state['baseline'], 1)
        self.assertEqual(self.nest.daily_snapshot()['date'], '2026-11-03')
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_a laid an Egg'])
        self.assertEqual(self.counts()['hen_a'], 1)
        self.assertEqual(self.counts()['hen_c'], 0)
        self.nest.update_day(['hen_a'])
        self.assertEqual(self.counts()['hen_a'], 1)
        self.assertIn('hen_b', self.counts())

    def test_midnight_while_empty_does_not_recount_existing_eggs(self):
        self.shots('no_chicken', 2)
        self.day = date(2026, 11, 3)
        self.shots('no_chicken', 2)
        self.assertEqual(sum(self.counts().values()), 0)
        self.shots('no_chicken', 3)
        self.assertEqual(self.counts()['unknown'], 1)

    def test_delayed_attribution_and_high_water_survive_daily_rollover(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 0)
        self.day = date(2026, 11, 3)
        self.restart()
        self.assertEqual(self.shots('no_chicken', 2), ['Hen_a laid an Egg'])
        self.assertEqual(self.counts()['hen_a'], 2)
        self.shots('no_chicken', 1)
        self.day = date(2026, 11, 4)
        self.restart()
        self.assertEqual(self.shots('no_chicken', 2), [])
        self.assertEqual(sum(self.counts().values()), 0)
        self.assertEqual(self.shots('no_chicken', 3), ['Hen_a laid an Egg'])
        self.assertEqual(self.counts()['hen_a'], 1)

    def test_backwards_date_fails_closed_instead_of_resetting_totals(self):
        before = self.nest.daily_snapshot()
        self.day = date(2026, 11, 1)
        with self.assertRaises(AppError):
            self.nest.update_day()
        self.assertEqual(self.nest.daily_snapshot(), before)

    def test_v2_migration_does_not_recount_preexisting_eggs(self):
        self.shots('no_chicken', 2)
        state = dict(self.nest.state)
        del state['daily_eggs']
        del state['journal']
        del state['pending_settled']
        del state['egg_high_water']
        del state['egg_tracking']
        state['version'] = 2
        self.nest.path.write_text(json.dumps(state))
        self.restart()
        self.shots('no_chicken', 2)
        self.assertEqual(self.counts()['unknown'], 0)
        self.shots('no_chicken', 3)
        self.assertEqual(self.counts()['unknown'], 1)

    def test_invalid_daily_persistence_is_rejected(self):
        good = dict(self.nest.state)
        for value in (None, {}, {'date': '2026-2-3', 'counts': {'unknown': 0}},
                      {'date': '2026-02-30', 'counts': {'unknown': 0}},
                      {'date': None, 'counts': {'unknown': 1}},
                      {'date': '2026-11-02', 'counts': {}},
                      {'date': '2026-11-02', 'counts': {'unknown': True}},
                      {'date': '2026-11-02', 'counts': {'unknown': -1}},
                      {'date': '2026-11-02', 'counts': {'unknown': 0, 'empty': 1}}):
            self.nest.path.write_text(json.dumps(dict(good, daily_eggs=value)))
            with self.assertRaises(AppError):
                self.restart()

    def test_daily_snapshot_cannot_mutate_internal_counts(self):
        snapshot = self.nest.daily_snapshot()
        snapshot['counts']['hen_a'] = 9
        self.assertEqual(self.counts()['hen_a'], 0)

    def test_mqtt_retries_publish_absolute_retained_totals_without_recounting(self):
        self.shots('no_chicken', 0)
        self.shots('hen_a')
        self.shots('no_chicken', 1)
        publisher = MQTT.__new__(MQTT)
        publisher.options = validate({})
        publisher.base = 'munai/synthetic'
        publisher.daily_configs = {}
        publisher.zone_configs = {}
        publisher.send = Mock(side_effect=AppError('synthetic failure'))
        with self.assertRaises(AppError):
            publisher.publish_daily(self.nest.daily_snapshot())
        self.restart()
        publisher.send = Mock()
        publisher.publish_daily(self.nest.daily_snapshot())
        call = publisher.send.call_args_list[0].args
        self.assertEqual(call[0], 'munai/synthetic/daily_eggs')
        self.assertEqual(json.loads(call[1]), self.nest.daily_snapshot())
        self.assertTrue(call[2])
        publisher.send.reset_mock()
        publisher.publish_daily(self.nest.daily_snapshot())
        publisher.send.assert_called_once_with(*call)
        self.assertEqual(self.counts()['hen_a'], 1)
        for name in ('discovery', 'egg_discovery', 'event_discovery'):
            setattr(publisher, name + '_topic', 'synthetic/' + name)
        publisher.config = publisher.egg_config = publisher.event_config = {}
        client = Mock()
        publisher.announce(client)
        client.publish.assert_any_call(
            'homeassistant/sensor/' + publisher.options['device_id'] + '/daily_eggs_hen_a/config',
            json.dumps(publisher.daily_configs['daily_eggs_hen_a']), qos=1, retain=True)

    def test_discovery_has_total_hen_unknown_and_date_attributes(self):
        options = validate({})
        total = daily_discovery(options)
        self.assertEqual(total['name'], 'Eggs today')
        self.assertIn('sum', total['value_template'])
        self.assertEqual(total['state_class'], 'total_increasing')
        self.assertNotIn('expire_after', total)
        for identity in ('hen_a', 'unknown'):
            config = daily_discovery(options, identity)
            self.assertIn("['" + identity + "']", config['value_template'])
            self.assertIn('date', config['json_attributes_template'])
            self.assertEqual(config['state_topic'], total['state_topic'])
        for identity in ('empty', "bad'identity", ''):
            with self.assertRaises(AppError):
                daily_discovery(options, identity)

    def test_runtime_rolls_over_while_disabled_and_mqtt_failure_does_not_block_local_state(self):
        store = MagicMock(root=self.root)
        store.reference_root.return_value = None
        publisher = Mock()
        publisher.publish_daily.side_effect = AppError('synthetic failure')
        runtime = Runtime(validate({}), store, publisher)
        runtime.nest = self.nest
        self.day = date(2026, 11, 3)
        runtime.update_daily()
        runtime.publish_daily()
        self.assertEqual(self.nest.daily_snapshot()['date'], '2026-11-03')
        (self.root / 'results').mkdir()
        runtime.observe('synthetic', {'label': 'no_chicken', 'egg_count': 0,
                                      'distance': None, 'margin': None})
        publisher.publish.assert_called_once_with(self.nest.snapshot(), identities=[])


if __name__ == '__main__':
    unittest.main()
