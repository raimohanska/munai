"""Synthetic MQTT discovery/payload checks; no broker or credentials."""
import json
import unittest
from unittest.mock import Mock

from ha_service.mqtt import MQTT, presence
from ha_service.options import validate


class SplitMQTTTests(unittest.TestCase):
    def test_presence_uses_exact_identity_keys_and_binary_values(self):
        identities = ['hen_a', 'hen_b', 'empty', 'unknown', 'pending']
        for occupant, expected in (('Hen_a', 1), ('empty', 0), ('unknown', 0), (None, 0)):
            self.assertEqual(presence({'occupant': occupant}, identities),
                             {'hen_a': expected, 'hen_b': 0})

    def test_presence_includes_all_regions_without_parsing_summary(self):
        for sides in (('left', 'right'), ('top', 'bottom')):
            state = {'occupant': 'not an identity', 'zones': {
                sides[0]: {'occupant': 'Hen_a'}, sides[1]: {'occupant': 'unknown'},
                'center': {'occupant': 'Hen_b'}}}
            self.assertEqual(presence(state, ['hen_a', 'hen_b', 'hen_c']),
                             {'hen_a': 1, 'hen_b': 1, 'hen_c': 0})
            self.assertEqual(presence(state, ['hen_b']), {'hen_b': 1})

    def test_presence_topic_is_non_retained_and_has_no_discovery(self):
        publisher = MQTT(validate({}))
        publisher.send = Mock()
        publisher.publish({'occupant': 'Hen_a', 'egg_count': None, 'latest_event': None},
                          identities=['hen_a', 'hen_b'])
        calls = [call for call in publisher.send.call_args_list
                 if call.args[0] == publisher.base + '/presence']
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0].args[1]), {'hen_a': 1, 'hen_b': 0})
        self.assertFalse(calls[0].args[2])
        client = Mock()
        publisher.announce(client)
        self.assertFalse(any('/presence/' in call.args[0]
                             for call in client.publish.call_args_list))

    def test_event_only_publication_does_not_replay_live_state(self):
        publisher = MQTT(validate({}))
        publisher.send = Mock()
        for text, expected in (('Hen_a departed', 'Hen_a departed'), (None, 'No events yet'),
                               ('Center: Hen_a arrived', 'Hen_a arrived')):
            publisher.publish_event(text)
            publisher.send.assert_called_with(publisher.base + '/latest_event', expected, True)
        self.assertEqual(publisher.send.call_count, 3)

    def test_zone_discovery_and_payload(self):
        publisher = MQTT(validate({'box_split': 'vertical'}))
        self.assertEqual(set(publisher.zone_configs), {'left_occupant', 'right_occupant',
                         'center_occupant', 'left_egg_count', 'right_egg_count'})
        client = Mock()
        publisher.announce(client)
        topics = [c.args[0] for c in client.publish.call_args_list]
        self.assertTrue(any('/left_occupant/config' in topic for topic in topics))
        publisher.send = Mock()
        state = {'occupant': 'Left: Hen_a', 'egg_count': None, 'latest_event': 'Left: Hen_a arrived',
                 'layout': 'vertical', 'zones': {'left': {'occupant': 'Hen_a', 'egg_count': 0}}}
        publisher.publish(state)
        payload = json.loads(publisher.send.call_args_list[1].args[1])
        self.assertEqual(payload['zones'], state['zones'])
        self.assertEqual(payload['layout'], 'vertical')
        self.assertFalse(publisher.send.call_args_list[1].args[2])
