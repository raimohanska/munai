"""Bounded private storage and a single atomically replaced reference folder."""
from contextlib import contextmanager
import ctypes
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
import stat
import warnings
import zipfile

from PIL import Image

from data_layout import LABEL, RESERVED, STAMP, REFERENCE_STAMP, MAX_HISTORY_FILES, MAX_CAPTURE_FILES

from .options import AppError

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_BYTES = 128 * 1024 * 1024
MAX_REFERENCES = 500
MAX_ARCHIVE_BYTES = MAX_REFERENCE_BYTES + 1024 * 1024
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


def exchange_directories(first, second):
    """Linux rename exchange: neither readers nor crashes see a missing folder."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise AppError('Atomic directory exchange is unavailable on this system.')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(str(first)), -100, os.fsencode(str(second)), 2) != 0:
        raise AppError('Cannot atomically replace references; original references are unchanged.')


class Store:
    def __init__(self, root, options):
        self.root = Path(root).absolute()
        if self.root.is_symlink():
            raise AppError('Data directory must not be a symlink.')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.options = options
        self.lock = threading.RLock()
        for name in ('references', 'captures', 'results', 'scratch', 'tls', 'training'):
            safe_path(self.root, name).mkdir(parents=True, exist_ok=True, mode=0o700)
        state_path = self.root / 'references' / 'state.json'
        if state_path.is_symlink():
            raise AppError('Reference state must not be a symlink.')
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if set(state) != {'active', 'previous'}:
                    raise ValueError()
                for version in state.values():
                    if version is not None and (not isinstance(version, str) or not VERSION_ID.fullmatch(version)
                                                or not safe_path(self.root / 'references' / 'versions', version).is_dir()):
                        raise ValueError()
            except Exception:
                raise AppError('Reference state is invalid; restore the app backup.') from None
            # One-time migration: preserve the active images, discard old versions.
            with tempfile.TemporaryDirectory(prefix='job-', dir=str(self.root / 'scratch')) as temporary:
                stage = Path(temporary) / 'references'
                if state['active'] is None:
                    stage.mkdir()
                else:
                    source = safe_path(self.root / 'references' / 'versions', state['active'])
                    if any(p.is_symlink() for p in source.rglob('*')):
                        raise AppError('Symlinks are not allowed in reference storage.')
                    self.require_space(sum(p.stat().st_size for p in source.rglob('*') if p.is_file()))
                    shutil.copytree(str(source), str(stage))
                self._activate(stage)
        elif (self.root / 'references' / 'versions').exists():
            raise AppError('Legacy references have no state; restore the app backup.')
        self._revision = self._content_revision()
        # A power loss can leave incomplete atomic-write files. Only remove our
        # exact generated temporary-file names, never reviewed data or symlinks.
        for name in ('captures', 'results', 'references', 'training'):
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
        for item in (self.root / 'scratch').iterdir():
            if item.name.startswith('job-') and item.is_dir() and not item.is_symlink():
                shutil.rmtree(str(item))
        self.last_success = None

    @property
    def revision(self):
        # A cache/concurrency checksum, not a stored version or rollback target.
        return self._revision

    def _content_revision(self, items=None):
        items = self.files('references') if items is None else items
        return digest(json.dumps(items, sort_keys=True).encode('utf-8'))[:32] if items else 'none'

    def require_space(self, size=0):
        reserve = self.options['minimum_free_mb'] * 1024 * 1024
        if shutil.disk_usage(str(self.root)).free < reserve + size:
            raise AppError('Storage reserve reached; free disk space or reduce capture retention.', 'low_storage')

    def reference_root(self):
        return self.root / 'references'

    def files(self, kind):
        if kind not in ('references', 'captures', 'results'):
            raise AppError('Unsupported data kind.')
        root = self.reference_root() if kind == 'references' else self.root / kind
        if root is None:
            return []
        return self._files(root, kind)

    def _files(self, root, kind):
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
                    and bool(re.fullmatch(REFERENCE_STAMP + r'\.jpg', parts[1])))
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
                raise Conflict('References changed; refresh status and retry.')
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
            with tempfile.TemporaryDirectory(prefix='job-', dir=str(self.root / 'scratch')) as staging:
                stage = Path(staging) / 'references'
                stage.mkdir()
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
                self._activate(stage)
            return self.revision

    def _activate(self, stage):
        checksum = self._content_revision(self._files(stage, 'references'))
        for path in stage.rglob('*'):
            if path.is_file():
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
        for directory in [p for p in stage.iterdir() if p.is_dir()] + [stage]:
            sync_directory(directory)
        sync_directory(stage.parent)
        exchange_directories(stage, self.reference_root())
        self._revision = checksum
        sync_directory(self.root)
        sync_directory(stage.parent)
        # The old folder is now disposable transaction scratch, not history.

    def remove_review_reference(self, relative, checksum):
        """Undo only an exact reference owned by a human review."""
        with self.lock:
            try:
                data = self.read('references', relative)
            except FileNotFoundError:
                return
            if digest(data) != checksum:
                raise Conflict('This reference changed outside review; it has not been removed.')
            with tempfile.TemporaryDirectory(prefix='job-', dir=str(self.root / 'scratch')) as temporary:
                stage = Path(temporary) / 'references'
                stage.mkdir()
                for item in self.files('references'):
                    if item['path'] == relative:
                        continue
                    target = safe_path(stage, item['path'])
                    target.parent.mkdir(exist_ok=True)
                    os.link(str(safe_path(self.reference_root(), item['path'])), str(target))
                self._activate(stage)

    def replace_references(self, archive, expected_hash, expected_revision, validate_reference):
        """Validate a complete stored ZIP, then exchange directories exactly once."""
        checksum = hashlib.sha256()
        size = 0
        archive.seek(0)
        while True:
            chunk = archive.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise AppError('Reference archive exceeds the transfer limit.', 'reference_limit')
            checksum.update(chunk)
        if checksum.hexdigest() != expected_hash:
            raise AppError('Reference archive checksum does not match.')
        archive.seek(0)
        with self.lock:
            if expected_revision != self.revision:
                raise Conflict('References changed; refresh status and retry.')
            try:
                with zipfile.ZipFile(archive) as bundle:
                    entries = bundle.infolist()
                    if not 1 <= len(entries) <= MAX_REFERENCES:
                        raise AppError('Reference count exceeds limits.', 'reference_limit')
                    paths = [entry.filename for entry in entries]
                    if len(set(paths)) != len(paths):
                        raise AppError('Duplicate reference archive paths.')
                    total = sum(entry.file_size for entry in entries)
                    if total > MAX_REFERENCE_BYTES:
                        raise AppError('Reference set exceeds limits.', 'reference_limit')
                    for entry in entries:
                        mode = stat.S_IFMT(entry.external_attr >> 16)
                        if (not self.valid_file('references', entry.filename)
                                or entry.compress_type != zipfile.ZIP_STORED
                                or entry.flag_bits & 1 or mode not in (0, stat.S_IFREG)
                                or not 1 <= entry.file_size <= MAX_FILE_BYTES):
                            raise AppError('Invalid reference archive entry.')
                    identities = {path.split('/')[0] for path in paths} - {'empty'}
                    if len(identities) < 2 or not any(p.startswith('empty/') for p in paths):
                        raise AppError('Replacement requires empty references and at least two hen identities.')
                    self.require_space(total)
                    with tempfile.TemporaryDirectory(prefix='job-', dir=str(self.root / 'scratch')) as temporary:
                        stage = Path(temporary) / 'references'
                        stage.mkdir()
                        for entry in entries:
                            data = bundle.read(entry)
                            image_valid(data)
                            target = safe_path(stage, entry.filename)
                            target.parent.mkdir(exist_ok=True)
                            target.write_bytes(data)
                        # All background images must exist before hen validation.
                        for relative in sorted(paths):
                            validate_reference(stage / relative)
                        # Desktop replacement must not silently discard teaching
                        # done in HA. Pull/merge those references, or Undo them first.
                        reviews = safe_path(self.root, 'reviews')
                        if reviews.exists():
                            for record_path in reviews.glob('*.json'):
                                checked = safe_path(reviews, record_path.name)
                                if checked.stat().st_size > 32768:
                                    raise AppError('Review metadata limit exceeded.')
                                for record in json.loads(checked.read_text()).values():
                                    relative = record.get('reference')
                                    if not relative:
                                        continue
                                    candidate = safe_path(stage, relative)
                                    if not candidate.is_file() or digest(candidate.read_bytes()) != record['sha256']:
                                        raise Conflict('Replacement would discard UI-reviewed references. Pull and merge them, or Undo their reviews first.')
                        desired = [{'path': path, 'bytes': (stage / path).stat().st_size,
                                    'sha256': digest((stage / path).read_bytes())} for path in sorted(paths)]
                        if desired != self.files('references'):
                            self._activate(stage)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
                raise AppError('Invalid reference ZIP archive.') from None
            return self.revision

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
            counts = {'captures': 0, 'results': 0}
            for kind in ('captures', 'results'):
                root = self.root / kind
                for path in root.rglob('*'):
                    if self.private_metadata(path, path.relative_to(root).as_posix()):
                        continue
                    checked = safe_path(root, path.relative_to(root).as_posix())
                    if checked.is_file():
                        if not self.valid_file(kind, path.relative_to(root).as_posix()):
                            raise AppError('Unexpected file in managed storage.')
                        files.append((checked.stat().st_mtime, checked.stat().st_size, checked, kind))
                        counts[kind] += 1
            total = sum(item[1] for item in files)
            removed = 0
            for modified, size, path, kind in sorted(files):
                limit = MAX_CAPTURE_FILES if kind == 'captures' else MAX_HISTORY_FILES
                if now - modified <= age and total <= maximum and counts[kind] <= limit:
                    continue
                path.unlink()
                total -= size
                counts[kind] -= 1
                removed += 1
            return removed


class Conflict(AppError):
    pass
