#!/usr/bin/env python3
"""Offline, reference-based chicken recognition using OpenCV, not a trained model.

Never accesses the camera or .env. All measurements use 640x360 coordinates.
"""
from __future__ import print_function

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys

import cv2
import numpy as np

from sort_captures import capture_files, chicken_region
from data_layout import RESERVED, reference_directory


FEATURES = ('brightness_25', 'brightness_50', 'brightness_75',
            'dark_fraction', 'texture_50', 'texture_75', 'edge_fraction')
# Fixed measurement tolerances, not fitted weights. Brightness is relative to
# the visible straw; texture and edges distinguish uniform vs patterned feathers.
SCALES = np.array([0.18, 0.18, 0.18, 0.25, 4.0, 5.0, 0.12])


class References(list):
    """Reviewed identities plus background calibration, never an identity feature."""

    def __init__(self, background_mask=None):
        super(References, self).__init__()
        self.background_mask = background_mask


def image_statistics(path, size=(640, 360)):
    image = path if isinstance(path, np.ndarray) else cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Cannot decode image: ' + str(path))
    gray = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    pixels = gray.astype(np.float32)
    mean = cv2.blur(pixels, (7, 7))
    std = np.sqrt(np.maximum(0, cv2.blur(pixels * pixels, (7, 7)) - mean * mean))
    return gray, pixels, std


def background_mask(directory, texture_threshold=8):
    """Exclude pixels consistently smooth in explicitly reviewed empty images.

    Walls/overlays must not count as feathers. Majority voting tolerates straw
    changes and small eggs. Only reviewed references/empty/ is used, never captures.
    Recomputed when references load; no disk cache or learned identity positions.
    """
    folder = Path(directory) / 'empty'
    if not folder.is_dir() or folder.is_symlink():
        return None
    votes = np.zeros((360, 640), np.uint32)
    count = 0
    for path in capture_files(folder):
        unused_gray, unused_pixels, std = image_statistics(path)
        votes += (std < texture_threshold)
        count += 1
    if not count:
        return None
    mask = votes > count // 2
    if np.count_nonzero(~mask) < 1000:
        raise ValueError('Empty references lack measurable background texture')
    return mask


