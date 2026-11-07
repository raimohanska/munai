"""Read-only latest processed frame and its paired recognition state."""
from datetime import datetime, timezone
from html import escape
import json

from .web_layout import start, end
from .captures_web import prediction_layout
from .training import without_center_eggs
from .web_time import time_html, event_text


def label(value):
    return {'no_chicken': 'Empty', 'empty': 'Empty', 'unknown': 'Unknown / uncertain',
            'obscured': 'Obscured / uncertain', None: 'Not yet confirmed'}.get(value, value)


def state_html(state, raw=False):
    zones = state.get('zones') or {'nest': state}
    parts = ['<dl class="states">']
    order = ('left', 'top', 'center', 'right', 'bottom')
    for zone in sorted(zones, key=lambda name: order.index(name) if name in order else len(order)):
        item = zones[zone]
        occupant = item.get('label') if raw else item.get('occupant')
        if raw and item.get('obscured'):
            occupant = 'obscured'
        display = label(occupant)
        egg_line = ''
        if zone != 'center' and occupant != 'obscured' and not item.get('obscured'):
            count = item.get('egg_count')
            eggs = ('{} egg{}'.format(count, '' if count == 1 else 's') if count is not None and
                    (not raw or occupant in ('no_chicken', 'empty')) else 'Egg count unavailable')
            if occupant in ('no_chicken', 'empty') and count is not None:
                display = eggs if count else 'Empty'
            else:
                egg_line = '<br><span class="muted">{}</span>'.format(escape(eggs))
        parts.append('<div><dt>{}</dt><dd>{}{}</dd></div>'.format(
            escape(zone.capitalize()), escape(display), egg_line))
    parts.append('</dl>')
    return ''.join(parts)


def render(snapshot, base='', confirmations=None):
    observation = snapshot.get('observation')
    parts = [start('Status', 'Status', base, script=True), '<header><h1>Status</h1>',
              '<p>Capture: <strong>{}</strong> · MQTT: {}</p>'.format(
                  escape(snapshot['capture_state']), escape(snapshot['mqtt_state'])),
              '<p>Latest event: {}</p>'.format(escape(event_text(
                  snapshot.get('latest_event', (observation or {}).get('latest_event')) or 'No events yet'))),
             '<p><a href="">Refresh</a></p>']
    for message in snapshot.get('errors', []):
        parts.append('<p class="notice">{}</p>'.format(escape(message)))
    parts.append('</header><main>')
    if observation is None:
        parts.append('<article><h2>Waiting for a capture</h2><p>No successfully processed image '
                     'is available since this app started. Enable capture and check the app logs '
                     'if this persists.</p></article>')
    else:
        stamp = datetime.strptime(observation['timestamp'], '%Y%m%dT%H%M%S_%fZ').replace(tzinfo=timezone.utc)
        age = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
        parts.append('<article><h2>Latest processed capture</h2><p>{0}'
                      ' · {1} seconds ago</p>'.format(time_html(observation['timestamp']), age))
        if age > snapshot['stale_after']:
            parts.append('<p class="notice">Stale image.</p>')
        source = base + 'latest/' + observation['timestamp'] + '.jpg'
        if snapshot.get('has_image'):
            parts.append('<a href="{0}" target="_blank" rel="noopener"><img class="latest" '
                         'src="{0}" alt="Latest processed nest capture"></a>'.format(escape(source, quote=True)))
        else:
            parts.append('<p class="notice">The image for this observation is unavailable.</p>')
        raw = without_center_eggs(observation['raw_prediction'])
        # Whole-box raw status historically calls its label "occupant".
        if 'zones' not in raw:
            raw = dict(raw, label=raw.get('occupant'))
        confirm = (confirmations.button('latest/' + observation['timestamp'] + '.jpg',
                                       raw, prediction_layout(raw))
                   if confirmations is not None and snapshot.get('has_image') else '')
        parts.extend(['<h3>This image’s prediction</h3>', state_html(raw, raw=True), confirm,
                      '</article>',
                       '<article><h2>Confirmed state</h2>', state_html(observation), '</article>',
                      '<article><details><summary>Recognition diagnostics</summary><pre>{}</pre></details></article>'.format(
                          escape(json.dumps(dict(distance=observation.get('distance'),
                              margin=observation.get('margin'), prediction=raw), indent=2, ensure_ascii=False)))])
    parts.extend(['</main>', end()])
    return ''.join(parts).encode('utf-8')
