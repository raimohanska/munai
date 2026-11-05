"""Synthetic appearance-only consensus mechanics; no camera or private data."""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from ha_service.plumage import describe, recover, patch_distance, MAX_PATCHES
from recognize_chickens import identify, image_statistics, measure, SCALES


def measurement(value=0, texture=0, patches=0):
    return {'chicken': True, 'features': (SCALES * value).tolist(),
            'appearance': {'texture': np.full(30, texture, dtype=float),
                           'patches': np.full((3, 7), patches, dtype=np.float32)}}


class ConsensusTests(unittest.TestCase):
    def setUp(self):
        self.query = measurement()
        self.refs = [('hen_a', Path('a'), measurement(.1, 0, .05)),
                     ('hen_b', Path('b'), measurement(.2, .05, .3))]
        self.decision = identify(self.query, self.refs)
        self.assertEqual(self.decision['label'], 'unknown')

    def recover(self, max_distance=1.25):
        return recover(self.decision, self.query, self.refs, max_distance)

    def test_agreeing_second_opinions_upgrade_only_label(self):
        result = self.recover()
        self.assertEqual(result, dict(self.decision, label='hen_a'))
        self.assertEqual(self.decision['label'], 'unknown')

    def test_original_accepted_identity_never_changes(self):
        decision = dict(self.decision, label='hen_b')
        with patch('ha_service.plumage.patch_distance', side_effect=AssertionError):
            self.assertIs(recover(decision, self.query, self.refs, 1.25), decision)

    def test_empty_or_unmeasurable_or_missing_appearance_remains_unchanged(self):
        for key, value in (('chicken', False), ('features', None), ('appearance', None)):
            query = dict(self.query, **{key: value})
            self.assertIs(recover(self.decision, query, self.refs, 1.25), self.decision)

    def test_disagreement_keeps_unknown(self):
        self.refs[1][2]['appearance']['patches'][:] = 0
        self.assertIs(self.recover(), self.decision)

    def test_weak_patch_separation_keeps_unknown(self):
        self.refs[1][2]['appearance']['patches'][:] = .06
        self.assertIs(self.recover(), self.decision)

    def test_tied_zero_distances_keep_unknown(self):
        for unused_label, unused_path, ref in self.refs:
            ref['appearance']['patches'][:] = 0
        self.assertIs(self.recover(), self.decision)

    def test_distant_legacy_match_keeps_unknown(self):
        self.assertIs(self.recover(max_distance=.05), self.decision)

    def test_distant_texture_match_keeps_unknown_despite_strong_ratio(self):
        self.refs[0][2]['appearance']['texture'][:] = .2
        self.refs[1][2]['appearance']['texture'][:] = .4
        self.assertIs(self.recover(), self.decision)

    def test_distant_patch_match_keeps_unknown_despite_strong_ratio(self):
        self.refs[0][2]['appearance']['patches'][:] = 2
        self.refs[1][2]['appearance']['patches'][:] = 4
        self.assertIs(self.recover(), self.decision)

    def test_missing_competitor_descriptor_does_not_remove_competitor(self):
        self.refs[1][2].pop('appearance')
        self.assertIs(self.recover(), self.decision)

    def test_nonfinite_or_empty_patch_descriptors_keep_unknown(self):
        for patches in (np.zeros((0, 7)), np.full((3, 7), np.nan), np.zeros((2, 6))):
            self.query['appearance']['patches'] = patches
            self.assertIs(self.recover(), self.decision)

    def test_separation_compares_identities_not_reference_images(self):
        self.refs.append(('hen_a', Path('a2'), measurement(.11, .001, .051)))
        self.assertEqual(self.recover()['label'], 'hen_a')

    def test_one_identity_is_not_enough(self):
        self.refs.pop()
        self.assertIs(self.recover(), self.decision)

    def test_position_area_and_reference_names_do_not_assign_identity(self):
        expected = self.recover()
        for item in [self.query] + [r[2] for r in self.refs]:
            item.update(box=[900, 2, 10, 50], area=999999, zone='other')
        self.assertEqual(self.recover(), expected)

    def test_patch_order_is_not_a_feature(self):
        a = np.arange(35, dtype=np.float32).reshape((5, 7)) / 100
        b = a + .01
        self.assertAlmostEqual(patch_distance(a, b), patch_distance(a[::-1], b[::-1]))


class DescriptorTests(unittest.TestCase):
    def describe(self, gray, inside):
        gray, pixels, std = image_statistics(gray, (gray.shape[1], gray.shape[0]))
        return describe(gray, pixels, std, inside, 120., cv2.Canny(gray, 40, 100))

    def test_bounded_position_independent_descriptors_on_both_split_shapes(self):
        for shape in ((360, 320), (180, 640)):
            gray = np.full(shape, 80, dtype=np.uint8)
            inside = np.zeros(shape, dtype=bool)
            inside[20:-20, 20:-20] = True
            result = self.describe(gray, inside)
            self.assertEqual(result['texture'].shape, (30,))
            self.assertEqual(result['patches'].shape[1], 7)
            self.assertTrue(0 < len(result['patches']) <= MAX_PATCHES)
            self.assertTrue(np.isfinite(result['patches']).all())
            self.assertEqual(set(result), {'texture', 'patches'})

    def test_background_far_from_feathers_does_not_change_descriptor(self):
        gray = np.full((180, 320), 80, dtype=np.uint8)
        inside = np.zeros(gray.shape, dtype=bool)
        inside[40:140, 80:240] = True
        expected = self.describe(gray, inside)
        gray[:, :30] = 230
        actual = self.describe(gray, inside)
        for key in expected:
            np.testing.assert_allclose(expected[key], actual[key])

    def test_moving_the_same_feathers_does_not_encode_position(self):
        random = np.random.RandomState(12)
        body = random.randint(50, 130, (120, 120)).astype(np.uint8)
        descriptors = []
        for x, y in ((60, 60), (120, 120)):
            gray = np.full((360, 320), 120, dtype=np.uint8)
            inside = np.zeros(gray.shape, dtype=bool)
            gray[y:y + 120, x:x + 120] = body
            inside[y:y + 120, x:x + 120] = True
            descriptors.append(self.describe(gray, inside))
        for key in descriptors[0]:
            np.testing.assert_allclose(descriptors[0][key], descriptors[1][key])

    def test_measure_callback_is_opt_in_and_only_for_measurable_hens(self):
        random = np.random.RandomState(5)
        gray = random.randint(30, 210, (360, 320)).astype(np.uint8)
        callback = Mock(return_value={'synthetic': True})
        self.assertNotIn('appearance', measure(gray, size=(320, 360), describe=callback))
        callback.assert_not_called()
        gray[70:280, 80:240] = 60
        ordinary = measure(gray, size=(320, 360))
        detailed = measure(gray, size=(320, 360), describe=callback)
        self.assertIsNotNone(ordinary['features'])
        self.assertEqual(detailed, dict(ordinary, appearance={'synthetic': True}))
        callback.assert_called_once()
