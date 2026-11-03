"""Read-only HA Ingress UI; deliberately separate from the transfer API."""
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import shutil
from urllib.parse import parse_qs, urlsplit

from .journal import ID
from .storage import MAX_FILE_BYTES, VERSION_ID, safe_path
from .references_web import render as render_references

PAGE_SIZE = 20
CSS = '''
:root { color-scheme: light dark; font: 16px system-ui, sans-serif; }
body { margin: auto; padding: 24px; max-width: 1100px; background: light-dark(#f5f6f2,#131a18); }
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
nav { display: flex; justify-content: space-between; padding: 16px; }
.notice { border-left: 4px solid #c99b40; padding-left: 12px; }
@media(max-width: 500px) { body { padding: 10px; } header,article { padding: 16px; } }
'''
JS = '''
document.querySelectorAll('time').forEach(node => {
  const date = new Date(node.dateTime);
  if (!isNaN(date)) { node.textContent = date.toLocaleString(); node.title = node.dateTime; }
});
if (!new URLSearchParams(location.search).get('page')) {
  setInterval(() => { if (!document.hidden && window.scrollY < 150) location.reload(); }, 60000);
}
'''


def timestamp(identifier):
    return datetime.strptime(identifier[:23], '%Y%m%dT%H%M%S_%fZ').replace(tzinfo=timezone.utc).isoformat()


def time_html(identifier):
    stamp = escape(timestamp(identifier))
    return '<time datetime="{}">{}</time>'.format(stamp, stamp)


def render(journal, page):
    with journal.lock:
        paths = journal.entries()
        pages = max(1, (len(paths) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages - 1)
        entries = [journal.read_entry(path) for path in paths[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
        usage = journal.usage_bytes / (1024 * 1024)
    low_space = journal.store.options['minimum_free_mb']
    free = shutil.disk_usage(str(journal.root)).free / (1024 * 1024)
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<title>Munai journal</title><link rel="stylesheet" href="journal.css">',
             '<script src="journal.js" defer></script></head><body><header>',
             '<h1>Munai journal</h1><p>The story of your nest, with the evidence behind each decision.</p>',
             '<p class="muted">{} events · {} days retention · archive {:.1f} MiB at last hourly cleanup</p>'.format(
                 len(paths), journal.store.options['journal_retention_days'], usage),
             '<p class="muted">Times are shown in your browser’s timezone. Event times indicate confirmation, '
             'not the exact moment of laying. Predictions are not reviewed identities.</p>',
             '<a href="./">Latest entries</a> · <a href="references">References</a> · <a href="">Refresh</a>']
    if free < low_space + 16:
        parts.append('<p class="notice">Storage is low. New observations may stop to protect the disk reserve. '
                     'Back up the archive and free space; younger journal entries are not automatically discarded.</p>')
    parts.append('</header><main>')
    if not entries:
        parts.append('<article><h2>Your journal starts here</h2><p>Confirmed arrivals, departures, laying '
                     'and collections will appear here with their supporting photos. Existing historical '
                     'events cannot be reconstructed.</p></article>')
    for entry in entries:
        parts.append('<article><p class="muted">Confirmed {}</p><h2>{}</h2>'.format(
            time_html(entry['id']), escape(entry['text'])))
        has_frames = False
        for group in entry['evidence']:
            if not group['frames']:
                continue
            has_frames = True
            parts.append('<h3>{}</h3><div class="photos">'.format(escape(group['role'])))
            for frame in group['frames']:
                source = 'images/' + frame['id'] + '.jpg'
                caption = ('Empty nest · {} eggs detected'.format(frame['egg_count'])
                            if frame['label'] == 'no_chicken' else 'Identity prediction: ' + frame['label'])
                if 'zone' in entry:
                    caption = entry['zone'].capitalize() + ' view · ' + caption
                overlay = ('<span class="zone {}-{}" aria-hidden="true"></span>'.format(
                    entry['layout'], entry['zone']) if 'zone' in entry else '')
                parts.append('<figure><a href="{0}" target="_blank" rel="noopener">'
                             '<div class="frame"><img src="{0}" loading="lazy" alt="{1}">{3}</div></a>'
                             '<figcaption>{2}<br>{1}<span class="photo-id">Photo ID: '
                             '<code>{4}</code></span></figcaption></figure>'.format(
                                  source, escape(caption, quote=True), time_html(frame['id']), overlay,
                                  escape(frame['id'])))
            parts.append('</div>')
        present_roles = {group['role'] for group in entry['evidence'] if group['frames']}
        required = ({'Before visit / previous count', 'Identity confirmation', 'Egg count confirmation'}
                    if entry['text'].endswith(' laid an Egg') else
                    {'Before visit / previous count', 'Egg count confirmation'}
                    if entry['text'].endswith(' collected') else
                    {'Identity confirmation', 'Departure confirmation'}
                    if entry['text'].endswith(' left') else {'Identity confirmation'})
        if not has_frames or required - present_roles:
            parts.append('<p class="notice">Some evidence predates journal support or is unavailable. '
                         'No historical photos have been fabricated.</p>')
        parts.append('</article>')
    parts.append('</main><nav>')
    if page:
        parts.append('<a href="?page={}">← Newer</a>'.format(page - 1))
    parts.append('<span>Page {} of {}</span>'.format(page + 1, pages))
    if page + 1 < pages:
        parts.append('<a href="?page={}">Older →</a>'.format(page + 1))
    parts.append('</nav></body></html>')
    return ''.join(parts).encode('utf-8')


class JournalServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, address, journal):
        self.journal = journal
        super().__init__(address, JournalHandler)

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
                         "script-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
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
            if parsed.path in ('/', '/index.html', '/references', '/references/'):
                query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
                if set(query) - {'page'} or len(query.get('page', ['0'])) != 1:
                    raise ValueError()
                page = query.get('page', ['0'])[0]
                if not page.isdigit() or len(page) > 8:
                    raise ValueError()
                data = (render_references(self.server.journal.store, int(page),
                                          base='../' if parsed.path.endswith('/') else '')
                        if parsed.path in ('/references', '/references/') else
                        render(self.server.journal, int(page)))
                self.response(200, data, 'text/html; charset=utf-8')
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
            self.response(404, b'Not found.', 'text/plain')
        except ValueError:
            self.response(400, b'Invalid journal request.', 'text/plain')
        except Exception:
            self.response(500, b'Journal unavailable; check app storage and logs.', 'text/plain')
