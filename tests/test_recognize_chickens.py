"""Offline recognition logic only; no camera capture or configuration access."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from recognize_chickens import identify, load_references, measure, SCALES


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


if __name__ == '__main__':
    unittest.main()
