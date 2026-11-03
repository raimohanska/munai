#!/usr/bin/env python3
"""Standard-library transfer CLI for the devcontainer. Never reads .env."""
import argparse
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import sys
import tempfile
from urllib.parse import quote, urlsplit

from data_layout import RESERVED, STAMP, REFERENCE_STAMP, MAX_HISTORY_FILES, reference_directory

MAX_BYTES = 8 * 1024 * 1024
DEFAULT_CONFIG = Path(__file__).resolve().parent / '.munai-remote.json'


class RemoteError(Exception):
    """Safe, fixed text only; never include raw HTTP/TLS/filesystem exceptions."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def endpoint(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError()
        return parsed.hostname, parsed.port or 443
    except Exception:
        raise RemoteError('Use an HTTPS origin such as https://homeassistant.local:8443, without credentials or paths.') from None


def fingerprint(value):
    value = value.replace(':', '').lower()
    if not re.fullmatch(r'[a-f0-9]{64}', value):
        raise RemoteError('Copy the full SHA256 certificate fingerprint from the HA app log.')
    return value


class Remote:
    def __init__(self, profile, token=None):
        self.host, self.port = endpoint(profile['url'])
        self.fingerprint = fingerprint(profile['fingerprint'])
        self.token = token

    def connect(self):
        # Trust is an explicit out-of-band certificate pin, not the public CA/DNS
        # system. Verify it BEFORE sending any HTTP headers or bearer token.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(self.host, self.port, context=context, timeout=90)
        try:
            connection.connect()
            actual = sha256(connection.sock.getpeercert(binary_form=True))
            if not hmac.compare_digest(actual, self.fingerprint):
                raise RemoteError('Certificate fingerprint changed or does not match. No token was sent; verify HA logs before re-pairing.')
            return connection
        except RemoteError:
            connection.close()
            raise
        except Exception:
            connection.close()
            raise RemoteError('Cannot establish HTTPS; check the HA app, Network port mapping, address and LAN/VPN connectivity.') from None

    def request(self, method, path, data=None, headers=None, binary=False):
        connection = self.connect()
        try:
            outgoing = dict(headers or {})
            if self.token:
                outgoing['Authorization'] = 'Bearer ' + self.token
            connection.request(method, path, body=data, headers=outgoing)
            response = connection.getresponse()
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise RemoteError('Server response exceeds the transfer limit.')
            if response.status != 200:
                # Codes select local, safe explanations. Never echo arbitrary
                # response text that could contain a token or raw diagnostics.
                if response.status == 400:
                    try:
                        code = json.loads(body).get('code')
                    except Exception:
                        code = None
                    known = {'low_storage': 'HA storage reserve reached; free disk space or reduce capture retention.',
                             'reference_limit': 'Reference limit reached: 500 files or 128 MiB.',
                             'invalid_image': 'Invalid JPEG: at most 8 MiB, 8192 pixels per side and 16 million pixels total.',
                              'invalid_reference': 'Reference is not measurable; review hen/empty images and recognition thresholds.'}
                    if isinstance(code, str) and code in known:
                        raise RemoteError(known[code])
                errors = {401: 'Token rejected or scope insufficient; check HA Configuration and restart the app.',
                          404: 'Remote file or API endpoint not found.',
                          409: 'Reference conflict: refresh the manifest, or explicitly allow overwrite.',
                          400: 'Upload rejected; check the JPEG, identity/path, checksum and configured recognition masks.'}
                raise RemoteError(errors.get(response.status, 'Server transfer failed; check app storage and configuration.'))
            if not hmac.compare_digest(response.getheader('X-Content-SHA256', ''), sha256(body)):
                raise RemoteError('Response checksum failed; nothing saved.')
            return body if binary else json.loads(body)
        except RemoteError:
            raise
        except Exception:
            raise RemoteError('Transfer interrupted or invalid response; retry after checking app status.') from None
        finally:
            connection.close()


def check_credentials_file(path, info=None):
    # Configuration/credentials are separate from the legacy camera .env.
    if path.name.startswith('.env'):
        raise RemoteError('Use a separate CLI credential JSON file, never .env.')
    if info is None:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
    if not stat.S_ISREG(info.st_mode):
        raise RemoteError('CLI credential file must be a regular file, not a symlink.')
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RemoteError('CLI credential file must be owned by your user with mode 0600; fix its permissions before retrying.')
    if info.st_size > 256 * 1024:
        raise RemoteError('CLI credential file exceeds the size limit.')


def load_profiles(path):
    check_credentials_file(path)
    try:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    except OSError:
        raise RemoteError('Cannot open CLI credential file; check permissions and refuse symlinks.') from None
    try:
        with os.fdopen(descriptor, 'r') as handle:
            check_credentials_file(path, os.fstat(handle.fileno()))
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except RemoteError:
        raise
    except Exception:
        raise RemoteError('Cannot read CLI credential JSON; restore a private backup or pair using a new --config file.') from None


def save_profiles(path, profiles):
    check_credentials_file(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix='.munai-remote.json.pending-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w') as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(profiles, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def local_references(root):
    try:
        root = reference_directory(root)
    except (ValueError, OSError):
        raise RemoteError('Managed references are invalid or have no active version; inspect local status or finish migration.') from None
    if root.is_symlink() or not root.is_dir():
        raise RemoteError('Reference root must be a real directory, not a symlink.')
    items = []
    for folder in sorted(root.iterdir()):
        if folder.is_symlink():
            raise RemoteError('Symlink reference folders are not allowed.')
        if not folder.is_dir() or (folder.name in RESERVED and folder.name != 'empty') or folder.name.startswith('.'):
            continue
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}', folder.name):
            raise RemoteError('Identity folders must use 1-48 lowercase letters, digits, underscores or hyphens.')
        # Deliberately nonrecursive: predictions in unreviewed/ NEVER qualify.
        for path in sorted(folder.iterdir()):
            if re.fullmatch(REFERENCE_STAMP + r'\.jpg', path.name):
                if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
                    raise RemoteError('References must be regular JPEG files of at most 8 MiB.')
                data = path.read_bytes()
                items.append((folder.name + '/' + path.name, path, sha256(data)))
    if not items:
        raise RemoteError('No reviewed timestamped JPEGs found directly inside named identity folders.')
    if len(items) > 500 or sum(item[1].stat().st_size for item in items) > 128 * 1024 * 1024:
        raise RemoteError('Local references exceed 500 files or 128 MiB.')
    return items


def push(remote, root, dry_run=False, overwrite=False):
    items = local_references(root)
    listing = remote.request('GET', '/v1/files/references')
    revision = listing['revision']
    existing = {item['path']: item['sha256'] for item in listing['files']}
    changed = [item for item in items if existing.get(item[0]) != item[2]]
    if not overwrite and any(item[0] in existing for item in changed):
        raise RemoteError('A different remote reference has the same filename; inspect it and use --overwrite explicitly.')
    print('{} {} reference(s); {} unchanged. No remote files will be deleted.'.format(
        'Would upload' if dry_run else 'Uploading', len(changed), len(items) - len(changed)))
    if not dry_run:
        for relative, path, expected_hash in changed:
            if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                raise RemoteError('Local reference changed during transfer; run push again.')
            data = path.read_bytes()
            if sha256(data) != expected_hash:
                raise RemoteError('Local reference changed during transfer; run push again.')
            result = remote.request('PUT', '/v1/references/' + quote(relative, safe='/'), data=data,
                                    headers={'X-Content-SHA256': expected_hash, 'If-Match': revision,
                                             'X-Overwrite': 'true' if overwrite else 'false'})
            revision = result['revision']
        print('Reference upload complete. Status shows when at least two identities are ready.')


def download_path(root, relative):
    parts = relative.split('/')
    if (not relative or len(parts) > 5 or len(relative) > 240
            or any(not re.fullmatch(r'[a-zA-Z0-9_-]+(?:\.(?:jpg|json))?', part) for part in parts)):
        raise RemoteError('Server returned an unsafe file path.')
    current = root
    if root.is_symlink():
        raise RemoteError('Download root must not be a symlink.')
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise RemoteError('Download paths must not contain symlinks.')
    return current


def pull(remote, kind, root, dry_run=False, overwrite=False):
    listing = remote.request('GET', '/v1/files/' + kind)
    if len(listing['files']) > MAX_HISTORY_FILES:
        raise RemoteError('Remote file count exceeds the transfer limit.')
    pending = []
    for item in listing['files']:
        destination = download_path(root, item['path'])
        if not isinstance(item['bytes'], int) or not 0 <= item['bytes'] <= MAX_BYTES:
            raise RemoteError('Remote file size exceeds the transfer limit.')
        if destination.exists():
            if not destination.is_file():
                raise RemoteError('Download conflicts with a local directory.')
            if destination.stat().st_size <= MAX_BYTES and sha256(destination.read_bytes()) == item['sha256']:
                continue
            if not overwrite:
                raise RemoteError('Download would overwrite a different local file; inspect it or use --overwrite.')
        pending.append((item, destination))
    print('{} {} {} file(s). No local files will be deleted.'.format(
        'Would download' if dry_run else 'Downloading', len(pending), kind))
    if dry_run:
        return
    for item, destination in pending:
        data = remote.request('GET', '/v1/files/' + kind + '/' + quote(item['path'], safe='/'), binary=True)
        if len(data) != item['bytes'] or sha256(data) != item['sha256']:
            raise RemoteError('Download changed or checksum failed; refresh the listing and retry.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix='.download-', dir=str(destination.parent))
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(name, str(destination))
            else:
                # Atomic no-replace activation, including a file created mid-transfer.
                os.link(name, str(destination))
        finally:
            if os.path.exists(name):
                os.unlink(name)


def history_summary(remote):
    """Read-only retention coverage; never access camera configuration or logs."""
    summary = {}
    for kind, suffix in (('captures', 'jpg'), ('results', 'json')):
        files = remote.request('GET', '/v1/files/' + kind)['files']
        if len(files) > MAX_HISTORY_FILES:
            raise RemoteError('Remote file count exceeds the transfer limit.')
        stamps = []
        size = 0
        for item in files:
            name = item['path'].rsplit('/', 1)[-1]
            if (not re.fullmatch(STAMP + r'\.' + suffix, name)
                    or type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_BYTES):
                raise RemoteError('Invalid history listing.')
            stamps.append(name.rsplit('.', 1)[0])
            size += item['bytes']
        summary[kind] = {'files': len(files), 'bytes': size,
                         'oldest_utc': min(stamps) if stamps else None,
                         'newest_utc': max(stamps) if stamps else None}
    return summary


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--profile', default='green')
    result.add_argument('--config', type=Path, default=DEFAULT_CONFIG, help='Private credential JSON (default: .munai-remote.json beside this script)')
    sub = result.add_subparsers(dest='command', required=True)
    remote = sub.add_parser('remote').add_subparsers(dest='remote_command', required=True)
    pair = remote.add_parser('pair', help='Verify the certificate, save a local token with mode 0600, print only its hash')
    pair.add_argument('url')
    pair.add_argument('--fingerprint', required=True)
    remote.add_parser('status')
    remote.add_parser('history', help='Show retained capture/result counts, sizes and UTC coverage')
    remote.add_parser('logout', help='Remove local token/profile, NOT server-side revocation')
    for command in ('push', 'pull', 'export'):
        transfer = sub.add_parser(command)
        if command == 'pull':
            transfer.add_argument('kind', choices=['references', 'captures', 'results'])
        transfer.add_argument('directory', type=Path)
        transfer.add_argument('--dry-run', action='store_true')
        transfer.add_argument('--overwrite', action='store_true')
    rollback = sub.add_parser('rollback')
    rollback.add_argument('--yes', action='store_true', help='Confirm activation of the previous reference version')
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    os.umask(0o077)
    try:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', arguments.profile):
            raise RemoteError('Profile name must contain 1-48 letters, digits, underscores or hyphens.')
        profiles = load_profiles(arguments.config)
        if arguments.command == 'remote' and arguments.remote_command == 'pair':
            if arguments.profile in profiles:
                raise RemoteError('Profile already exists; revoke the old digest in HA and log out locally before re-pairing.')
            profile = {'url': arguments.url.rstrip('/'), 'fingerprint': fingerprint(arguments.fingerprint)}
            Remote(profile).connect().close()
            token = secrets.token_urlsafe(32)
            profile['token'] = token
            profiles[arguments.profile] = profile
            save_profiles(arguments.config, profiles)
            print('Token saved to the local credential file (mode 0600). Paste this digest into Transfer token SHA256 (transfer_token_sha256) in HA Configuration:')
            print(sha256(token.encode('utf-8')))
            print('Set Transfer client permissions (transfer_token_scope) to write for uploads, save, restart, then run remote status.')
            return 0
        if arguments.profile not in profiles:
            raise RemoteError('No matching profile; run remote pair first.')
        profile = profiles[arguments.profile]
        if arguments.command == 'remote' and arguments.remote_command == 'logout':
            del profiles[arguments.profile]
            save_profiles(arguments.config, profiles)
            print('Local token removed. Revoke its digest in HA Configuration and restart to disable server access.')
            return 0
        token = profile.get('token')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
            raise RemoteError('Profile has no valid local token; revoke the old digest in HA, log out locally and pair again.')
        remote = Remote(profile, token)
        if arguments.command == 'remote':
            value = history_summary(remote) if arguments.remote_command == 'history' else remote.request('GET', '/v1/status')
            print(json.dumps(value, indent=2))
        elif arguments.command == 'push':
            push(remote, arguments.directory, arguments.dry_run, arguments.overwrite)
        elif arguments.command == 'pull':
            pull(remote, arguments.kind, arguments.directory, arguments.dry_run, arguments.overwrite)
        elif arguments.command == 'export':
            for kind in ('references', 'captures', 'results'):
                pull(remote, kind, arguments.directory / kind, arguments.dry_run, arguments.overwrite)
        elif arguments.command == 'rollback':
            if not arguments.yes:
                raise RemoteError('Review app status and pass --yes to activate the previous reference version.')
            revision = remote.request('GET', '/v1/status')['reference_revision']
            result = remote.request('POST', '/v1/references/rollback', data=b'', headers={'If-Match': revision})
            print('Activated previous reference version: ' + result['revision'])
        return 0
    except RemoteError as error:
        print('Munai: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        print('Munai: operation failed; check local credential/file permissions and app status.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
