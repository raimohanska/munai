"""Offline split-presence overlays, with no capture, relabelling or live updates."""
import argparse
from html import escape
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from box_split import detect
from data_layout import reference_directory
from recognize_chickens import background_mask


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--references', type=Path, required=True,
                        help='Reviewed full-frame references, before half-frame conversion')
    parser.add_argument('--split', choices=('vertical', 'horizontal'), default='vertical')
    parser.add_argument('--output', type=Path, required=True, help='New private report directory')
    parser.add_argument('--include-references', action='store_true')
    args = parser.parse_args()
    cv2.setNumThreads(1)
    root = reference_directory(args.references)
    mask = background_mask(root)
    if mask is None:
        parser.error('Full-frame reviewed empty references are required')
    paths = sorted(p for p in args.directory.rglob('*.jpg') if p.is_file() and not p.is_symlink())
    if args.include_references:
        paths = sorted(root.glob('*/*.jpg')) + paths
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'previews').mkdir()
    records, parts = [], []
    for index, path in enumerate(paths):
        result = detect(path, args.split, mask)
        result['source'] = str(path)
        records.append(result)
        image = cv2.imread(str(path))
        image = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
        if args.split == 'vertical':
            cv2.line(image, (320, 0), (320, 359), (255, 180, 0), 1)
        else:
            cv2.line(image, (0, 180), (639, 180), (255, 180, 0), 1)
        for body in result['bodies']:
            x, y, w, h = body['box']
            color = (0, 180, 255) if body['zone'] == 'center' else (0, 230, 80)
            cv2.rectangle(image, (x, y), (x + w - 1, y + h - 1), color, 2)
            cv2.putText(image, body['zone'], (x + 3, max(15, y + 15)),
                        cv2.FONT_HERSHEY_SIMPLEX, .55, color, 2)
        preview = 'previews/{:04d}.jpg'.format(index)
        if not cv2.imwrite(str(args.output / preview), image):
            raise OSError('Could not write review preview')
        zones = ', '.join(body['zone'] for body in result['bodies']) or 'no detected body'
        parts.append('<article><h2>{}</h2><a href="{}"><img loading="lazy" src="{}" alt="Presence overlay"></a>'
                     '<p>{}{}</p><p>Egg counting blocked: {}</p><p class="source">{}</p></article>'.format(
                         escape(path.name), preview, preview, escape(zones),
                         ' · AMBIGUOUS' if result['ambiguous'] else '',
                         escape(', '.join(result['blocked_for_eggs']) or 'neither half (candidate only)'),
                         escape(str(path))))
    (args.output / 'detections.json').write_text(json.dumps(records, indent=2))
    (args.output / 'index.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>Split presence review</title>'
        '<style>body{font:16px system-ui;background:#14202b;color:#edf4fa;padding:20px}'
        'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}'
        'article{background:#263543;padding:12px}h2{font-size:16px}img{width:100%}'
        '.source{font-size:12px;overflow-wrap:anywhere}</style><h1>Split presence prototype</h1>'
        '<p>Presence and spatial grouping only; no identities, counts, or events are inferred. '
        'A center candidate may be a crossing hen or overlapping hens, not necessarily a perched hen. '
        'A clear candidate is not proof that eggs are visible. Calibration frames are not independent validation. '
        'Originals and review decisions are unchanged.</p><main>' + ''.join(parts) + '</main></html>')
    print('Reviewed {} images; report: {}'.format(len(records), args.output / 'index.html'))


if __name__ == '__main__':
    main()
