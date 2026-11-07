"""Active-reference gallery; never includes staging or predictions."""
from collections import Counter
from html import escape

from .options import AppError
from .storage import MAX_FILE_BYTES, MAX_REFERENCES, safe_path
from .web_layout import start, end

def inventory(store):
    """Snapshot metadata under the caller's store lock; don't hash/decode photos."""
    revision = store.revision
    root = store.reference_root()
    items = []
    if root is not None:
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(root).as_posix()
            if store.private_metadata(path, relative):
                continue
            checked = safe_path(root, relative)
            if checked.is_dir():
                continue
            if not checked.is_file() or not store.valid_file('references', relative):
                raise AppError('Invalid reference gallery entry.')
            size = checked.stat().st_size
            if size > MAX_FILE_BYTES or len(items) >= MAX_REFERENCES:
                raise AppError('Reference gallery limit exceeded.')
            items.append({'path': relative, 'bytes': size})
    return revision, items


def render(store, base='', confirmations=None, token=None):
    with store.lock:
        revision, items = inventory(store)
        layout = store.options['box_split']
    items.sort(key=lambda item: (item['path'].startswith('empty/'), item['path']))
    counts = Counter(item['path'].split('/')[0] for item in items)
    identities = sorted(label for label in counts if label != 'empty')
    parts = [start('References', 'References', base), '<header><h1>Active references</h1>',
             '<p><a href="{}references">Refresh references</a></p>'.format(base),
              '<p>Reference content checksum: <code>{}</code><br>Configured box split: <strong>{}</strong></p>'.format(
                 escape(revision), escape(layout)),
             '<p>{} identity photos across {} hens · {} full-frame empty calibration photos</p>'.format(
                 sum(counts[label] for label in identities), len(identities), counts['empty'])]
    if identities:
        parts.append('<p>' + ' · '.join('{} ({})'.format(escape(label), counts[label]) for label in identities) + '</p>')
    if token is not None:
        parts.append('<script src="{0}references.js" data-api="{0}references/remove" '
                     'data-token="{1}" data-revision="{2}" defer></script>'.format(
                         escape(base, quote=True), escape(token, quote=True), escape(revision, quote=True)))
    parts.append('</header><main>')
    if not items:
        parts.append('<article><h2>No active reference photos</h2><p>Upload reviewed identity and '
                     'empty-nest references using the transfer CLI.</p></article>')
    previous = None
    for item in items:
        label, name = item['path'].split('/')
        if label != previous:
            if previous is not None:
                parts.append('</div></article>')
            title = 'Empty-nest background calibration' if label == 'empty' else label.capitalize()
            parts.append('<article><h2>{}</h2><p>{} active reference photos</p>'.format(escape(title), counts[label]))
            parts.append('<div class="photos">')
            previous = label
        source = base + 'references/' + revision + '/' + item['path']
        confirm = (confirmations.button('references/' + revision + '/' + item['path'],
                                       {'label': label}, layout,
                                       'whole' if label == 'empty' or layout == 'none' else 'crop',
                                       'reference') if confirmations is not None else '')
        remove = ('<button type="button" class="reference-remove" data-path="{}" '
                  'aria-label="Remove reference {}" title="Remove reference">×</button>'.format(
                      escape(item['path'], quote=True), escape(item['path'], quote=True))
                  if token is not None else '')
        parts.append('<figure><div class="frame"><a href="{0}" target="_blank" rel="noopener">'
                      '<img src="{0}" loading="lazy" alt="{1}"></a>{5}</div>'
                     '<figcaption><code>{2}</code><br>{3:.1f} KiB · '
                     '<a href="{0}" target="_blank" rel="noopener">Full-size photo</a>'
                      ' {4}</figcaption></figure>'.format(
                         escape(source, quote=True), escape(label + ': ' + name, quote=True),
                           escape(item['path']), item['bytes'] / 1024.0, confirm, remove))
    if previous is not None:
        parts.append('</div></article>')
    parts.append('</main>' + end())
    return ''.join(parts).encode('utf-8')
