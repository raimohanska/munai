"""Shared data-layout rules, compatible with Python 3.5 and native CLI use."""
import json
from pathlib import Path
import re

STAMP = r'\d{8}T\d{6}_\d{6}Z'
# Combined capture/result ceiling: a minute-by-minute day uses 2,880 files.
# Transfer listings use the same bound so retained files remain exportable.
MAX_HISTORY_FILES = 10000
RESERVED = frozenset(('unknown', 'unreviewed', 'empty', 'eggs', 'pending',
                      'no_chicken', 'unavailable', 'versions', 'staging', 'state'))
LABEL = re.compile(r'^[a-z0-9][a-z0-9_-]{0,47}$')


def prediction_category(label):
    if label == 'no_chicken':
        return ('empty',)
    if label == 'unknown':
        return ('unknown',)
    if not isinstance(label, str) or not LABEL.fullmatch(label) or label in RESERVED:
        raise ValueError('Invalid prediction identity')
    return (label, 'unreviewed')


def reference_directory(directory):
    """Resolve a managed reference root, or accept an explicit flat import folder.

    Never follow symlinks or silently fall back from a corrupt managed state.
    There is no dependency on .env, OpenCV, Pillow, or HA options.
    """
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Reference root must not be a symlink')
    state = directory / 'state.json'
    versions = directory / 'versions'
    if state.is_symlink() or versions.is_symlink():
        raise ValueError('Reference metadata must not be a symlink')
    if not state.exists():
        if versions.exists():
            raise ValueError('Managed references have no active version')
        return directory
    with state.open() as handle:
        data = json.load(handle)
    active = data.get('active') if isinstance(data, dict) else None
    if not isinstance(active, str) or not re.fullmatch(r'[a-f0-9]{32}', active):
        raise ValueError('Invalid active reference version')
    result = versions / active
    if result.is_symlink() or not result.is_dir():
        raise ValueError('Active reference version is missing or unsafe')
    return result
