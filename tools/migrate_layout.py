"""Preview/apply the old local image-layout migration. Never reads .env.

Only known image paths and root CSV reports are moved. Originals are kept until
the validated reference snapshot is durable. Dry-run is the default; no names,
image paths, credentials, or raw library exceptions are logged.
"""
import argparse
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camera_profile import camera_bounds
from data_layout import LABEL, RESERVED, STAMP
from ha_service.options import AppError, validate
from ha_service.runtime import Runtime
from ha_service.storage import Store, digest, safe_path, sync_directory


class MigrationError(Exception):
    pass


def inventory(root):
    captures = root / 'captures'
    if captures.is_symlink():
        raise MigrationError('Capture root must not be a symlink.')
    references, moves = [], []
    if not captures.exists():
        return references, moves
    for source in sorted(captures.rglob('*')):
        if source.is_symlink():
            raise MigrationError('Migration refuses symlinks; review the local data tree.')
        if not source.is_file() or source.name.startswith('.'):
            continue
        parts = source.relative_to(captures).parts
        if len(parts) == 1 and source.suffix == '.csv':
            moves.append((source, root / 'reports' / 'legacy' / source.name))
        elif re.fullmatch(STAMP + r'\.jpg', source.name):
            if len(parts) == 1:
                moves.append((source, captures / 'pending' / source.name))
            elif parts[0] == 'chicken' and len(parts) == 2:
                moves.append((source, captures / 'unknown' / source.name))
            elif parts[:2] == ('chicken', 'unknown') and len(parts) == 3:
                moves.append((source, captures / 'unknown' / source.name))
            elif parts[0] == 'chicken' and len(parts) == 3:
                if not LABEL.fullmatch(parts[1]) or parts[1] in RESERVED:
                    raise MigrationError('A legacy identity conflicts with a reserved category; review it before migration.')
                references.append((source, parts[1] + '/' + source.name))
            elif parts[0] == 'chicken' and len(parts) == 4 and parts[2] == 'unreviewed':
                if not LABEL.fullmatch(parts[1]) or parts[1] in RESERVED:
                    raise MigrationError('Invalid legacy prediction identity; review before migration.')
                moves.append((source, captures / parts[1] / 'unreviewed' / source.name))
            elif not Store.valid_file('captures', '/'.join(parts)):
                raise MigrationError('An image has an unsupported location; review before migration.')
        else:
            raise MigrationError('An unrecognized non-image file requires manual review before migration.')
    return references, moves


def checked(root, path):
    relative = path.relative_to(root).as_posix()
    if path.suffix == '.csv':
        safe_path(root, relative[:-4])
        if path.is_symlink():
            raise MigrationError('Report files must not be symlinks.')
        return path
    return safe_path(root, relative)


def same_contents(first, second):
    # Streaming comparison avoids allocating unbounded reports or image data.
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open('rb') as left, second.open('rb') as right:
        while True:
            a, b = left.read(65536), right.read(65536)
            if a != b:
                return False
            if not a:
                return True


def preflight(root, references, moves):
    targets = {}
    for source, destination in moves:
        checked(root, source)
        checked(root, destination)
        if destination.exists() and (not destination.is_file() or not same_contents(source, destination)):
            raise MigrationError('A destination contains different data; nothing may be overwritten.')
        if destination in targets and not same_contents(source, targets[destination]):
            raise MigrationError('Different legacy files share a destination; resolve the conflict first.')
        targets[destination] = source
    for source, relative in references:
        checked(root, source)
        if not Store.valid_file('references', relative):
            raise MigrationError('A reviewed reference has an unsafe identity or filename.')


def migrate(root, apply=False):
    root = Path(root).absolute()
    references, moves = inventory(root)
    preflight(root, references, moves)
    print('{} {} reviewed reference(s), {} capture/report move(s).'.format(
        'Migrate' if apply else 'Would migrate', len(references), len(moves)))
    if not apply or not references and not moves:
        return len(references), len(moves)
    # Never overlap the legacy capture, combined-device, sorter, or local service.
    with ExitStack() as stack:
        for path in (root / '.chicken-device.lock', root / 'captures' / '.capture.lock',
                     root / 'captures' / '.sort.lock', root / 'service.lock'):
            if path.is_symlink():
                raise MigrationError('Migration lock must not be a symlink.')
            lock = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise MigrationError('A capture/service/sorter is running; stop it before migrating.') from None
        # Recheck after locking: a writer may have run during the initial preview.
        references, moves = inventory(root)
        preflight(root, references, moves)
        options = validate({})
        left, right, rectangles = camera_bounds()
        options['camera_profile'] = {'horizontal_bounds': [left, right], 'excluded_rectangles': rectangles}
        store = Store(root, options)
        runtime = Runtime(options, store)
        manifest = {item['path']: item for item in store.files('references')}
        # Find ALL reference conflicts before activating the first change.
        from ha_service.storage import MAX_FILE_BYTES
        for source, relative in references:
            if not 0 < source.stat().st_size <= MAX_FILE_BYTES:
                raise MigrationError('A reference exceeds the image size limit.')
            if relative in manifest and digest(source.read_bytes()) != manifest[relative]['sha256']:
                raise MigrationError('A managed reference has different data; resolve the conflict before migration.')
        for source, relative in references:
            data = source.read_bytes()
            store.upload(relative, data, digest(data), store.revision, False, runtime.validate_reference)
        # Verify the durable active snapshot against every original BEFORE moving
        # any originals. A partial failed import can safely be rerun.
        for source, relative in references:
            if digest(store.read('references', relative)) != digest(source.read_bytes()):
                raise MigrationError('Reference verification failed; originals have not been removed.')
        for source, destination in moves:
            checked(root, source)
            checked(root, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                # Same data-root/filesystem: durable no-overwrite hardlink, then
                # unlink the old name. A crash leaves at least one intact copy.
                os.link(str(source), str(destination))
            elif not same_contents(source, destination):
                raise MigrationError('Destination changed during migration; originals preserved.')
            sync_directory(destination.parent)
            source.unlink()
        for source, relative in references:
            # A safe deletion only of the verified original; never a broad tree.
            if digest(store.read('references', relative)) != digest(source.read_bytes()):
                raise MigrationError('An original changed; it has been preserved. Review before retrying.')
            source.unlink()
        # Remove only empty legacy directories; preserve hidden/user metadata.
        legacy = root / 'captures' / 'chicken'
        if legacy.exists():
            for path in sorted(legacy.rglob('*'), key=lambda p: len(p.parts), reverse=True) + [legacy]:
                if path.is_dir() and not path.is_symlink():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
        # Ensure all image paths can now be exported using the same rules as HA.
        store.files('captures')
        print('Migration verified. Originals are in managed references/captures; historical CSVs are in reports/legacy/.')
        return len(references), len(moves)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--apply', action='store_true', help='Actually migrate; default is a read-only preview')
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        migrate(arguments.root, arguments.apply)
        return 0
    except (MigrationError, AppError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Migration stopped; originals/durable copies are preserved. Check dependencies, permissions, disk space and image validity.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
