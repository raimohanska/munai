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
        # maxima on one egg. A straw notch can also create two peaks: require a
        # genuine neck (15% radius drop), not just two distinct maximum pixels.
        unused_count, cores = cv2.connectedComponents((distance >= radius * 0.85).astype(np.uint8))
        core = cores[int(round(y)), int(round(x))]
        if all((x - cx) ** 2 + (y - cy) ** 2 >= max(12, radius) ** 2 and
               (core == 0 or core != cores[int(round(cy)), int(round(cx))])
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
    padded_smooth = cv2.copyMakeBorder(smooth, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    contours = cv2.findContours(smooth, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    detections = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        # Do not split walls into egg-sized parts. Modestly clipped eggs can be
        # recovered from their visible curved boundary, but not from a tiny arc.
        if (cv2.contourArea(contour) < min_area or
                cv2.contourArea(contour) > 4 * max_area):
            continue
        region = np.zeros((h + 2, w + 2), np.uint8)
        local_contour = contour - np.array([[[x - 1, y - 1]]], np.int32)
        cv2.drawContours(region, [local_contour], -1, 255, -1)
        border = x == 0 or y == 0 or x + w == 640 or y + h == 360
        # A wall may have many distance peaks. Never manufacture individual
        # eggs by splitting a border-connected component into smaller pieces.
        for part in ([region] if border else split_region(region)):
            smooth_patch = padded_smooth[y:y + h + 2, x:x + w + 2]
            smooth_fraction = (float(np.count_nonzero((part != 0) & (smooth_patch != 0))) /
                               max(np.count_nonzero(part), 1))
            # Filling contours must not turn textured bedding enclosed by a
            # smooth outline into an egg. Require mostly smooth visible surface.
            if smooth_fraction < 0.75:
                continue
            pieces = cv2.findContours(part, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
            if not pieces:
                continue
            piece = max(pieces, key=cv2.contourArea)
            piece = piece + np.array([[[x - 1, y - 1]]], np.int32)
            area = cv2.contourArea(piece)
            if not min_area <= area <= max_area or len(piece) < 5:
                continue
            hull = cv2.convexHull(piece)
            hull_area = cv2.contourArea(hull)
            perimeter = cv2.arcLength(hull, True)
            circularity = 4 * np.pi * hull_area / max(perimeter * perimeter, 1)
            solidity = area / max(hull_area, 1)
            # A straw notch or a contact seam can make the raw perimeter very
            # jagged without changing the egg's outer shape. Fit the envelope,
            # not those inward nicks; retain a solidity check against straw.
            points = hull.reshape(-1, 2)
            visible = ((points[:, 0] > 0) & (points[:, 0] < 639) &
                       (points[:, 1] > 0) & (points[:, 1] < 359))
            clipped = not bool(np.all(visible))
            curved = points[visible] if clipped else points
            if len(curved) < 5:
                continue
            ellipse = cv2.fitEllipse(curved.reshape(-1, 1, 2))
            axes = ellipse[1]
            aspect = min(axes) / max(max(axes), 1)
            ellipse_area = np.pi * axes[0] * axes[1] / 4
            coverage = area / max(ellipse_area, 1)
            if (circularity >= 0.72 and solidity >= 0.82 and aspect >= 0.50 and
                    0.70 <= coverage <= 1.10 and min_area <= ellipse_area <= max_area):
                detections.append({'box': [int(v) for v in cv2.boundingRect(piece)],
                                   'ellipse': ellipse, 'area': float(area),
                                   'partial': clipped, 'smooth_fraction': smooth_fraction})
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
    # Keep the full camera frame visible: a footer painted over the bottom edge
    # would conceal precisely the partly clipped eggs we want reviewers to see.
    image = cv2.copyMakeBorder(image, 0, 30, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    for index, detection in enumerate(result['detections']):
        cv2.ellipse(image, detection['ellipse'], (0, 220, 0), 2)
        x, y, unused_w, unused_h = detection['box']
        cv2.putText(image, str(index + 1), (x, y - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 220, 0), 1)
    caption = ('Visible eggs (estimate): ' + str(result['count'])
               if result['count'] is not None else result['status'])
    cv2.putText(image, caption, (5, 381), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
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
                             'chicken_area', 'texture_threshold', 'min_area', 'max_area',
                             'partial_detections', 'smooth_fractions'])
            sheet = None
            for index, path in enumerate(paths):
                result = count_image(path, exclusions, args.chicken_area, args.texture_threshold,
                                     min_area=args.min_area, max_area=args.max_area)
                writer.writerow([str(path), result['status'], result['count'],
                                 repr([d['box'] for d in result['detections']])] + list(values) +
                                [repr([i + 1 for i, d in enumerate(result['detections']) if d['partial']]),
                                 repr([round(d['smooth_fraction'], 3) for d in result['detections']])])
                preview = annotated(path, result)
                write_image(output / 'annotated' / ('{:04d}.jpg'.format(index)), preview)
                # Bounded 24-image pages, not a growing in-memory image collection.
                slot = index % 24
                if slot == 0:
                    sheet = np.full((6 * 220, 4 * 320, 3), 240, np.uint8)
                x, y = (slot % 4) * 320, (slot // 4) * 220
                sheet[y:y + 195, x:x + 320] = cv2.resize(preview, (320, 195))
                cv2.putText(sheet, '{:04d} {}'.format(index, path.stem[:24]), (x + 3, y + 211),
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
