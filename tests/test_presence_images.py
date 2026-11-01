"""Committed synthetic-image regressions, no camera/configuration/network access."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from count_eggs import count_image, detect_eggs
from ha_service.options import validate
from ha_service.runtime import Runtime
from recognize_chickens import background_mask, identify, image_statistics, measure, References
from sort_captures import chicken_region, classify
from tools.check_public_tree import SYNTHETIC_IMAGE_NAMES
from tools.check_public_tree import main as check_public_tree


FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'nest_presence'


class PresenceImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((FIXTURES / 'manifest.json').read_text())['cases']
        cls.temporary = tempfile.TemporaryDirectory(prefix='munai-presence-images-')
        root = Path(cls.temporary.name)
        (root / 'empty').mkdir()
        for index, case in enumerate(c for c in cls.cases if c['category'] == 'empty'):
            destination = root / 'empty' / ('20000101T120000_{:06d}Z.jpg'.format(index))
            shutil.copyfile(str(FIXTURES / case['file']), str(destination))
        cls.exclusions = background_mask(root)
        if cls.exclusions is None:
            raise AssertionError('Committed empty images must produce background calibration')
        cls.references = References(cls.exclusions)
        for identity, name in (('hen_a', 'dark'), ('hen_b', 'light')):
            path = FIXTURES / ('synthetic_hen_' + name + '.jpg')
            cls.references.append((identity, path, measure(path, background_mask=cls.exclusions)))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_fixture_inventory_is_complete_and_decodable(self):
        categories = {name: 0 for name in ('empty', 'egged', 'clustered', 'chickened')}
        names = [case['file'] for case in self.cases]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), {p.name for p in FIXTURES.glob('*.jpg')})
        self.assertEqual(set(names), SYNTHETIC_IMAGE_NAMES)
        for case in self.cases:
            with self.subTest(file=case['file']):
                self.assertEqual(Path(case['file']).name, case['file'])
                self.assertTrue(case['file'].startswith('synthetic_'))
                image = cv2.imread(str(FIXTURES / case['file']), cv2.IMREAD_GRAYSCALE)
                self.assertIsNotNone(image)
                self.assertEqual(image.shape, (360, 640))
                categories[case['category']] += 1
        self.assertEqual(categories, {'empty': 4, 'egged': 4, 'clustered': 5, 'chickened': 11})

    def test_presence_and_presorter_agree_on_every_committed_image(self):
        for case in self.cases:
            with self.subTest(file=case['file']):
                path = FIXTURES / case['file']
                expected_hen = case['category'] == 'chickened'
                result = measure(path, background_mask=self.exclusions)
                self.assertEqual(result['chicken'], expected_hen)
                prediction = identify(result, self.references)
                self.assertEqual(prediction['label'] == 'no_chicken', not expected_hen)
                category, unused_area, unused_eggs = classify(path, background_mask=self.exclusions)
                self.assertEqual(category == 'chicken', expected_hen)
                if expected_hen:
                    self.assertIsNotNone(result['features'])
                    self.assertEqual(unused_eggs, [])
                else:
                    self.assertIsNone(result['features'])
                    self.assertIsNone(result['box'])
                if case['category'] in ('empty', 'egged'):
                    self.assertEqual(category, 'empty' if case['category'] == 'empty' else 'eggs')
                if case['category'] == 'clustered':
                    # Ensure these really reproduce the old area-only false positive.
                    self.assertGreaterEqual(result['area'], 4500)

    def test_egg_counter_runs_for_piles_but_never_for_hens(self):
        for case in self.cases:
            with self.subTest(file=case['file']):
                with patch('count_eggs.detect_eggs', wraps=detect_eggs) as detector:
                    result = count_image(FIXTURES / case['file'], self.exclusions)
                if case['category'] == 'chickened':
                    detector.assert_not_called()
                    self.assertEqual(result['status'], 'skipped_chicken')
                    self.assertIsNone(result['count'])
                else:
                    detector.assert_called_once()
                    self.assertEqual(result['status'], 'estimated')
                    if case['category'] == 'clustered':
                        # Presence fix is not a promise to count every occluded shell.
                        self.assertGreater(result['count'], 0)
                        self.assertLessEqual(result['count'], case['visible_eggs'])
                    else:
                        self.assertEqual(result['count'], case['visible_eggs'])

    def test_images_exercise_both_thickness_and_texture_branches(self):
        for name, broad, textured in (('hen_light', True, False),
                                      ('hen_coarse', False, True),
                                      ('hen_fine', False, True),
                                      ('hen_small_patterned', False, True),
                                      ('pile_15', False, False)):
            with self.subTest(image=name):
                path = FIXTURES / ('synthetic_' + name + '.jpg')
                unused_gray, unused_pixels, std = image_statistics(path)
                valid = (~self.exclusions).astype(np.uint8) * 255
                smooth = ((std < 8) & ~self.exclusions).astype(np.uint8) * 255
                smooth = cv2.morphologyEx(smooth, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
                contour, area, chicken = chicken_region(smooth, valid)
                self.assertGreaterEqual(area, 4500)
                region = np.zeros(smooth.shape, np.uint8)
                cv2.drawContours(region, [contour], -1, 255, -1)
                inside = (region != 0) & (valid != 0)
                distance = cv2.distanceTransform(smooth, cv2.DIST_L2, 5)
                self.assertEqual(float(distance[inside].max()) >= 22, broad)
                self.assertEqual(float(np.mean(smooth[inside] != 0)) < 0.75, textured)
                self.assertEqual(chicken, broad or textured)

    def test_ha_pipeline_uses_the_same_presence_gate(self):
        runtime = Runtime(validate({}), None)
        for case in self.cases:
            with self.subTest(file=case['file']):
                with patch('ha_service.runtime.detect_eggs', wraps=detect_eggs) as detector:
                    result = runtime.predict(FIXTURES / case['file'], self.references)
                if case['category'] == 'chickened':
                    self.assertNotEqual(result['label'], 'no_chicken')
                    self.assertEqual(result['egg_count'], 0)
                    detector.assert_not_called()
                else:
                    self.assertEqual(result['label'], 'no_chicken')
                    detector.assert_called_once()
                    if case['category'] in ('empty', 'egged'):
                        self.assertEqual(result['egg_count'], case['visible_eggs'])

    def test_rejected_larger_pile_does_not_hide_smaller_hen(self):
        path = FIXTURES / 'synthetic_hen_beside_pile.jpg'
        gray, unused_pixels, std = image_statistics(path)
        smooth = ((std < 8) & ~self.exclusions).astype(np.uint8) * 255
        smooth = cv2.morphologyEx(smooth, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        valid = (~self.exclusions).astype(np.uint8) * 255
        joined = cv2.morphologyEx(smooth, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        joined = cv2.bitwise_and(joined, valid)
        contours = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        largest = max(cv2.contourArea(c) for c in contours)
        contour, area, chicken = chicken_region(smooth, valid)
        self.assertTrue(chicken)
        self.assertLess(area, largest)
        self.assertEqual(measure(path, background_mask=self.exclusions)['area'], area)

    def test_position_rotation_and_resolution_do_not_determine_presence(self):
        for name, expected in (('synthetic_pile_15.jpg', False),
                               ('synthetic_hen_coarse.jpg', True),
                               ('synthetic_hen_light.jpg', True)):
            original = cv2.imread(str(FIXTURES / name), cv2.IMREAD_GRAYSCALE)
            for flipped in (False, True):
                # Rotate image and exclusions together; the wall stays excluded.
                image = cv2.flip(original, -1) if flipped else original
                exclusions = cv2.flip(self.exclusions.astype(np.uint8), -1).astype(bool) if flipped else self.exclusions
                for scale in (1, 3):
                    with self.subTest(image=name, rotated=flipped, scale=scale):
                        path = Path(self.temporary.name) / 'transformed.jpg'
                        enlarged = cv2.resize(image, (640 * scale, 360 * scale), interpolation=cv2.INTER_NEAREST)
                        self.assertTrue(cv2.imwrite(str(path), enlarged))
                        self.assertEqual(measure(path, background_mask=exclusions)['chicken'], expected)


class PresenceMaskTests(unittest.TestCase):
    def test_no_candidates_and_fully_excluded_foreground_are_safe(self):
        for smooth, valid in ((np.zeros((360, 640), np.uint8), np.full((360, 640), 255, np.uint8)),
                              (np.zeros((360, 640), np.uint8), np.zeros((360, 640), np.uint8))):
            contour, area, chicken = chicken_region(smooth, valid)
            self.assertIsNone(contour)
            self.assertEqual(area, 0)
            self.assertFalse(chicken)

    def test_egg_sized_region_does_not_override_area_setting(self):
        smooth = np.zeros((360, 640), np.uint8)
        smooth[80:200, 120:240] = 255
        valid = np.full(smooth.shape, 255, np.uint8)
        self.assertTrue(chicken_region(smooth, valid, 4500)[2])
        self.assertFalse(chicken_region(smooth, valid, 20000)[2])


class PublicFixturePrivacyTests(unittest.TestCase):
    def check_paths(self, paths):
        data = ('\0'.join(paths) + '\0').encode('utf-8')
        with patch('tools.check_public_tree.subprocess.check_output', return_value=data):
            with redirect_stdout(io.StringIO()) as output:
                result = check_public_tree()
        return result, output.getvalue()

    def test_only_inventoried_synthetic_images_are_allowed(self):
        paths = ['tests/fixtures/nest_presence/' + name for name in SYNTHETIC_IMAGE_NAMES]
        self.assertEqual(self.check_paths(paths)[0], 0)
        for name in ('tests/fixtures/nest_presence/synthetic_unreviewed.jpg',
                     'tests/fixtures/nest_presence/photo.jpg',
                     'tests/fixtures/other/synthetic_hen_dark.jpg',
                     'captures/synthetic_hen_dark.jpg',
                     'references/synthetic_hen_dark.jpg'):
            with self.subTest(path=name):
                result, output = self.check_paths(paths + [name])
                self.assertEqual(result, 1)
                self.assertNotIn(name, output)


if __name__ == '__main__':
    unittest.main()
