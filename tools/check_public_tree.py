"""Check Git-tracked paths only. Never opens data or environment files."""
from pathlib import PurePosixPath
import subprocess
import sys

PRIVATE_DIRECTORIES = {'captures', 'recognition', 'models', 'references', 'local-data',
                       'results', 'scratch', 'tls', 'reports'}
PRIVATE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.onnx', '.pt', '.pth', '.npz', '.pkl', '.joblib', '.pem', '.key'}
SYNTHETIC_FIXTURES = PurePosixPath('tests/fixtures/nest_presence')
SYNTHETIC_IMAGE_NAMES = frozenset(
    ['synthetic_empty_{}.jpg'.format(i) for i in range(1, 5)] +
    ['synthetic_eggs_{:02d}.jpg'.format(i) for i in (1, 3, 9, 15)] +
    ['synthetic_pile_{:02d}.jpg'.format(i) for i in (3, 6, 9, 12, 15)] +
    ['synthetic_hen_{}.jpg'.format(name) for name in (
        'dark', 'light', 'coarse', 'fine', 'small_patterned', 'clipped_left',
        'clipped_right', 'clipped_top', 'clipped_bottom', 'with_eggs', 'beside_pile')])


def main():
    paths = subprocess.check_output(['git', 'ls-files', '-z']).decode('utf-8').split('\0')
    bad = 0
    for name in paths:
        if not name:
            continue
        path = PurePosixPath(name)
        # Only the explicitly inventoried, reproducibly drawn synthetic JPEGs
        # belong in Git. This is not an exception for real camera images.
        synthetic_image = (path.parent == SYNTHETIC_FIXTURES and
                           path.name in SYNTHETIC_IMAGE_NAMES)
        if (any(part in PRIVATE_DIRECTORIES for part in path.parts)
                or (path.suffix.lower() in PRIVATE_SUFFIXES and not synthetic_image)
                or path.name in {'camera-profile.json', 'profiles.json'}
                or path.name.startswith('.munai-remote.json')
                or (path.name.startswith('.env') and name != '.env.example')):
            # Do not echo potentially sensitive filenames.
            bad += 1
    if bad:
        print('Public-tree check failed: {} private-data path(s) are tracked. Remove them before publishing.'.format(bad))
        return 1
    print('Public-tree path check passed (does not detect secrets or identities embedded in source text).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
