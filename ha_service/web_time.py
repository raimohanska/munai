"""UI timestamps in the service's Home Assistant local timezone."""
from datetime import datetime, timezone
from html import escape


def local_time(identifier):
    return datetime.strptime(identifier[:23], '%Y%m%dT%H%M%S_%fZ').replace(
        tzinfo=timezone.utc).astimezone()


def time_html(identifier, clock=False):
    local = local_time(identifier)
    return '<time datetime="{}">{}</time>'.format(
        escape(local.isoformat()), local.strftime('%H:%M:%S' if clock else '%Y-%m-%d %H:%M:%S'))


def event_text(text):
    return text[len('Center: '):] if text.startswith('Center: ') else text
