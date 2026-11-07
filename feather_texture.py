"""Position-free texture novelty against reviewed empty-nest images.

Local rotation-invariant binary-pattern histograms describe texture, not image
coordinates or hen identities. This is a presence/measurement fallback only;
appearance distances and identity margins remain unchanged.
"""
import cv2
import numpy as np


MAX_SAMPLES = 2048
PATCH = 32
STEP = 8


def patches(gray, step):
    """Bounded, equal-scale texture histograms and their sampling grid."""
    pixels = gray.astype(np.float32)
    yy, xx = np.indices(gray.shape, dtype=np.float32)
    ys, xs = np.mgrid[PATCH // 2:gray.shape[0] - PATCH // 2 + 1:step,
                      PATCH // 2:gray.shape[1] - PATCH // 2 + 1:step]
    channels = []
    for radius in (1, 2, 4):
        bits = []
        for index in range(8):
            angle = index * 2 * np.pi / 8
            sample = cv2.remap(pixels, (xx + radius * np.cos(angle)).astype(np.float32),
                               (yy + radius * np.sin(angle)).astype(np.float32), cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REFLECT_101)
            # Ignore near-ties: JPEG noise on a flat shell or repeated straw
            # must not turn an otherwise identical patch into novel texture.
            bits.append(sample >= pixels - 2)
        transitions = sum(bits[index] != bits[(index + 1) % 8] for index in range(8))
        count = sum(bit.astype(np.uint8) for bit in bits)
        code = np.where(transitions <= 2, count, 9)
        for value in range(10):
            histogram = cv2.boxFilter((code == value).astype(np.float32), -1, (PATCH, PATCH))
            channels.append(np.sqrt(np.maximum(histogram[ys, xs], 0) / 3))
    return np.stack(channels, axis=-1).reshape((-1, 30)), (ys, xs)


def sample(values, limit):
    if len(values) <= limit:
        return values
    return values[np.linspace(0, len(values) - 1, limit).astype(int)]


class FeatherTexture:
    """Small in-memory empty-texture dictionary; never stores identity labels."""

    def __init__(self, frames):
        if not frames:
            raise ValueError('Reviewed empty images are required')
        per_frame = max(1, MAX_SAMPLES // len(frames))
        groups = [sample(patches(frame, PATCH)[0], per_frame) for frame in frames]
        self.samples = sample(np.concatenate(groups), MAX_SAMPLES)
        matcher = cv2.BFMatcher(cv2.NORM_L2)
        distances = []
        # Hold out each empty image, rather than matching a patch to itself.
        # A singleton uses its second neighbour and retains the absolute floor.
        for index, group in enumerate(groups):
            queries = sample(group, 64)
            if len(groups) == 1:
                matches = matcher.knnMatch(queries, self.samples, k=2)
                distances.extend(pair[1].distance for pair in matches if len(pair) == 2)
            else:
                others = sample(np.concatenate(groups[:index] + groups[index + 1:]), MAX_SAMPLES)
                distances.extend(match.distance for match in matcher.match(queries, others))
        # Hellinger-style histogram distance. The floor avoids reacting to tiny
        # straw/codec changes; diverse backgrounds can only tighten this gate.
        self.threshold = max(0.08, float(np.percentile(distances, 99))) if distances else 1.0

    def mask(self, gray, std, exclusions, chicken_area, texture_threshold):
        descriptors, (ys, xs) = patches(gray, STEP)
        matches = cv2.BFMatcher(cv2.NORM_L2).match(descriptors, self.samples)
        distances = np.array([match.distance for match in matches], np.float32).reshape(ys.shape)
        yy, xx = np.indices(gray.shape, dtype=np.float32)
        dense = cv2.remap(distances, (xx - PATCH // 2) / STEP, (yy - PATCH // 2) / STEP,
                          cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        novel = (dense > self.threshold).astype(np.uint8) * 255
        novel = cv2.morphologyEx(novel, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        novel = cv2.morphologyEx(novel, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
        result = np.zeros(gray.shape, np.uint8)
        for contour in cv2.findContours(novel, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]:
            if cv2.contourArea(contour) < chicken_area:
                continue
            region = np.zeros(gray.shape, np.uint8)
            cv2.drawContours(region, [contour], -1, 255, -1)
            inside = (region != 0) & (~exclusions)
            support = inside & (dense > self.threshold)
            if np.count_nonzero(support) < chicken_area:
                continue
            # Eggs, smooth walls, thin edges and disconnected straw changes do
            # not supply a broad, genuinely textured body. Fill contour holes,
            # not convex hulls; exclusions still cannot become measurements.
            thickness = cv2.distanceTransform(region, cv2.DIST_L2, 5)
            textured = cv2.distanceTransform(((std >= texture_threshold) & inside).astype(np.uint8),
                                             cv2.DIST_L2, 5)
            if (float(thickness[inside].max()) < 22
                    or float(textured.max()) < 14
                    or float(np.mean(std[inside] < texture_threshold)) >= 0.75):
                continue
            result[inside] = 255
        return result
