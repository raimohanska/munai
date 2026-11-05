"""Synthetic MQTT discovery/payload checks; no broker or credentials."""
import json
import unittest
from unittest.mock import Mock

from ha_service.mqtt import MQTT
from ha_service.options import validate


class SplitMQTTTests(unittest.TestCase):
    def test_event_only_publication_does_not_replay_live_state(self):
        publisher = MQTT(validate({}))
        publisher.send = Mock()
        for text, expected in (('Sleep mode', 'Sleep mode'), (None, 'No events yet'),
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
