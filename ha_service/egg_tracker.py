"""Egg-only spatial memory. Strict detections acquire; shell remnants retain.

All coordinates are in the detector's normalized side/whole view. No hen
identity decision uses these locations. A missing ellipse is not a removal.
"""
import copy
import math

import cv2
import numpy as np

MAX_TRACKS = 32
CONFIRM = 3


def empty_tracker():
    return {'tracks': [], 'unlocated': 0, 'next_id': 1}


def finite(value, low, high):
    return (type(value) in (int, float) and math.isfinite(value)
            and low <= value <= high)


def valid_tracker(value):
    if value is None:
        return True  # Pre-tracker baseline, not evidence of no eggs.
    if (not isinstance(value, dict) or set(value) != {'tracks', 'unlocated', 'next_id'}
            or type(value['unlocated']) is not int or not 0 <= value['unlocated'] <= 1000000
            or type(value['next_id']) is not int or not 1 <= value['next_id'] <= 1000000000
            or not isinstance(value['tracks'], list) or len(value['tracks']) > MAX_TRACKS):
        return False
    ids = set()
    for track in value['tracks']:
        if (not isinstance(track, dict) or set(track) !=
                {'id', 'ellipse', 'brightness', 'hits', 'absent', 'status', 'counted'}):
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
        track.update(absent=0, status='uncertain')
    return value


def surface(image, ellipse):
    mask = np.zeros(image.shape, np.uint8)
    center, axes, angle = ellipse
    # Avoid the shell boundary, whose texture includes straw even on a clear egg.
    cv2.ellipse(mask, (tuple(center), tuple(v * .82 for v in axes), angle), 255, -1)
    return mask != 0


def statistics(image, threshold):
    pixels = image.astype(np.float32)
    mean = cv2.blur(pixels, (7, 7))
    std = np.sqrt(np.maximum(0, cv2.blur(pixels * pixels, (7, 7)) - mean * mean))
    return std < threshold


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


def advance(value, image, detections, previous_count=None, threshold=8):
    """Pure staged update; caller persists memory with nest decisions atomically."""
    value = copy.deepcopy(value) if value is not None else empty_tracker()
    if previous_count is not None and not value['tracks'] and value['next_id'] == 1:
        value['unlocated'] = max(value['unlocated'], previous_count)
    smooth = statistics(image, threshold)
    tracks = value['tracks']
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
    kept = []
    for i, track in enumerate(tracks):
        if i in matched:
            detection = detections[matched[i]]
            track.update(ellipse=detection['ellipse'], hits=min(CONFIRM, track['hits'] + 1),
                         absent=0, status='visible')
            # Refresh only on strict full detections, never learn covering straw.
            mask = surface(image, track['ellipse']) & smooth
            if mask.any():
                track['brightness'] = float(np.median(image[mask]))
        elif track['hits'] < CONFIRM:
            continue
        else:
            track['status'] = probe(image, smooth, track)
            track['absent'] = track['absent'] + 1 if track['status'] == 'absent' else 0
            if track['absent'] >= CONFIRM:
                continue
        kept.append(track)
    for j, detection in enumerate(detections):
        if j in used or len(kept) >= MAX_TRACKS:
            continue
        mask = surface(image, detection['ellipse']) & smooth
        if not mask.any():
            continue
        anchored = bool(value['unlocated'])
        kept.append({'id': value['next_id'], 'ellipse': detection['ellipse'],
                     'brightness': float(np.median(image[mask])), 'hits': CONFIRM if anchored else 1,
                     'absent': 0, 'status': 'visible', 'counted': anchored})
        value['next_id'] += 1
        # Anchor an older count without announcing it as another new egg.
        if value['unlocated']:
            value['unlocated'] -= 1
    value['tracks'] = kept
    return value, sum(t['hits'] == CONFIRM for t in kept) + value['unlocated']
