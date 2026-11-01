"""Check Git-tracked paths only. Never opens data or environment files."""
from pathlib import PurePosixPath
import subprocess
import sys

PRIVATE_DIRECTORIES = {'captures', 'recognition', 'models', 'references', 'local-data',
                       'results', 'scratch', 'tls', 'reports'}
PRIVATE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.onnx', '.pt', '.pth', '.npz', '.pkl', '.joblib', '.pem', '.key'}


def main():
    paths = subprocess.check_output(['git', 'ls-files', '-z']).decode('utf-8').split('\0')
    bad = 0
    for name in paths:
        if not name:
            continue
        path = PurePosixPath(name)
        if (any(part in PRIVATE_DIRECTORIES for part in path.parts)
                or path.suffix.lower() in PRIVATE_SUFFIXES
                or path.name in {'camera-profile.json', 'profiles.json'}
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
