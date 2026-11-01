"""Draw public offline fixtures from scratch; never reads camera/private data.

Run with Python + NumPy/OpenCV. Tests consume the committed JPEGs, not freshly
generated replacements. Keep this renderer independent of detector thresholds.
"""
from __future__ import print_function

import json
from pathlib import Path

import cv2
import numpy as np


DESTINATION = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'nest_presence'
PILE_ROWS = {3: [2, 1], 6: [3, 2, 1], 9: [3, 4, 2],
             12: [4, 4, 4], 15: [4, 5, 4, 2]}


def bedding(seed, brightness=1.0):
    rng = np.random.RandomState(seed)
    image = rng.randint(40, 205, (360, 640)).astype(np.uint8)
    # Synthetic straw, no sampled/cropped photo textures.
    for unused in range(450):
        x, y = rng.randint(0, 640), rng.randint(0, 360)
        end = (int(x + rng.randint(-45, 46)), int(y + rng.randint(-45, 46)))
        cv2.line(image, (int(x), int(y)), end, int(rng.randint(35, 220)), 1)
    image = np.clip(image.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
    image[:, 610:] = 105  # A smooth wall excluded by empty-reference calibration.
    return image


def egg(image, center, angle, shade=145):
    mask = np.zeros(image.shape, np.uint8)
    cv2.ellipse(mask, center, (25, 29), angle, 0, 360, 255, -1)
    yy, xx = np.indices(image.shape)
    surface = np.clip(shade + (xx - center[0]) * 0.10 - (yy - center[1]) * 0.12,
                      0, 255).astype(np.uint8)
    image[mask != 0] = surface[mask != 0]
    # Visible contact boundary: adjacent shells are not a single painted blob.
    cv2.ellipse(image, center, (25, 29), angle, 0, 360, shade - 30, 1)


def pile(image, count, rng, origin=(205, 265)):
    centers = []
    for row, length in enumerate(PILE_ROWS[count]):
        for column in range(length):
            centers.append((origin[0] + 42 * column + 21 * (row % 2) + int(rng.randint(-2, 3)),
                            origin[1] - 38 * row + int(rng.randint(-2, 3))))
    for center in sorted(centers, key=lambda p: p[1]):
        egg(image, center, float(rng.uniform(-35, 35)), int(rng.randint(130, 165)))


def hen(image, center=(305, 180), axes=(105, 74), shade=100, pattern=None):
    body = np.zeros(image.shape, np.uint8)
    cv2.ellipse(body, center, axes, -12, 0, 360, 255, -1)
    # Abstract head and tail extend the silhouette, without real-hen markings.
    cv2.circle(body, (center[0] + axes[0] - 5, center[1] - 30), max(14, axes[1] // 3), 255, -1)
    plumage = np.full(image.shape, shade, np.uint8)
    if pattern:
        step = 18 if pattern == 'coarse' else 15
        for y in range(0, 360, step):
            for x in range(0, 640, step):
                cv2.ellipse(plumage, (x + (y // step % 2) * (step // 2), y),
                            (step // 2, step // 3), 15, 10, 170, shade - 50, 2)
    image[body != 0] = plumage[body != 0]


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    cases = []

    def save(name, image, category, count=0, description=''):
        filename = 'synthetic_' + name + '.jpg'
        if not cv2.imwrite(str(DESTINATION / filename), image, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            raise OSError('Cannot write synthetic fixture')
        cases.append({'file': filename, 'category': category, 'visible_eggs': count,
                      'description': description})

    for index, brightness in enumerate((1.0, 0.78, 1.12, 0.9)):
        save('empty_{}'.format(index + 1), bedding(100 + index, brightness), 'empty',
             description='Independent synthetic straw and a smooth background wall')
    for count in (1, 3, 9, 15):
        image = bedding(200 + count)
        rng = np.random.RandomState(200 + count)
        centers = [(85 + column * 105, 70 + row * 104) for row in range(3) for column in range(5)]
        rng.shuffle(centers)
        for center in centers[:count]:
            egg(image, center, float(rng.uniform(-40, 40)))
        save('eggs_{:02d}'.format(count), image, 'egged', count,
             'Separated shells with varied positions and rotations')
    for count in (3, 6, 9, 12, 15):
        image = bedding(300 + count)
        pile(image, count, np.random.RandomState(300 + count))
        save('pile_{:02d}'.format(count), image, 'clustered', count,
             'One touching pile, partial shell occlusions, and contact seams')
    hens = [
        ('dark', {}, 45), ('light', {}, 205),
        ('coarse', {'pattern': 'coarse'}, 130),
        ('fine', {'pattern': 'fine'}, 125),
        ('small_patterned', {'axes': (62, 49), 'pattern': 'coarse'}, 130),
        ('clipped_left', {'center': (15, 180)}, 95),
        ('clipped_right', {'center': (585, 180)}, 95),
        ('clipped_top', {'center': (300, 15)}, 95),
        ('clipped_bottom', {'center': (300, 345)}, 95),
        ('with_eggs', {'pattern': 'coarse'}, 130),
        ('beside_pile', {'center': (500, 130), 'axes': (62, 49)}, 95)]
    for index, (name, kwargs, shade) in enumerate(hens):
        image = bedding(400 + index)
        count = 0
        if name == 'with_eggs':
            count = 3
            for center in ((95, 90), (120, 240), (495, 265)):
                egg(image, center, 10)
        if name == 'beside_pile':
            count = 15
            pile(image, count, np.random.RandomState(490), origin=(130, 265))
        hen(image, shade=shade, **kwargs)
        save('hen_' + name, image, 'chickened', count,
             'Abstract drawn hen: ' + name.replace('_', ' '))
    manifest = {'provenance': 'Entirely procedural; no camera images, names, or sampled textures.',
                'size': [640, 360], 'cases': cases}
    (DESTINATION / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Wrote {} synthetic image fixtures.'.format(len(cases)))


if __name__ == '__main__':
    main()
