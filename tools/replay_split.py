"""Replay saved images into a NEW local split journal. No HA/camera/MQTT access."""
import argparse
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from data_layout import STAMP
from ha_service.journal_web import CSS, JS, render
from ha_service.options import validate
from ha_service.runtime import Runtime
from ha_service.split_recognition import load
from ha_service.storage import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--references', type=Path, required=True)
    parser.add_argument('--split', choices=('vertical', 'horizontal'), required=True)
    parser.add_argument('--output', type=Path, required=True, help='New private output; must not exist')
    args = parser.parse_args()
    if args.directory.resolve() == args.output.resolve() or args.directory.resolve() in args.output.resolve().parents:
        parser.error('Replay output must be outside the source capture directory')
    cv2.setNumThreads(1)
    paths = sorted((p for p in args.directory.rglob('*.jpg') if p.is_file() and not p.is_symlink()
                    and re.fullmatch(STAMP + r'\.jpg', p.name)), key=lambda p: p.stem)
    if not paths or len({p.stem for p in paths}) != len(paths):
        parser.error('Need uniquely timestamped original full-frame images')
    options = validate({'box_split': args.split, 'capture_enabled': False, 'mqtt_enabled': False})
    refs = load(args.references, options)
    args.output.mkdir(parents=True, exist_ok=False)
    store = Store(args.output, options)
    runtime = Runtime(options, store)
    runtime.journal.retain(runtime.nest, force=True)
    previous = None
    from datetime import datetime
    for path in paths:
        stamp = datetime.strptime(path.stem, '%Y%m%dT%H%M%S_%fZ')
        if previous is not None and (stamp - previous).total_seconds() > options['capture_interval'] * 1.5:
            runtime.nest.reset_confirmation()
        prediction = runtime.predict(path, refs)
        frame = runtime.journal.save_frame(path, prediction, stamp=path.stem)
        runtime.observe(path.stem, prediction, frame)
        previous = stamp
    journal = runtime.journal
    journal.retain(runtime.nest, force=True)
    pages = max(1, (len(journal.entries()) + 19) // 20)
    for page in range(pages):
        html = render(journal, page).decode('utf-8')
        # Static links, not dynamic ?page routes. Historical replay is labelled.
        html = html.replace('<h1>Munai journal</h1>', '<h1>Offline split replay — reconstructed decisions</h1>')
        html = html.replace('The story of your nest, with the evidence behind each decision.',
                            'New algorithm on retained images, not recovered historical events. '
                            'Correlated reference images may appear in this replay; not independent accuracy evidence.')
        html = re.sub(r'href="\?page=(\d+)"', lambda m: 'href="page-{}.html"'.format(m.group(1)), html)
        (journal.root / ('page-{}.html'.format(page))).write_text(html)
        if page == 0:
            (journal.root / 'index.html').write_text(html)
    (journal.root / 'journal.css').write_text(CSS)
    (journal.root / 'journal.js').write_text(JS.split('if (!new URLSearchParams')[0])
    print('Replayed {} images; {} journal events; results in {}; page {}'.format(
        len(paths), len(journal.entries()), store.root / 'results', journal.root / 'index.html'))


if __name__ == '__main__':
    main()
