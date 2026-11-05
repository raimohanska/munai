"""Private training archive transfer and checksum-guarded acknowledgement."""
from datetime import datetime, timezone
import json
import os
import re
import tempfile

from . import VERSION
from .options import AppError
from .storage import Conflict, MAX_FILE_BYTES, atomic_json, digest, image_valid, safe_path, sync_directory

MAX_ANNOTATIONS = 10000
TRAINING_PATH = re.compile(r'^(?:images/[a-f0-9]{64}\.jpg|annotations/[a-f0-9]{64}\.json)$')


def without_center_eggs(interpretation, region='whole'):
    """Center is presence-only, not an egg-counting region."""
    result = dict(interpretation)
    if region == 'center':
        result.pop('egg_count', None)
    zones = result.get('zones')
    if zones and 'center' in zones:
        result['zones'] = dict(zones)
        result['zones']['center'] = dict(zones['center'])
        result['zones']['center'].pop('egg_count', None)
    return result


class TrainingArchive:
    def __init__(self, store):
        self.store = store
        self.root = safe_path(store.root, 'training')
        for name in ('images', 'annotations'):
            safe_path(self.root, name).mkdir(parents=True, exist_ok=True, mode=0o700)

    def read(self, relative):
        if not TRAINING_PATH.fullmatch(relative):
            raise AppError('Invalid training path.')
        path = safe_path(self.root, relative)
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise AppError('Invalid training file.')
        return path.read_bytes()

    def save(self, snapshot, data, key):
        """Retain a human-confirmed interpretation and its original image."""
        annotation = safe_path(self.root / 'annotations', key + '.json')
        if annotation.exists():
            return 'annotations/' + annotation.name
        image_valid(data)
        if len(list((self.root / 'annotations').glob('*.json'))) >= MAX_ANNOTATIONS:
            raise AppError('Training archive limit reached. Back up and manage training storage.')
        checksum = digest(data)
        image = safe_path(self.root / 'images', checksum + '.jpg')
        record = dict(snapshot)
        record['interpretation'] = without_center_eggs(record['interpretation'], record['region'])
        record.update(schema=1, human_confirmed=True, image='images/' + checksum + '.jpg',
                      sha256=checksum, app_version=VERSION,
                      confirmed_at=datetime.now(timezone.utc).isoformat())
        needed = len(json.dumps(record, allow_nan=False).encode('utf-8')) + (0 if image.exists() else len(data))
        self.require_capacity(needed)
        self.store.require_space(needed)
        if not image.exists():
            descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=str(image.parent))
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, str(image))
                sync_directory(image.parent)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        atomic_json(annotation, record)
        return 'annotations/' + annotation.name

    def require_capacity(self, additional):
        usage = 0
        for folder in ('images', 'annotations'):
            for path in safe_path(self.root, folder).iterdir():
                checked = safe_path(self.root, folder + '/' + path.name) if not path.name.startswith('.pending-') else path
                if checked.is_symlink() or not checked.is_file():
                    raise AppError('Invalid training storage entry.')
                usage += checked.stat().st_size
        if usage + additional > self.store.options['training_storage_mb'] * 1024 * 1024:
            raise AppError('Training storage cap reached. Pull training data and remove transferred examples.', 'training_limit')

    def files(self):
        items = []
        for folder in ('images', 'annotations'):
            for path in sorted(safe_path(self.root, folder).iterdir()):
                if re.fullmatch(r'\.pending-[a-z0-9_]{8}', path.name) and not path.is_symlink():
                    continue
                relative = folder + '/' + path.name
                data = self.read(relative)
                if len(items) >= MAX_ANNOTATIONS * 2:
                    raise AppError('Training archive transfer limit exceeded.')
                items.append(dict(path=relative, bytes=len(data), sha256=digest(data)))
        return items

    def prune(self, files):
        """Remove only acknowledged hashes; preserve images used by new annotations."""
        if not isinstance(files, list) or len(files) > MAX_ANNOTATIONS * 2:
            raise AppError('Invalid training acknowledgement.')
        with self.store.lock:
            requested = {}
            for item in files:
                if (not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256'}
                        or not isinstance(item['path'], str) or not TRAINING_PATH.fullmatch(item['path'])
                        or not isinstance(item['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256'])
                        or type(item['bytes']) is not int or not 0 < item['bytes'] <= MAX_FILE_BYTES
                        or item['path'] in requested):
                    raise AppError('Invalid training acknowledgement.')
                requested[item['path']] = item
            # Validate the complete request before deleting anything. Missing files
            # are allowed so a lost response can be retried after partial removal.
            present = set()
            for relative, item in requested.items():
                if not safe_path(self.root, relative).exists():
                    continue
                data = self.read(relative)
                if len(data) != item['bytes'] or digest(data) != item['sha256']:
                    raise Conflict('Training data changed; download and verify again.')
                present.add(relative)
                if relative.startswith('annotations/'):
                    record = json.loads(data.decode('utf-8'))
                    image = requested.get(record['image'])
                    if not image or image['sha256'] != record['sha256']:
                        raise AppError('Acknowledge each annotation with its verified image.')
            used = set()
            for path in safe_path(self.root, 'annotations').glob('*.json'):
                relative = 'annotations/' + path.name
                if relative not in present:
                    record = json.loads(self.read(relative).decode('utf-8'))
                    used.add(record['image'])
            removed = 0
            for relative in present:
                if relative.startswith('annotations/'):
                    safe_path(self.root, relative).unlink()
                    removed += 1
            sync_directory(self.root / 'annotations')
            for relative in present:
                if relative.startswith('images/') and relative not in used:
                    safe_path(self.root, relative).unlink()
                    removed += 1
            sync_directory(self.root / 'images')
            return {'removed': removed}
