"""Bounded private storage and immutable, atomically activated reference sets."""
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid
import warnings

from PIL import Image

from data_layout import LABEL, RESERVED, STAMP, MAX_HISTORY_FILES

from .options import AppError

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_BYTES = 128 * 1024 * 1024
MAX_REFERENCES = 500
MAX_PIXELS = 16_000_000
VERSION_ID = re.compile(r'^[a-f0-9]{32}$')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def image_valid(data):
    if not data or len(data) > MAX_FILE_BYTES:
        raise AppError('JPEG must be nonempty and at most 8 MiB.', 'invalid_image')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != 'JPEG' or image.width * image.height > MAX_PIXELS:
                    raise ValueError()
                if image.width < 64 or image.height < 64 or max(image.size) > 8192:
                    raise ValueError()
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except Exception:
        raise AppError('Invalid JPEG; maximum dimensions are 8192 per side and 16 million pixels.', 'invalid_image') from None


def safe_path(root, relative):
    parts = relative.split('/')
    if (not relative or len(relative) > 240 or len(parts) > 5
            or any(not re.fullmatch(r'[a-zA-Z0-9_-]+(?:\.(?:jpg|json))?', part) for part in parts)):
        raise AppError('Invalid data path.')
    current = root
    if root.is_symlink():
        raise AppError('Symlinks are not allowed in data storage.')
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise AppError('Symlinks are not allowed in data storage.')
    return current


