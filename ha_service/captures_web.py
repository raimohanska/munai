"""Stored capture gallery with explicit review, paged by UTC capture day."""
from datetime import datetime
from html import escape
import json

from data_layout import MAX_HISTORY_FILES
from .options import AppError
from .storage import MAX_FILE_BYTES, safe_path
from .web_layout import start, end
from .review_web import button, dialog


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
        items.append({'path': relative, 'stamp': stamp,
                      'day': captured.strftime('%Y-%m-%d')})
    return items


def state_summary(raw):
    """Only this frame: spatially ordered labels and one raw frame egg count."""
    def name(item):
        value = item.get('label', item.get('occupant'))
        if item.get('obscured') or value in (None, 'unknown', 'obscured', 'unavailable'):
            return 'uncertain'
        return 'empty' if value in ('empty', 'no_chicken') else value.lower()

    zones = raw.get('zones')
    if zones:
        order = ('left', 'center', 'right') if 'left' in zones or 'right' in zones else ('top', 'center', 'bottom')
        summary = ' | '.join(name(zones.get(zone, {})) for zone in order)
    else:
        summary = name(raw)
    eggs = raw.get('egg_count')
    if type(eggs) is not int or eggs < 0:
        return summary
    return '{} ({} egg{})'.format(summary, eggs, '' if eggs == 1 else 's')


def interpretation(store, stamp):
    """Use only the exact paired result; never infer state from the folder."""
    try:
        with store.lock:
            result = json.loads(store.read('results', stamp + '.json').decode('utf-8'))
        if result.get('timestamp') != stamp or not isinstance(result.get('raw_prediction'), dict):
            return 'Interpretation unavailable'
        return state_summary(result['raw_prediction'])
    except (OSError, ValueError, TypeError, AttributeError, AppError):
        return 'Interpretation unavailable'


def render(store, day=None, base=''):
    with store.lock:
        items = inventory(store)
        enabled = store.options['store_captures']
    days = sorted({item['day'] for item in items})
    if day is None and days:
        day = days[-1]
    selected = sorted((item for item in items if item['day'] == day),
                      key=lambda item: (item['stamp'], item['path']), reverse=True)
    parts = [start('Captures', 'Captures', base, reviews=True), '<header><h1>Stored captures</h1>',
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
        parts.append('<h2>No stored captures{}</h2>'.format(' for this day' if day else ''))
    else:
        parts.append('<div class="photos">')
        for item in selected:
            source = escape(base + 'captures/' + item['path'], quote=True)
            name = item['path'].rsplit('/', 1)[-1]
            summary = interpretation(store, item['stamp'])
            parts.append('<figure><div class="review-photo"><a href="{0}" target="_blank" rel="noopener">'
                         '<img src="{0}" loading="lazy" alt="{1}"></a>{3}</div>'
                         '<figcaption>{2}<br><code>{1}</code></figcaption></figure>'.format(
                             source, escape(name, quote=True), escape(summary),
                             button('captures/' + item['path'])))
        parts.append('</div>')
    parts.append('</article></main>' + dialog(base) + end())
    return ''.join(parts).encode('utf-8')
