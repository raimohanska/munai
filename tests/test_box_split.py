"""Synthetic geometry/presence checks; no camera capture or real identities."""
import unittest
from unittest.mock import patch
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np

from box_split import assign_regions, detect, half_boxes
from recognize_chickens import background_mask


def body(x, y, w, h):
    mask = np.zeros((360, 640), dtype=bool)
    mask[y:y + h, x:x + w] = True
    return mask


class SplitTests(unittest.TestCase):
    def test_small_convincing_body_is_uncertain_not_a_visitor(self):
        gray = np.full((360, 640), 120, dtype=np.uint8)
        exclusions = np.zeros(gray.shape, dtype=bool)
        for size, visitor in ((100, False), (200, True)):
            std = np.full(gray.shape, 20, dtype=np.float32)
            std[40:40 + size, 20:20 + size] = 4
            with patch('box_split.image_statistics', return_value=(gray, gray.astype(float), std)):
                result = detect('synthetic', 'vertical', exclusions,
                                chicken_area=35000, fragment_area=3500)
            self.assertEqual(bool(result['bodies']), visitor)
            self.assertEqual(result['uncertain_zones'], [] if visitor else ['left'])
            self.assertEqual(bool(result['fragments']), not visitor)

    def test_crop_coverage_and_names(self):
        for split in ('none', 'vertical', 'horizontal'):
            canvas = np.zeros((361, 641), dtype=int)
            for x1, y1, x2, y2 in half_boxes(641, 361, split).values():
                canvas[y1:y2, x1:x2] += 1
            self.assertTrue(np.all(canvas == 1))
        self.assertEqual(set(half_boxes(640, 360, 'vertical')), {'left', 'right'})
        self.assertEqual(set(half_boxes(640, 360, 'horizontal')), {'top', 'bottom'})

    def test_two_side_bodies_do_not_become_one_center_hen(self):
        result = assign_regions([body(10, 40, 180, 280), body(440, 40, 180, 280)], 'vertical')
        self.assertEqual([b['zone'] for b in result['bodies']], ['left', 'right'])
        self.assertFalse(result['center_present'])
        self.assertFalse(result['ambiguous'])
        self.assertEqual(result['clear_candidates'], [])

    def test_perched_hen_blocks_both_counts(self):
        result = assign_regions([body(210, 180, 220, 170)], 'vertical')
        self.assertTrue(result['center_present'])
        self.assertEqual(len(result['bodies']), 1)
        self.assertEqual(result['blocked_for_eggs'], ['left', 'right'])

    def test_center_and_separate_body_is_ambiguous_not_assumed_impossible(self):
        result = assign_regions([body(260, 180, 130, 170), body(10, 20, 140, 140)], 'vertical')
        self.assertTrue(result['ambiguous'])
        self.assertEqual(result['clear_candidates'], [])

    def test_center_fragments_can_be_grouped(self):
        result = assign_regions([body(260, 180, 140, 170), body(180, 240, 65, 100)], 'vertical')
        self.assertEqual(len(result['bodies']), 1)
        self.assertTrue(result['center_present'])

    def test_small_crossing_is_not_a_second_occupant(self):
        result = assign_regions([body(130, 100, 210, 220)], 'vertical')
        self.assertEqual(result['bodies'][0]['zone'], 'left')
        self.assertEqual(result['clear_candidates'], ['right'])

    def test_large_merged_body_is_ambiguous_even_if_it_spans_center(self):
        result = assign_regions([body(0, 0, 640, 300)], 'vertical')
        self.assertTrue(result['ambiguous'])
        self.assertEqual(result['clear_candidates'], [])

    def test_horizontal_and_empty_and_unsplit(self):
        result = assign_regions([body(10, 10, 260, 120), body(350, 220, 260, 120)], 'horizontal')
        self.assertEqual([b['zone'] for b in result['bodies']], ['top', 'bottom'])
        self.assertEqual(assign_regions([], 'vertical')['clear_candidates'], ['left', 'right'])
        self.assertEqual(assign_regions([body(100, 100, 300, 200)], 'none')['blocked_for_eggs'], ['whole'])

    def test_reject_invalid_geometry(self):
        with self.assertRaises(ValueError):
            half_boxes(640, 360, 'diagonal')
        with self.assertRaises(ValueError):
            assign_regions([np.zeros((360, 640), dtype=bool)], 'vertical')

    def test_synthetic_image_presence_and_egg_piles(self):
        fixtures = Path(__file__).resolve().parent / 'fixtures' / 'nest_presence'
        cases = json.loads((fixtures / 'manifest.json').read_text())['cases']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'empty').mkdir()
            for index, case in enumerate(c for c in cases if c['category'] == 'empty'):
                shutil.copyfile(str(fixtures / case['file']),
                                str(root / 'empty' / ('20000101T120000_{:06d}Z.jpg'.format(index))))
            exclusions = background_mask(root)
            for case in cases:
                with self.subTest(image=case['file']):
                    result = detect(fixtures / case['file'], 'vertical', exclusions)
                    self.assertEqual(bool(result['bodies']), case['category'] == 'chickened')
            with self.assertRaises(ValueError):
                detect(fixtures / cases[0]['file'], 'vertical', None)


if __name__ == '__main__':
    unittest.main()
