"""Synthetic split image/reference regressions; no capture or deployment data."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import cv2

from ha_service.options import AppError, validate
from ha_service import split_recognition as split
from recognize_chickens import image_statistics, measure
from count_eggs import detect_eggs


class SplitRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        cls.root = Path(cls.temp.name)
        cls.fixtures = Path(__file__).resolve().parent / 'fixtures' / 'nest_presence'
        cls.cases = json.loads((cls.fixtures / 'manifest.json').read_text())['cases']
        (cls.root / 'empty').mkdir()
        for i in range(1, 5):
            shutil.copyfile(str(cls.fixtures / ('synthetic_empty_{}.jpg'.format(i))),
                            str(cls.root / 'empty' / ('20000101T120000_{:06d}Z.jpg'.format(i))))
        for identity, name in (('hen_a', 'dark'), ('hen_b', 'light')):
            (cls.root / identity).mkdir()
            image = cv2.imread(str(cls.fixtures / ('synthetic_hen_{}.jpg'.format(name))), 0)
            cv2.imwrite(str(cls.root / identity / '20000101T120000_000000Z-center.jpg'), image[:, 160:480])
        cls.options = validate({'box_split': 'vertical'})
        cls.refs = split.load(cls.root, cls.options)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_half_measurement_preserves_full_frame_pixel_scale(self):
        image = cv2.imread(str(self.fixtures / 'synthetic_empty_1.jpg'), 0)
        gray, unused_pixels, unused_std = image_statistics(image[:, :320], size=(320, 360))
        self.assertEqual(gray.shape, (360, 320))
        self.assertEqual(measure(image[:, :320], size=(320, 360))['chicken'], False)
        self.assertEqual(split.measurement_size('horizontal'), (640, 180))

    def test_full_frame_identity_reference_rejected_in_split_mode(self):
        with self.assertRaises(AppError):
            split.validate_half(self.fixtures / 'synthetic_hen_dark.jpg', self.root, 'vertical')

    def test_empty_and_egg_piles_not_named_as_hens(self):
        for case in self.cases:
            if case['category'] == 'chickened':
                continue
            with self.subTest(image=case['file']):
                result = split.predict(self.fixtures / case['file'], self.refs, self.options)
                self.assertTrue(all(i['label'] in ('no_chicken', 'unknown') for i in result['zones'].values()))
                if case['category'] == 'empty':
                    self.assertEqual(result['egg_count'], 0)

    def test_center_detection_does_not_call_egg_detector(self):
        presence = {'ambiguous': False, 'center_present': True, 'bodies': [{'zone': 'center'}]}
        with patch('ha_service.split_recognition.detect', return_value=presence), \
                patch('ha_service.split_recognition.detect_eggs') as detector:
            result = split.predict(self.fixtures / 'synthetic_hen_dark.jpg', self.refs, self.options)
        detector.assert_not_called()
        self.assertTrue(result['zones']['left']['obscured'])
        self.assertTrue(result['zones']['right']['obscured'])
        self.assertNotEqual(result['zones']['center']['label'], 'no_chicken')

    def test_contradictory_presence_blocks_every_zone(self):
        presence = {'ambiguous': True, 'center_present': True, 'bodies': [{'zone': 'center'}, {'zone': 'left'}]}
        with patch('ha_service.split_recognition.detect', return_value=presence), \
                patch('ha_service.split_recognition.detect_eggs') as detector:
            result = split.predict(self.fixtures / 'synthetic_hen_dark.jpg', self.refs, self.options)
        detector.assert_not_called()
        self.assertTrue(all(i['obscured'] for i in result['zones'].values()))

    def test_egg_sizes_not_doubled_by_half_frame_resize(self):
        image = cv2.imread(str(self.fixtures / 'synthetic_eggs_01.jpg'), 0)
        self.assertEqual(len(detect_eggs(image)), 1)
        self.assertEqual(sum(len(detect_eggs(image[:, start:end], size=(320, 360)))
                             for start, end in ((0, 320), (320, 640))), 1)
