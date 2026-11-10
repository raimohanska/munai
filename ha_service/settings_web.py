"""Ingress-only live state reset controls."""
from html import escape

from .web_layout import start, end


def render(daily, token, base=''):
    parts = [start('Settings', 'Settings', base),
             '<script src="{}settings.js" defer></script>'.format(base),
             '<header><h1>Settings</h1></header><main><article><h2>State</h2>',
             '<p>Today: {}</p><dl class="states">'.format(escape(daily['date']))]
    for identity, count in sorted(daily['counts'].items()):
        parts.append('<div><dt>{}</dt><dd>{}</dd></div>'.format(escape(identity), count))
    parts.extend(['</dl><p class="notice">Clears live tracking and today’s totals. '
                  'Keeps references, photos and journal.</p>',
                  '<button type="button" id="reset-state" data-endpoint="{}settings/reset" '
                  'data-token="{}" data-date="{}">Reset state</button>'.format(
                      escape(base, quote=True), escape(token, quote=True), escape(daily['date'], quote=True)),
                  '<p id="reset-message" role="status" aria-live="polite"></p>',
                  '</article></main>', end()])
    return ''.join(parts).encode('utf-8')
