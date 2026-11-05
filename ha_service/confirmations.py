"""Explicit confirmation of displayed interpretations, never reference promotion."""
from html import escape
import hmac
import json
import re
import secrets

from .journal import ID
from .options import AppError
from .storage import Conflict, MAX_FILE_BYTES, VERSION_ID, digest, safe_path
from .training import MAX_ANNOTATIONS, TrainingArchive, without_center_eggs

MAX_CONFIRMATIONS = MAX_ANNOTATIONS
MAX_REQUEST_BYTES = 32768
STAMP = re.compile(r'^[0-9]{8}T[0-9]{6}_[0-9]{6}Z\.jpg$')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class Confirmations:
    def __init__(self, journal, runtime=None):
        self.journal = journal
        self.store = journal.store
        self.runtime = runtime
        self.secret = secrets.token_bytes(32)
        self.root = TrainingArchive(self.store).root

    def path(self, source):
        if source.startswith('captures/'):
            relative = source[len('captures/'):]
            if self.store.valid_file('captures', relative):
                return safe_path(self.store.root / 'captures', relative)
        elif source.startswith('images/') and source.endswith('.jpg') and ID.fullmatch(source[7:-4]):
            return safe_path(self.journal.root / 'images', source[7:])
        elif source.startswith('references/'):
            parts = source.split('/', 2)
            if (len(parts) == 3 and (VERSION_ID.fullmatch(parts[1]) or parts[1] == 'none')
                    and self.store.valid_file('references', parts[2])):
                if parts[1] != self.store.revision:
                    raise Conflict('References changed. Refresh the page.')
                return safe_path(self.store.reference_root(), parts[2])
        raise AppError('Invalid confirmation image.')

    def image(self, source, read=False):
        if source.startswith('latest/') and STAMP.fullmatch(source[7:]) and self.runtime:
            data = self.runtime.web_image(source[7:-4])
            if data is None:
                raise FileNotFoundError()
            return digest(data), data if read else None
        path = self.path(source)
        info = path.stat()
        if not path.is_file() or info.st_size > MAX_FILE_BYTES:
            raise AppError('Image exceeds confirmation size limit.')
        # Bind the displayed snapshot without decoding/hashing entire galleries.
        fingerprint = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        return fingerprint, path.read_bytes() if read else None

    def button(self, source, interpretation, layout='none', region='whole', view='prediction'):
        try:
            with self.store.lock, self.journal.lock:
                fingerprint, unused = self.image(source)
                snapshot = dict(source=source,
                                interpretation=without_center_eggs(interpretation, region), layout=layout,
                                region=region, view=view, fingerprint=fingerprint)
                payload = encoded(snapshot)
                signature = hmac.new(self.secret, payload, 'sha256').hexdigest()
                request = encoded(dict(snapshot=snapshot, signature=signature))
                if len(request) > MAX_REQUEST_BYTES:
                    raise AppError('Interpretation exceeds confirmation size limit.')
                confirmed = safe_path(self.root / 'annotations', digest(payload) + '.json').exists()
        except FileNotFoundError:
            return ''
        return ('<button type="button" class="confirm-button" data-confirm="{}" {}'
                'title="Retain image and interpretation for training">{}</button>'
                '<span class="confirm-message" role="status"></span>').format(
                    escape(request.decode('utf-8'), quote=True),
                    'disabled ' if confirmed else '', 'Confirmed' if confirmed else 'Confirm')

    def save(self, request):
        snapshot, signature = request.get('snapshot'), request.get('signature')
        if not isinstance(snapshot, dict) or not isinstance(signature, str):
            raise AppError('Invalid confirmation request.')
        payload = encoded(snapshot)
        expected = hmac.new(self.secret, payload, 'sha256').hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise Conflict('Confirmation expired or changed. Refresh the page.')
        with self.store.lock, self.journal.lock:
            annotation = safe_path(self.root / 'annotations', digest(payload) + '.json')
            if annotation.exists():
                return {'confirmed': True}
            fingerprint, data = self.image(snapshot['source'], read=True)
            after, unused = self.image(snapshot['source'])
            if fingerprint != snapshot['fingerprint'] or after != fingerprint:
                raise Conflict('Image changed. Refresh the page.')
            if len(list((self.root / 'annotations').glob('*.json'))) >= MAX_CONFIRMATIONS:
                raise AppError('Training archive limit reached. Back up and manage training storage.')
            record = dict(snapshot)
            record.pop('fingerprint')
            TrainingArchive(self.store).save(record, data, digest(payload))
            return {'confirmed': True}
