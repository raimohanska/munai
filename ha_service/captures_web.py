"""Read-only stored capture gallery, paged by UTC capture day."""
from datetime import datetime
from html import escape

from data_layout import MAX_HISTORY_FILES
from .options import AppError
from .storage import MAX_FILE_BYTES, safe_path
from .web_layout import start, end


def inventory(store):
    """Read bounded metadata only; caller holds the store lock."""
    root = store.root / 'captures'
    items = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if store.private_metadata(path, relative):
            continue
        checked = safe_path(root, relative)
        if checked.is_dir():
            continue
        if not checked.is_file() or not store.valid_file('captures', relative):
            raise AppError('Invalid capture gallery entry.')
        size = checked.stat().st_size
        if size > MAX_FILE_BYTES or len(items) >= MAX_HISTORY_FILES:
            raise AppError('Capture gallery limit exceeded.')
        stamp = checked.stem
        captured = datetime.strptime(stamp, '%Y%m%dT%H%M%S_%fZ')
        items.append({'path': relative, 'bytes': size, 'stamp': stamp,
                      'day': captured.strftime('%Y-%m-%d'),
                      'time': captured.strftime('%H:%M:%S.%f')})
    return items


def render(store, day=None, base=''):
    with store.lock:
        items = inventory(store)
        enabled = store.options['store_captures']
    days = sorted({item['day'] for item in items})
    if day is None and days:
        day = days[-1]
    selected = sorted((item for item in items if item['day'] == day),
                      key=lambda item: (item['stamp'], item['path']), reverse=True)
    parts = [start('Captures', 'Captures', base), '<header><h1>Stored captures</h1>',
             '<p>One UTC day at a time, newest photos first. Folder labels are predictions, '
             'not reviewed identities. This page is read-only.</p>',
             '<p class="muted">Only retained capture photos are shown, not every observation or '
             'journal evidence. Deduplication and retention can leave gaps.</p>',
             '<p><a href="{}captures">Newest stored day</a> · <a href="">Refresh</a></p>'.format(base)]
    if not enabled:
        parts.append('<p class="notice">Ordinary capture storage is disabled. Enable store_captures '
                     'in the app configuration to retain future capture photos.</p>')
    if day:
        parts.append('<h2>{} (UTC)</h2><p>{} stored photos</p>'.format(escape(day), len(selected)))
    parts.append('</header><nav aria-label="Capture days">')
    older = [value for value in days if value < day] if day else []
    newer = [value for value in days if value > day] if day else []
    if newer:
        parts.append('<a href="?date={}">← Newer day ({})</a>'.format(newer[0], newer[0]))
    if older:
        parts.append('<a href="?date={}">Older day ({}) →</a>'.format(older[-1], older[-1]))
    parts.append('</nav><main><article>')
    if not selected:
        parts.append('<h2>No stored captures{}</h2><p>Photos may not have been saved or may have '
                     'expired under the retention limits.</p>'.format(' for this day' if day else ''))
    else:
        parts.append('<div class="photos">')
        for item in selected:
            source = escape(base + 'captures/' + item['path'], quote=True)
            category = item['path'].rsplit('/', 1)[0]
            parts.append('<figure><a href="{0}" target="_blank" rel="noopener">'
                         '<img src="{0}" loading="lazy" alt="{1}"></a>'
                         '<figcaption>{2} UTC · {1}<br><code>{3}</code><br>{4:.1f} KiB · '
                         '<a href="{0}" target="_blank" rel="noopener">Full-size photo</a>'
                         '</figcaption></figure>'.format(source, escape(category, quote=True),
                                                        item['time'], escape(item['path']),
                                                        item['bytes'] / 1024.0))
        parts.append('</div>')
    parts.append('</article></main>' + end())
    return ''.join(parts).encode('utf-8')
