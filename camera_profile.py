"""Optional, local-only camera exclusions shared by the offline helpers.

Coordinates use the 640x360 processing frame. No profile means no exclusions.
The profile is data, never Python code; it is independent of .env.
"""
import json
from pathlib import Path


def camera_bounds():
    path = Path(__file__).resolve().parent / 'camera-profile.json'
    if not path.exists():
        return 0, 640, []
    with path.open() as handle:
        profile = json.load(handle)
    if not isinstance(profile, dict):
        raise ValueError('Invalid local camera profile')
    bounds = profile.get('horizontal_bounds', [0, 640])
    rectangles = profile.get('excluded_rectangles', [])
    if (not isinstance(bounds, list) or len(bounds) != 2
            or any(type(v) is not int for v in bounds)
            or not 0 <= bounds[0] < bounds[1] <= 640):
        raise ValueError('Invalid local camera horizontal bounds')
    if not isinstance(rectangles, list):
        raise ValueError('Invalid local camera exclusions')
    for rect in rectangles:
        if (not isinstance(rect, list) or len(rect) != 4
                or any(type(v) is not int for v in rect)
                or not 0 <= rect[0] < rect[2] <= 640
                or not 0 <= rect[1] < rect[3] <= 360):
            raise ValueError('Invalid local camera exclusion rectangle')
    return bounds[0], bounds[1], rectangles


def exclude_camera_regions(mask):
    left, right, rectangles = camera_bounds()
    mask[:, :left] = 0
    mask[:, right:] = 0
    for x1, y1, x2, y2 in rectangles:
        mask[y1:y2, x1:x2] = 0
    return left, right
