#!/usr/bin/env python3
"""Capture, recognize, and publish one Home Assistant MQTT update; run via cron."""
from __future__ import print_function

import argparse
import errno
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

from capture import CaptureError, SCRIPT_DIR, capture, configuration, load_env
from recognize_chickens import identify, load_references, measure
from sort_captures import classify
from data_layout import prediction_category


class DeviceError(Exception):
    """Only fixed, credential-free messages may be exposed to the user."""


def mqtt_configuration(values):
    host = values.get('MQTT_HOST', '').strip()
    if not host:
        raise DeviceError('MQTT_HOST is required.')
    tls = values.get('MQTT_TLS', 'false').strip().lower()
    if tls not in ('true', 'false'):
        raise DeviceError('MQTT_TLS must be true or false.')
    try:
        port = int(values.get('MQTT_PORT', '8883' if tls == 'true' else '1883'))
    except ValueError:
        raise DeviceError('MQTT_PORT must be an integer from 1 to 65535.')
    if not 1 <= port <= 65535:
        raise DeviceError('MQTT_PORT must be an integer from 1 to 65535.')
    user = values.get('MQTT_USER', '')
    password = values.get('MQTT_PASSWORD', '')
    if password and not user:
        raise DeviceError('MQTT_USER is required when MQTT_PASSWORD is set.')
    return {'host': host, 'port': port, 'user': user, 'password': password,
            'tls': tls == 'true'}


def discovery(device_id, expire_after):
    base = 'munai/' + device_id
    return {
        'name': 'Chicken',
        'unique_id': device_id + '_chicken',
        'state_topic': base + '/state',
        'value_template': '{{ value_json.chicken }}',
        'expire_after': expire_after,
        'qos': 1,
        'icon': 'mdi:bird',
        'device': {'identifiers': [device_id], 'name': 'Munai',
                   'manufacturer': 'Munai', 'model': 'Nest recognizer'},
    }


def state_payload(prediction):
    label = prediction['label']
    # Nonempty JSON preserves the empty-string state without sending a zero-byte
    # MQTT payload. An unidentified hen is explicitly distinct from an empty nest.
    chicken = '' if label == 'no_chicken' else 'Unknown chicken' if label == 'unknown' else label.capitalize()
    return json.dumps({'chicken': chicken})


def storage_category(prediction, background_category=None):
    """Automatic identities stay below unreviewed/, never in reference inputs."""
    label = prediction['label']
    if label == 'no_chicken':
        if background_category not in ('empty', 'eggs'):
            raise DeviceError('Missing empty/egg storage category.')
        return (background_category,)
    try:
        return prediction_category(label)
    except ValueError:
        raise DeviceError('Invalid chicken reference folder name.')


def categorize(path, references, detect_eggs=True):
    prediction = identify(measure(path), references)
    background = None
    if prediction['label'] == 'no_chicken' and detect_eggs:
        background, unused_area, unused_eggs = classify(path)
        if background == 'chicken':
            # Conservative fallback if the two foreground masks disagree:
            # never call a potential chicken an egg or an empty nest.
            prediction['label'] = 'unknown'
    category = storage_category(prediction, background) if detect_eggs else None
    return prediction, category


