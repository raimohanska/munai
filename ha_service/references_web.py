"""Read-only active-reference gallery; never includes staging or predictions."""
from collections import Counter
from html import escape

from .options import AppError
from .storage import MAX_FILE_BYTES, MAX_REFERENCES, safe_path

PAGE_SIZE = 20


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


def render(store, page=0, base=''):
    with store.lock:
        revision, items = inventory(store)
        layout = store.options['box_split']
    items.sort(key=lambda item: (item['path'].startswith('empty/'), item['path']))
    counts = Counter(item['path'].split('/')[0] for item in items)
    identities = sorted(label for label in counts if label != 'empty')
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<title>Munai references</title><link rel="stylesheet" href="{}journal.css">'.format(base),
             '</head><body><header><h1>Active references</h1>',
             '<p><a href="{}">Journal</a> · <a href="{}references">Refresh references</a></p>'.format(base or './', base),
             '<p>These are the server’s currently active reviewed references, not local candidates, '
             'unreviewed captures, or journal predictions.</p>',
             '<p>Active reference version: <code>{}</code><br>Configured box split: <strong>{}</strong></p>'.format(
                 escape(revision), escape(layout)),
             '<p>{} identity photos across {} hens · {} full-frame empty calibration photos</p>'.format(
                 sum(counts[label] for label in identities), len(identities), counts['empty']),
             '<p class="muted">Recognition may still be unavailable if these references are incompatible '
             'with the configured layout. Listing them does not validate recognition accuracy. '
             'Reference version is separate from the app release version.</p>']
    if identities:
        parts.append('<p>' + ' · '.join('{} ({})'.format(escape(label), counts[label]) for label in identities) + '</p>')
    parts.append('</header><main>')
    if not items:
        parts.append('<article><h2>No active reference photos</h2><p>Upload reviewed identity and '
                     'empty-nest references using the transfer CLI. This page is read-only.</p></article>')
    previous = None
    for item in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        label, name = item['path'].split('/')
        if label != previous:
            if previous is not None:
                parts.append('</div></article>')
            title = 'Empty-nest background calibration' if label == 'empty' else label.capitalize()
            parts.append('<article><h2>{}</h2><p>{} active reference photos</p>'.format(escape(title), counts[label]))
            if label == 'empty':
                parts.append('<p class="muted">Reviewed hen-free full frames. These calibrate background '
                             'exclusions; they are not a hen identity and may contain eggs.</p>')
            else:
                parts.append('<p class="muted">Shared appearance references: side/view suffixes record '
                             'provenance, not where this hen must be identified.</p>')
            parts.append('<div class="photos">')
            previous = label
        source = base + 'references/' + revision + '/' + item['path']
        parts.append('<figure><a href="{0}" target="_blank" rel="noopener">'
                     '<img src="{0}" loading="lazy" alt="{1}"></a>'
                     '<figcaption><code>{2}</code><br>{3:.1f} KiB · '
                     '<a href="{0}" target="_blank" rel="noopener">Full-size photo</a>'
                     '</figcaption></figure>'.format(
                         escape(source, quote=True), escape(label + ': ' + name, quote=True),
                         escape(item['path']), item['bytes'] / 1024.0))
    if previous is not None:
        parts.append('</div></article>')
    parts.append('</main><nav>')
    if page:
        parts.append('<a href="?page={}">← Previous</a>'.format(page - 1))
    parts.append('<span>Page {} of {}</span>'.format(page + 1, pages))
    if page + 1 < pages:
        parts.append('<a href="?page={}">Next →</a>'.format(page + 1))
    parts.append('</nav></body></html>')
    return ''.join(parts).encode('utf-8')
