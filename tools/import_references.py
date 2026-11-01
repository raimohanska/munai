"""Import explicitly reviewed images into the shared local reference store.

Run in the devcontainer. No .env, live capture, broker, or HA connection.
"""
import argparse
from contextlib import ExitStack
import fcntl
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camera_profile import camera_bounds
from ha_service.options import AppError, validate
from ha_service.runtime import Runtime
from ha_service.storage import Store
from munai_remote import RemoteError, local_references


def import_references(source, root, apply=False, overwrite=False):
    items = local_references(source)
    print('{} {} reviewed reference(s); originals will be preserved.'.format(
        'Import' if apply else 'Would import', len(items)))
    if not apply:
        return len(items)
    root = Path(root).absolute()
    if root.is_symlink():
        raise AppError('Data root must not be a symlink.')
    root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for name in ('.chicken-device.lock', 'service.lock'):
            path = root / name
            if path.is_symlink():
                raise AppError('Reference import lock must not be a symlink.')
            lock = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise AppError('A local capture/service/import is running; stop it before importing.') from None
        options = validate({})
        left, right, rectangles = camera_bounds()
        options['camera_profile'] = {'horizontal_bounds': [left, right], 'excluded_rectangles': rectangles}
        store = Store(root, options)
        runtime = Runtime(options, store)
        current = {item['path']: item['sha256'] for item in store.files('references')}
        if not overwrite and any(relative in current and current[relative] != sha for relative, path, sha in items):
            raise AppError('A reference differs from the reviewed file; inspect it before using --overwrite.')
        for relative, path, sha in items:
            if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                raise AppError('Reference source changed during import; retry after review.')
            store.upload(relative, path.read_bytes(), sha, store.revision, overwrite, runtime.validate_reference)
        print('Reviewed reference import complete. Push references/ to HA when ready.')
        return len(items)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, help='Explicitly reviewed flat identity folders, or a managed reference root')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    arguments = parser.parse_args()
    os.umask(0o077)
    try:
        import_references(arguments.directory, arguments.root, arguments.apply, arguments.overwrite)
        return 0
    except (AppError, RemoteError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Reference import stopped; check images, permissions and disk space. Sources are preserved.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
