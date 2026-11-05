"""Stored capture gallery with explicit review, paged by local capture day."""
from html import escape
import json

from data_layout import MAX_HISTORY_FILES
from .options import AppError
from .storage import MAX_FILE_BYTES, safe_path
from .web_layout import start, end
from .review_web import button, dialog
from .web_time import local_time, time_html


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
        captured = local_time(stamp)
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
    def obscured(item):
        return item.get('obscured') or item.get('label', item.get('occupant')) == 'obscured'
    if obscured(raw) or any(obscured(item) for item in (zones or {}).values()):
        return summary
    eggs = raw.get('egg_count')
    if type(eggs) is not int or eggs < 0:
        return summary
    return '{} ({} egg{})'.format(summary, eggs, '' if eggs == 1 else 's')


def prediction(store, stamp):
    """Use only the exact paired result; never infer state from the folder."""
    try:
        with store.lock:
            result = json.loads(store.read('results', stamp + '.json').decode('utf-8'))
        if result.get('timestamp') != stamp or not isinstance(result.get('raw_prediction'), dict):
            return None
        return result['raw_prediction']
    except (OSError, ValueError, TypeError, AttributeError, AppError):
        return None


def interpretation(store, stamp):
    raw = prediction(store, stamp)
    return state_summary(raw) if raw is not None else 'Interpretation unavailable'


def prediction_layout(raw):
    zones = raw.get('zones')
    if not zones:
        return 'none'
    return 'vertical' if 'left' in zones or 'right' in zones else 'horizontal'


def render(store, day=None, base='', confirmations=None):
    with store.lock:
        items = inventory(store)
        enabled = store.options['store_captures']
    days = sorted({item['day'] for item in items})
    if day is None and days:
        day = days[-1]
    selected = sorted((item for item in items if item['day'] == day),
                      key=lambda item: (item['stamp'], item['path']), reverse=True)
    parts = [start('Captures', 'Captures', base, reviews=True), '<header><h1>Stored captures</h1>',
             '<p><a href="{}captures">Newest stored day</a></p>'.format(base)]
    if not enabled:
        parts.append('<p class="notice">Ordinary capture storage is disabled. Enable store_captures '
                     'in the app configuration to retain future capture photos.</p>')
    parts.append('<nav class="day-navigation" aria-label="Capture days">')
    older = [value for value in days if value < day] if day else []
    newer = [value for value in days if value > day] if day else []
    if older:
        parts.append('<a href="?date={}">← Previous day</a>'.format(older[-1]))
    else:
        parts.append('<span></span>')
    parts.append('<h2>{}</h2>'.format(escape(day or 'No stored days')))
    if newer:
        parts.append('<a href="?date={}">Next day →</a>'.format(newer[0]))
    else:
        parts.append('<span></span>')
    parts.append('</nav><p>{} stored photos</p></header><main><article>'.format(len(selected)))
    if not selected:
        parts.append('<h2>No stored captures{}</h2>'.format(' for this day' if day else ''))
    else:
        parts.append('<div class="photos">')
        for item in selected:
            source = escape(base + 'captures/' + item['path'], quote=True)
            name = item['path'].rsplit('/', 1)[-1]
            raw = prediction(store, item['stamp'])
            summary = state_summary(raw) if raw is not None else 'Interpretation unavailable'
            confirm = (confirmations.button('captures/' + item['path'], raw, prediction_layout(raw))
                       if confirmations is not None and raw is not None else '')
            parts.append('<figure><h3>{5}</h3><div class="review-photo"><a href="{0}" target="_blank" rel="noopener">'
                         '<img src="{0}" loading="lazy" alt="{1}"></a>{3}</div>'
                          '<figcaption>{2}<br><code>{1}</code></figcaption>{4}</figure>'.format(
                             source, escape(name, quote=True), escape(summary),
                               button('captures/' + item['path']), confirm, time_html(item['stamp'], clock=True)))
        parts.append('</div>')
    parts.append('</article></main>' + dialog(base) + end())
    return ''.join(parts).encode('utf-8')
