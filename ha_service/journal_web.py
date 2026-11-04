"""HA Ingress pages and explicit human review, separate from the transfer API."""
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import shutil
import re
import json
import hmac
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .journal import ID
from .storage import MAX_FILE_BYTES, VERSION_ID, safe_path
from .references_web import render as render_references
from .captures_web import render as render_captures
from .status_web import render as render_status
from .web_layout import start, end
from .review_web import button, dialog, CSS as REVIEW_CSS
from .reviews import Reviews
from .options import AppError
from .storage import Conflict

PAGE_SIZE = 20
CSS = '''
:root { color-scheme: light dark; font: 16px system-ui, sans-serif; }
body { margin: 0; background: light-dark(#f5f6f2,#131a18); }
.content { margin-left: 200px; padding: 24px; max-width: 1100px; }
.sidebar { position: fixed; inset: 0 auto 0 0; width: 200px; box-sizing: border-box;
 padding: 24px 16px; border-right: 1px solid #788b7950; background: light-dark(white,#1c2723); }
.brand { font-size: 1.5rem; font-weight: 700; padding: 0 12px 20px; }
.sidebar nav { display: flex; flex-direction: column; gap: 8px; padding: 0; }
.sidebar a { padding: 12px; text-decoration: none; border-radius: 8px; }
.sidebar a[aria-current] { background: #788b7930; font-weight: 700; }
.states { display: flex; flex-wrap: wrap; gap: 24px; } dt { font-weight: 600; } dd { margin: 8px 0; }
.latest { display: block; max-height: 65vh; object-fit: contain; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
header, article { padding: 24px; margin-bottom: 20px; border: 1px solid #788b7950;
border-radius: 18px; background: light-dark(white,#1c2723); }
h1 { margin-top: 0; } h2 { font-size: 1.35rem; margin: 8px 0; }
h3 { font-size: 1rem; margin-bottom: 8px; } p, figcaption { line-height: 1.5; }
a { color: light-dark(#235e45,#a0d6b3); } .muted, time { opacity: .8; font-size: .9rem; }
.photos { display: grid; grid-template-columns: repeat(auto-fit,minmax(210px,1fr)); gap: 12px; }
figure { margin: 0; } img { width: 100%; height: auto; border-radius: 8px; }
.frame { position: relative; } .frame img { display: block; }
.zone { position: absolute; border: 3px solid #ffb72b; box-sizing: border-box; pointer-events: none; }
.vertical-left { inset: 0 50% 0 0; } .vertical-right { inset: 0 0 0 50%; }
.vertical-center { inset: 0 25%; } .horizontal-top { inset: 0 0 50% 0; }
.horizontal-bottom { inset: 50% 0 0 0; } .horizontal-center { inset: 25% 0; }
figcaption { font-size: .85rem; overflow-wrap: anywhere; }
.photo-id { display: block; font-size: .75rem; margin-top: 6px; user-select: all; }
.event-details { margin-top: 16px; }
.event-details > summary { cursor: pointer; font-weight: 600; padding: 8px 0; }
nav { display: flex; justify-content: space-between; padding: 16px; }
.notice { border-left: 4px solid #c99b40; padding-left: 12px; }
@media(max-width: 700px) { .sidebar { position: static; width: auto; padding: 16px; }
 .brand { padding: 0 0 12px; } .sidebar nav { flex-direction: row; flex-wrap: wrap; }
 .content { margin: 0; padding: 10px; } header,article { padding: 16px; } }
''' + REVIEW_CSS
JS = '''
document.querySelectorAll('time').forEach(node => {
  const date = new Date(node.dateTime);
  if (!isNaN(date)) { node.textContent = date.toLocaleString(); node.title = node.dateTime; }
});
const active = document.querySelector('a[aria-current="page"]');
const status = active && active.textContent === 'Status';
if (status || !new URLSearchParams(location.search).get('page')) {
  setInterval(() => { if (!document.hidden && !document.querySelector('dialog[open], .event-details[open]') && (status || window.scrollY < 150)) location.reload(); }, status ? 15000 : 60000);
}
'''


def timestamp(identifier):
    return datetime.strptime(identifier[:23], '%Y%m%dT%H%M%S_%fZ').replace(tzinfo=timezone.utc).isoformat()


def time_html(identifier):
    stamp = escape(timestamp(identifier))
    return '<time datetime="{}">{}</time>'.format(stamp, stamp)


