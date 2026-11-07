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

    def overlaps(mask):
        h, w = mask.shape
        boxes = half_boxes(w, h, split)
        if split != 'none':
            boxes['center'] = ((w // 4, 0, w // 4 + w // 2, h) if split == 'vertical'
                               else (0, h // 4, w, h // 4 + h // 2))
        return {zone: int(mask[y1:y2, x1:x2].sum())
                for zone, (x1, y1, x2, y2) in boxes.items()}

    def location(mask):
        if split == 'none':
            return 'whole'
        area = overlaps(mask)
        # Compare the SAME body in equal-size views, not its bounding rectangle
        # or the combined area of two independent hens. A tied side keeps its
        # position rather than moving a hen whose tail merely enters center.
        if area['center'] > max(area[side] for side in side_names):
            return 'center'
        return max(side_names, key=lambda side: area[side])

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
        labels = [location(merged)] + [labels[i] for i in sorted(remaining)]
    zones = {}
    for label in labels:
        zones[label] = zones.get(label, 0) + 1
    ambiguous = (any(n > 1 for n in zones.values()) or
                 ('center' in zones and len(zones) > 1) or
                 any(mask.sum() > mask.size * 0.5 for mask in masks))
    bodies = []
    for mask, label in zip(masks, labels):
        y, x = np.nonzero(mask)
        bodies.append({'zone': label, 'pixels': int(mask.sum()), 'overlap_pixels': overlaps(mask),
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


def detect(path, split, background_mask, chicken_area=4500, texture_threshold=8,
           novelty_mask=None, texture_mask=None, fragment_area=None,
            minimum_fragment_change=None, feather_mask=None):
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
    candidate_area = chicken_area if fragment_area is None else fragment_area
    for contour in contours:
        if cv2.contourArea(contour) < candidate_area:
            break
        region = np.zeros(gray.shape, np.uint8)
        cv2.drawContours(region, [contour], -1, 255, -1)
        region = cv2.bitwise_and(region, valid)
        unused_contour, unused_area, hen = chicken_region(cv2.bitwise_and(smooth, region),
                                                        region, candidate_area)
        if hen:
            masks.append(region != 0)
    if feather_mask is not None:
        for contour in cv2.findContours(feather_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]:
            region = np.zeros(gray.shape, np.uint8)
            cv2.drawContours(region, [contour], -1, 255, -1)
            masks.append((region != 0) & (~background_mask))
    if novelty_mask is not None or texture_mask is not None or feather_mask is not None:
        # Broad changes relative to reviewed empty backgrounds catch patterned
        # bodies whose smooth feather patches are individually too fragmented.
        for change, texture_only in ((novelty_mask, False), (texture_mask, True)):
            if change is None:
                continue
            novel = (change & (~background_mask)).astype(np.uint8) * 255
            novel = cv2.morphologyEx(novel, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
            distance = cv2.distanceTransform(novel, cv2.DIST_L2, 5)
            for contour in cv2.findContours(novel, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]:
                if cv2.contourArea(contour) < candidate_area:
                    continue
                region = np.zeros(gray.shape, np.uint8)
                cv2.drawContours(region, [contour], -1, 255, -1)
                inside = (region != 0) & (~background_mask)
                if not inside.any() or float(distance[inside].max()) < 22:
                    continue
                if texture_only:
                    # Egg piles also replace textured straw with smooth shells.
                    # Keep the shared broad-plumage / feather-texture gate,
                    # measuring smooth thickness BEFORE closing joins shells.
                    # A reviewed wall exclusion may now lie inside plumage.
                    # It must not punch artificial holes into a candidate's
                    # unclosed smooth patch. The candidate itself still needs
                    # broad change on nonexcluded background pixels.
                    unclosed = (std < texture_threshold).astype(np.uint8) * 255
                    unclosed = cv2.morphologyEx(unclosed, cv2.MORPH_OPEN,
                                                np.ones((3, 3), np.uint8))
                    smooth_distance = cv2.distanceTransform(cv2.bitwise_and(unclosed, region),
                                                            cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
                    if (float(smooth_distance[inside].max()) < 22
                            and float(np.mean(smooth[inside] != 0)) >= 0.75):
                        continue
                masks.append(inside)
        # Two methods may describe the same body. Never count overlapping masks
        # as separate hens; genuinely merged bodies still fail ambiguity checks.
        merged = []
        for mask in masks:
            changed = True
            while changed:
                changed = False
                for index, existing in enumerate(merged):
                    if np.any(mask & existing):
                        mask = mask | merged.pop(index)
                        changed = True
                        break
            merged.append(mask)
        masks = merged
    visitors = [mask for mask in masks if np.count_nonzero(mask) >= chicken_area]
    fragments = [mask for mask in masks if np.count_nonzero(mask) < chicken_area]
    if minimum_fragment_change is not None and novelty_mask is not None and texture_mask is not None:
        # Closing/filling a contour can enclose unchanged straw around a feather.
        # Require enough actual change, not merely a large enclosing silhouette.
        changed = (novelty_mask | texture_mask) & (~background_mask)
        fragments = [mask for mask in fragments
                     if np.count_nonzero(mask & changed) >= minimum_fragment_change]
    result = assign_regions(visitors, split)
    result['fragments'] = []
    uncertain = set()
    for mask in fragments:
        body = assign_regions([mask], split)['bodies'][0]
        result['fragments'].append(body)
        if body['zone'] == 'center':
            uncertain.update(('left', 'right', 'center') if split == 'vertical'
                             else ('top', 'bottom', 'center'))
        else:
            uncertain.add(body['zone'])
    result['uncertain_zones'] = sorted(uncertain)
    if visitors or fragments:
        result['blocked_for_eggs'] = (['whole'] if split == 'none' else
                                     list(half_boxes(640, 360, split)))
        result['clear_candidates'] = []
    return result
