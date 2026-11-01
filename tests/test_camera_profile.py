"""Synthetic local-profile checks only; no camera or real configuration reads."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from camera_profile import camera_bounds, exclude_camera_regions


class CameraProfileTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='munai-profile-test-')
        self.root = Path(self.scratch.name)
        self.patch = patch('camera_profile.__file__', str(self.root / 'camera_profile.py'))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.scratch.cleanup()

    def write_profile(self, data):
        with (self.root / 'camera-profile.json').open('w') as handle:
            json.dump(data, handle)

    def test_missing_profile_considers_full_frame(self):
        self.assertEqual(camera_bounds(), (0, 640, []))
        mask = np.full((360, 640), 255, np.uint8)
        exclude_camera_regions(mask)
        self.assertTrue(np.all(mask == 255))

    def test_synthetic_profile_excludes_only_selected_pixels(self):
        self.write_profile({'horizontal_bounds': [10, 630],
                            'excluded_rectangles': [[40, 50, 60, 70]]})
        mask = np.full((360, 640), 255, np.uint8)
        self.assertEqual(exclude_camera_regions(mask), (10, 630))
        expected = np.full((360, 640), 255, np.uint8)
        expected[:, :10] = 0
        expected[:, 630:] = 0
        expected[50:70, 40:60] = 0
        np.testing.assert_array_equal(mask, expected)

    def test_empty_profile_is_generic(self):
        self.write_profile({})
        self.assertEqual(camera_bounds(), (0, 640, []))

    def test_invalid_profiles_are_rejected(self):
        for profile in ([], None, {'horizontal_bounds': [0, 641]},
                        {'horizontal_bounds': [50, 10]},
                        {'horizontal_bounds': [False, 640]},
                        {'excluded_rectangles': None},
                        {'excluded_rectangles': [[0, 0, 20, 361]]},
                        {'excluded_rectangles': [[20, 20, 10, 10]]},
                        {'excluded_rectangles': ['invalid']}):
            self.write_profile(profile)
            with self.assertRaises(ValueError):
                camera_bounds()

    def test_invalid_json_is_not_silently_ignored(self):
        (self.root / 'camera-profile.json').write_text('{')
        with self.assertRaises(ValueError):
            camera_bounds()


if __name__ == '__main__':
    unittest.main()
