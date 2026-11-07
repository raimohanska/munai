"""Explicit Supervisor options adapter. Never reads .env."""
import json
import math
from pathlib import Path
import re

OPTIONAL_DEFAULTS = {'transfer_token_sha256': '', 'transfer_tokens': []}
# Ignore retired settings on upgrade; never read a local camera profile.
RETIRED_MASK_OPTIONS = frozenset(('horizontal_bounds', 'horizontal_left', 'horizontal_right',
                                  'excluded_rectangles'))
RETIRED_OPTIONS = RETIRED_MASK_OPTIONS | frozenset(('capture_pause_start', 'capture_pause_end'))


class AppError(Exception):
    """Only fixed, credential-free messages belong here."""

    def __init__(self, message, code='invalid', diagnostic=None):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic


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
    if not isinstance(raw, dict) or set(raw) - (set(defaults) | set(OPTIONAL_DEFAULTS) | RETIRED_OPTIONS):
        raise AppError('Unknown or invalid app options; check the HA Configuration tab.')
    options = dict(defaults, **OPTIONAL_DEFAULTS)
    options.update({key: value for key, value in raw.items() if key not in RETIRED_OPTIONS})
    for key in ('capture_enabled', 'mqtt_enabled', 'mqtt_tls', 'transfer_enabled', 'store_captures', 'capture_deduplicate'):
        if type(options[key]) is not bool:
            raise AppError('Enable/disable options must be booleans.')
    for key in ('camera_url', 'mqtt_host', 'mqtt_user', 'mqtt_password', 'device_name'):
        if not isinstance(options[key], str) or len(options[key]) > 2048 or '\x00' in options[key]:
            raise AppError('Invalid text option; check camera and MQTT configuration.')
    ranges = {'capture_interval': (30, 86400), 'capture_timeout': (1, 60),
              'capture_heartbeat_seconds': (60, 86400),
              'expire_after': (60, 172800), 'mqtt_port': (1, 65535),
              'retention_days': (1, 365), 'capture_storage_mb': (16, 4096),
               'journal_retention_days': (1, 3650), 'training_storage_mb': (16, 4096),
               'minimum_free_mb': (64, 4096), 'chicken_area': (100, 100000),
               'egg_min_area': (100, 10000), 'egg_max_area': (100, 10000),
               'texture_threshold': (1, 100)}
    for key, (low, high) in ranges.items():
        if type(options[key]) is not int or not low <= options[key] <= high:
            raise AppError('Invalid numeric option: ' + key + '.')
    if options['egg_min_area'] > options['egg_max_area']:
        raise AppError('egg_min_area must not exceed egg_max_area.')
    if options['box_split'] not in ('none', 'vertical', 'horizontal'):
        raise AppError('box_split must be none, vertical or horizontal.')
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
    records = []
    for token in tokens:
        # Old options.json object entries remain readable; the HA form now uses
        # optional text rows because empty object lists are rendered optional by
        # HA but nevertheless required by Supervisor's list-of-dict validator.
        if isinstance(token, str):
            parts = token.split('|')
            if len(parts) != 3:
                raise AppError('Additional transfer clients use name|read-or-write|SHA256, one client per row.')
            token = dict(zip(('name', 'scope', 'sha256'), (part.strip() for part in parts)))
        records.append(token)
    primary = options['transfer_token_sha256']
    if not isinstance(options['transfer_token_name'], str) or not 1 <= len(options['transfer_token_name']) <= 80:
        raise AppError('Transfer client name must contain 1-80 characters.')
    if not isinstance(primary, str):
        raise AppError('Transfer token digest must be text.')
    if options['transfer_token_scope'] not in ('read', 'write'):
        raise AppError('Transfer token scope must be read or write.')
    if primary:
        records.append({'name': options['transfer_token_name'], 'scope': options['transfer_token_scope'],
                        'sha256': primary})
    if len(records) > 16:
        raise AppError('Provide at most 16 transfer tokens.')
    for token in records:
        if (not isinstance(token, dict) or set(token) != {'name', 'sha256', 'scope'}
                or not isinstance(token['name'], str) or not 1 <= len(token['name']) <= 80
                or not isinstance(token['sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', token['sha256'])
                or token['scope'] not in ('read', 'write')):
            raise AppError('Each transfer token needs a name, lowercase SHA256 digest and read/write scope.')
    options['transfer_tokens'] = records
    return options