def atomic_json(path, value):
    descriptor, name = tempfile.mkstemp(prefix='.pending-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, str(path))
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sync_directory(path):
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Store:
    def __init__(self, root, options):
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise AppError('Data directory must not be a symlink.')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.options = options
        self.lock = threading.RLock()
        for name in ('references', 'references/versions', 'references/staging', 'captures', 'results', 'scratch', 'tls'):
            safe_path(self.root, name).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.root / 'references' / 'state.json'
        self.state = {'active': None, 'previous': None}
        if self.state_path.is_symlink():
            raise AppError('Reference state must not be a symlink.')
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text())
                if set(self.state) != {'active', 'previous'}:
                    raise ValueError()
                for version in self.state.values():
                    if version is not None and (not isinstance(version, str) or not VERSION_ID.fullmatch(version)
                                                or not safe_path(self.root / 'references' / 'versions', version).is_dir()):
                        raise ValueError()
            except Exception:
                raise AppError('Reference state is invalid; restore the app backup.') from None
        self._prune_versions()
        # A power loss can leave incomplete atomic-write files. Only remove our
        # exact generated temporary-file names, never reviewed data or symlinks.
        for name in ('captures', 'results', 'references'):
            managed = self.root / name
            for item in managed.rglob('.pending-*'):
                if not re.fullmatch(r'\.pending-[a-z0-9_]{8}', item.name):
                    continue
                relative_parent = item.parent.relative_to(managed).as_posix()
                if relative_parent != '.':
                    safe_path(managed, relative_parent)
                if item.is_symlink() or not item.is_file():
                    raise AppError('Invalid temporary entry in managed storage.')
                item.unlink()
        # These roots contain only application-created, disposable scratch data.
        for name in ('references/staging', 'scratch'):
            for item in (self.root / name).iterdir():
                if item.name.startswith('job-') and item.is_dir() and not item.is_symlink():
                    shutil.rmtree(str(item))
        self.last_success = None

    @property
    def revision(self):
        return self.state['active'] or 'none'

    def require_space(self, size=0):
        reserve = self.options['minimum_free_mb'] * 1024 * 1024
        if shutil.disk_usage(str(self.root)).free < reserve + size:
            raise AppError('Storage reserve reached; free disk space or reduce capture retention.', 'low_storage')

    def reference_root(self):
        if self.state['active'] is None:
            return None
        return safe_path(self.root / 'references' / 'versions', self.state['active'])

    def files(self, kind):
        if kind not in ('references', 'captures', 'results'):
            raise AppError('Unsupported data kind.')
        root = self.reference_root() if kind == 'references' else self.root / kind
        if root is None:
            return []
        items = []
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root).as_posix()
            if self.private_metadata(path, relative):
                continue
            checked = safe_path(root, relative)
            if checked.is_dir():
                continue
            if not checked.is_file():
                raise AppError('Invalid data entry.')
            if not self.valid_file(kind, relative):
                raise AppError('Unexpected file in managed storage.')
            if len(items) >= (MAX_REFERENCES if kind == 'references' else MAX_HISTORY_FILES):
                raise AppError('Too many files in managed storage.')
            if checked.stat().st_size > MAX_FILE_BYTES:
                raise AppError('Stored file exceeds the transfer size limit.')
            data = checked.read_bytes()
            items.append({'path': relative, 'bytes': len(data), 'sha256': digest(data)})
        return items

    @staticmethod
    def private_metadata(path, relative):
        if path.is_symlink():
            raise AppError('Symlinks are not allowed in data storage.')
        return any(part.startswith('.') for part in relative.split('/'))

    @staticmethod
    def valid_file(kind, relative):
        parts = relative.split('/')
        if kind == 'references':
            return (len(parts) == 2 and bool(LABEL.fullmatch(parts[0]))
                    and (parts[0] not in RESERVED or parts[0] == 'empty')
                    and bool(re.fullmatch(STAMP + r'\.jpg', parts[1])))
        if kind == 'results':
            return bool(re.fullmatch(STAMP + r'\.json', relative))
        return (bool(re.fullmatch(r'(?:empty|unknown|pending|eggs)/' + STAMP + r'\.jpg', relative))
                or (len(parts) == 3 and LABEL.fullmatch(parts[0]) and parts[0] not in RESERVED
                    and parts[1] == 'unreviewed' and bool(re.fullmatch(STAMP + r'\.jpg', parts[2]))))

    def read(self, kind, relative):
        if kind not in ('references', 'captures', 'results') or not self.valid_file(kind, relative):
            raise AppError('Invalid data path.')
        root = self.reference_root() if kind == 'references' else self.root / kind
        if root is None:
            raise FileNotFoundError()
        path = safe_path(root, relative)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise AppError('Stored file exceeds the transfer size limit.')
        return path.read_bytes()

    def upload(self, relative, data, expected_hash, expected_revision, overwrite, validate_reference):
        if not self.valid_file('references', relative):
            raise AppError('Use an identity or reviewed empty folder and a timestamped JPEG.')
        if not re.fullmatch(r'[a-f0-9]{64}', expected_hash) or digest(data) != expected_hash:
            raise AppError('Upload checksum does not match.')
        image_valid(data)
        with self.lock:
            if expected_revision != self.revision:
                raise Conflict('Reference version changed; refresh status and retry.')
            manifest = self.files('references')
            existing = next((item for item in manifest if item['path'] == relative), None)
            if existing and existing['sha256'] == expected_hash:
                return self.revision
            if existing and not overwrite:
                raise Conflict('Reference already exists; explicit overwrite is required.')
            count = len(manifest) + (0 if existing else 1)
            size = sum(item['bytes'] for item in manifest) + len(data) - (existing['bytes'] if existing else 0)
            if count > MAX_REFERENCES or size > MAX_REFERENCE_BYTES:
                raise AppError('Reference limit reached (500 files / 128 MiB).', 'reference_limit')
            self.require_space(size + len(data))
            with tempfile.TemporaryDirectory(prefix='job-', dir=str(self.root / 'references' / 'staging')) as staging:
                stage = Path(staging)
                for item in manifest:
                    if item['path'] == relative:
                        continue
                    source = safe_path(self.reference_root(), item['path'])
                    target = safe_path(stage, item['path'])
                    target.parent.mkdir(exist_ok=True)
                    os.link(str(source), str(target))
                target = safe_path(stage, relative)
                target.parent.mkdir(exist_ok=True)
                with target.open('xb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                validate_reference(target)
                for directory in [p for p in stage.iterdir() if p.is_dir()] + [stage]:
                    sync_directory(directory)
                version = uuid.uuid4().hex
                destination = self.root / 'references' / 'versions' / version
                os.replace(str(stage), str(destination))
                sync_directory(destination.parent)
                updated = {'active': version, 'previous': self.state['active']}
                atomic_json(self.state_path, updated)
                self.state = updated
            self._prune_versions()
            return self.revision

    def rollback(self, expected_revision):
        with self.lock:
            if expected_revision != self.revision:
                raise Conflict('Reference version changed; refresh status and retry.')
            if self.state['previous'] is None:
                raise Conflict('No previous reference version is available.')
            updated = {'active': self.state['previous'], 'previous': self.state['active']}
            atomic_json(self.state_path, updated)
            self.state = updated
            return self.revision

    def _prune_versions(self):
        for path in (self.root / 'references' / 'versions').iterdir():
            if VERSION_ID.fullmatch(path.name) and path.name not in self.state.values():
                if path.is_symlink() or not path.is_dir():
                    raise AppError('Invalid reference version directory.')
                shutil.rmtree(str(path))

    @contextmanager
    def job(self):
        # Upload activation and capture/recognition cannot overlap.
        with self.lock:
            with tempfile.TemporaryDirectory(prefix='job-', dir=str(self.root / 'scratch')) as scratch:
                yield Path(scratch)

    def retain(self):
        with self.lock:
            now = time.time()
            maximum = self.options['capture_storage_mb'] * 1024 * 1024
            age = self.options['retention_days'] * 86400
            files = []
            for kind in ('captures', 'results'):
                root = self.root / kind
                for path in root.rglob('*'):
                    if self.private_metadata(path, path.relative_to(root).as_posix()):
                        continue
                    checked = safe_path(root, path.relative_to(root).as_posix())
                    if checked.is_file():
                        if not self.valid_file(kind, path.relative_to(root).as_posix()):
                            raise AppError('Unexpected file in managed storage.')
                        files.append((checked.stat().st_mtime, checked.stat().st_size, checked))
            total = sum(item[1] for item in files)
            removed = 0
            for modified, size, path in sorted(files):
                if now - modified <= age and total <= maximum and len(files) - removed <= MAX_HISTORY_FILES:
                    break
                path.unlink()
                total -= size
                removed += 1
            return removed


class Conflict(AppError):
    pass
