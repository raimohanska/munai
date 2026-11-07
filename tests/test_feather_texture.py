"""Procedural texture regressions only: no camera, private images or labels."""
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from feather_texture import FeatherTexture, MAX_SAMPLES
from recognize_chickens import image_statistics, measure, identify, References
from ha_service import split_recognition as split
from ha_service.options import validate


def background(seed, shape=(360, 640)):
    return np.random.RandomState(seed).randint(65, 180, shape).astype(np.uint8)


def patterned(image, center, axes=(100, 150)):
    yy, xx = np.indices(image.shape)
    texture = np.where((xx // 4 + yy // 4) % 2, 55, 155).astype(np.uint8)
    mask = np.zeros(image.shape, np.uint8)
    cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1)
    result = image.copy()
    result[mask != 0] = texture[mask != 0]
    return result


class FeatherTextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = [background(seed) for seed in range(3)]
        cls.detector = FeatherTexture(cls.frames)

    def mask(self, image, minimum=35000, exclusions=None):
        gray, unused_pixels, std = image_statistics(image)
        if exclusions is None:
            exclusions = np.zeros(gray.shape, dtype=bool)
        return self.detector.mask(gray, std, exclusions, minimum, 8)

    def test_dictionary_is_bounded_and_has_no_identity_or_position_fields(self):
        self.assertLessEqual(len(self.detector.samples), MAX_SAMPLES)
        self.assertEqual(self.detector.samples.shape[1], 30)
        self.assertEqual(set(self.detector.__dict__), {'samples', 'threshold'})

    def test_independent_straw_and_lighting_variations_are_not_bodies(self):
        for seed, factor in ((19, 1), (20, .8), (21, 1.15)):
            image = np.clip(background(seed).astype(float) * factor, 0, 255).astype(np.uint8)
            self.assertFalse(self.mask(image).any())

    def test_textured_body_is_measurable_without_smooth_feather_patches(self):
        for x in (110, 320, 530):
            image = patterned(background(8), (x, 180))
            self.assertFalse(measure(image)['chicken'])
            result = measure(image, feather_detector=self.detector)
            self.assertTrue(result['chicken'])
            self.assertIsNotNone(result['features'])
            self.assertGreater(np.count_nonzero(self.mask(image)), 35000)

    def test_small_fragments_and_smooth_egg_pile_do_not_pass(self):
        fragment = patterned(background(9), (0, 180), (35, 60))
        self.assertFalse(self.mask(fragment).any())
        eggs = background(10)
        for y in range(90, 280, 45):
            for x in range(60, 260, 45):
                cv2.ellipse(eggs, (x, y), (25, 29), 0, 0, 360, 160, -1)
        self.assertFalse(self.mask(eggs).any())

    def test_excluded_texture_cannot_become_body_support(self):
        image = patterned(background(11), (110, 180))
        excluded = np.ones(image.shape, dtype=bool)
        self.assertFalse(self.mask(image, exclusions=excluded).any())

    def test_codec_changes_to_repetitive_background_are_not_a_new_body(self):
        raw = np.tile(np.array([80, 160], np.uint8), (360, 320))
        detector = FeatherTexture([raw])
        success, encoded = cv2.imencode('.jpg', raw)
        self.assertTrue(success)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
        gray, unused_pixels, std = image_statistics(decoded)
        self.assertFalse(detector.mask(gray, std, np.zeros(gray.shape, bool), 35000, 8).any())

    def test_different_reviewed_background_texture_can_only_tighten_gate(self):
        diverse = FeatherTexture(self.frames + [np.tile(np.array([80, 160], np.uint8), (360, 320))])
        self.assertGreaterEqual(diverse.threshold, .08)
        self.assertLessEqual(len(diverse.samples), MAX_SAMPLES)

    def test_texture_presence_does_not_override_ambiguous_identity(self):
        result = measure(patterned(background(12), (320, 180)), feather_detector=self.detector)
        refs = [('hen_a', Path('a'), result), ('hen_b', Path('b'), result)]
        self.assertEqual(identify(result, refs)['label'], 'unknown')

    def test_split_reference_validation_and_live_measurement_share_fallback(self):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as temporary:
            root = Path(temporary)
            (root / 'empty').mkdir()
            for index, frame in enumerate(self.frames):
                cv2.imwrite(str(root / 'empty' / ('20000101T120000_{:06d}Z.jpg'.format(index))), frame)
            image = patterned(background(13), (110, 180))
            for label, shade in (('hen_a', 40), ('hen_b', 210)):
                (root / label).mkdir()
                view = background(14)[:, :320].copy()
                cv2.ellipse(view, (150, 180), (95, 145), 0, 0, 360, shade, -1)
                cv2.imwrite(str(root / label / '20000101T120000_000000Z-left.jpg'), view)
            candidate = root / 'candidate.jpg'
            cv2.imwrite(str(candidate), image[:, :320])
            options = validate({'box_split': 'vertical'})
            self.assertIsNotNone(split.measure_reference(candidate, root, options)['features'])
            refs = split.load(root, options)
            full = root / 'frame.jpg'
            cv2.imwrite(str(full), image)
            result = split.predict(full, refs, options)
            self.assertTrue(any(body['zone'] == 'left' for body in result['presence']['bodies']))
            self.assertNotEqual(result['zones']['left']['label'], 'no_chicken')
            self.assertFalse(result['zones']['left']['obscured'])
            self.assertIsNone(result['zones']['left']['egg_count'])
            self.assertIsNone(result['zones']['center']['egg_count'])

    def test_fallback_respects_center_presence_and_horizontal_split(self):
        refs = References()
        for label, shade in (('hen_a', 40), ('hen_b', 210)):
            image = background(14)
            cv2.ellipse(image, (320, 180), (95, 145), 0, 0, 360, shade, -1)
            refs.append((label, Path(label), measure(image)))
        refs.background_frames = self.frames
        refs.feather_detector = self.detector
        refs.full_background_mask = np.zeros((360, 640), dtype=bool)
        options = validate({'box_split': 'vertical'})
        for layout, center, axes, expected in (
                ('vertical', (320, 180), (100, 150), 'center'),
                ('horizontal', (320, 85), (190, 70), 'top')):
            image = patterned(background(18), center, axes)
            refs.background_mask = np.zeros(split.measurement_size(layout)[::-1], dtype=bool)
            stats = [image_statistics(frame) for frame in self.frames]
            blurred = [cv2.GaussianBlur(item[0], (21, 21), 0).astype(np.int16) for item in stats]
            refs.background_low = np.minimum.reduce(blurred)
            refs.background_high = np.maximum.reduce(blurred)
            refs.background_texture_low = np.minimum.reduce([
                cv2.GaussianBlur(item[2], (21, 21), 0) for item in stats])
            with tempfile.TemporaryDirectory(dir='/tmp/opencode') as temporary:
                path = Path(temporary) / 'frame.jpg'
                cv2.imwrite(str(path), image)
                result = split.predict(path, refs, dict(options, box_split=layout))
            self.assertTrue(any(body['zone'] == expected for body in result['presence']['bodies']))
            self.assertNotEqual(result['zones'][expected]['label'], 'no_chicken')
            self.assertIsNone(result['zones']['center']['egg_count'])
            if expected == 'center':
                self.assertTrue(result['zones']['left']['obscured'])
                self.assertTrue(result['zones']['right']['obscured'])
                self.assertIsNone(result['zones']['left']['egg_count'])
                self.assertIsNone(result['zones']['right']['egg_count'])


if __name__ == '__main__':
    unittest.main()
