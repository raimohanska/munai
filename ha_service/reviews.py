"""Explicit human review and reference promotion; no historical state mutation."""
from datetime import datetime, timezone
import io
import json
import secrets

from PIL import Image, ImageOps

from data_layout import LABEL, RESERVED
from .journal import ID
from .options import AppError
from .references_web import inventory as reference_inventory
from .storage import (Conflict, MAX_FILE_BYTES, atomic_json, digest, image_valid,
                      safe_path)
from .training import TrainingArchive

MAX_REVIEWS = 10000


class Reviews:
    def __init__(self, journal, validate_reference):
        self.journal = journal
        self.store = journal.store
        self.validate_reference = validate_reference
        self.token = secrets.token_hex(32)
        self.root = safe_path(self.store.root, 'reviews')
        self.root.mkdir(exist_ok=True, mode=0o700)

    def source(self, source):
        """Resolve only managed full frames, using recorded layout, not current layout."""
        if source.startswith('captures/'):
            relative = source[len('captures/'):]
            data = self.store.read('captures', relative)
            stamp = relative.rsplit('/', 1)[-1][:-4]
            try:
                result = json.loads(self.store.read('results', stamp + '.json').decode('utf-8'))
                if result['timestamp'] != stamp:
                    raise ValueError()
                zones = result['raw_prediction'].get('zones')
                if zones:
                    names = set(zones)
                    if names <= {'left', 'right', 'center'} and names & {'left', 'right'}:
                        layout = 'vertical'
                    elif names <= {'top', 'bottom', 'center'} and names & {'top', 'bottom'}:
                        layout = 'horizontal'
                    else:
                        layout = 'unknown'
                else:
                    layout = 'none'
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                layout = 'unknown'
        elif source.startswith('images/') and source.endswith('.jpg') and ID.fullmatch(source[7:-4]):
            with self.journal.lock:
                path = safe_path(self.journal.root / 'images', source[7:])
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise AppError('Image exceeds review size limit.')
                data = path.read_bytes()
                layouts = set()
                for entry_path in self.journal.entries():
                    entry = self.journal.read_entry(entry_path)
                    if any(frame['id'] == source[7:-4] for group in entry['evidence']
                           for frame in group['frames']):
                        layouts.add(entry.get('layout', 'none'))
                layout = layouts.pop() if len(layouts) == 1 else 'unknown'
        else:
            raise AppError('Invalid review image.')
        image_valid(data)
        return data, layout

    def load(self, checksum):
        path = safe_path(self.root, checksum + '.json')
        if not path.exists():
            return {}, 'none'
        if path.stat().st_size > 32768:
            raise AppError('Review metadata limit exceeded.')
        data = path.read_bytes()
        return json.loads(data.decode('utf-8')), digest(data)

    def context(self, source):
        with self.store.lock:
            data, layout = self.source(source)
            checksum = digest(data)
            records, revision = self.load(checksum)
            regions = {'vertical': ['whole', 'left', 'center', 'right'],
                       'horizontal': ['whole', 'top', 'center', 'bottom']}.get(layout, ['whole'])
            _, reference_items = reference_inventory(self.store)
            identities = sorted({item['path'].split('/')[0] for item in reference_items}
                                - {'empty'})
            return dict(source=source, image=checksum, layout=layout, regions=regions,
                        identities=identities, records=records, revision=revision,
                        references=self.store.revision, token=self.token,
                        current_layout=self.store.options['box_split'])

    def remove_training(self, record):
        relative = record.get('training')
        if not relative:
            return
        archive = TrainingArchive(self.store)
        if not safe_path(archive.root, relative).exists():
            return  # Already transferred and acknowledged.
        payload = archive.read(relative)
        saved = json.loads(payload.decode('utf-8'))
        image = archive.read(saved['image'])
        archive.prune([dict(path=relative, bytes=len(payload), sha256=digest(payload)),
                       dict(path=saved['image'], bytes=len(image), sha256=digest(image))])

    def save(self, request):
        with self.store.lock:
            context = self.context(request['source'])
            for key in ('image', 'layout', 'revision', 'references'):
                if request.get(key) != context[key]:
                    raise Conflict('Image, reviews or references changed. Reopen the review popup.')
            region, label = request.get('region'), request.get('label')
            if region not in context['regions']:
                raise AppError('Invalid review region.')
            key = context['layout'] + '-' + region
            records = context['records']
            previous = records.get(key)
            if request.get('action') == 'undo':
                if previous:
                    reference = previous.get('reference')
                    if reference and previous.get('created'):
                        for path in self.root.glob('*.json'):
                            checked = safe_path(self.root, path.name)
                            if checked.stat().st_size > 32768:
                                raise AppError('Review metadata limit exceeded.')
                            for other_key, other in json.loads(checked.read_text()).items():
                                if path.name == context['image'] + '.json' and other_key == key:
                                    continue
                                if other.get('reference') == reference:
                                    raise Conflict('Another review uses this reference. Undo that review first.')
                        self.store.remove_review_reference(reference, previous['sha256'])
                    del records[key]
                    self.remove_training(previous)
                    atomic_json(safe_path(self.root, context['image'] + '.json'), records)
                return self.context(request['source'])
            if request.get('action') != 'save' or not isinstance(label, str):
                raise AppError('Invalid review action.')
            if type(request.get('add_reference', False)) is not bool:
                raise AppError('Invalid reference selection.')
            if label not in ('empty', 'obscured') and (not LABEL.fullmatch(label) or label in RESERVED):
                raise AppError('Use a hen name of 1–48 lowercase letters, digits, hyphens or underscores.')
            if previous:
                if previous['label'] == label:
                    return context
                raise Conflict('Undo the existing review for this region before changing it.')
            if context['revision'] == 'none' and len(list(self.root.glob('*.json'))) >= MAX_REVIEWS:
                raise AppError('Review limit reached. Back up and manage review storage first.')
            data, layout = self.source(request['source'])
            promote = (request.get('add_reference', False) and label != 'obscured' and layout == self.store.options['box_split']
                       and ((label == 'empty' and region == 'whole') or
                            (label != 'empty' and ((layout == 'none' and region == 'whole') or
                                                  (layout in ('vertical', 'horizontal') and region != 'whole')))))
            record = dict(label=label, region=region, layout=layout, source=request['source'],
                          created=False, status='annotation', reference=None)
            snapshot = dict(source=request['source'], layout=layout, region=region, view='review',
                            interpretation=dict(label=label))
            training_key = digest(json.dumps(dict(image=context['image'], **snapshot), sort_keys=True).encode('utf-8'))
            record['training'] = TrainingArchive(self.store).save(snapshot, data, training_key)
            if promote:
                if region != 'whole':
                    with Image.open(io.BytesIO(data)) as image:
                        image = ImageOps.exif_transpose(image)
                        w, h = image.size
                        boxes = {'left': (0, 0, w // 2, h), 'right': (w // 2, 0, w, h),
                                 'top': (0, 0, w, h // 2), 'bottom': (0, h // 2, w, h)}
                        boxes['center'] = ((w // 4, 0, w // 4 + w // 2, h) if layout == 'vertical'
                                           else (0, h // 4, w, h // 4 + h // 2))
                        output = io.BytesIO()
                        image.crop(boxes[region]).convert('RGB').save(output, 'JPEG', quality=95)
                        data = output.getvalue()
                checksum = digest(data)
                existing = next((item['path'] for item in self.store.files('references')
                                 if item['path'].startswith(label + '/') and item['sha256'] == checksum), None)
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
                reference = existing or label + '/' + stamp + ('' if region == 'whole' else '-' + region) + '.jpg'
                if not existing and safe_path(self.store.reference_root(), reference).exists():
                    raise Conflict('Reference filename already exists. Reopen the popup and retry.')
                record.update(reference=reference, sha256=checksum, created=not bool(existing), status='pending')
                records[key] = record
                # Durable intent before activation: after a crash, Undo can safely
                # remove exactly this new path/hash, never an unrelated reference.
                self.store.require_space(MAX_FILE_BYTES)
                atomic_json(safe_path(self.root, context['image'] + '.json'), records)
                try:
                    self.store.upload(reference, data, checksum, context['references'], False,
                                      self.validate_reference)
                except Exception:
                    # Keep pending intent if activation succeeded but final sync failed.
                    if not safe_path(self.store.reference_root(), reference).exists():
                        self.remove_training(record)
                        del records[key]
                        atomic_json(safe_path(self.root, context['image'] + '.json'), records)
                    raise
                record['status'] = 'active'
            records[key] = record
            self.store.require_space(32768)
            atomic_json(safe_path(self.root, context['image'] + '.json'), records)
            return self.context(request['source'])