def render(journal, page, base=''):
    with journal.lock:
        paths = journal.entries()
        pages = max(1, (len(paths) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages - 1)
        entries = [journal.read_entry(path) for path in paths[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
        usage = journal.usage_bytes / (1024 * 1024)
    low_space = journal.store.options['minimum_free_mb']
    free = shutil.disk_usage(str(journal.root)).free / (1024 * 1024)
    parts = [start('Journal', 'Journal', base, script=True, reviews=True), '<header>',
             '<h1>Journal</h1>',
             '<p class="muted">{} events · {} days retention · archive {:.1f} MiB at last hourly cleanup</p>'.format(
                 len(paths), journal.store.options['journal_retention_days'], usage),
             '<a href="{}journal">Latest entries</a> · <a href="">Refresh</a>'.format(base)]
    if free < low_space + 16:
        parts.append('<p class="notice">Storage is low. Back up the archive and free space.</p>')
    parts.append('</header><main>')
    if not entries:
        parts.append('<article><h2>No journal events</h2></article>')
    for entry in entries:
        parts.append('<article><p class="muted">Confirmed {}</p><h2>{}</h2>'.format(
            time_html(entry['id']), escape(entry['text'])))
        parts.append('<details class="event-details"><summary>Details &amp; pictures</summary>')
        has_frames = False
        for group in entry['evidence']:
            if not group['frames']:
                continue
            has_frames = True
            parts.append('<h3>{}</h3><div class="photos">'.format(escape(group['role'])))
            for frame in group['frames']:
                source = base + 'images/' + frame['id'] + '.jpg'
                caption = ('Empty nest · {} eggs detected'.format(frame['egg_count'])
                            if frame['label'] == 'no_chicken' else 'Identity prediction: ' + frame['label'])
                if 'zone' in entry:
                    caption = entry['zone'].capitalize() + ' view · ' + caption
                overlay = ('<span class="zone {}-{}" aria-hidden="true"></span>'.format(
                    entry['layout'], entry['zone']) if 'zone' in entry else '')
                parts.append('<figure><div class="review-photo"><a href="{0}" target="_blank" rel="noopener">'
                             '<div class="frame"><img src="{0}" loading="lazy" alt="{1}">{3}</div></a>{5}</div>'
                             '<figcaption>{2}<br>{1}<span class="photo-id">Photo ID: '
                             '<code>{4}</code></span></figcaption></figure>'.format(
                                  source, escape(caption, quote=True), time_html(frame['id']), overlay,
                                   escape(frame['id']), button('images/' + frame['id'] + '.jpg',
                                                              entry.get('zone', 'whole'))))
            parts.append('</div>')
        present_roles = {group['role'] for group in entry['evidence'] if group['frames']}
        required = ({'Before visit / previous count', 'Identity confirmation', 'Egg count confirmation'}
                    if entry['text'].endswith(' laid an Egg') else
                    {'Before visit / previous count', 'Egg count confirmation'}
                    if entry['text'].endswith(' collected') else
                    {'Identity confirmation', 'Departure confirmation'}
                    if entry['text'].endswith(' left') else {'Identity confirmation'})
        if not has_frames or required - present_roles:
            parts.append('<p class="notice">Some evidence is unavailable.</p>')
        parts.append('</details></article>')
    parts.append('</main><nav>')
    if page:
        parts.append('<a href="?page={}">← Newer</a>'.format(page - 1))
    parts.append('<span>Page {} of {}</span>'.format(page + 1, pages))
    if page + 1 < pages:
        parts.append('<a href="?page={}">Older →</a>'.format(page + 1))
    parts.append('</nav>' + dialog(base) + end())
    return ''.join(parts).encode('utf-8')


class JournalServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address, journal, runtime=None):
        self.journal = journal
        self.runtime = runtime
        self.reviews = Reviews(journal, runtime.validate_reference if runtime else self.no_validator)
        super().__init__(address, JournalHandler)

    @staticmethod
    def no_validator(path):
        raise AppError('Recognition validation is unavailable. Reference was not activated.')

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address

    def handle_error(self, request, client_address):
        pass


class JournalHandler(BaseHTTPRequestHandler):
    server_version = 'Munai'
    sys_version = ''

    def log_message(self, format, *args):
        pass

    def response(self, status, data, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'none'; img-src 'self'; style-src 'self'; "
                         "script-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(data)

    def do_GET(self):
        # Supervisor's ingress proxy is the only trusted caller. Do not trust
        # spoofable forwarded headers; the port is not published on the host.
        if self.client_address[0] != '172.30.32.2':
            self.response(403, b'Home Assistant Ingress required.', 'text/plain')
            return
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise ValueError()
            if parsed.path == '/review':
                query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
                if set(query) != {'source'} or len(query['source']) != 1:
                    raise ValueError()
                value = self.server.reviews.context(query['source'][0])
                self.response(200, json.dumps(value).encode('utf-8'), 'application/json')
            elif parsed.path == '/review.js' and not parsed.query:
                self.response(200, Path(__file__).with_name('review.js').read_bytes(),
                              'text/javascript; charset=utf-8')
            elif parsed.path in ('/captures', '/captures/'):
                query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True) if parsed.query else {}
                if set(query) - {'date'} or len(query.get('date', [''])) != 1:
                    raise ValueError()
                day = query.get('date', [None])[0]
                if day is not None:
                    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
                        raise ValueError()
                    datetime.strptime(day, '%Y-%m-%d')
                base = '../' if parsed.path.endswith('/') else ''
                data = render_captures(self.server.journal.store, day=day, base=base)
                self.response(200, data, 'text/html; charset=utf-8')
            elif parsed.path.startswith('/captures/') and not parsed.query:
                relative = parsed.path[len('/captures/'):]
                store = self.server.journal.store
                if not store.valid_file('captures', relative):
                    raise ValueError()
                with store.lock:
                    data = store.read('captures', relative)
                self.response(200, data, 'image/jpeg')
            elif parsed.path in ('/', '/index.html', '/status', '/status/', '/journal', '/journal/', '/references', '/references/'):
                query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
                if set(query) - {'page'} or len(query.get('page', ['0'])) != 1:
                    raise ValueError()
                page = query.get('page', ['0'])[0]
                if not page.isdigit() or len(page) > 8:
                    raise ValueError()
                base = '../' if parsed.path not in ('/',) and parsed.path.endswith('/') else ''
                if parsed.path in ('/references', '/references/'):
                    data = render_references(self.server.journal.store, base=base)
                elif parsed.path in ('/journal', '/journal/'):
                    data = render(self.server.journal, int(page), base=base)
                else:
                    data = render_status(self.server.runtime.web_status(), base=base)
                self.response(200, data, 'text/html; charset=utf-8')
            elif parsed.path.startswith('/latest/') and not parsed.query:
                name = parsed.path[len('/latest/'):]
                if not re.fullmatch(r'[0-9]{8}T[0-9]{6}_[0-9]{6}Z\.jpg', name):
                    raise ValueError()
                data = self.server.runtime.web_image(name[:-4])
                if data is None:
                    raise FileNotFoundError()
                self.response(200, data, 'image/jpeg')
            elif parsed.path == '/journal.css' and not parsed.query:
                self.response(200, CSS.encode('utf-8'), 'text/css; charset=utf-8')
            elif parsed.path == '/journal.js' and not parsed.query:
                self.response(200, JS.encode('utf-8'), 'text/javascript; charset=utf-8')
            elif parsed.path.startswith('/references/') and not parsed.query:
                parts = parsed.path[len('/references/'):].split('/', 1)
                store = self.server.journal.store
                if (len(parts) != 2 or not VERSION_ID.fullmatch(parts[0])
                        or not store.valid_file('references', parts[1])):
                    raise ValueError()
                with store.lock:
                    if store.revision != parts[0]:
                        self.response(409, b'References changed; refresh the References page.', 'text/plain')
                        return
                    data = store.read('references', parts[1])
                self.response(200, data, 'image/jpeg')
            elif parsed.path.startswith('/images/') and not parsed.query:
                name = parsed.path[len('/images/'):]
                if not name.endswith('.jpg') or not ID.fullmatch(name[:-4]):
                    raise ValueError()
                with self.server.journal.lock:
                    path = safe_path(self.server.journal.root / 'images', name)
                    if path.stat().st_size > MAX_FILE_BYTES:
                        raise ValueError()
                    data = path.read_bytes()
                self.response(200, data, 'image/jpeg')
            else:
                self.response(404, b'Not found.', 'text/plain')
        except FileNotFoundError:
            if parsed.path == '/review':
                self.response(404, b'{"error":"Photo no longer available."}', 'application/json')
            else:
                self.response(404, b'Not found.', 'text/plain')
        except AppError as error:
            if parsed.path == '/review':
                self.response(400, json.dumps({'error': str(error)}).encode('utf-8'), 'application/json')
            else:
                self.response(500, b'Journal unavailable; check app storage and logs.', 'text/plain')
        except ValueError:
            self.response(400, b'Invalid journal request.', 'text/plain')
        except Exception:
            self.response(500, b'Journal unavailable; check app storage and logs.', 'text/plain')

    def do_POST(self):
        if self.client_address[0] != '172.30.32.2':
            self.response(403, b'Home Assistant Ingress required.', 'text/plain')
            return
        try:
            if self.path != '/review':
                raise AppError('Invalid review endpoint.')
            tokens = self.headers.get_all('X-Munai-Review', [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0], self.server.reviews.token):
                self.response(403, b'{"error":"Reopen review to authorize this action."}', 'application/json')
                return
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1 or
                    not lengths[0].isdigit() or not 1 <= int(lengths[0]) <= 4096 or
                    self.headers.get('Content-Type') != 'application/json'):
                raise AppError('A bounded JSON review request is required.')
            data = self.rfile.read(int(lengths[0]))
            if len(data) != int(lengths[0]):
                raise AppError('Incomplete review request.')
            request = json.loads(data.decode('utf-8'))
            if not isinstance(request, dict) or not isinstance(request.get('source'), str):
                raise AppError('Invalid review request.')
            value = self.server.reviews.save(request)
            self.response(200, json.dumps(value).encode('utf-8'), 'application/json')
        except Conflict as error:
            self.response(409, json.dumps({'error': str(error)}).encode('utf-8'), 'application/json')
        except FileNotFoundError:
            self.response(404, b'{"error":"Photo no longer available."}', 'application/json')
        except (AppError, ValueError, KeyError, TypeError) as error:
            text = str(error) if isinstance(error, AppError) else 'Invalid review request.'
            self.response(400, json.dumps({'error': text}).encode('utf-8'), 'application/json')
        except Exception:
            self.response(500, b'{"error":"Review could not be completed. Reopen the popup to check its status."}', 'application/json')
