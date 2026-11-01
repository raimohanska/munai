#!/usr/bin/env python3
"""Experimental offline visible-egg counter. No camera, credentials, or HA access.

Measurements use 640x360 coordinates. Predictions are review aids, not laying
events or ground truth. Compatible with Python 3.5 and OpenCV 3/4.
"""
from __future__ import print_function

import argparse
import csv
from datetime import datetime
import math
from pathlib import Path
import sys

import cv2
import numpy as np

from data_layout import reference_directory
from recognize_chickens import background_mask, measure


def split_region(mask):
    """Separate touching smooth objects at distinct distance-transform peaks."""
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    maximum = float(distance.max())
    if maximum < 6:
        return []
    peaks = ((distance >= cv2.dilate(distance, np.ones((21, 21), np.uint8))) &
             (distance >= max(6, maximum * 0.45))).astype(np.uint8)
    count, markers = cv2.connectedComponents(peaks)
    if count <= 2:
        return [mask]
    # Partition at the nearest peak center. This avoids straw edges pulling a
    # watershed boundary across an egg. Overlapping/occluded eggs remain estimates.
    candidates = []
    for label in range(1, count):
        ys, xs = np.where(markers == label)
        candidates.append((float(distance[markers == label].max()),
                           float(xs.mean()), float(ys.mean())))
    centers = []
    for radius, x, y in sorted(candidates, reverse=True):
        # Quantized distance transforms can have disconnected, almost identical
        # maxima on one egg. Merge nearby peaks before partitioning.
        if all((x - cx) ** 2 + (y - cy) ** 2 >= max(12, radius) ** 2
               for cx, cy in centers):
            centers.append((x, y))
    if len(centers) == 1:
        return [mask]
    yy, xx = np.indices(mask.shape)
    distances = [(xx - x) ** 2 + (yy - y) ** 2 for x, y in centers]
    nearest = np.argmin(distances, axis=0)
    return [((nearest == label) & (mask != 0)).astype(np.uint8) * 255
            for label in range(len(centers))]


