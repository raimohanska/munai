#!/usr/bin/env python3
"""Lightweight offline nest pre-sorter; no camera, models, or .env access."""
from __future__ import print_function

import argparse
import csv
import ctypes
import fcntl
import os
from pathlib import Path
import random
import re
import sys
import time

import cv2
import numpy as np

from camera_profile import exclude_camera_regions


CATEGORIES = ('empty', 'chicken', 'eggs')
CAPTURE_NAME = re.compile(r'^\d{8}T\d{6}_\d{6}Z\.jpg$')


def capture_files(directory):
    return sorted(p for p in directory.iterdir()
                  if CAPTURE_NAME.match(p.name) and p.is_file()
                  and not p.is_symlink())


def classify(path, chicken_area=4500, texture_threshold=8):
    """Scores/coordinates are at 640x360, with optional local camera exclusions.

    Straw is locally textured. Smooth connected regions represent foreground;
    a large region wins over any egg candidates. This is a heuristic, not ML.
    """
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError('Cannot decode image: ' + str(path))
    gray = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)
    pixels = gray.astype(np.float32)
    mean = cv2.blur(pixels, (7, 7))
    std = np.sqrt(np.maximum(0, cv2.blur(pixels * pixels, (7, 7)) - mean * mean))
    mask = (std < texture_threshold).astype(np.uint8) * 255
    left, right = exclude_camera_regions(mask)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # Join feather-sized gaps for chickens; separate touching eggs by opening.
    chicken_mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    chicken_contours = cv2.findContours(chicken_mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)[-2]
    largest = max([cv2.contourArea(c) for c in chicken_contours] or [0])
    egg_mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    contours = cv2.findContours(egg_mask, cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_SIMPLE)[-2]
    eggs = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not 800 <= area <= 3000 or len(contour) < 5:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if x <= left or x + w >= right or y <= 0 or y + h >= 360:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * np.pi * area / max(perimeter * perimeter, 1)
        solidity = area / max(cv2.contourArea(cv2.convexHull(contour)), 1)
        axes = cv2.fitEllipse(contour)[1]
        aspect = min(axes) / max(max(axes), 1)
        if circularity >= 0.65 and solidity >= 0.88 and aspect >= 0.6:
            eggs.append((x, y, w, h))
    category = 'chicken' if largest >= chicken_area else 'eggs' if eggs else 'empty'
    return category, largest, eggs


def move_no_replace(source, destination):
    """Linux atomic rename with RENAME_NOREPLACE; never overwrite a file."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError('This helper requires Linux renameat2 for safe moves')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(str(source)), -100,
              os.fsencode(str(destination)), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', default=str(Path(__file__).resolve().parent / 'captures'))
    parser.add_argument('--target', type=int, default=10, help='Total images wanted per category (default: 10)')
    parser.add_argument('--seed', type=int, default=42, help='Repeatable random order')
    parser.add_argument('--move', action='store_true', help='Actually move images; otherwise preview only')
    parser.add_argument('--chicken-area', type=float, default=4500)
    parser.add_argument('--texture-threshold', type=float, default=8)
    args = parser.parse_args()
    if args.target < 1 or args.chicken_area <= 0 or args.texture_threshold <= 0:
        parser.error('Target and thresholds must be positive')
    directory = Path(args.directory).resolve()
    cv2.setNumThreads(1)
    with (directory / '.sort.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        counts = {}
        for category in CATEGORIES:
            folder = directory / category
            if folder.is_symlink():
                raise ValueError('Refusing symlink category directory: ' + str(folder))
            if args.move:
                folder.mkdir(exist_ok=True)
            counts[category] = len(capture_files(folder)) if folder.exists() else 0
        files = capture_files(directory)
        random.Random(args.seed).shuffle(files)
        scanned = 0
        started = time.monotonic()
        # Exclusive creation preserves every previous manifest, even after failure.
        manifest = None
        try:
            if args.move:
                manifest_path = directory / ('sort-%d-%d.csv' % (time.time() * 1000000, os.getpid()))
                manifest = manifest_path.open('x', newline='')
                writer = csv.writer(manifest)
                writer.writerow(['source', 'destination', 'category', 'largest_blob', 'egg_boxes', 'seed', 'chicken_area', 'texture_threshold'])
                manifest.flush()
            for path in files:
                if all(counts[c] >= args.target for c in CATEGORIES):
                    break
                category, largest, eggs = classify(path, args.chicken_area, args.texture_threshold)
                scanned += 1
                if counts[category] >= args.target:
                    continue
                destination = directory / category / path.name
                if args.move:
                    move_no_replace(path, destination)
                    writer.writerow([str(path), str(destination), category, round(largest, 1), repr(eggs), args.seed, args.chicken_area, args.texture_threshold])
                    manifest.flush()
                    os.fsync(manifest.fileno())
                counts[category] += 1
                print('{} {} -> {} (blob={:.0f}, eggs={})'.format(
                    'Moved' if args.move else 'Would move', path.name, category, largest, len(eggs)))
        finally:
            if manifest is not None:
                manifest.close()
        print('{}; scanned {} in {:.2f}s{}'.format(
            ', '.join('{}={}'.format(c, counts[c]) for c in CATEGORIES), scanned,
            time.monotonic() - started, '' if args.move else ' (preview only)'))
        return 0 if all(counts[c] >= args.target for c in CATEGORIES) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print('Sort failed: {}'.format(error), file=sys.stderr)
        sys.exit(1)
