"""Synthetic recognition decisions only; no camera capture functionality."""
import unittest

from ha_service.identity_retry import IdentityRetry, MAX_EXTRA_SHOTS


def item(label, obscured=False):
    return {'label': label, 'egg_count': 0 if label == 'no_chicken' else None,
            'distance': 0.1, 'margin': 0.2, 'obscured': obscured}


def split(left, right, center='no_chicken'):
    return {'label': 'unknown', 'egg_count': None, 'layout': 'vertical',
            'zones': {'left': item(left), 'right': item(right), 'center': item(center)}}


class IdentityRetryTests(unittest.TestCase):
    def test_one_identity_wins_over_many_unknowns(self):
        selection = IdentityRetry()
        for index in range(MAX_EXTRA_SHOTS):
            selection.add(item('unknown'), {'id': str(index)})
            self.assertTrue(selection.needs_more())
        selection.add(item('hen_a'), {'id': 'identified'})
        self.assertFalse(selection.needs_more())
        prediction, evidence, zones = selection.result()
        self.assertEqual(prediction['label'], 'hen_a')
        self.assertEqual(evidence['id'], 'identified')
        self.assertIsNone(zones)

    def test_no_identity_stays_unknown(self):
        selection = IdentityRetry()
        for _ in range(MAX_EXTRA_SHOTS + 1):
            selection.add(item('unknown'), None)
        self.assertEqual(selection.result()[0]['label'], 'unknown')

    def test_empty_and_known_do_not_request_retries(self):
        for label in ('no_chicken', 'hen_a'):
            selection = IdentityRetry()
            selection.add(item(label), None)
            self.assertFalse(selection.needs_more())

    def test_regions_keep_their_own_identity_and_actual_evidence(self):
        selection = IdentityRetry()
        first = split('hen_a', 'unknown')
        selection.add(first, {'id': 'first'})
        self.assertTrue(selection.needs_more())
        selection.add(split('unknown', 'hen_b'), {'id': 'second'})
        self.assertFalse(selection.needs_more())
        prediction, unused, frames = selection.result()
        self.assertEqual(prediction['zones']['left']['label'], 'hen_a')
        self.assertEqual(prediction['zones']['right']['label'], 'hen_b')
        self.assertEqual(frames['left']['id'], 'first')
        self.assertEqual(frames['right']['id'], 'second')
        self.assertEqual(first['zones']['right']['label'], 'unknown')
        self.assertIsNone(prediction['egg_count'])

    def test_empty_breaks_identity_carry(self):
        selection = IdentityRetry()
        for label in ('hen_a', 'no_chicken', 'unknown'):
            selection.add(item(label), None)
        self.assertEqual(selection.result()[0]['label'], 'unknown')
        self.assertTrue(selection.needs_more())

    def test_obscured_breaks_identity_carry_without_requesting_retry(self):
        selection = IdentityRetry()
        selection.add(item('hen_a'), None)
        selection.add(item('unknown', obscured=True), None)
        self.assertFalse(selection.needs_more())
        selection.add(item('unknown'), None)
        self.assertTrue(selection.needs_more())
        self.assertEqual(selection.result()[0]['label'], 'unknown')

    def test_new_accepted_identity_replaces_old_selection(self):
        selection = IdentityRetry()
        selection.add(split('hen_a', 'unknown'), None)
        selection.add(split('hen_b', 'unknown'), None)
        self.assertEqual(selection.result()[0]['zones']['left']['label'], 'hen_b')
