"""Offline image categorization only; no camera capture, .env, or network."""
import unittest
from unittest.mock import patch

from chicken_device import categorize, DeviceError, storage_category


class CategorizationTests(unittest.TestCase):
    def test_known_hens_are_not_automatically_reference_images(self):
        for name in ('hen_a', 'hen_b', 'hen_c'):
            self.assertEqual(storage_category({'label': name}), (name, 'unreviewed'))

    def test_unknown_has_separate_directory(self):
        self.assertEqual(storage_category({'label': 'unknown'}), ('unknown',))

    def test_empty_and_eggs(self):
        for category in ('empty', 'eggs'):
            self.assertEqual(storage_category({'label': 'no_chicken'}, category), (category,))

    def test_invalid_labels_cannot_escape_directory(self):
        for label in ('../outside', '/outside', 'bad/name', 'UPPER', ''):
            with self.assertRaises(DeviceError):
                storage_category({'label': label})

    @patch('chicken_device.measure', return_value={})
    @patch('chicken_device.identify', return_value={'label': 'hen_a'})
    @patch('chicken_device.classify')
    def test_hen_takes_priority_without_examining_eggs(self, classifier, identifier, measurement):
        prediction, category = categorize('offline.jpg', [])
        self.assertEqual(category, ('hen_a', 'unreviewed'))
        classifier.assert_not_called()

    @patch('chicken_device.measure', return_value={})
    @patch('chicken_device.identify', return_value={'label': 'no_chicken'})
    @patch('chicken_device.classify', return_value=('eggs', 1500, [(1, 2, 3, 4)]))
    def test_eggs_do_not_change_empty_chicken_sensor(self, classifier, identifier, measurement):
        prediction, category = categorize('offline.jpg', [])
        self.assertEqual(category, ('eggs',))
        self.assertEqual(prediction['label'], 'no_chicken')

    @patch('chicken_device.measure', return_value={})
    @patch('chicken_device.identify', return_value={'label': 'no_chicken'})
    @patch('chicken_device.classify', return_value=('chicken', 5000, []))
    def test_disagreeing_presence_detectors_are_conservative(self, classifier, identifier, measurement):
        prediction, category = categorize('offline.jpg', [])
        self.assertEqual(category, ('unknown',))
        self.assertEqual(prediction['label'], 'unknown')

    @patch('chicken_device.measure', return_value={})
    @patch('chicken_device.identify', return_value={'label': 'no_chicken'})
    @patch('chicken_device.classify')
    def test_storage_disabled_does_not_need_egg_processing(self, classifier, identifier, measurement):
        prediction, category = categorize('offline.jpg', [], detect_eggs=False)
        self.assertIsNone(category)
        classifier.assert_not_called()


if __name__ == '__main__':
    unittest.main()
