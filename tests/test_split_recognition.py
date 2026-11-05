"""Synthetic split image/reference regressions; no capture or deployment data."""
from pathlib import Path
import copy
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

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

    def test_reference_comparison_uses_live_area_resampling(self):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as temporary:
            root = Path(temporary) / 'references'
            shutil.copytree(str(self.root), str(root))
            for path in (root / 'empty').glob('*.jpg'):
                image = cv2.imread(str(path), 0)
                high = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_NEAREST)
                # Subpixel detail distinguishes point sampling from area means.
                high[1::3, 1::3] = 255 - high[1::3, 1::3]
                cv2.imwrite(str(path), high)
            refs = split.load(root, self.options)
            for path, frame in zip(sorted((root / 'empty').glob('*.jpg')), refs.background_frames):
                self.assertTrue(np.array_equal(frame, image_statistics(path)[0]))

    def test_fine_background_texture_change_is_not_a_body(self):
        gray = np.tile(np.array([80, 160], dtype=np.uint8), (360, 160))
        empty = 240 - gray
        exclusions = np.zeros(gray.shape, dtype=bool)
        self.assertFalse(split.changed_textured_body(gray, [empty], exclusions, self.options))

    def test_small_changed_ring_does_not_count_unchanged_contour_interior(self):
        empty = np.full((360, 320), 130, dtype=np.uint8)
        exclusions = np.zeros(empty.shape, dtype=bool)
        for filled in (False, True):
            gray = empty.copy()
            gray[80:180, 60:160] = 40
            if not filled:
                gray[90:170, 70:150] = 130
            std = np.full(gray.shape, 20, dtype=np.float32)
            with patch('ha_service.split_recognition.image_statistics',
                       return_value=(gray, gray.astype(float), std)):
                area = split.changed_textured_body(gray, [empty], exclusions, self.options)
            self.assertEqual(bool(area), filled)

    def test_broad_patterned_change_survives_texture_suppression_and_mask_holes(self):
        gray = np.tile(np.array([40, 80], dtype=np.uint8), (360, 160))
        empty = np.full(gray.shape, 130, dtype=np.uint8)
        exclusions = np.zeros(gray.shape, dtype=np.uint8)
        for y in range(16, 360, 32):
            for x in range(16, 320, 32):
                cv2.circle(exclusions, (x, y), 3, 1, -1)
        self.assertTrue(split.changed_textured_body(gray, [empty], exclusions != 0, self.options))

    def test_split_views_use_the_calibrated_full_frame_grid(self):
        presence = {'ambiguous': False, 'center_present': False, 'bodies': []}
        image = cv2.imread(str(self.fixtures / 'synthetic_empty_1.jpg'), 0)
        image = cv2.resize(image, (641, 361))
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as temporary:
            path = Path(temporary) / 'odd.jpg'
            cv2.imwrite(str(path), image)
            normalized = image_statistics(path)[0]
            for layout, zones in (('vertical', ('left', 'right')), ('horizontal', ('top', 'bottom'))):
                options = dict(self.options, box_split=layout)
                with patch('ha_service.split_recognition.detect', return_value=presence), \
                        patch('ha_service.split_recognition.changed_textured_body', return_value=False) as check:
                    split.predict(path, self.refs, options)
                self.assertEqual(check.call_count, 2)
                for call, zone in zip(check.call_args_list, zones):
                    self.assertTrue(np.array_equal(call[0][0], split.crop(normalized, zone, layout)))

    def test_equal_brightness_patterned_body_is_not_empty_on_either_split_axis(self):
        background = np.tile(np.array([80, 160], dtype=np.uint8), (360, 320))
        patterned = np.tile(np.array([110, 130], dtype=np.uint8), (360, 320))
        gray, unused_pixels, std = image_statistics(background)
        refs = copy.copy(self.refs)
        refs.full_background_mask = np.zeros(gray.shape, dtype=bool)
        refs.background_frames = [gray]
        blurred = cv2.GaussianBlur(gray, (21, 21), 0).astype(np.int16)
        refs.background_low = refs.background_high = blurred
        refs.background_texture_low = cv2.GaussianBlur(std, (21, 21), 0)
        for layout, zone, box in (('vertical', 'left', (20, 30, 220, 330)),
                                  ('horizontal', 'top', (160, 10, 480, 160))):
            image = background.copy()
            x1, y1, x2, y2 = box
            image[y1:y2, x1:x2] = patterned[y1:y2, x1:x2]
            refs.background_mask = np.zeros((360, 320) if layout == 'vertical' else (180, 640), dtype=bool)
            with tempfile.TemporaryDirectory(dir='/tmp/opencode') as temporary:
                path = Path(temporary) / 'pattern.jpg'
                cv2.imwrite(str(path), image)
                with patch('ha_service.split_recognition.detect_eggs', return_value=[]) as eggs:
                    result = split.predict(path, refs, dict(self.options, box_split=layout))
                self.assertTrue(any(body['zone'] == zone for body in result['presence']['bodies']))
                self.assertNotEqual(result['zones'][zone]['label'], 'no_chicken')
                self.assertIsNone(result['zones'][zone]['egg_count'])
                other = ('right' if zone == 'left' else 'bottom')
                eggs.assert_called_once()
                self.assertEqual(result['zones'][other]['egg_count'], 0)
                self.assertIsNone(result['zones']['center']['egg_count'])

    def test_visitor_minimum_is_at_least_ten_maximum_eggs(self):
        presence = {'ambiguous': False, 'center_present': False, 'bodies': []}
        for area in (3500, 4000):
            with patch('ha_service.split_recognition.detect', return_value=presence) as detector:
                split.predict(self.fixtures / 'synthetic_empty_1.jpg', self.refs,
                              dict(self.options, egg_max_area=area))
            self.assertEqual(detector.call_args[0][3], 10 * area)
            self.assertEqual(detector.call_args[1]['fragment_area'], area)
            self.assertEqual(detector.call_args[1]['minimum_fragment_change'], self.options['chicken_area'])

    def test_small_border_fragment_is_not_identified_or_counted(self):
        presence = {'ambiguous': False, 'center_present': False, 'bodies': [],
                    'uncertain_zones': ['left'], 'fragments': [{'zone': 'left', 'pixels': 6000}]}
        with patch('ha_service.split_recognition.detect', return_value=presence), \
                patch('ha_service.split_recognition.identify') as identity, \
                patch('ha_service.split_recognition.detect_eggs', return_value=[]) as eggs:
            result = split.predict(self.fixtures / 'synthetic_empty_1.jpg', self.refs, self.options)
        identity.assert_not_called()
        eggs.assert_called_once()  # The other side remains observable.
        self.assertEqual(result['zones']['left']['obscured_reason'], 'small_body')
        self.assertTrue(result['zones']['left']['obscured'])
        self.assertIsNone(result['egg_count'])

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
                self.assertEqual(result['presence']['bodies'], [])
                if case['category'] == 'egged':
                    self.assertEqual(result['label'], 'no_chicken')
                    self.assertEqual(result['egg_count'], case['visible_eggs'])
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

    def test_consensus_cannot_displace_an_existing_identity_on_other_side(self):
        presence = {'ambiguous': False, 'center_present': False,
                    'bodies': [{'zone': 'left'}, {'zone': 'right'}]}
        accepted = {'label': 'hen_a', 'distance': .1, 'margin': .5}
        unknown = {'label': 'unknown', 'distance': .2, 'margin': .1}
        with patch('ha_service.split_recognition.detect', return_value=presence), \
                patch('ha_service.split_recognition.identify', side_effect=[accepted, unknown]), \
                patch('ha_service.split_recognition.recover', return_value=dict(unknown, label='hen_a')):
            result = split.predict(self.fixtures / 'synthetic_hen_dark.jpg', self.refs, self.options)
        self.assertEqual(result['zones']['left']['label'], 'hen_a')
        self.assertEqual(result['zones']['right']['label'], 'unknown')
        self.assertFalse(result['zones']['left']['obscured'])
        self.assertNotIn('identity_method', result['zones']['right'])

    def test_accepted_identity_does_not_compute_live_second_opinion(self):
        presence = {'ambiguous': False, 'center_present': True, 'bodies': [{'zone': 'center'}]}
        accepted = {'label': 'hen_a', 'distance': .1, 'margin': .5}
        with patch('ha_service.split_recognition.detect', return_value=presence), \
                patch('ha_service.split_recognition.identify', return_value=accepted), \
                patch('ha_service.split_recognition.measure', wraps=split.measure) as measurement, \
                patch('ha_service.split_recognition.recover') as fallback:
            result = split.predict(self.fixtures / 'synthetic_hen_dark.jpg', self.refs, self.options)
        self.assertEqual(result['zones']['center']['label'], 'hen_a')
        measurement.assert_called_once()
        self.assertNotIn('describe', measurement.call_args[1])
        fallback.assert_not_called()

    def test_center_upgrade_leaves_side_uncertainty_and_egg_suppression_intact(self):
        presence = {'ambiguous': False, 'center_present': True, 'bodies': [{'zone': 'center'}]}
        unknown = {'label': 'unknown', 'distance': .2, 'margin': .1}
        with patch('ha_service.split_recognition.detect', return_value=presence), \
                patch('ha_service.split_recognition.identify', return_value=unknown), \
                patch('ha_service.split_recognition.recover', return_value=dict(unknown, label='hen_a')), \
                patch('ha_service.split_recognition.detect_eggs') as eggs:
            result = split.predict(self.fixtures / 'synthetic_hen_dark.jpg', self.refs, self.options)
        self.assertEqual(result['zones']['center']['identity_method'], 'plumage_consensus')
        self.assertEqual(result['zones']['center']['label'], 'hen_a')
        self.assertIsNone(result['zones']['center']['egg_count'])
        for zone in ('left', 'right'):
            self.assertEqual(result['zones'][zone]['label'], 'unknown')
            self.assertEqual(result['zones'][zone]['obscured_reason'], 'center_occupied')
            self.assertNotIn('identity_method', result['zones'][zone])
        eggs.assert_not_called()

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
