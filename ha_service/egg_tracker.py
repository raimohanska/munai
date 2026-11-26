"""Egg-only spatial memory. Strict detections acquire; shell remnants retain.

All coordinates are in the detector's normalized side/whole view. No hen
identity decision uses these locations. A missing ellipse is not a removal.
"""
import base64
import binascii
import copy
import math

import cv2
import numpy as np

MAX_TRACKS = 32
CONFIRM = 3
BASELINE_SIZE = (80, 90)
TEXTURE_RADIUS = 3


def empty_tracker():
    return {'tracks': [], 'unlocated': 0, 'next_id': 1}


def finite(value, low, high):
    return (type(value) in (int, float) and math.isfinite(value)
            and low <= value <= high)


def valid_tracker(value):
    if value is None:
        return True  # Pre-tracker baseline, not evidence of no eggs.
    required = {'tracks', 'unlocated', 'next_id'}
    if (not isinstance(value, dict) or not required <= value.keys()
            or not value.keys() <= required | {'baseline_pending', 'relocation_baseline'}
            or ('baseline_pending' in value and type(value['baseline_pending']) is not bool)
            or type(value['unlocated']) is not int or not 0 <= value['unlocated'] <= 1000000
            or type(value['next_id']) is not int or not 1 <= value['next_id'] <= 1000000000
            or not isinstance(value['tracks'], list) or len(value['tracks']) > MAX_TRACKS):
        return False
    if 'relocation_baseline' in value and decode_baseline(value['relocation_baseline']) is None:
        return False
    ids = set()
    fields = {'id', 'ellipse', 'brightness', 'hits', 'absent', 'status', 'counted'}
    for track in value['tracks']:
        if (not isinstance(track, dict) or not fields <= track.keys() <= fields | {'shell_profile', 'check_bedding', 'fragment'}
                or ('check_bedding' in track and type(track['check_bedding']) is not bool)):
            return False
        if 'fragment' in track:
            fragment = track['fragment']
            if (not track.get('check_bedding') or not isinstance(fragment, list) or len(fragment) != 3
                    or not all(type(v) is int for v in fragment)
                    or not all(0 <= v < 640 for v in fragment[:2]) or not 1 <= fragment[2] <= CONFIRM):
                return False
        if 'shell_profile' in track:
            profile = track['shell_profile']
            if (not isinstance(profile, (list, tuple)) or len(profile) != 2
                    or not all(finite(v, 0, 255) for v in profile)):
                return False
        ellipse = track['ellipse']
        if (type(track['id']) is not int or not 1 <= track['id'] < value['next_id']
                or track['id'] in ids or not isinstance(ellipse, (list, tuple)) or len(ellipse) != 3
                or not isinstance(ellipse[0], (list, tuple)) or len(ellipse[0]) != 2
                or not isinstance(ellipse[1], (list, tuple)) or len(ellipse[1]) != 2
                or not all(finite(v, -640, 1280) for v in ellipse[0])
                or not all(finite(v, 1, 640) for v in ellipse[1])
                or not finite(ellipse[2], 0, 360) or not finite(track['brightness'], 0, 255)
                or type(track['hits']) is not int or not 1 <= track['hits'] <= CONFIRM
                or type(track['absent']) is not int or not 0 <= track['absent'] < CONFIRM
                or type(track['counted']) is not bool
                or track['status'] not in ('visible', 'obstructed', 'uncertain', 'absent')):
            return False
        ids.add(track['id'])
    return True


def pause(value):
    """Hidden nests, failures and restarts break removal/acquisition streaks."""
    if value is None:
        return None
    value = copy.deepcopy(value)
    value['tracks'] = [t for t in value['tracks'] if t['hits'] == CONFIRM]
    for track in value['tracks']:
        track.pop('fragment', None)
        track.update(absent=0, status='uncertain', check_bedding=True)
    return value


def surface(image, ellipse):
    mask = np.zeros(image.shape, np.uint8)
    center, axes, angle = ellipse
    # Avoid the shell boundary, whose texture includes straw even on a clear egg.
    cv2.ellipse(mask, (tuple(center), tuple(v * .82 for v in axes), angle), 255, -1)
    return mask != 0


def texture(image):
    pixels = image.astype(np.float32)
    size = 2 * TEXTURE_RADIUS + 1
    mean = cv2.blur(pixels, (size, size))
    return np.sqrt(np.maximum(0, cv2.blur(pixels * pixels, (size, size)) - mean * mean))


def statistics(image, threshold):
    return texture(image) < threshold


def shell_appearance(pixels, deviations):
    low, median, high = np.percentile(pixels, (10, 50, 90))
    return float(median), [float(high - low), float(np.percentile(deviations, 75))]