class Publisher(object):
    def __init__(self, settings, device_id, expire_after, timeout=10):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise DeviceError('Install paho-mqtt==1.5.1 before running this program.')
        self.mqtt = mqtt
        self.settings = settings
        self.device_id = device_id
        self.config = discovery(device_id, expire_after)
        self.timeout = timeout
        self.connected = threading.Event()
        self.rejected = threading.Event()
        self.client = mqtt.Client(client_id=device_id, clean_session=True,
                                  protocol=mqtt.MQTTv311)
        if settings['user']:
            self.client.username_pw_set(settings['user'], settings['password'])
        if settings['tls']:
            # System CA trust and hostname verification; no insecure TLS mode.
            self.client.tls_set()
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected.set()
        else:
            self.rejected.set()

    def on_disconnect(self, client, userdata, rc):
        self.connected.clear()

    def start(self):
        self.client.connect_async(self.settings['host'], self.settings['port'], keepalive=30)
        self.client.loop_start()
        deadline = time.monotonic() + self.timeout
        while not self.connected.wait(0.05):
            if self.rejected.is_set():
                raise DeviceError('MQTT connection rejected; check authentication and broker permissions.')
            if time.monotonic() >= deadline:
                raise DeviceError('MQTT connection timed out; check broker settings, TLS, and connectivity.')

    def send(self, topic, payload, retain):
        info = self.client.publish(topic, payload, qos=1, retain=retain)
        if info.rc != self.mqtt.MQTT_ERR_SUCCESS:
            raise DeviceError('MQTT publication failed.')
        deadline = time.monotonic() + self.timeout
        while not info.is_published():
            if time.monotonic() >= deadline:
                raise DeviceError('MQTT publication acknowledgement timed out.')
            time.sleep(0.02)

    def publish(self, prediction):
        self.send('homeassistant/sensor/' + self.device_id + '/chicken/config',
                  json.dumps(self.config), True)
        # Do not retain expiring states: HA must not replay a stale name as fresh.
        self.send(self.config['state_topic'], state_payload(prediction), False)

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


def run(args):
    # This is the only configuration read. Never print its values or exceptions
    # originating from external libraries: they can contain configured secrets.
    env_path = os.path.abspath(args.env)
    values = load_env(env_path)
    settings = mqtt_configuration(values)
    references = load_references(Path(args.references).resolve())
    store = 'CAPTURE_DIR' in values
    if store and not values['CAPTURE_DIR'].strip():
        raise DeviceError('CAPTURE_DIR must be nonempty, or omitted to disable storage.')
    # A stable lock also prevents overlap when each round has its own temp dir.
    with open(os.path.join(SCRIPT_DIR, '.chicken-device.lock'), 'a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                print('Device skipped: another round is running.')
                return
            raise
        with tempfile.TemporaryDirectory(prefix='munai-') as scratch:
            configured = dict(values)
            if not store:
                configured['CAPTURE_DIR'] = scratch
            capture_settings = configuration(env_path, values=configured)
            predictions = []

            def process(path):
                prediction, category = categorize(path, references, detect_eggs=store)
                predictions.append(prediction)
                return category

            # Save/filter the capture before connecting to MQTT, so a broker
            # outage does not discard an otherwise successful stored image.
            capture(*capture_settings, on_capture=process, store=store)
            if not predictions:
                return  # Existing capture lock was held by another invocation.
            publisher = Publisher(settings, args.device_id, args.expire_after,
                                  timeout=args.mqtt_timeout)
            try:
                publisher.start()
                publisher.publish(predictions[0])
                print('MQTT chicken state updated.')
            finally:
                publisher.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default=os.path.join(SCRIPT_DIR, '.env'))
    parser.add_argument('--references', default=os.path.join(SCRIPT_DIR, 'references'))
    parser.add_argument('--device-id', default='munai_nest', help='Stable unique MQTT device ID')
    parser.add_argument('--expire-after', type=int, default=180, help='HA stale-state timeout in seconds')
    parser.add_argument('--mqtt-timeout', type=float, default=10, help='Connection/publication timeout in seconds')
    args = parser.parse_args()
    if not re.match(r'^[a-zA-Z0-9_-]{1,48}$', args.device_id):
        parser.error('Device ID must be 1-48 letters, digits, underscores, or hyphens')
    if args.expire_after <= 0 or not math.isfinite(args.mqtt_timeout) or args.mqtt_timeout <= 0:
        parser.error('Timeouts must be positive and finite')
    try:
        import cv2
        cv2.setNumThreads(1)
        run(args)
    except (CaptureError, DeviceError) as error:
        print('Device error: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        # OpenCV, MQTT/TLS, OS, and decode exceptions may contain sensitive paths
        # or configuration. Never include their raw text or a traceback.
        print('Device failed; check dependencies, references, filesystem permissions, and MQTT settings.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
