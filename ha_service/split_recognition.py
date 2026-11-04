"""Shared appearance references and fixed-scale split/divider observations."""
from pathlib import Path

import cv2
import numpy as np

from box_split import detect, half_boxes
from count_eggs import detect_eggs
from data_layout import RESERVED
from recognize_chickens import References, background_mask, identify, measure, image_statistics
from sort_captures import capture_files
from .options import AppError


def measurement_size(split):
    return (320, 360) if split == 'vertical' else (640, 180)


def crop(image, zone, split):
    h, w = image.shape[:2]
    if zone == 'center':
        box = ((w // 4, 0, w // 4 + w // 2, h) if split == 'vertical'
               else (0, h // 4, w, h // 4 + h // 2))
    else:
        box = half_boxes(w, h, split)[zone]
    x1, y1, x2, y2 = box
    return image[y1:y2, x1:x2]


def calibration(root, options):
    empties = capture_files(Path(root) / 'empty') if (Path(root) / 'empty').is_dir() else []
    ratios = []
    for path in empties:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise AppError('Empty reference cannot be decoded.')
        ratios.append(image.shape[1] / float(image.shape[0]))
    if ratios and any(abs(r / ratios[0] - 1) > 0.02 for r in ratios):
        raise AppError('Split empty calibration must contain full frames with matching aspect ratios, not half crops.')
    full = background_mask(root, options['texture_threshold'])
    if full is None:
        raise AppError('Split mode requires reviewed full-frame empty references.')
    split = options['box_split']
    names = ('left', 'right') if split == 'vertical' else ('top', 'bottom')
    # One neutral mask for all reference/live views. Neither view name nor
    # original side is passed to the identity comparison.
    common = np.logical_and.reduce([crop(full, zone, split) for zone in names + ('center',)])
    return full, common


def validate_half(path, root, split):
    empties = capture_files(Path(root) / 'empty')
    if not empties:
        raise AppError('Upload full-frame empty references first.')
    full = cv2.imread(str(empties[0]), cv2.IMREAD_GRAYSCALE)
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if full is None or image is None:
        raise AppError('Reference cannot be decoded.')
    ratio = full.shape[1] / float(full.shape[0])
    expected = ratio / 2 if split == 'vertical' else ratio * 2
    if abs(image.shape[1] / float(image.shape[0]) / expected - 1) > 0.02:
        raise AppError('Split identities must be half-frame crops, including a centered half-frame view for divider hens.')


def validate_empty(path):
    other = [p for p in capture_files(Path(path).parent) if p != Path(path)]
    if other:
        first = cv2.imread(str(other[0]), cv2.IMREAD_GRAYSCALE)
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if (first is None or image is None or
                abs((image.shape[1] / float(image.shape[0])) /
                    (first.shape[1] / float(first.shape[0])) - 1) > 0.02):
            raise AppError('Empty split references must be full frames matching the existing calibration.')


def load(root, options):
    full, common = calibration(root, options)
    refs = References(common)
    refs.full_background_mask = full
    # Compare the same area-averaged pixels as live image_statistics(), not
    # linearly sampled straw texture (which aliases when shrinking a full frame).
    refs.background_frames = []
    # A broad loss of background texture can reveal a feathered body with the
    # same mean brightness as straw. Use the lowest reviewed texture, not one
    # selected side/identity, so ordinary reviewed smooth areas remain allowed.
    refs.background_texture_low = None
    for path in capture_files(Path(root) / 'empty'):
        frame, unused_pixels, std = image_statistics(path)
        refs.background_frames.append(frame)
        texture = cv2.GaussianBlur(std, (21, 21), 0)
        blurred = cv2.GaussianBlur(frame, (21, 21), 0).astype(np.int16)
        if refs.background_texture_low is None:
            refs.background_texture_low = texture
            refs.background_low, refs.background_high = blurred.copy(), blurred.copy()
        else:
            # Do not retain floating-point statistics for every reference.
            np.minimum(refs.background_texture_low, texture, out=refs.background_texture_low)
            np.minimum(refs.background_low, blurred, out=refs.background_low)
            np.maximum(refs.background_high, blurred, out=refs.background_high)
    for folder in sorted(Path(root).iterdir()):
        if not folder.is_dir() or folder.is_symlink() or folder.name in RESERVED or folder.name.startswith('.'):
            continue
        for path in capture_files(folder):
            validate_half(path, root, options['box_split'])
            result = measure(path, options['chicken_area'], options['texture_threshold'],
                             common, measurement_size(options['box_split']))
            if result['features'] is None:
                raise AppError('Split reference has insufficient measurable plumage; review the half-frame crop.')
            refs.append((folder.name, path, result))
    if len({r[0] for r in refs}) < 2:
        raise AppError('Split recognition requires reviewed half-frame references for at least two identities.')
    return refs


def changed_textured_body(view, empty_views, exclusions, options):
    """Changed textured area (or zero), not an identity or a second visitor."""
    size = measurement_size(options['box_split'])
    gray, unused_pixels, std = image_statistics(view, size)
    # Fine straw edges can move without a body appearing. Suppress one-pixel
    # texture changes in BOTH operands, retaining original texture for the
    # feather test below. Never smooth/stretch a half view to full-frame scale.
    comparison = cv2.GaussianBlur(gray, (3, 3), 0)
    empty_comparisons = [cv2.GaussianBlur(empty, (3, 3), 0) for empty in empty_views]
    nearest = min(empty_comparisons,
                  key=lambda empty: float(np.mean(cv2.absdiff(comparison, empty))))
    changed = (cv2.absdiff(comparison, nearest) > 30).astype(np.float32)
    mask = ((cv2.GaussianBlur(changed, (13, 13), 0) > 0.25) & (~exclusions)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    contours = sorted(cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2],
                      key=cv2.contourArea, reverse=True)
    for contour in contours:
        if cv2.contourArea(contour) < options['chicken_area']:
            continue
        region = np.zeros(gray.shape, np.uint8)
        cv2.drawContours(region, [contour], -1, 255, -1)
        inside = (region != 0) & (~exclusions)
        # Measure the body's silhouette, not holes from neutral background
        # exclusions. Otherwise a broad/clipped body can appear too thin.
        distance = cv2.distanceTransform(region, cv2.DIST_L2, 5)
        if (inside.any() and float(distance[inside].max()) >= 22
                and float(np.mean(std[inside] < options['texture_threshold'])) < 0.75):
            return float(cv2.contourArea(contour))
    return 0


def predict(path, references, options):
    split = options['box_split']
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise AppError('Split image cannot be decoded.')
    frame, unused_pixels, std = image_statistics(image)
    blurred = cv2.GaussianBlur(frame, (21, 21), 0).astype(np.int16)
    novelty = (blurred < references.background_low - 25) | (blurred > references.background_high + 25)
    texture_change = cv2.GaussianBlur(std, (21, 21), 0) < references.background_texture_low * 0.5
    minimum_body = max(options['chicken_area'], 10 * options['egg_max_area'])
    presence = detect(path, split, references.full_background_mask,
                      minimum_body, options['texture_threshold'],
                      novelty_mask=novelty, texture_mask=texture_change,
                      fragment_area=options['egg_max_area'])
    sides = ('left', 'right') if split == 'vertical' else ('top', 'bottom')
    zones = {}
    for zone in sides + ('center',):
        reason = ('ambiguous_presence' if presence['ambiguous'] else
                  'center_occupied' if presence['center_present'] and zone != 'center' else
                  'small_body' if zone in presence.get('uncertain_zones', ()) else None)
        obscured = reason is not None
        # Use the same full-frame normalization and split grid as calibration,
        # including when a source image has odd dimensions.
        view = crop(frame, zone, split)
        item = {'label': 'unknown' if obscured else 'no_chicken', 'egg_count': None,
                'distance': None, 'margin': None, 'obscured': obscured,
                'obscured_reason': reason}
        detected = any(body['zone'] == zone for body in presence['bodies'])
        if not obscured and not detected and zone != 'center':
            empty_views = [crop(frame, zone, split) for frame in references.background_frames]
            changed_area = changed_textured_body(view, empty_views,
                                                 crop(references.full_background_mask, zone, split), options)
            obscured = bool(changed_area)
            if obscured:
                item.update(label='unknown', obscured=True,
                            obscured_reason='background_change' if changed_area >= minimum_body else 'small_body')
        # Center presence is decided on the whole frame, never on a central crop
        # that could include feathers from two separate side occupants.
        if not obscured and detected:
            result = measure(view, options['chicken_area'], options['texture_threshold'],
                             references.background_mask, measurement_size(split))
            decision = identify(result, references, options['max_distance'], options['min_margin'])
            item.update({key: decision[key] for key in ('label', 'distance', 'margin')})
            if detected and item['label'] == 'no_chicken':
                item['label'] = 'unknown'  # Full-frame presence wins over a clipped crop.
        zones[zone] = item
    left, right = (zones[zone] for zone in sides)
    if left['label'] == right['label'] and left['label'] not in ('unknown', 'no_chicken'):
        # A configured hen cannot be two independent visitors simultaneously.
        # Do not choose a side or a corrected identity from positional coverage.
        for zone in sides:
            zones[zone].update(label='unknown', egg_count=None, obscured=True,
                               obscured_reason='duplicate_identity')
        presence['ambiguous'] = True
    # Count each clear side independently; the overlapping center view is only
    # for presence, never an additional egg-counting region.
    center = zones['center']
    if not center['obscured'] and center['label'] == 'no_chicken':
        for zone in sides:
            if zones[zone]['obscured'] or zones[zone]['label'] != 'no_chicken':
                continue
            zones[zone]['egg_count'] = len(detect_eggs(
                crop(frame, zone, split), options['texture_threshold'], options['egg_min_area'],
                options['egg_max_area'], size=measurement_size(split)))
    labels = {item['label'] for item in zones.values() if item['label'] != 'no_chicken'}
    label = next(iter(labels)) if len(labels) == 1 else 'unknown' if labels else 'no_chicken'
    counts = [zones[zone]['egg_count'] for zone in sides]
    return {'label': label, 'egg_count': sum(counts) if all(n is not None for n in counts) else None,
            'distance': None, 'margin': None, 'zones': zones, 'layout': split,
            'ambiguous': presence['ambiguous'], 'presence': presence}