def detected_appearance(image, ellipse, threshold, deviations=None, smooth=None):
    """Sample a strict detection without allocating partition-sized arrays."""
    if deviations is None:
        (x, y), axes, angle = ellipse
        # The circle around the ellipse covers any orientation. Include the
        # rasterized edge and the full blur halo, even for clipped shells.
        radius = .41 * max(axes) + TEXTURE_RADIUS + 2
        h, w = image.shape
        left, top = max(0, math.floor(x - radius)), max(0, math.floor(y - radius))
        right, bottom = min(w, math.ceil(x + radius)), min(h, math.ceil(y + radius))
        if left >= right or top >= bottom:
            return None
        image = image[top:bottom, left:right]
        ellipse = ((x - left, y - top), axes, angle)
        deviations = texture(image)
        smooth = deviations < threshold
    shell = surface(image, ellipse) & smooth
    return shell_appearance(image[shell], deviations[shell]) if shell.any() else None


def matches_shell(track, brightness, profile, relaxed=False):
    known = track.get('shell_profile')
    if known is None:
        # Old state can learn on the next strict match. Brightness alone is
        # not enough to claim a distant partial patch of bedding as this egg.
        return not relaxed and abs(brightness - track['brightness']) <= 35
    return (abs(brightness - track['brightness']) <= (22 if relaxed else 35)
            and profile[0] <= known[0] + 12 and profile[1] <= known[1] + 3)


def probe(image, smooth, track):
    """Retain small smooth shell fragments without requiring a closed ellipse.

    Straw-replaced surface must be almost entirely textured to support absence.
    Intermediate evidence is uncertainty and has no timeout. Complete burial
    cannot always be distinguished from removal by this fixed camera.
    """
    mask = surface(image, track['ellipse'])
    area = int(mask.sum())
    if area < 50:
        return 'uncertain'
    shell = mask & smooth & (np.abs(image.astype(np.float32) - track['brightness']) <= 35)
    # Connected support rejects isolated smooth straw pixels. Only retention is
    # relaxed: fragments can never acquire a new egg.
    n, labels, stats, unused = cv2.connectedComponentsWithStats(shell.astype(np.uint8), connectivity=8)
    largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if n > 1 else 0
    if largest >= max(40, area * .06) or int(shell.sum()) >= area * .14:
        return 'obstructed'
    if float(np.count_nonzero(smooth & mask)) / area <= .04:
        return 'absent'
    return 'uncertain'


def relocation_radius(track):
    return min(112, 2 * max(track['ellipse'][1]))


def baseline_pixels(image):
    return cv2.resize(image, BASELINE_SIZE, interpolation=cv2.INTER_AREA)


