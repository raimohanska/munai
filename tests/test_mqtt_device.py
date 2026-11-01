"""MQTT configuration/payload tests only: no .env, camera, or network access."""
import json
import unittest

from chicken_device import DeviceError, Publisher, discovery, mqtt_configuration, state_payload


class MQTTTests(unittest.TestCase):
    def test_minimal_settings(self):
        settings = mqtt_configuration({'MQTT_HOST': 'broker.invalid'})
        self.assertEqual(settings['port'], 1883)
        self.assertFalse(settings['tls'])

    def test_tls_settings(self):
        settings = mqtt_configuration({'MQTT_HOST': 'broker.invalid', 'MQTT_TLS': 'true'})
        self.assertEqual(settings['port'], 8883)
        self.assertTrue(settings['tls'])

    def test_explicit_port_and_credentials(self):
        settings = mqtt_configuration({'MQTT_HOST': 'broker.invalid', 'MQTT_PORT': '1884',
                                       'MQTT_USER': 'placeholder', 'MQTT_PASSWORD': 'placeholder'})
        self.assertEqual(settings['port'], 1884)
        self.assertEqual(settings['user'], 'placeholder')

    def test_invalid_settings_are_safe(self):
        for extra in ({'MQTT_PORT': 'secret-invalid'}, {'MQTT_PORT': '0'},
                      {'MQTT_PORT': '65536'}, {'MQTT_TLS': 'secret-invalid'},
                      {'MQTT_PASSWORD': 'secret-invalid'}):
            with self.assertRaises(DeviceError) as caught:
                mqtt_configuration(dict({'MQTT_HOST': 'broker.invalid'}, **extra))
            self.assertNotIn('secret-invalid', str(caught.exception))

    def test_missing_host(self):
        with self.assertRaises(DeviceError):
            mqtt_configuration({})

    def test_chicken_names(self):
        for label, expected in (('hen_a', 'Hen_a'), ('hen_b', 'Hen_b'), ('hen_c', 'Hen_c')):
            self.assertEqual(json.loads(state_payload({'label': label})), {'chicken': expected})

    def test_empty_nest_is_nonempty_json_with_empty_string(self):
        payload = state_payload({'label': 'no_chicken'})
        self.assertTrue(payload)
        self.assertEqual(json.loads(payload), {'chicken': ''})

    def test_uncertain_is_not_empty_nest(self):
        self.assertEqual(json.loads(state_payload({'label': 'unknown'}))['chicken'], 'Unknown chicken')

    def test_discovery_has_stable_identity_and_expiry(self):
        config = discovery('test_nest', 180)
        self.assertEqual(config['unique_id'], 'test_nest_chicken')
        self.assertEqual(config['device']['identifiers'], ['test_nest'])
        self.assertEqual(config['expire_after'], 180)
        self.assertEqual(config['value_template'], '{{ value_json.chicken }}')
        self.assertNotIn('device_class', config)  # Must remain a text sensor.

    def test_discovery_retained_but_state_not_retained(self):
        # Exercise only payload publication with a fake sender. No client/socket.
        publisher = Publisher.__new__(Publisher)
        publisher.device_id = 'test_nest'
        publisher.config = discovery('test_nest', 180)
        sent = []
        publisher.send = lambda *args: sent.append(args)
        publisher.publish({'label': 'no_chicken'})
        self.assertEqual(sent[0][0], 'homeassistant/sensor/test_nest/chicken/config')
        self.assertTrue(sent[0][2])
        self.assertEqual(sent[1][0], 'munai/test_nest/state')
        self.assertFalse(sent[1][2])
        self.assertEqual(json.loads(sent[1][1])['chicken'], '')


if __name__ == '__main__':
    unittest.main()
