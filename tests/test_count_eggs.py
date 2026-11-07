"""Synthetic offline egg detection only; never exercises camera capture."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from count_eggs import annotated, count_image, detect_eggs, main


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

    def test_smaller_eggs_have_dimension_margin(self):
        # Synthetic baseline axes are (23, 30). Round down after shrinking each
        # dimension 15%, making this slightly stricter than the requested margin.
        axes = (int(23 * 0.85), int(30 * 0.85))
        for center in ((90, 90), (320, 180), (530, 270)):
            image = nest()
            cv2.ellipse(image, center, axes, 15, 0, 360, 140, -1)
            self.assertEqual(len(detect_eggs(image)), 1)

    def test_touching_eggs(self):
        self.assertEqual(len(detect_eggs(nest([(180, 180), (217, 180)]))), 2)

    def test_notched_egg_keeps_smooth_surface_evidence(self):
        image = nest([(180, 180)])
        # A short straw intrusion should not disqualify the entire egg.
        cv2.line(image, (155, 172), (171, 178), 25, 2)
        result = detect_eggs(image)
        self.assertEqual(len(result), 1)
        self.assertGreaterEqual(result[0]['smooth_fraction'], 0.75)

    def test_touching_pair_with_jagged_contact_seam(self):
        image = nest([(180, 180), (217, 180)])
        cv2.line(image, (196, 169), (200, 180), 35, 2)
        cv2.line(image, (200, 180), (196, 190), 35, 2)
        self.assertEqual(len(detect_eggs(image)), 2)

    def test_mostly_visible_border_egg(self):
        for center in ((180, 344), (180, 15), (10, 180), (629, 180)):
            result = detect_eggs(nest([center]))
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0]['partial'])

    def test_smooth_wall_corner_candidate_is_ignored(self):
        # A clipped smooth arc ending in a flat intrusion fits an egg-shaped
        # hull, but lacks enough curved perimeter. No edge exclusion is needed.
        image = nest()
        background = image.copy()
        cv2.ellipse(image, (620, 180), (27, 30), 15, 0, 360, 140, -1)
        image[185:230, 610:660] = background[185:230, 610:660]
        with patch('count_eggs.ellipse_boundary_support', return_value=1):
            self.assertEqual(len(detect_eggs(image)), 1)
        self.assertEqual(detect_eggs(image), [])

    def test_split_crops_retain_eggs_near_edges_and_divider(self):
        for size in ((320, 360), (640, 180)):
            width, height = size
            for center in ((width // 2, height // 2),
                           (30, height // 2), (width - 30, height // 2),
                           (width // 2, 40), (width // 2, height - 40)):
                image = nest()[:height, :width].copy()
                cv2.ellipse(image, center, (23, 30), 15, 0, 360, 140, -1)
                self.assertEqual(len(detect_eggs(image, size=size)), 1)

    def test_smooth_border_wall_is_not_split_into_eggs(self):
        image = nest()
        for center in ((0, 80), (0, 115), (0, 150), (0, 185)):
            cv2.ellipse(image, center, (30, 28), 0, 0, 360, 140, -1)
        self.assertEqual(detect_eggs(image), [])

    def test_plausible_ellipse_outside_its_visible_surface_is_rejected(self):
        original_fit = cv2.fitEllipse

        def unsupported_fit(points):
            center, axes, angle = original_fit(points)
            return ((center[0] + 100, center[1] + 100), axes, angle)

        # Size, aspect and area coverage remain unchanged; spatial support does
        # not. This models an unstable fit from a short smooth wall/corner arc.
        with patch('count_eggs.cv2.fitEllipse', side_effect=unsupported_fit):
            self.assertEqual(detect_eggs(nest([(180, 180)])), [])

    def test_smooth_ring_does_not_hide_textured_bedding(self):
        image = nest()
        cv2.ellipse(image, (180, 180), (30, 35), 0, 0, 360, 140, 13)
        self.assertEqual(detect_eggs(image), [])

    def test_size_and_shape_reject_smooth_non_eggs(self):
        for axes in ((8, 10), (60, 70), (12, 65)):
            image = nest()
            cv2.ellipse(image, (180, 180), axes, 0, 0, 360, 140, -1)
            self.assertEqual(detect_eggs(image), [])

    def test_footer_does_not_obscure_bottom_of_frame(self):
        image = np.full((360, 640, 3), 110, np.uint8)
        with patch('count_eggs.cv2.imread', return_value=image):
            preview = annotated('synthetic.jpg', {'detections': [], 'count': 0})
        self.assertEqual(preview.shape, (390, 640, 3))
        np.testing.assert_array_equal(preview[:360], image)

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
