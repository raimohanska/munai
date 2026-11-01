"""Offline recognition logic only; no camera capture or configuration access."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import cv2

from recognize_chickens import background_mask, identify, load_references, measure, SCALES


def measurement(features=None, chicken=True):
    return {'area': 10000 if chicken else 0, 'chicken': chicken,
            'box': None, 'features': features}


def reference(label, features, name=None):
    return (label, Path(name or label), measurement(features))


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.dark = np.zeros(7).tolist()
        self.light = (SCALES * 3).tolist()
        self.references = [reference('hen_a', self.dark), reference('hen_b', self.light)]

    def test_no_chicken(self):
        self.assertEqual(identify(measurement(chicken=False), self.references)['label'], 'no_chicken')

    def test_unmeasurable_chicken_is_unknown(self):
        self.assertEqual(identify(measurement(), self.references)['label'], 'unknown')

    def test_exact_reference(self):
        result = identify(measurement(self.dark), self.references)
        self.assertEqual(result['label'], 'hen_a')
        self.assertEqual(result['distance'], 0)

    def test_tied_identities_are_unknown(self):
        result = identify(measurement((SCALES * 1.5).tolist()), self.references)
        self.assertEqual(result['label'], 'unknown')

    def test_identity_ignores_position_and_body_box(self):
        expected = identify(measurement(self.dark), self.references)
        # Reference locations deliberately disagree with the query location.
        self.references[0][2]['box'] = [20, 40, 140, 280]
        self.references[1][2]['box'] = [200, 240, 350, 100]
        for box in ([20, 40, 140, 280], [200, 240, 350, 100],
                    [430, 40, 150, 280]):
            query = measurement(self.dark)
            query['box'] = box
            query['area'] = box[2] * box[3]
            self.assertEqual(identify(query, self.references), expected)

    def test_matching_reference_position_does_not_resolve_ambiguity(self):
        query = measurement((SCALES * 1.5).tolist())
        query['box'] = [20, 40, 140, 280]
        self.references[0][2]['box'] = query['box']
        self.references[1][2]['box'] = [430, 40, 150, 280]
        self.assertEqual(identify(query, self.references)['label'], 'unknown')

    def test_distant_match_is_unknown(self):
        result = identify(measurement((SCALES * 10).tolist()), self.references)
        self.assertEqual(result['label'], 'unknown')

    def test_margin_compares_identities_not_two_images_of_same_hen(self):
        refs = self.references + [reference('hen_a', self.dark, 'second-hen_a')]
        self.assertEqual(identify(measurement(self.dark), refs)['label'], 'hen_a')

    def test_self_exclusion(self):
        refs = self.references + [reference('hen_a', (SCALES * .1).tolist(), 'second-hen_a')]
        result = identify(measurement(self.dark), refs, exclude=Path('hen_a'))
        self.assertEqual(result['reference'], 'second-hen_a')
        self.assertGreater(result['distance'], 0)

    @patch('recognize_chickens.measure')
    def test_automatic_and_unknown_images_are_not_references(self, mock_measure):
        mock_measure.return_value = measurement(self.dark)
        with tempfile.TemporaryDirectory(prefix='munai-reference-test-') as directory:
            root = Path(directory)
            for folder in ('hen_a', 'hen_b', 'unknown', 'hen_a/unreviewed'):
                destination = root / folder
                destination.mkdir(parents=True, exist_ok=True)
                (destination / '20261101T120000_000000Z.jpg').touch()
            refs = load_references(root)
            self.assertEqual([item[0] for item in refs], ['hen_a', 'hen_b'])
            self.assertEqual(mock_measure.call_count, 2)

    @patch('recognize_chickens.cv2.imread', return_value=None)
    def test_undecodable_image(self, unused):
        with self.assertRaises(ValueError):
            measure(Path('invalid.jpg'))


class EmptyCalibrationTests(unittest.TestCase):
    """Synthetic offline images only; never test camera capture."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='munai-empty-test-')
        self.root = Path(self.temporary.name)
        self.empty = self.root / 'empty'
        self.empty.mkdir()
        self.rng = np.random.RandomState(19)

    def tearDown(self):
        self.temporary.cleanup()

    def image(self):
        pixels = self.rng.randint(50, 200, (360, 640)).astype(np.uint8)
        pixels[:, 570:] = 110  # Synthetic smooth wall, not a hen.
        return pixels

    def write(self, folder, index, pixels):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ('20000101T120000_{:06d}Z.jpg'.format(index))
        self.assertTrue(cv2.imwrite(str(path), pixels))
        return path

    def calibrate(self):
        for index in range(3):
            pixels = self.image()
            if index == 0:
                pixels[150:170, 250:270] = 220  # An egg does not define a permanent exclusion.
            self.write(self.empty, index, pixels)
        return background_mask(self.root)

    def test_background_removes_wall_false_positive_and_ignores_small_egg(self):
        mask = self.calibrate()
        pixels = self.image()
        pixels[180:200, 300:320] = 220
        path = self.write(self.root, 10, pixels)
        self.assertTrue(measure(path)['chicken'])
        self.assertFalse(measure(path, background_mask=mask)['chicken'])
        self.assertFalse(mask[160, 260])

    def test_hen_is_detected_at_different_positions(self):
        mask = self.calibrate()
        for index, x in enumerate((30, 230, 400)):
            pixels = self.image()
            pixels[80:260, x:x + 120] = 40
            path = self.write(self.root, 20 + index, pixels)
            result = measure(path, background_mask=mask)
            self.assertTrue(result['chicken'])
            self.assertIsNotNone(result['features'])

    def test_empty_references_calibrate_but_are_never_identities(self):
        self.calibrate()
        for index, (label, brightness) in enumerate((('hen_a', 40), ('hen_b', 210))):
            pixels = self.image()
            pixels[80:260, 220:400] = brightness
            self.write(self.root / label, index, pixels)
        refs = load_references(self.root)
        self.assertEqual([item[0] for item in refs], ['hen_a', 'hen_b'])
        self.assertIsNotNone(refs.background_mask)

    def test_capture_predictions_and_nested_empty_images_do_not_calibrate(self):
        self.write(self.root / 'captures' / 'empty', 0, self.image())
        self.write(self.empty / 'unreviewed', 0, self.image())
        self.assertIsNone(background_mask(self.root))

    def test_calibration_is_recomputed_from_changed_reviewed_images(self):
        mask = self.calibrate()
        self.assertFalse(mask[120, 60])
        for index in range(3):
            pixels = self.image()
            pixels[40:240, 20:100] = 100
            self.write(self.empty, index, pixels)
        self.assertTrue(background_mask(self.root)[120, 60])


if __name__ == '__main__':
    unittest.main()
