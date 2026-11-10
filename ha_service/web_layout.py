"""Shared Ingress-relative page shell and navigation."""
from html import escape


def start(title, active, base='', script=False, reviews=False):
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<title>MunAI · {}</title>'.format(escape(title)),
             '<link rel="stylesheet" href="{}journal.css">'.format(base)]
    parts.append('<script src="{0}confirm.js" data-confirm-api="{0}confirm" defer></script>'.format(base))
    if script:
        parts.append('<script src="{}journal.js" defer></script>'.format(base))
    if reviews:
        parts.append('<script src="{}review.js" defer></script>'.format(base))
    parts.append('</head><body><aside class="sidebar"><div class="brand">MunAI</div>'
                 '<nav aria-label="Main navigation">')
    for label, path in (('Status', './'), ('Journal', 'journal'),
                        ('Captures', 'captures'), ('References', 'references'), ('Settings', 'settings')):
        parts.append('<a href="{}{}"{}>{}</a>'.format(
            base, path, ' aria-current="page"' if active == label else '', label))
    parts.append('</nav></aside><div class="content">')
    return ''.join(parts)


def end():
    return '</div></body></html>'
