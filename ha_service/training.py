"""Private training archive transfer and checksum-guarded acknowledgement."""
import json
import re

from .options import AppError
from .storage import Conflict, MAX_FILE_BYTES, digest, safe_path, sync_directory

MAX_ANNOTATIONS = 10000
TRAINING_PATH = re.compile(r'^(?:images/[a-f0-9]{64}\.jpg|annotations/[a-f0-9]{64}\.json)$')


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