def detect_eggs(gray, texture_threshold=8, min_area=500, max_area=3500):
    """Find smooth, convex, egg-sized ellipses; never use nest position."""
    gray = cv2.resize(gray, (640, 360), interpolation=cv2.INTER_AREA)
    pixels = gray.astype(np.float32)
    mean = cv2.blur(pixels, (7, 7))
    std = np.sqrt(np.maximum(0, cv2.blur(pixels * pixels, (7, 7)) - mean * mean))
    smooth = (std < texture_threshold).astype(np.uint8) * 255
    smooth = cv2.morphologyEx(smooth, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    contours = cv2.findContours(smooth, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    detections = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        # Do not try to count clipped objects or split walls into egg-sized parts.
        if (x == 0 or y == 0 or x + w >= 640 or y + h >= 360 or
                cv2.contourArea(contour) < min_area or
                cv2.contourArea(contour) > 4 * max_area):
            continue
        region = np.zeros((h + 2, w + 2), np.uint8)
        local_contour = contour - np.array([[[x - 1, y - 1]]], np.int32)
        cv2.drawContours(region, [local_contour], -1, 255, -1)
        for part in split_region(region):
            pieces = cv2.findContours(part, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
            if not pieces:
                continue
            piece = max(pieces, key=cv2.contourArea)
            piece = piece + np.array([[[x - 1, y - 1]]], np.int32)
            area = cv2.contourArea(piece)
            if not min_area <= area <= max_area or len(piece) < 5:
                continue
            perimeter = cv2.arcLength(piece, True)
            circularity = 4 * np.pi * area / max(perimeter * perimeter, 1)
            solidity = area / max(cv2.contourArea(cv2.convexHull(piece)), 1)
            ellipse = cv2.fitEllipse(piece)
            axes = ellipse[1]
            aspect = min(axes) / max(max(axes), 1)
            if circularity >= 0.60 and solidity >= 0.88 and aspect >= 0.55:
                detections.append({'box': [int(v) for v in cv2.boundingRect(piece)],
                                   'ellipse': ellipse, 'area': float(area)})
    return sorted(detections, key=lambda item: item['box'])


def count_image(path, exclusions, chicken_area=4500, texture_threshold=8,
                min_area=500, max_area=3500):
    """A detected hen always suppresses the count, including visible eggs."""
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Cannot decode image: ' + str(path))
    presence = measure(path, chicken_area, texture_threshold, exclusions)
    if presence['chicken']:
        return {'status': 'skipped_chicken', 'count': None, 'detections': []}
    detections = detect_eggs(image, texture_threshold, min_area, max_area)
    return {'status': 'estimated', 'count': len(detections), 'detections': detections}


def annotated(path, result):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError('Cannot decode image: ' + str(path))
    image = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
    for index, detection in enumerate(result['detections']):
        cv2.ellipse(image, detection['ellipse'], (0, 220, 0), 2)
        x, y, unused_w, unused_h = detection['box']
        cv2.putText(image, str(index + 1), (x, y - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 220, 0), 1)
    caption = ('Visible eggs (estimate): ' + str(result['count'])
               if result['count'] is not None else result['status'])
    cv2.rectangle(image, (0, 330), (640, 360), (0, 0, 0), -1)
    cv2.putText(image, caption, (5, 351), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
    return image


def write_image(path, image):
    if not cv2.imwrite(str(path), image):
        raise OSError('Cannot write preview: ' + str(path))


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default=str(root / 'captures' / 'eggs'))
    parser.add_argument('--references', default=str(root / 'references'),
                        help='Reviewed references with empty/ images for hen-presence gating')
    parser.add_argument('--output', help='New report directory (must not exist)')
    parser.add_argument('--chicken-area', type=float, default=4500)
    parser.add_argument('--texture-threshold', type=float, default=8)
    parser.add_argument('--min-area', type=float, default=500)
    parser.add_argument('--max-area', type=float, default=3500)
    args = parser.parse_args(argv)
    values = (args.chicken_area, args.texture_threshold, args.min_area, args.max_area)
    if any(not math.isfinite(v) or v <= 0 for v in values) or args.min_area > args.max_area:
        parser.error('Thresholds must be finite and positive; min-area must not exceed max-area')
    cv2.setNumThreads(1)
    try:
        source = Path(args.directory)
        if source.is_symlink() or not source.is_dir():
            raise ValueError('Input must be an existing, non-symlink directory')
        # Accept manually renamed frames as well as standard timestamp names.
        paths = sorted(p for p in source.iterdir() if not p.is_symlink() and
                       p.is_file() and p.suffix.lower() in ('.jpg', '.jpeg', '.png'))
        if not paths:
            raise ValueError('No input images found')
        exclusions = background_mask(reference_directory(args.references), args.texture_threshold)
        if exclusions is None:
            raise ValueError('Reviewed empty references are required for hen-presence gating')
        output = Path(args.output) if args.output else root / 'reports' / (
            'eggs-' + datetime.utcnow().strftime('%Y%m%dT%H%M%S_%fZ'))
        # Prevent generated previews becoming inputs on a subsequent run.
        if source.resolve() == output.resolve() or source.resolve() in output.resolve().parents:
            raise ValueError('Report directory must be outside the input directory')
        output.mkdir(parents=True, exist_ok=False)
        (output / 'annotated').mkdir()
        counts = []
        skipped = 0
        with (output / 'counts.csv').open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['source', 'status', 'visible_eggs_estimate', 'boxes_640x360',
                             'chicken_area', 'texture_threshold', 'min_area', 'max_area'])
            sheet = None
            for index, path in enumerate(paths):
                result = count_image(path, exclusions, args.chicken_area, args.texture_threshold,
                                     min_area=args.min_area, max_area=args.max_area)
                writer.writerow([str(path), result['status'], result['count'],
                                 repr([d['box'] for d in result['detections']])] + list(values))
                preview = annotated(path, result)
                write_image(output / 'annotated' / ('{:04d}.jpg'.format(index)), preview)
                # Bounded 24-image pages, not a growing in-memory image collection.
                slot = index % 24
                if slot == 0:
                    sheet = np.full((6 * 205, 4 * 320, 3), 240, np.uint8)
                x, y = (slot % 4) * 320, (slot // 4) * 205
                sheet[y:y + 180, x:x + 320] = cv2.resize(preview, (320, 180))
                cv2.putText(sheet, '{:04d} {}'.format(index, path.stem[:24]), (x + 3, y + 196),
                            cv2.FONT_HERSHEY_SIMPLEX, .38, (0, 0, 0), 1)
                if slot == 23 or index == len(paths) - 1:
                    write_image(output / 'sheet-{:03d}.jpg'.format(index // 24 + 1), sheet)
                if result['count'] is None:
                    skipped += 1
                else:
                    counts.append(result['count'])
        print('Reviewed {} images; {} skipped for hen presence.'.format(len(paths), skipped))
        if counts:
            print('Estimated visible eggs per frame: {} to {} (not cumulative).'.format(min(counts), max(counts)))
        print('Review counts.csv, annotated/ and sheet-*.jpg in ' + str(output))
        return 0
    except (OSError, ValueError, cv2.error) as error:
        print('Egg review failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
