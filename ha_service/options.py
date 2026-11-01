"""Explicit Supervisor options adapter. Never reads .env."""
import json
import math
from pathlib import Path
import re

from camera_profile import camera_bounds


class AppError(Exception):
    """Only fixed, credential-free messages belong here."""

    def __init__(self, message, code='invalid'):
        super().__init__(message)
        self.code = code


def read_options(path=Path('/data/options.json')):
    try:
        with path.open() as handle:
            return validate(json.load(handle))
    except AppError:
        raise
    except Exception:
        raise AppError('Cannot read app options; check the HA Configuration tab.') from None


def validate(raw):
    # Defaults live in the single HA metadata file, not a developer env file.
    with (Path(__file__).resolve().parent.parent / 'config.json').open() as handle:
        defaults = json.load(handle)['options']
    if not isinstance(raw, dict) or set(raw) - set(defaults):
        raise AppError('Unknown or invalid app options; check the HA Configuration tab.')
    options = dict(defaults, **raw)
    for key in ('capture_enabled', 'mqtt_enabled', 'mqtt_tls', 'transfer_enabled', 'store_captures'):
        if type(options[key]) is not bool:
            raise AppError('Enable/disable options must be booleans.')
    for key in ('camera_url', 'mqtt_host', 'mqtt_user', 'mqtt_password', 'device_name'):
        if not isinstance(options[key], str) or len(options[key]) > 2048 or '\x00' in options[key]:
            raise AppError('Invalid text option; check camera and MQTT configuration.')
    ranges = {'capture_interval': (30, 86400), 'capture_timeout': (1, 60),
              'expire_after': (60, 172800), 'mqtt_port': (1, 65535),
              'retention_days': (1, 365), 'capture_storage_mb': (16, 4096),
              'minimum_free_mb': (64, 4096), 'chicken_area': (100, 100000),
              'texture_threshold': (1, 100)}
    for key, (low, high) in ranges.items():
        if type(options[key]) is not int or not low <= options[key] <= high:
            raise AppError('Invalid numeric option: ' + key + '.')
    for key, low in (('max_distance', 0.01), ('min_margin', 0)):
        value = options[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= 10:
            raise AppError('Invalid recognition threshold: ' + key + '.')
    if options['expire_after'] <= options['capture_interval'] + options['capture_timeout'] + 10:
        raise AppError('expire_after must exceed capture_interval + capture_timeout + 10 seconds.')
    if options['capture_enabled'] and not options['camera_url'].lower().startswith(('rtsp://', 'rtsps://')):
        raise AppError('Set camera_url to an RTSP URL before enabling capture.')
    if options['mqtt_enabled'] and not options['mqtt_host'].strip():
        raise AppError('Set mqtt_host before enabling MQTT.')
    if options['mqtt_password'] and not options['mqtt_user']:
        raise AppError('Set mqtt_user when providing an MQTT password.')
    if not isinstance(options['device_id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', options['device_id']):
        raise AppError('device_id must contain 1-48 letters, digits, underscores or hyphens.')
    if options['log_level'] not in ('info', 'debug'):
        raise AppError('log_level must be info or debug.')
    tokens = options['transfer_tokens']
    if not isinstance(tokens, list) or len(tokens) > 16:
        raise AppError('Provide at most 16 transfer tokens.')
    for token in tokens:
        if (not isinstance(token, dict) or set(token) != {'name', 'sha256', 'scope'}
                or not isinstance(token['name'], str) or not 1 <= len(token['name']) <= 80
                or not isinstance(token['sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', token['sha256'])
                or token['scope'] not in ('read', 'write')):
            raise AppError('Each transfer token needs a name, lowercase SHA256 digest and read/write scope.')
    try:
        rectangles = options['excluded_rectangles']
        if not isinstance(rectangles, list) or len(rectangles) > 32:
            raise ValueError()
        profile = {'horizontal_bounds': options['horizontal_bounds'],
                   'excluded_rectangles': [[r[k] for k in ('x1', 'y1', 'x2', 'y2')] for r in rectangles]}
        camera_bounds(profile)
        options['camera_profile'] = profile
    except Exception:
        raise AppError('Invalid camera mask; use two horizontal bounds and valid exclusion rectangles.') from None
    return options
