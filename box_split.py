"""Offline split-box presence prototype; never assigns identity from position.

All geometry uses the original frame's 640x360 measurement scale. A divider
region is not an empty nest and is not proof that exactly one hen is present.
This module does not access cameras, configuration files, MQTT or live state.
"""
import cv2
import numpy as np

from recognize_chickens import image_statistics
from sort_captures import chicken_region

SPLITS = ('none', 'vertical', 'horizontal')


def half_boxes(width, height, split):
    """Return unscaled, non-overlapping crop bounds, including odd edge pixels."""
    if split == 'none':
        return {'whole': (0, 0, width, height)}
    if split == 'vertical':
        return {'left': (0, 0, width // 2, height),
                'right': (width // 2, 0, width, height)}
    if split == 'horizontal':
        return {'top': (0, 0, width, height // 2),
                'bottom': (0, height // 2, width, height)}
    raise ValueError('Split must be none, vertical or horizontal')


def assign_regions(masks, split, fragment_gap=24):
    """Locate detected body components, without measuring identity features.

    A body spanning the divider substantially is a center candidate. Nearby
    fragments can belong to that same body, but a separate body makes the
    observation ambiguous. Missing detections and touching hens remain possible:
    this is a review prototype, not a general multi-object detector.
    """
    if split not in SPLITS:
        raise ValueError('Split must be none, vertical or horizontal')
    masks = [np.asarray(mask, dtype=bool) for mask in masks]
    if masks and any(mask.ndim != 2 or mask.shape != masks[0].shape or not mask.any()
                     for mask in masks):
        raise ValueError('Body masks must be nonempty and share a two-dimensional shape')
    side_names = ('left', 'right') if split == 'vertical' else ('top', 'bottom')

    def location(mask):
        if split == 'none':
            return 'whole'
        y, x = np.nonzero(mask)
        axis, size = (x, mask.shape[1]) if split == 'vertical' else (y, mask.shape[0])
        fraction = float(np.mean(axis < size // 2))
        # Substantial area on both sides, not a beak/tail crossing the line.
        if 0.2 <= fraction <= 0.8 and 0.35 <= float(np.mean(axis)) / size <= 0.65:
            return 'center'
        return side_names[0 if fraction > 0.5 else 1]

    labels = [location(mask) for mask in masks]
    centers = [i for i, label in enumerate(labels) if label == 'center']
    if len(centers) == 1:
        anchor = centers[0]
        merged = masks[anchor].copy()
        remaining = set(range(len(masks))) - {anchor}
        # Feather texture can split one body into several connected components.
        # Attach only fragments near a detected center body, never merge the two
        # side occupants merely because their combined centroid is central.
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                          (fragment_gap * 2 + 1, fragment_gap * 2 + 1))
        for index in sorted(remaining):
            near = cv2.dilate(masks[anchor].astype(np.uint8), kernel) != 0
            if np.any(near & masks[index]):
                merged |= masks[index]
                remaining.remove(index)
        masks = [merged] + [masks[i] for i in sorted(remaining)]
        labels = ['center'] + [labels[i] for i in sorted(remaining)]
    zones = {}
    for label in labels:
        zones[label] = zones.get(label, 0) + 1
    ambiguous = (any(n > 1 for n in zones.values()) or
                 ('center' in zones and len(zones) > 1) or
                 any(mask.sum() > mask.size * 0.5 for mask in masks))
    bodies = []
    for mask, label in zip(masks, labels):
        y, x = np.nonzero(mask)
        bodies.append({'zone': label, 'pixels': int(mask.sum()),
                       'box': [int(x.min()), int(y.min()),
                               int(x.max() - x.min() + 1), int(y.max() - y.min() + 1)]})
    if split == 'none':
        blocked = ['whole'] if masks else []
    elif ambiguous or 'center' in zones:
        # Do not infer unoccluded egg visibility from a perched hen's centroid.
        blocked = list(side_names)
    else:
        blocked = [name for name in side_names if name in zones]
    return {'split': split, 'bodies': bodies, 'ambiguous': ambiguous,
            'center_present': 'center' in zones, 'blocked_for_eggs': blocked,
            'clear_candidates': [name for name in (('whole',) if split == 'none' else side_names)
                                 if name not in blocked]}


def detect(path, split, background_mask, chicken_area=4500, texture_threshold=8):
    """Find every accepted presence component, then assign spatial occupancy.

    Reviewed full-frame empty calibration is required. No identity reference or
    nest-position prior participates in the presence decision.
    """
    if background_mask is None:
        raise ValueError('Reviewed full-frame empty references are required')
    gray, unused_pixels, std = image_statistics(path)
    if background_mask.shape != gray.shape:
        raise ValueError('Background mask has the wrong measurement dimensions')
    valid = (~background_mask).astype(np.uint8) * 255
    smooth = ((std < texture_threshold) & (~background_mask)).astype(np.uint8) * 255
    smooth = cv2.morphologyEx(smooth, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    joined = cv2.bitwise_and(cv2.morphologyEx(smooth, cv2.MORPH_CLOSE,
                                            np.ones((15, 15), np.uint8)), valid)
    contours = sorted(cv2.findContours(joined, cv2.RETR_EXTERNAL,
                                      cv2.CHAIN_APPROX_SIMPLE)[-2],
                      key=cv2.contourArea, reverse=True)
    masks = []
    for contour in contours:
        if cv2.contourArea(contour) < chicken_area:
            break
        region = np.zeros(gray.shape, np.uint8)
        cv2.drawContours(region, [contour], -1, 255, -1)
        region = cv2.bitwise_and(region, valid)
        unused_contour, unused_area, hen = chicken_region(cv2.bitwise_and(smooth, region),
                                                        region, chicken_area)
        if hen:
            masks.append(region != 0)
    return assign_regions(masks, split)
