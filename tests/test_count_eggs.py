"""Synthetic offline egg detection only; never exercises camera capture."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from count_eggs import count_image, detect_eggs, main


def nest(centers=()):
    # High-frequency synthetic straw, with smooth elliptical foreground objects.
    rng = np.random.RandomState(7)
    image = rng.randint(35, 210, (360, 640)).astype(np.uint8)
    for center in centers:
        cv2.ellipse(image, center, (23, 30), 15, 0, 360, 140, -1)
    return image


class EggTests(unittest.TestCase):
    def test_empty_textured_nest(self):
        self.assertEqual(detect_eggs(nest()), [])

    def test_one_and_separated_eggs(self):
        for centers in ([(180, 180)], [(180, 180), (340, 220)]):
            self.assertEqual(len(detect_eggs(nest(centers))), len(centers))

    def test_touching_eggs(self):
        self.assertEqual(len(detect_eggs(nest([(180, 180), (217, 180)]))), 2)

    def test_location_is_not_a_detection_requirement(self):
        for center in ((90, 90), (320, 180), (530, 270)):
            self.assertEqual(len(detect_eggs(nest([center]))), 1)

    def test_clipped_objects_are_not_counted(self):
        self.assertEqual(detect_eggs(nest([(0, 180)])), [])

    @patch('count_eggs.detect_eggs')
    @patch('count_eggs.measure', return_value={'chicken': True})
    @patch('count_eggs.cv2.imread', return_value=nest([(180, 180)]))
    def test_hen_suppresses_all_eggs(self, read, measure, detect):
        result = count_image('synthetic.jpg', None)
        self.assertEqual(result['status'], 'skipped_chicken')
        self.assertIsNone(result['count'])
        detect.assert_not_called()

    def test_undecodable_image_fails(self):
        with patch('count_eggs.cv2.imread', return_value=None):
            with self.assertRaises(ValueError):
                count_image('synthetic.jpg', None)

    def test_report_accepts_renamed_frames_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'eggs'
            source.mkdir()
            image = source / 'reviewed-egg.jpg'
            cv2.imwrite(str(image), nest([(180, 180)]))
            original = image.read_bytes()
            report = root / 'report'
            with patch('count_eggs.background_mask', return_value=np.zeros((360, 640), bool)):
                self.assertEqual(main([str(source), '--references', str(root / 'refs'),
                                       '--output', str(report)]), 0)
                # Existing reports must never be overwritten.
                self.assertEqual(main([str(source), '--references', str(root / 'refs'),
                                       '--output', str(report)]), 1)
            self.assertEqual(image.read_bytes(), original)
            self.assertTrue((report / 'counts.csv').is_file())
            self.assertTrue((report / 'annotated' / '0000.jpg').is_file())
            self.assertTrue((report / 'sheet-001.jpg').is_file())

    def test_missing_background_calibration_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cv2.imwrite(str(root / 'synthetic.jpg'), nest())
            with patch('count_eggs.background_mask', return_value=None):
                self.assertEqual(main([temp, '--references', str(root / 'refs'),
                                       '--output', str(root.parent / 'unused-report')]), 1)


if __name__ == '__main__':
    unittest.main()
