"""Private event archive. Images precede the atomic nest-state/outbox commit."""
import copy
from datetime import datetime, timezone
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid

from .options import AppError
from .storage import MAX_FILE_BYTES, atomic_json, safe_path, sync_directory

ID = re.compile(r'^[0-9]{8}T[0-9]{6}_[0-9]{6}Z_[a-f0-9]{32}$')
ZONES = ('left', 'right', 'top', 'bottom', 'center')


def empty_evidence():
    return dict(baseline=[], visit=[], pending=[], presence=[], count=[], outbox=[])


def valid_evidence(value):
    """Bound the private persistence format before any paths are used."""
    if not isinstance(value, dict) or set(value) != set(empty_evidence()):
        return False
    for key in ('baseline', 'presence', 'count'):
        if not valid_frames(value[key], 5 if key == 'presence' else 3):
            return False
    for key in ('visit', 'pending'):
        if not valid_groups(value[key]):
            return False
    return (isinstance(value['outbox'], list) and len(value['outbox']) <= 2
            and all(valid_entry(entry) for entry in value['outbox']))


def valid_frames(frames, limit=3):
    return (isinstance(frames, list) and len(frames) <= limit and all(
        isinstance(frame, dict) and set(frame) in ({'id', 'label', 'egg_count'}, {'id', 'label', 'egg_count', 'zone'})
        and ('zone' not in frame or frame['zone'] in ZONES)
        and isinstance(frame['id'], str) and ID.fullmatch(frame['id'])
        and isinstance(frame['label'], str) and len(frame['label']) <= 80
        and (frame['egg_count'] is None or
             (type(frame['egg_count']) is int and 0 <= frame['egg_count'] <= 1000000))
        for frame in frames))


ROLES = ('Before visit / previous count', 'Identity confirmation',
         'Departure confirmation', 'Egg count confirmation')


def valid_groups(groups):
    return (isinstance(groups, list) and len(groups) <= 4 and all(
        isinstance(group, dict) and set(group) == {'role', 'frames'}
        and group['role'] in ROLES
        and valid_frames(group['frames'], 5 if group['role'] == ROLES[2] else 3)
        for group in groups))


def valid_entry(entry):
    return (isinstance(entry, dict) and set(entry) in (
            {'id', 'text', 'evidence'}, {'id', 'text', 'evidence', 'zone', 'layout'})
            and ('zone' not in entry or (entry['zone'] in
                 (('left', 'right', 'center') if entry['layout'] == 'vertical' else ('top', 'bottom', 'center'))
                 and entry['layout'] in ('vertical', 'horizontal')))
            and isinstance(entry['id'], str) and ID.fullmatch(entry['id'])
            and isinstance(entry['text'], str) and 1 <= len(entry['text']) <= 256
            and valid_groups(entry['evidence']))


def group(role, frames):
    return {'role': role, 'frames': frames}


def evolve(before, after, frame, streak, egg_streak, events, count_allowed=True):
    """Use the state machine's actual streaks, never a parallel decision rule."""
    evidence = copy.deepcopy(before['journal'])
    evidence['presence'] = (evidence['presence'] if streak > 1 else [])[-4:] + [frame]
    if count_allowed:
        evidence['count'] = ((evidence['count'] if egg_streak > 1 else [])[-2:] + [frame]
                             if egg_streak else [])
    presence, counts = evidence['presence'], evidence['count']
    context = evidence['visit'] if before['hen'] is not None else evidence['pending']
    for index, text in enumerate(events):
        if text.endswith(' arrived'):
            baseline = evidence['baseline'] if after['baseline'] is not None else []
            if before['hen'] == 'unknown' and evidence['visit']:
                baseline = evidence['visit'][0]['frames']
            groups = [group(ROLES[0], baseline), group(ROLES[1], presence)]
        elif text.endswith(' collected'):
            groups = [group(ROLES[0], evidence['baseline']), group(ROLES[3], counts)]
        else:
            departure = ([group(ROLES[2], presence)]
                         if text.endswith(' laid an Egg') and before['hen'] is not None else [])
            groups = context + departure + [group(ROLES[3] if text.endswith(' laid an Egg')
                                      else ROLES[2], counts if text.endswith(' laid an Egg') else presence)]
        evidence['outbox'].append({'id': frame['id'][:23] + '_' + format(index, '02x') + uuid.uuid4().hex[2:],
                                   'text': text, 'evidence': copy.deepcopy(groups)})
    if after['hen'] is not None and after['hen'] != before['hen']:
        # A direct swap has no safe before-visit egg baseline.
        baseline = evidence['baseline'] if after['baseline'] is not None else []
        if before['hen'] == 'unknown':
            baseline = evidence['visit'][0]['frames'] if evidence['visit'] else []
        evidence['visit'] = [group(ROLES[0], baseline), group(ROLES[1], presence)]
    if before['hen'] is not None and after['hen'] is None:
        evidence['pending'] = evidence['visit'] + [group(ROLES[2], presence)]
        evidence['visit'] = []
    if after['pending_hen'] is None:
        evidence['pending'] = []
    if count_allowed and after['hen'] is None and egg_streak >= 3:
        evidence['baseline'] = counts
    after['journal'] = evidence


