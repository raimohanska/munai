"""Offline synthetic images and a small set of authorized real egg crops."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from count_eggs import detect_eggs
from ha_service import egg_tracker
from ha_service.nest import NestState, valid_state
from ha_service.options import AppError
from ha_service.options import validate
from ha_service.runtime import Runtime
from ha_service.split_nest import SplitNest
from ha_service.storage import Store
from tests.test_count_eggs import nest


class TrackerImages:
    def detect(self, image):
        return detect_eggs(image, size=(image.shape[1], image.shape[0]))

    def advance(self, value, image, n=1, detections=None):
        if detections is None:
            detections = self.detect(image)
        for unused in range(n):
            value, count = egg_tracker.advance(value, image, detections)
        self.assertTrue(egg_tracker.valid_tracker(value))
        return value, count

    def known(self, centers=((180, 180),)):
        return self.advance(None, nest(centers), 3)[0]


class TrackerTests(TrackerImages, unittest.TestCase):
    def test_straw_crossing_a_known_egg_retains_identity_without_strict_outline(self):
        value = self.known()
        image = nest([(180, 180)])
        image[165:180, 140:220] = nest()[165:180, 140:220]
        self.assertEqual(self.detect(image), [])
        result, count = self.advance(value, image, 50)
        self.assertEqual(count, 1)
        self.assertEqual(result['tracks'][0]['id'], value['tracks'][0]['id'])
        self.assertEqual(result['tracks'][0]['status'], 'obstructed')
        self.assertEqual(value['tracks'][0]['status'], 'visible')  # Pure staging.

    def test_small_remaining_shell_is_enough_to_retain_but_never_acquire(self):
        image = nest([(180, 180)])
        image[:185] = nest()[:185]
        self.assertEqual(self.detect(image), [])
        self.assertEqual(self.advance(None, image, 5)[1], 0)
        self.assertEqual(self.advance(self.known(), image, 20)[1], 1)

    def test_slight_movement_reuses_id(self):
        value = self.known()
        result, count = self.advance(value, nest([(194, 187)]), 3)
        self.assertEqual(count, 1)
        self.assertEqual(result['tracks'][0]['id'], value['tracks'][0]['id'])

    def test_one_detection_cannot_match_two_eggs(self):
        value = self.known(((180, 180), (230, 180)))
        image = nest([(205, 180)])
        result, count = self.advance(value, image)
        self.assertEqual(sum(t['status'] == 'visible' for t in result['tracks']), 1)
        self.assertEqual(count, 2)  # No instantaneous removal.

    def test_three_clear_absences_remove_and_reappearance_gets_new_id(self):
        value = self.known()
        value, count = self.advance(value, nest(), 2)
        self.assertEqual(count, 1)
        value, count = self.advance(value, nest())
        self.assertEqual(count, 0)
        value, count = self.advance(value, nest([(180, 180)]), 3)
        self.assertEqual(count, 1)
        self.assertEqual(value['tracks'][0]['id'], 2)

    def test_intermediate_evidence_has_no_timeout(self):
        image = np.full((360, 640), 20, np.uint8)  # Not shell, not textured background.
        value, count = self.advance(self.known(), image, 20, [])
        self.assertEqual(count, 1)
        self.assertEqual(value['tracks'][0]['status'], 'uncertain')

    def test_obstruction_breaks_absence_streak(self):
        value, unused = self.advance(self.known(), nest(), 2)
        with patch('ha_service.egg_tracker.probe', return_value='obstructed'):
            value, count = self.advance(value, nest())
        self.assertEqual(value['tracks'][0]['absent'], 0)
        self.assertEqual(self.advance(value, nest(), 2)[1], 1)

    def test_restart_or_hidden_nest_breaks_streak_and_discards_tentative(self):
        value, unused = self.advance(self.known(), nest(), 2)
        value = egg_tracker.pause(json.loads(json.dumps(value)))
        self.assertEqual(self.advance(value, nest(), 2)[1], 1)
        tentative, unused = self.advance(None, nest([(180, 180)]))
        self.assertEqual(egg_tracker.pause(tentative)['tracks'], [])

    def test_migration_retains_unlocated_baseline_until_anchored(self):
        value, count = egg_tracker.advance(None, nest(), [], previous_count=1)
        self.assertEqual(count, 1)
        image = nest([(180, 180)])
        value, count = egg_tracker.advance(value, image, self.detect(image), previous_count=1)
        self.assertEqual(count, 1)
        self.assertEqual(value['unlocated'], 0)
        self.assertEqual(len(egg_tracker.pause(value)['tracks']), 1)

    def test_partial_collection_preserves_other_track(self):
        value = self.known(((180, 180), (340, 220)))
        value, count = self.advance(value, nest([(340, 220)]), 3)
        self.assertEqual(count, 1)
        self.assertEqual(value['tracks'][0]['id'], 2)

    def test_invalid_memory_is_rejected(self):
        value = self.known()
        for field, bad in (('brightness', float('nan')), ('hits', 4), ('absent', 3),
                           ('ellipse', [[1, 2], [0, 30], 0]), ('status', 'invented')):
            invalid = copy.deepcopy(value)
            invalid['tracks'][0][field] = bad
            self.assertFalse(egg_tracker.valid_tracker(invalid))


class NestTrackerTests(TrackerImages, unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(dir='/tmp/opencode')
        self.root = Path(self.scratch.name)
        self.state = NestState(self.root)

    def tearDown(self):
        self.scratch.cleanup()

    def shots(self, image, n=5, detections=None):
        detections = self.detect(image) if detections is None else detections
        events = []
        for unused in range(n):
            events += self.state.observe('no_chicken', len(detections), egg_image=image,
                                         egg_detections=detections)
        self.assertTrue(valid_state(self.state.state))
        return events

    def test_obstruction_rebound_does_not_recount_egg_and_real_collection_does(self):
        self.shots(nest())
        for unused in range(3):
            self.state.observe('hen_a', 0)
        self.shots(nest([(180, 180)]), 5)
        self.assertEqual(self.state.daily_snapshot()['counts'], {'unknown': 0, 'hen_a': 1})
        image = nest([(180, 180)])
        image[165:180, 140:220] = nest()[165:180, 140:220]
        self.assertEqual(self.shots(image, 20), [])
        self.assertEqual(self.shots(nest([(180, 180)])), [])
        self.assertEqual(self.state.daily_snapshot()['counts']['hen_a'], 1)
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 0)
        self.assertEqual(self.shots(nest(), 5), ['1 egg collected'])

    def test_restart_preserves_egg_memory(self):
        self.shots(nest([(180, 180)]))
        before = self.state.state['egg_tracking']['tracks'][0]['id']
        self.state = NestState(self.root)
        self.shots(nest([(180, 180)]))
        self.assertEqual(self.state.state['egg_tracking']['tracks'][0]['id'], before)
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 1)

    def test_failed_commit_does_not_consume_tracker_update(self):
        self.shots(nest([(180, 180)]))
        before = copy.deepcopy(self.state.state)
        with patch('ha_service.nest.atomic_json', side_effect=AppError('Synthetic failure')):
            with self.assertRaises(AppError):
                self.shots(nest(), 1)
        self.assertEqual(self.state.state, before)

    def test_count_only_zero_cannot_erase_spatial_memory(self):
        self.shots(nest([(180, 180)]))
        for unused in range(10):
            self.assertEqual(self.state.observe('no_chicken', 0), [])
        self.assertEqual(self.state.state['eggs'], 1)

    def test_v5_migration_preserves_counts_and_pending_attribution(self):
        self.state.observe('no_chicken', 0)
        saved = copy.deepcopy(self.state.state)
        saved.pop('egg_tracking')
        saved['version'] = 5
        self.state.path.write_text(json.dumps(saved))
        self.state = NestState(self.root)
        self.assertEqual(self.state.state['version'], 6)
        self.assertIsNone(self.state.state['egg_tracking'])

    def test_distinct_new_egg_is_counted_even_when_total_stays_the_same(self):
        self.shots(nest())
        self.shots(nest([(180, 180)]))
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 1)
        # Removal and a genuinely different egg appear together. A count-only
        # high-water mark cannot see this replacement; track IDs can.
        self.shots(nest([(340, 220)]), 5)
        self.assertEqual(self.state.state['eggs'], 1)
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 2)
        self.shots(nest([(350, 224)]), 5)
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 2)

    def test_journal_counts_describe_retained_egg_not_broken_raw_outline(self):
        self.shots(nest([(180, 180)]))
        image = nest([(180, 180)])
        for i in range(3):
            frame = {'id': '20000101T00000{}_000000Z_'.format(i) + 'a' * 32,
                     'label': 'no_chicken', 'egg_count': 0}
            self.state.observe('no_chicken', 0, evidence=frame,
                               egg_image=image, egg_detections=[])
        self.assertEqual([f['egg_count'] for f in self.state.state['journal']['baseline']], [1, 1, 1])


class RealCropTests(unittest.TestCase):
    def test_real_straw_crossing_retains_one_egg_until_actual_removal(self):
        root = Path(__file__).parent / 'fixtures' / 'egg_tracking'
        images = {name: cv2.imread(str(root / (name + '.png')), 0)
                  for name in ('clear_shell', 'straw_crossing', 'removed_shell')}
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as folder:
            state = NestState(Path(folder))
            # An established empty baseline and one appearance-confirmed visit.
            empty = images['removed_shell']
            for unused in range(3):
                state.observe('no_chicken', 0, egg_image=empty, egg_detections=[])
            for unused in range(3):
                state.observe('hen_a', 0)
            events = []
            first_id = None
            for name, n, expected in (('clear_shell', 5, 1), ('straw_crossing', 20, 1),
                                      ('clear_shell', 5, 1), ('straw_crossing', 20, 1),
                                      ('removed_shell', 5, 0)):
                image = images[name]
                detections = detect_eggs(image, size=(90, 95))
                self.assertEqual(len(detections), int(name == 'clear_shell'))
                for unused in range(n):
                    events += state.observe('no_chicken', len(detections), egg_image=image,
                                            egg_detections=detections)
                self.assertEqual(state.state['eggs'], expected)
                if expected:
                    identifier = state.state['egg_tracking']['tracks'][0]['id']
                    first_id = first_id or identifier
                    self.assertEqual(identifier, first_id)
            self.assertEqual(events, ['Hen_a laid an Egg', '1 egg collected'])
            self.assertEqual(state.daily_snapshot()['counts'], {'unknown': 0, 'hen_a': 1})


class RuntimeTrackerTests(unittest.TestCase):
    def test_whole_and_both_split_axes_keep_raw_counts_separate_from_memory(self):
        for layout in ('none', 'vertical', 'horizontal'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory(dir='/tmp/opencode') as folder:
                options = validate({'box_split': layout})
                store = Store(Path(folder), options)
                runtime = Runtime(options, store)
                image = nest([(180, 130)])
                encoded = cv2.imencode('.png', image)[1].tobytes()
                if layout == 'none':
                    detection = detect_eggs(image)
                    prediction = {'label': 'no_chicken', 'egg_count': len(detection),
                                  'egg_detections': detection, 'distance': None, 'margin': None}
                else:
                    first, second = ('left', 'right') if layout == 'vertical' else ('top', 'bottom')
                    view = image[:, :320] if layout == 'vertical' else image[:180]
                    detection = detect_eggs(view, size=(view.shape[1], view.shape[0]))
                    zones = {zone: {'label': 'no_chicken', 'egg_count': 0, 'obscured': False,
                                     'egg_detections': []} for zone in (first, second)}
                    zones[first].update(egg_count=len(detection), egg_detections=detection)
                    zones['center'] = {'label': 'no_chicken', 'egg_count': None, 'obscured': False}
                    prediction = {'label': 'no_chicken', 'egg_count': len(detection), 'zones': zones,
                                  'ambiguous': False, 'distance': None, 'margin': None}
                for i in range(5):
                    runtime.observe('20000101T00000{}_000000Z'.format(i), prediction, image=encoded)
                # Deliberately omit the strict outline, leaving the actual shell
                # available for the local retention probe.
                invisible = copy.deepcopy(prediction)
                invisible['egg_count'] = 0
                target = invisible if layout == 'none' else invisible['zones'][first]
                target.update(egg_count=0, egg_detections=[])
                for i in range(5, 8):
                    runtime.observe('20000101T00000{}_000000Z'.format(i), invisible, image=encoded)
                self.assertEqual(runtime.last_observation['egg_count'], 1)
                self.assertEqual(runtime.last_observation['raw_prediction']['egg_count'], 0)
                if layout != 'none':
                    self.assertIsNone(runtime.last_observation['zones']['center']['egg_count'])
                    self.assertNotIn('center', runtime.last_observation['egg_tracking'])


class SplitTrackerTests(unittest.TestCase):
    def test_small_body_and_occupied_divider_break_absence_streaks(self):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as folder:
            state = SplitNest(Path(folder), 'vertical')
            image = nest([(180, 180)])[:, :320]
            empty = nest()[:, :320]

            def shot(view=None, center_label='no_chicken', obscured=False):
                detections = detect_eggs(view, size=(320, 360)) if view is not None else []
                zones = {'left': {'label': 'no_chicken', 'egg_count': len(detections),
                                  'egg_detections': detections, 'obscured': obscured,
                                  'obscured_reason': 'small_body' if obscured else None},
                         'right': {'label': 'no_chicken', 'egg_count': 0},
                         'center': {'label': center_label, 'egg_count': None}}
                return state.observe_zones(zones, egg_images={'left': view} if view is not None else {})

            for unused in range(5):
                shot(image)
            for unused in range(2):
                shot(empty)
            self.assertEqual(state.regions['left'].state['egg_tracking']['tracks'][0]['absent'], 2)
            shot(obscured=True)
            self.assertEqual(state.regions['left'].state['egg_tracking']['tracks'][0]['absent'], 0)
            for unused in range(2):
                shot(empty)
            shot(empty, center_label='hen_a')
            self.assertEqual(state.regions['left'].state['egg_tracking']['tracks'][0]['absent'], 0)
            for unused in range(2):
                shot(empty)
            self.assertEqual(state.snapshot()['zones']['left']['egg_count'], 1)
            self.assertIsNone(state.snapshot()['zones']['center']['egg_count'])
