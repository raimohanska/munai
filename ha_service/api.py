"""Small, LAN-only HTTPS transfer endpoint. No HA or Supervisor API access."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import socket
import ssl
import subprocess
import threading
import tempfile
from urllib.parse import unquote, urlsplit

from .options import AppError
from .storage import Conflict, MAX_FILE_BYTES, MAX_ARCHIVE_BYTES, digest


def tls_context(root):
    cert = root / 'tls' / 'server.pem'
    key = root / 'tls' / 'server.key'
    if cert.is_symlink() or key.is_symlink():
        raise AppError('TLS files must not be symlinks.')
    if not cert.exists() and not key.exists():
        temporary_cert = cert.with_suffix('.pending.pem')
        temporary_key = key.with_suffix('.pending.key')
        try:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-sha256', '-nodes',
                            '-days', '3650', '-subj', '/CN=Munai transfer',
                            '-addext', 'basicConstraints=critical,CA:FALSE',
                            '-keyout', str(temporary_key), '-out', str(temporary_cert)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True, timeout=30)
            os.chmod(str(temporary_key), 0o600)
            os.replace(str(temporary_key), str(key))
            os.replace(str(temporary_cert), str(cert))
        except Exception:
            raise AppError('Cannot create HTTPS certificate; check OpenSSL and data permissions.') from None
        finally:
            temporary_cert.unlink(missing_ok=True)
            temporary_key.unlink(missing_ok=True)
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(key))
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    except Exception:
        raise AppError('Cannot load HTTPS certificate/key; restore a matching TLS pair from backup.') from None
    return context, fingerprint


class TransferServer(HTTPServer):
    allow_reuse_address = True
    request_queue_size = 5

    def __init__(self, address, context, store, tokens, validate_reference, status):
        self.context = context
        self.store = store
        self.tokens = tokens
        self.validate_reference = validate_reference
        self.status = status
        super().__init__(address, Handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(10)
        try:
            return self.context.wrap_socket(sock, server_side=True), address
        except Exception:
            sock.close()
            raise

    def handle_error(self, request, client_address):
        # The default implementation prints tracebacks containing untrusted data.
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'Munai'
    sys_version = ''

    def handle(self):
        # Socket timeouts alone reset on every read and do not bound slow-drip
        # headers/bodies. There is only one request worker and one deadline timer.
        def expire():
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        deadline = threading.Timer(120, expire)
        deadline.daemon = True
        deadline.start()
        try:
            super().handle()
        finally:
            deadline.cancel()

    def log_message(self, format, *args):
        pass  # No per-request logs, headers, tokens, paths, or tracebacks.

    def response(self, status, value=None, data=None):
        if data is None:
            data = json.dumps(value, allow_nan=False).encode('utf-8')
            content_type = 'application/json'
        else:
            content_type = 'application/octet-stream'
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-SHA256', digest(data))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(data)

    def authorized(self, write):
        headers = self.headers.get_all('Authorization', [])
        if len(headers) != 1 or not headers[0].startswith('Bearer '):
            return False
        token = headers[0][7:]
        if not 32 <= len(token) <= 128:
            return False
        hashed = digest(token.encode('utf-8'))
        allowed = False
        for record in self.server.tokens:
            matches = hmac.compare_digest(hashed, record['sha256'])
            allowed |= matches and (not write or record['scope'] == 'write')
        return allowed

    def handle_api(self):
        if not self.authorized(self.command in ('PUT', 'POST')):
            self.response(401, {'error': 'Authentication required or token scope insufficient.'})
            return
        try:
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment or parsed.netloc or parsed.scheme:
                raise AppError('Invalid API path.')
            path = unquote(parsed.path, errors='strict')
            self.route(path)
        except Conflict as error:
            self.response(409, {'error': str(error)})
        except AppError as error:
            self.response(400, {'error': str(error), 'code': error.code})
        except FileNotFoundError:
            self.response(404, {'error': 'Data file not found.'})
        except Exception:
            self.response(500, {'error': 'Transfer failed; check storage, image validity and app configuration.'})

    def route(self, path):
        store = self.server.store
        if self.command == 'GET' and path == '/v1/status':
            self.response(200, self.server.status())
        elif self.command == 'GET' and path.startswith('/v1/files/'):
            parts = path[len('/v1/files/'):].split('/', 1)
            kind = parts[0]
            if kind not in ('references', 'captures', 'results'):
                raise AppError('Unsupported data kind.')
            if len(parts) == 1:
                with store.lock:
                    listing = {'revision': store.revision, 'files': store.files(kind)}
                self.response(200, listing)
            else:
                with store.lock:
                    data = store.read(kind, parts[1])
                self.response(200, data=data)
        elif self.command == 'PUT' and path.startswith('/v1/references/'):
            if self.headers.get('Transfer-Encoding'):
                raise AppError('Chunked uploads are not supported.')
            length = self.headers.get('Content-Length', '')
            if not length.isdecimal() or not 1 <= int(length) <= MAX_FILE_BYTES:
                raise AppError('Upload must have a Content-Length from 1 byte to 8 MiB.')
            data = self.rfile.read(int(length))
            if len(data) != int(length):
                raise AppError('Incomplete upload; nothing activated.')
            overwrite = self.headers.get('X-Overwrite', 'false')
            if overwrite not in ('true', 'false'):
                raise AppError('X-Overwrite must be true or false.')
            revision = store.upload(path[len('/v1/references/'):], data,
                                    self.headers.get('X-Content-SHA256', ''),
                                    self.headers.get('If-Match', ''), overwrite == 'true',
                                    self.server.validate_reference)
            self.response(200, {'revision': revision})
        elif self.command == 'POST' and path == '/v1/references/replace':
            length = self.headers.get('Content-Length', '')
            if (self.headers.get('Transfer-Encoding') or not length.isdecimal()
                    or not 1 <= int(length) <= MAX_ARCHIVE_BYTES):
                raise AppError('Replacement requires a bounded Content-Length.')
            store.require_space(int(length))
            with tempfile.TemporaryFile(dir=str(store.root / 'scratch')) as archive:
                remaining = int(length)
                while remaining:
                    chunk = self.rfile.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise AppError('Incomplete replacement; references unchanged.')
                    archive.write(chunk)
                    remaining -= len(chunk)
                revision = store.replace_references(archive, self.headers.get('X-Content-SHA256', ''),
                                                    self.headers.get('If-Match', ''),
                                                    self.server.validate_reference)
            self.response(200, {'revision': revision})
        else:
            self.response(404, {'error': 'API endpoint not found.'})

    do_GET = handle_api
    do_PUT = handle_api
    do_POST = handle_api