def measure(path, chicken_area=4500, texture_threshold=8, background_mask=None, size=(640, 360)):
    gray, pixels, std = image_statistics(path, size)
    valid = np.full(gray.shape, 255, np.uint8)
    if background_mask is not None:
        valid[background_mask] = 0
    smooth = ((std < texture_threshold) & (valid != 0)).astype(np.uint8) * 255
    smooth = cv2.morphologyEx(smooth, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contour, area, chicken = chicken_region(smooth, valid, chicken_area)
    result = {'area': area, 'chicken': chicken,
              'box': None, 'features': None}
    if not chicken:
        return result
    # Fill holes, but not a convex hull (which can swallow a lot of straw).
    mask = np.zeros(gray.shape, np.uint8)
    cv2.drawContours(mask, [contour], -1, 255, -1)
    mask = cv2.erode(mask, np.ones((9, 9), np.uint8))
    mask = cv2.bitwise_and(mask, valid)
    inside = mask != 0
    outside = (valid != 0) & (cv2.dilate(mask, np.ones((31, 31), np.uint8)) == 0)
    if np.count_nonzero(inside) < 500 or np.count_nonzero(outside) < 1000:
        # Insufficient visible body/background for a meaningful comparison.
        return result
    background = max(float(np.median(pixels[outside])), 1.0)
    brightness = pixels[inside] / background
    edges = cv2.Canny(gray, 40, 100)
    values = list(np.percentile(brightness, [25, 50, 75]))
    values += [float(np.mean(brightness < 0.55))]
    values += list(np.percentile(std[inside], [50, 75]))
    values += [float(np.mean(edges[inside] != 0))]
    result['features'] = [float(value) for value in values]
    result['box'] = [int(value) for value in cv2.boundingRect(contour)]
    return result


def load_references(directory, chicken_area=4500, texture_threshold=8):
    directory = reference_directory(directory)
    references = References(background_mask(directory, texture_threshold))
    for folder in sorted(directory.iterdir()):
        if (not folder.is_dir() or folder.is_symlink() or folder.name.startswith('.')
                or folder.name.lower() in RESERVED):
            continue
        for path in capture_files(folder):
            measurement = measure(path, chicken_area, texture_threshold, references.background_mask)
            if measurement['features'] is None:
                raise ValueError('Reference has no measurable chicken: ' + str(path))
            references.append((folder.name, path, measurement))
    if len(set(item[0] for item in references)) < 2:
        raise ValueError('Need reference images in at least two named chicken folders')
    return references


def identify(measurement, references, max_distance=1.25, min_margin=0.25,
             exclude=None):
    """Compare appearance only; never use box coordinates, area, or nest zone.

    The chicken flag gates presence, but size/location do not identify a hen.
    Missing reference coverage of a position is not evidence of identity.
    """
    if not measurement['chicken']:
        return {'label': 'no_chicken', 'nearest': '', 'distance': None,
                'margin': None, 'reference': ''}
    if measurement['features'] is None:
        return {'label': 'unknown', 'nearest': '', 'distance': None,
                'margin': None, 'reference': ''}
    scores = {}
    for label, path, reference in references:
        if path == exclude:
            continue
        delta = (np.array(measurement['features']) - reference['features']) / SCALES
        distance = float(np.sqrt(np.mean(delta * delta)))
        if label not in scores or distance < scores[label][0]:
            scores[label] = (distance, path)
    ranked = sorted((value[0], label, value[1]) for label, value in scores.items())
    if len(ranked) < 2:
        raise ValueError('Need at least two identities to compare')
    distance, nearest, path = ranked[0]
    margin = ranked[1][0] - distance
    label = nearest if distance <= max_distance and margin >= min_margin else 'unknown'
    return {'label': label, 'nearest': nearest, 'distance': distance,
            'margin': margin, 'reference': str(path)}


def contact_sheet(items, destination):
    """Annotated previews only; never copy/move/modify original captures."""
    width, height = 320, 210
    canvas = np.full((height * ((len(items) + 3) // 4), width * 4, 3), 245, np.uint8)
    for index, item in enumerate(items):
        path, measurement, prediction = item
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError('Cannot decode image: ' + str(path))
        image = cv2.resize(image, (320, 180), interpolation=cv2.INTER_AREA)
        if measurement['box']:
            x, y, w, h = measurement['box']
            cv2.rectangle(image, (x // 2, y // 2), ((x + w) // 2, (y + h) // 2), (0, 220, 0), 1)
        x, y = (index % 4) * width, (index // 4) * height
        canvas[y:y + 180, x:x + 320] = image
        caption = '{} d={:.2f} gap={:.2f}'.format(
            prediction['nearest'], prediction['distance'], prediction['margin'])
        cv2.putText(canvas, path.stem[:15], (x + 3, y + 192), cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 0, 0), 1)
        cv2.putText(canvas, caption, (x + 3, y + 205), cv2.FONT_HERSHEY_SIMPLEX, .4, (0, 0, 0), 1)
    if not cv2.imwrite(str(destination), canvas):
        raise OSError('Cannot write contact sheet: ' + str(destination))


def check_labels(directory, references, output, args):
    """Calibration checks, not independent accuracy measurements.

    Exclude the image itself when checking identities. A singleton identity
    cannot be evaluated this way, and is explicitly marked as untested.
    """
    with (output / 'label-check.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['path', 'expected', 'predicted', 'check'])
        for label, path, measurement in references:
            if sum(r[0] == label for r in references) == 1:
                writer.writerow([str(path), label, '', 'untested: only one reference'])
                continue
            prediction = identify(measurement, references, args.max_distance,
                                  args.min_margin, exclude=path)
            writer.writerow([str(path), label, prediction['label'], 'self excluded; other frames may be correlated'])
        for category in ('empty', 'eggs'):
            folder = directory / category
            if not folder.is_dir() or folder.is_symlink():
                continue
            for path in capture_files(folder):
                measurement = measure(path, args.chicken_area, args.texture_threshold,
                                      references.background_mask)
                prediction = identify(measurement, references, args.max_distance, args.min_margin)
                writer.writerow([str(path), 'no_chicken', prediction['label'], 'labeled negative'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default=str(Path(__file__).resolve().parent / 'captures'))
    parser.add_argument('--output', default='recognition', help='New report directory; must not exist')
    parser.add_argument('--references', help='Managed reference root (default: references/ beside captures/)')
    parser.add_argument('--max-distance', type=float, default=1.25)
    parser.add_argument('--min-margin', type=float, default=0.25)
    parser.add_argument('--chicken-area', type=float, default=4500)
    parser.add_argument('--texture-threshold', type=float, default=8)
    parser.add_argument('--examples', type=int, default=24, help='Candidate previews per identity')
    args = parser.parse_args()
    if (args.max_distance <= 0 or args.min_margin < 0 or args.chicken_area <= 0
            or args.texture_threshold <= 0 or args.examples < 1):
        parser.error('Thresholds/examples must be positive (margin may be zero)')
    directory = Path(args.directory).resolve()
    output = Path(args.output).resolve()
    cv2.setNumThreads(1)
    reference_root = Path(args.references).resolve() if args.references else directory.parent / 'references'
    references = load_references(reference_root, args.chicken_area, args.texture_threshold)
    if references.background_mask is None:
        raise ValueError('Add reviewed no-hen JPEGs to references/empty/ before recognition')
    output.mkdir(parents=True, exist_ok=False)
    check_labels(directory, references, output, args)
    with (output / 'references.json').open('w') as handle:
        json.dump({'features': FEATURES, 'scales': SCALES.tolist(),
                   'settings': vars(args),
                   'references': [{'label': label, 'path': str(path), 'measurement': measurement}
                                  for label, path, measurement in references]}, handle, indent=2)
    counts = {}
    candidates = {}
    with (output / 'predictions.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['path', 'label', 'nearest', 'distance', 'margin', 'reference', 'area', 'box'] + list(FEATURES))
        for path in capture_files(directory / 'pending'):
            measurement = measure(path, args.chicken_area, args.texture_threshold,
                                  references.background_mask)
            prediction = identify(measurement, references, args.max_distance, args.min_margin)
            counts[prediction['label']] = counts.get(prediction['label'], 0) + 1
            writer.writerow([str(path), prediction['label'], prediction['nearest'],
                             prediction['distance'], prediction['margin'], prediction['reference'],
                             measurement['area'], measurement['box']] + (measurement['features'] or []))
            if prediction['label'] not in ('no_chicken', 'unknown'):
                candidates.setdefault(prediction['label'], []).append((path, measurement, prediction))
    for label, items in sorted(candidates.items()):
        items.sort(key=lambda item: item[2]['distance'])
        # Prefer examples at least ten minutes apart, avoiding a sheet consisting
        # entirely of adjacent frames from a single visit.
        selected = []
        for item in items:
            stamp = datetime.strptime(item[0].name[:15], '%Y%m%dT%H%M%S')
            if all(abs((stamp - datetime.strptime(other[0].name[:15], '%Y%m%dT%H%M%S')).total_seconds()) >= 600
                   for other in selected):
                selected.append(item)
            if len(selected) >= args.examples:
                break
        contact_sheet(selected, output / (label + '-candidates.jpg'))
        with (output / (label + '-candidates.txt')).open('w') as handle:
            handle.write('\n'.join(str(item[0]) for item in selected) + '\n')
    print('References: ' + ', '.join('{}={}'.format(label, sum(r[0] == label for r in references))
                                      for label in sorted(set(r[0] for r in references))))
    print('Predictions (unverified): ' + ', '.join('{}={}'.format(k, v) for k, v in sorted(counts.items())))
    print('Reports: ' + str(output))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print('Recognition failed: {}'.format(error), file=sys.stderr)
        sys.exit(1)