def decode_baseline(encoded):
    size = math.prod(BASELINE_SIZE)
    if not isinstance(encoded, str) or len(encoded) != 4 * ((size + 2) // 3):
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(data) != size:
        return None
    return np.frombuffer(data, dtype=np.uint8).reshape(BASELINE_SIZE[::-1])


def bedding_disturbed(before, after, shape, track):
    """Compare bedding around (not inside) the old shell on the frozen thumbnail."""
    (cx, cy), axes, unused = track['ellipse']
    y, x = np.ogrid[:before.shape[0], :before.shape[1]]
    distance = np.hypot((x + .5) * shape[1] / before.shape[1] - cx,
                        (y + .5) * shape[0] / before.shape[0] - cy)
    radius = max(axes)
    ring = (distance > .65 * radius) & (distance < 1.6 * radius)
    if np.count_nonzero(ring) < 16:
        return False
    delta = after[ring].astype(np.int16) - before[ring]
    # Discount a uniform exposure shift. Widespread local changes make a
    # textured shell location evidence of possible burial, not a pickup.
    return np.mean(np.abs(delta - np.median(delta)) >= 15) >= .30


def fragment_status(image, track, reserved):
    """Fine-scale shell support after bedding has shifted over a confirmed egg."""
    (cx, cy), axes, unused = track['ellipse']
    radius = .8 * max(axes)
    h, w = image.shape
    left, top = max(0, int(cx-radius)), max(0, int(cy-radius))
    right, bottom = min(w, math.ceil(cx+radius+1)), min(h, math.ceil(cy+radius+1))
    if left >= right or top >= bottom:
        return 'uncertain'
    patch = np.s_[top:bottom, left:right]
    pixels = image[patch].astype(np.float32)
    mean = cv2.blur(pixels, (3, 3))
    variance = cv2.blur(pixels * pixels, (3, 3)) - mean * mean
    y, x = np.ogrid[top:bottom, left:right]
    mask = ((variance < 16) & (np.abs(pixels - track['brightness']) < 30) & ~reserved[patch]
            & ((x-cx)**2 + (y-cy)**2 <= radius**2))
    n, labels, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    previous = track.get('fragment')
    candidates = []
    for i in range(1, n):
        a, b, width, height, area = stats[i]
        if area < max(24, .01 * axes[0] * axes[1]):
            continue
        component = (labels[b:b+height, a:a+width] == i).astype(np.uint8)
        if cv2.distanceTransform(np.pad(component, 1), cv2.DIST_L2, 5).max() < 2:
            continue
        px, py = (int(round(v)) for v in centers[i] + (left, top))
        same = previous is not None and math.hypot(px-previous[0], py-previous[1]) <= 5
        candidates.append((same, area, px, py))
    if candidates:
        same, unused, px, py = max(candidates)
        track['fragment'] = [px, py, min(CONFIRM, previous[2]+1) if same else 1]
        reserved[patch] |= mask
        return 'obstructed'
    # Only loss of a repeatedly localized fragment can override disturbed bedding.
    if previous is not None and previous[2] == CONFIRM:
        return 'absent'
    track.pop('fragment', None)
    return 'uncertain'


def relocated_shell(image, deviations, smooth, track, reserved, whole_partition, changed):
    """Find substantial shell-like support, not just a smooth straw sliver."""
    if 'shell_profile' not in track:
        return False
    (x, y), axes, unused = track['ellipse']
    bottom, right = image.shape
    left = top = 0
    radius = math.inf if whole_partition else relocation_radius(track)
    if not whole_partition:
        # Include enough room for the shell outline around candidate centers.
        margin = radius + max(axes)
        left, top = max(0, int(x - margin)), max(0, int(y - margin))
        right, bottom = min(right, math.ceil(x + margin)), min(bottom, math.ceil(y + margin))
    if left >= right or top >= bottom:
        return False
    window = np.s_[top:bottom, left:right]
    quality = smooth[window] & (np.abs(image[window].astype(np.float32) - track['brightness']) <= 35)
    mask = (quality & ~reserved[window]).astype(np.uint8)
    n, labels, stats, centers = cv2.connectedComponentsWithStats(mask, connectivity=8)
    expected = math.pi * axes[0] * axes[1] / 4
    candidates = []
    for i in range(1, n):
        a, b, w, h, area = stats[i]
        cx, cy = centers[i] + (left, top)
        distance = math.hypot(cx - x, cy - y)
        if (distance <= radius and .25 * expected <= area <= 1.35 * expected
                and max(w, h) <= 1.5 * max(axes) and area >= .35 * w * h):
            candidates.append((distance, i))
    for unused, i in sorted(candidates):
        a, b, w, h, unused = stats[i]
        component = labels[b:b+h, a:a+w] == i
        # Padding prevents the transform from overestimating support at an edge.
        thick = cv2.distanceTransform(np.pad(component.astype(np.uint8), 1), cv2.DIST_L2, 5)
        if float(thick.max()) >= max(4, .18 * min(axes)):
            patch = np.s_[top+b:top+b+h, left+a:left+a+w]
            # A material part of the proposed shell must be new, not merely
            # a static patch that happens to cross the appearance thresholds.
            if np.count_nonzero(changed[patch] & component) < .35 * int(component.sum()):
                continue
            if matches_shell(track, *shell_appearance(image[patch][component], deviations[patch][component]),
                             relaxed=True):
                reserved[patch] = True
                return True
    return False


def advance(value, image, detections, previous_count=None, threshold=8, whole_partition=False):
    """Pure staged update; caller persists memory with nest decisions atomically."""
    value = copy.deepcopy(value) if value is not None else empty_tracker()
    if previous_count is not None and not value['tracks'] and value['next_id'] == 1:
        value['unlocated'] = max(value['unlocated'], previous_count)
    tracks = value['tracks']
    if not tracks and not detections:
        value.pop('relocation_baseline', None)
        return value, value['unlocated']
    # Global nearest-first, one-to-one associations. An egg can roll modestly;
    # one detection must never keep two remembered eggs visible.
    pairs = []
    for i, track in enumerate(tracks):
        center, axes, unused = track['ellipse']
        for j, detection in enumerate(detections):
            other, other_axes, unused = detection['ellipse']
            distance = math.hypot(center[0] - other[0], center[1] - other[1])
            ratio = (other_axes[0] * other_axes[1]) / (axes[0] * axes[1])
            gate = max(12, min(max(axes), max(other_axes)) * .65)
            if distance <= gate and .45 <= ratio <= 2.2:
                pairs.append((distance / gate, i, j))
    matched, used = {}, set()
    for unused, i, j in sorted(pairs):
        if i not in matched and j not in used:
            matched[i] = j
            used.add(j)
    # Only confirmed tracks with actual absence evidence get a wider search.
    # Match strict detections first; a relaxed search never creates or re-anchors
    # a track. Neither path lets another egg's evidence be reused.
    unmatched = [i for i, t in enumerate(tracks) if i not in matched and t['hits'] == CONFIRM]
    deviations = texture(image) if unmatched else None
    smooth = deviations < threshold if deviations is not None else None
    statuses = {i: probe(image, smooth, tracks[i]) for i in unmatched}
    missing = [i for i, status in statuses.items() if status == 'absent']
    distant = []
    if missing:
        for j, detection in enumerate(detections):
            if j in used:
                continue
            appearance = detected_appearance(image, detection['ellipse'], threshold, deviations, smooth)
            if appearance is None:
                continue
            brightness, profile = appearance
            center, axes, unused = detection['ellipse']
            for i in missing:
                track = tracks[i]
                prior, prior_axes, unused = track['ellipse']
                distance = math.hypot(center[0] - prior[0], center[1] - prior[1])
                ratio = (axes[0] * axes[1]) / (prior_axes[0] * prior_axes[1])
                if ((whole_partition or distance <= relocation_radius(track)) and .65 <= ratio <= 1.55
                        and matches_shell(track, brightness, profile)):
                    distant.append((distance, i, j))
        for unused, i, j in sorted(distant):
            if i not in matched and j not in used:
                matched[i] = j
                used.add(j)

        baseline = decode_baseline(value.get('relocation_baseline'))
        if baseline is not None and any(i not in matched for i in missing):
            current = baseline_pixels(image)
            delta = cv2.absdiff(current, baseline)
            changed = cv2.resize(delta, image.shape[::-1], interpolation=cv2.INTER_LINEAR) >= 15
            reserved = np.zeros(image.shape, dtype=bool)
            for detection in detections:
                reserved |= surface(image, detection['ellipse'])
            for i, track in enumerate(tracks):
                if i not in matched and statuses.get(i) != 'absent':
                    reserved |= surface(image, track['ellipse'])
            for i in missing:
                if i not in matched and relocated_shell(image, deviations, smooth, tracks[i], reserved,
                                                        whole_partition, changed):
                    statuses[i] = 'obstructed'
                elif (i not in matched and tracks[i].get('check_bedding') and
                      bedding_disturbed(baseline, current, image.shape, tracks[i])):
                    statuses[i] = fragment_status(image, tracks[i], reserved)
    kept = []
    for i, track in enumerate(tracks):
        if i in matched:
            detection = detections[matched[i]]
            track.pop('check_bedding', None)
            track.pop('fragment', None)
            track.update(ellipse=detection['ellipse'], hits=min(CONFIRM, track['hits'] + 1),
                         absent=0, status='visible')
            # Refresh only on strict full detections, never learn covering straw.
            appearance = detected_appearance(image, track['ellipse'], threshold, deviations, smooth)
            if appearance is not None:
                track['brightness'], track['shell_profile'] = appearance
        elif track['hits'] < CONFIRM:
            continue
        else:
            track['status'] = statuses[i]
            track['absent'] = track['absent'] + 1 if track['status'] == 'absent' else 0
            if track['absent'] >= CONFIRM:
                continue
        kept.append(track)
    for j, detection in enumerate(detections):
        if j in used or len(kept) >= MAX_TRACKS:
            continue
        appearance = detected_appearance(image, detection['ellipse'], threshold, deviations, smooth)
        if appearance is None:
            continue
        anchored = bool(value['unlocated'])
        brightness, profile = appearance
        kept.append({'id': value['next_id'], 'ellipse': detection['ellipse'],
                     'brightness': brightness, 'shell_profile': profile, 'hits': CONFIRM if anchored else 1,
                     'absent': 0, 'status': 'visible', 'counted': anchored})
        value['next_id'] += 1
        # Anchor an older count without announcing it as another new egg.
        if value['unlocated']:
            value['unlocated'] -= 1
    value['tracks'] = kept
    confirmed = [t for t in kept if t['hits'] == CONFIRM]
    if not confirmed:
        value.pop('relocation_baseline', None)
    elif all(t['status'] == 'visible' for t in confirmed):
        # Freeze while any confirmed shell is missing. Otherwise a relocated
        # shell could become the background before it is strictly detected.
        value['relocation_baseline'] = base64.b64encode(baseline_pixels(image).tobytes()).decode('ascii')
    return value, sum(t['hits'] == CONFIRM for t in kept) + value['unlocated']