def frame_ids(value):
    if isinstance(value, dict):
        if {'id', 'label', 'egg_count'} <= set(value) <= {'id', 'label', 'egg_count', 'zone'}:
            yield value['id']
        else:
            for child in value.values():
                yield from frame_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from frame_ids(child)


class Journal:
    def __init__(self, store):
        self.store = store
        self.root = safe_path(store.root, 'journal')
        self.lock = threading.RLock()
        for name in ('entries', 'images'):
            safe_path(self.root, name).mkdir(parents=True, exist_ok=True, mode=0o700)
        self.last_cleanup = None
        self.usage_bytes = 0
        self.archived = set()
        self.transient = set()

    def save_frame(self, source, prediction, stamp=None):
        self.store.require_space(MAX_FILE_BYTES * 2)
        if stamp is None:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        if not isinstance(stamp, str) or not re.fullmatch(r'[0-9]{8}T[0-9]{6}_[0-9]{6}Z', stamp):
            raise AppError('Invalid journal frame timestamp.')
        identifier = stamp + '_' + uuid.uuid4().hex
        destination = safe_path(self.root / 'images', identifier + '.jpg')
        self.transient.add(identifier)
        descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=str(destination.parent))
        try:
            with os.fdopen(descriptor, 'wb') as output, source.open('rb') as incoming:
                shutil.copyfileobj(incoming, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, str(destination))
            sync_directory(destination.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {'id': identifier, 'label': prediction['label'],
                'egg_count': prediction['egg_count'] if prediction['label'] == 'no_chicken' else None}

    def flush(self, nest):
        """Idempotent outbox replay, including after a crash between file writes."""
        with self.lock:
            for entry in nest.state['journal']['outbox']:
                atomic_json(safe_path(self.root / 'entries', entry['id'] + '.json'), entry)
                self.archived.update(frame_ids(entry))
            if nest.state['journal']['outbox']:
                state = copy.deepcopy(nest.state)
                state['journal']['outbox'] = []
                nest._commit(state)

    def cleanup_pending(self, nest):
        """Discard superseded candidate frames promptly, not a whole hour later."""
        if self.last_cleanup is None:
            return  # Startup retention first builds the complete archive index.
        with self.lock:
            current = set(frame_ids(nest.state['journal']))
            for identifier in self.transient - current - self.archived:
                path = safe_path(self.root / 'images', identifier + '.jpg')
                if path.exists():
                    path.unlink()
            self.transient = current

    def entries(self):
        paths = []
        for path in (self.root / 'entries').iterdir():
            if re.fullmatch(r'\.pending-[a-z0-9_]{8}', path.name):
                continue
            if path.suffix != '.json' or not ID.fullmatch(path.stem):
                raise AppError('Invalid journal entry path.')
            paths.append(safe_path(self.root / 'entries', path.name))
        return sorted(paths, reverse=True)

    def read_entry(self, path):
        if path.stat().st_size > 65536:
            raise AppError('Journal entry exceeds its size limit.')
        entry = json.loads(path.read_text())
        if not valid_entry(entry) or entry['id'] != path.stem:
            raise AppError('Invalid journal entry; restore the app backup.')
        return entry

    def retain(self, nest, force=False):
        now = time.time()
        if not force and self.last_cleanup is not None and now - self.last_cleanup < 3600:
            return
        with self.lock:
            self.flush(nest)
            cutoff = now - self.store.options['journal_retention_days'] * 86400
            used = set(frame_ids(nest.state['journal']))
            archived = set()
            size = 0
            for path in self.entries():
                entry = self.read_entry(path)
                stamp = datetime.strptime(entry['id'][:23], '%Y%m%dT%H%M%S_%fZ').replace(tzinfo=timezone.utc)
                if stamp.timestamp() < cutoff:
                    path.unlink()
                else:
                    used.update(frame_ids(entry))
                    archived.update(frame_ids(entry))
                    size += path.stat().st_size
            # Persist entry removals before deleting their now-unreferenced photos.
            sync_directory(self.root / 'entries')
            for path in (self.root / 'entries').glob('.pending-*'):
                if re.fullmatch(r'\.pending-[a-z0-9_]{8}', path.name):
                    if path.is_symlink() or not path.is_file():
                        raise AppError('Invalid temporary journal entry.')
                    path.unlink()
            for path in (self.root / 'images').iterdir():
                if path.is_symlink() or not path.is_file():
                    raise AppError('Invalid journal image path.')
                if re.fullmatch(r'\.pending-[a-z0-9_]{8}', path.name):
                    path.unlink()
                elif path.suffix != '.jpg' or not ID.fullmatch(path.stem):
                    raise AppError('Invalid journal image path.')
                elif path.stem not in used:
                    path.unlink()
                else:
                    size += path.stat().st_size
            self.usage_bytes = size
            self.archived = archived
            self.transient = set(frame_ids(nest.state['journal']))
            self.last_cleanup = now
