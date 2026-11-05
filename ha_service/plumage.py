"""Conservative appearance-only second opinions for unknown split identities."""
import cv2
import numpy as np

from recognize_chickens import SCALES


RATIO = 0.8
MAX_PATCHES = 96


def describe(gray, pixels, std, inside, background, edges):
    """Summarize feather texture and local appearance; never retain coordinates."""
    yy, xx = np.indices(gray.shape, dtype=np.float32)
    texture = []
    for radius in (1, 2, 4):
        bits = []
        for index in range(8):
            angle = index * 2 * np.pi / 8
            sample = cv2.remap(pixels, xx + radius * np.cos(angle),
                               yy + radius * np.sin(angle), cv2.INTER_LINEAR)
            bits.append(sample >= pixels)
        transitions = sum(bits[index] != bits[(index + 1) % 8] for index in range(8))
        count = sum(bit.astype(np.uint8) for bit in bits)
        # Rotation-invariant uniform local binary patterns, plus a mixed bin.
        code = np.where(transitions <= 2, count, 9)
        histogram = np.histogram(code[inside], bins=10, range=(-0.5, 9.5))[0].astype(float) + 1e-6
        texture.extend(np.sqrt(histogram / histogram.sum()) / np.sqrt(3))

    height, width = gray.shape
    positions = []
    for side in (24, 40):
        for y in range(0, height - side + 1, side // 2):
            for x in range(0, width - side + 1, side // 2):
                if np.mean(inside[y:y + side, x:x + side]) >= 0.8:
                    positions.append((x, y, side))
    if len(positions) > MAX_PATCHES:
        positions = [positions[index] for index in
                     np.linspace(0, len(positions) - 1, MAX_PATCHES).astype(int)]
    brightness = pixels / background
    patches = []
    for x, y, side in positions:
        view = (slice(y, y + side), slice(x, x + side))
        keep = inside[view]
        bright, local_std = brightness[view][keep], std[view][keep]
        values = list(np.percentile(bright, [25, 50, 75]))
        values += [float(np.mean(bright < 0.55))]
        values += list(np.percentile(local_std, [50, 75]))
        values += [float(np.mean(edges[view][keep] != 0))]
        patches.append(np.array(values) / SCALES / np.sqrt(7))
    return {'texture': np.array(texture),
            'patches': np.array(patches, dtype=np.float32).reshape((-1, 7))}


def usable(appearance):
    if not isinstance(appearance, dict):
        return False
    texture, patches = appearance.get('texture'), appearance.get('patches')
    return (isinstance(texture, np.ndarray) and texture.shape == (30,)
            and isinstance(patches, np.ndarray) and patches.ndim == 2
            and patches.shape[1] == 7 and 0 < len(patches) <= MAX_PATCHES
            and np.isfinite(texture).all() and np.isfinite(patches).all())


def patch_distance(query, reference):
    squared = np.maximum(0, np.sum(query * query, axis=1)[:, None]
                         + np.sum(reference * reference, axis=1)[None, :]
                         - 2 * np.dot(query, reference.T))

    def trimmed(values):
        return float(np.mean(np.sort(values)[:max(1, int(len(values) * 0.85))]))

    return 0.5 * (trimmed(np.sqrt(squared.min(axis=1)))
                  + trimmed(np.sqrt(squared.min(axis=0))))


def recover(decision, measurement, references, max_distance):
    """Upgrade only an unknown with two agreeing, separated, bounded matches.

    Preserve the original distance/margin diagnostics and all accepted decisions.
    Every reference must support both opinions, so an unmeasurable competitor
    cannot disappear from the comparison. No image, reference or label is saved.
    """
    if (decision['label'] != 'unknown' or measurement.get('features') is None
            or not measurement.get('chicken') or decision['distance'] is None
            or decision['distance'] > max_distance):
        return decision
    appearance = measurement.get('appearance')
    if not usable(appearance) or not references:
        return decision
    scores = ({}, {})
    for label, unused_path, reference in references:
        other = reference.get('appearance')
        if not usable(other) or reference.get('features') is None:
            return decision
        delta = (np.array(measurement['features']) - reference['features']) / SCALES
        base = float(np.sqrt(np.mean(delta * delta)))
        texture = float(np.linalg.norm(appearance['texture'] - other['texture']))
        distances = (float(np.sqrt(base * base + 4 * texture * texture)),
                     patch_distance(appearance['patches'], other['patches']))
        for values, distance in zip(scores, distances):
            values[label] = min(values.get(label, float('inf')), distance)
    winners = []
    for values in scores:
        ranked = sorted((distance, label) for label, distance in values.items())
        if len(ranked) < 2:
            return decision
        best, second = ranked[:2]
        if (not np.isfinite(best[0]) or not np.isfinite(second[0])
                or best[0] > max_distance or second[0] <= 0
                or best[0] >= second[0] or best[0] > RATIO * second[0]):
            return decision
        winners.append(best[1])
    if winners[0] != winners[1]:
        return decision
    # The winner must also fit the configured absolute brightness/texture bound.
    fits = any(label == winners[0] and float(np.sqrt(np.mean(
        ((np.array(measurement['features']) - reference['features']) / SCALES) ** 2)))
        <= max_distance for label, unused_path, reference in references)
    return dict(decision, label=winners[0]) if fits else decision
