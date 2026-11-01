"""Persistent MQTT discovery, reconnects, last will, and expiring observations."""
import json
import threading

from .options import AppError


def discovery(options):
    device_id = options['device_id']
    base = 'munai/' + device_id
    return {
        'name': 'Occupant', 'unique_id': device_id + '_occupant',
        'state_topic': base + '/state', 'value_template': '{{ value_json.occupant }}',
        'availability_topic': base + '/availability', 'expire_after': options['expire_after'],
        'qos': 1, 'icon': 'mdi:bird',
        'device': {'identifiers': [device_id], 'name': options['device_name'],
                   'manufacturer': 'Munai', 'model': 'Reference-based nest recognizer'},
    }


def occupant(prediction):
    label = prediction['label']
    return 'empty' if label == 'no_chicken' else 'unknown' if label == 'unknown' else label.capitalize()


class MQTT:
    def __init__(self, options):
        import paho.mqtt.client as mqtt
        self.mqtt = mqtt
        self.config = discovery(options)
        self.base = 'munai/' + options['device_id']
        self.discovery_topic = 'homeassistant/sensor/' + options['device_id'] + '/occupant/config'
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=options['device_id'])
        if options['mqtt_user']:
            self.client.username_pw_set(options['mqtt_user'], options['mqtt_password'])
        if options['mqtt_tls']:
            self.client.tls_set()
        self.client.will_set(self.base + '/availability', 'offline', qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.options = options

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected.set()
            client.publish(self.discovery_topic, json.dumps(self.config), qos=1, retain=True)
            client.publish(self.base + '/availability', 'offline', qos=1, retain=True)
            client.subscribe('homeassistant/status', qos=1)

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()

    def on_message(self, client, userdata, message):
        if message.topic == 'homeassistant/status' and message.payload == b'online':
            client.publish(self.discovery_topic, json.dumps(self.config), qos=1, retain=True)
            # Never replay an old observation as if it were freshly captured.

    def start(self):
        self.client.connect_async(self.options['mqtt_host'], self.options['mqtt_port'], keepalive=30)
        self.client.loop_start()

    def send(self, topic, payload, retain):
        if not self.connected.is_set():
            raise AppError('MQTT is disconnected; check broker settings, credentials and TLS.')
        info = self.client.publish(topic, payload, qos=1, retain=retain)
        try:
            info.wait_for_publish(timeout=5)
            if info.rc != self.mqtt.MQTT_ERR_SUCCESS or not info.is_published():
                raise ValueError()
        except Exception:
            raise AppError('MQTT publish failed or timed out; check broker connectivity and permissions.') from None

    def publish(self, prediction):
        # Expiring state MUST NOT be retained. Availability alone is insufficient
        # to present an occupant after a restart or an extended capture failure.
        self.send(self.base + '/state', json.dumps({'occupant': occupant(prediction)}), False)
        self.send(self.base + '/availability', 'online', True)

    def offline(self):
        if self.connected.is_set():
            self.send(self.base + '/availability', 'offline', True)

    def close(self):
        try:
            self.offline()
        finally:
            self.client.disconnect()
            self.client.loop_stop()
