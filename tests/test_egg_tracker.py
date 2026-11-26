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
    def test_empty_nest_skips_texture_but_keeps_unlocated_baseline(self):
        with patch('ha_service.egg_tracker.texture') as texture:
            state, count = egg_tracker.advance(None, nest(), [], previous_count=1)
        texture.assert_not_called()
        self.assertEqual(count, 1)
        self.assertEqual(state['unlocated'], 1)

    def test_static_match_only_computes_texture_around_the_shell(self):
        value = self.known()
        image = nest([(180, 180)])
        with patch('ha_service.egg_tracker.texture', wraps=egg_tracker.texture) as texture, \
                patch('ha_service.egg_tracker.relocated_shell') as search:
            result, count = self.advance(value, image)
        search.assert_not_called()
        texture.assert_called_once()
        self.assertLess(texture.call_args[0][0].size, image.size // 10)
        self.assertEqual(count, 1)
        self.assertEqual(result, value)

    def test_cropped_shell_sampling_matches_full_frame_at_edges(self):
        image = np.random.RandomState(42).randint(120, 151, (120, 160)).astype(np.uint8)
        deviations = egg_tracker.texture(image)
        threshold = 8
        for cx, cy in ((0.75, 0.25), (159.25, 119.5), (82.5, 0.75), (0.75, 67.5), (83.25, 60.75)):
            with self.subTest(center=(cx, cy)):
                ellipse = ((cx, cy), (34.75, 51.25), 32.0)
                local = egg_tracker.detected_appearance(image, ellipse, threshold)
                full = egg_tracker.detected_appearance(image, ellipse, threshold, deviations,
                                                       deviations < threshold)
                self.assertIsNotNone(local)
                self.assertEqual(local, full)

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

    def test_disturbed_bedding_is_not_confirmation_of_collection(self):
        value = egg_tracker.pause(self.known())  # A hen obscured the tracked shell.
        covered = nest()
        covered[95:270, 95:180] //= 2  # A different layer of textured bedding.
        value, count = self.advance(value, covered, 8, detections=[])
        self.assertEqual(count, 1)
        self.assertEqual(value['tracks'][0]['status'], 'uncertain')
        self.assertEqual(self.advance(value, nest(), 3)[1], 0)

    def test_slight_movement_reuses_id(self):
        value = self.known()
        result, count = self.advance(value, nest([(194, 187)]), 3)
        self.assertEqual(count, 1)
        self.assertEqual(result['tracks'][0]['id'], value['tracks'][0]['id'])

    def test_repeated_fragment_then_pickup_in_disturbed_bedding(self):
        value = egg_tracker.pause(self.known())
        bedding = nest()
        bedding[95:270, 95:180] //= 2
        covered = bedding.copy()
        cv2.circle(covered, (170, 184), 5, 140, -1)
        fleeting, _ = self.advance(value, covered, 1, detections=[])
        self.assertEqual(self.advance(fleeting, bedding, 5, detections=[])[1], 1)
        stable, count = self.advance(value, covered, 6, detections=[])
        self.assertEqual(count, 1)
        self.assertEqual(self.advance(stable, bedding, 2, detections=[])[1], 1)
        self.assertEqual(self.advance(stable, bedding, 3, detections=[])[1], 0)

    def test_wider_search_retains_a_partly_covered_moved_egg_without_acquiring(self):
        value = self.known()
        image = nest([(180, 95)])
        image[78:95, 140:220] = nest()[78:95, 140:220]
        self.assertEqual(self.detect(image), [])
        self.assertEqual(self.advance(None, image, 5)[1], 0)
        result, count = self.advance(value, image, 8)
        self.assertEqual(count, 1)
        self.assertEqual(result['tracks'][0]['status'], 'obstructed')
        self.assertEqual(result['tracks'][0]['ellipse'], value['tracks'][0]['ellipse'])
        result, count = self.advance(result, nest([(180, 95)]), 5)
        self.assertEqual(count, 1)
        self.assertEqual(result['tracks'][0]['id'], value['tracks'][0]['id'])
        self.assertEqual(self.advance(result, nest(), 3)[1], 0)

    def test_wider_search_does_not_reuse_another_tracks_shell(self):
        value = self.known(((180, 180), (180, 95)))
        for covered in (False, True):
            with self.subTest(covered=covered):
                image = nest([(180, 95)])
                if covered:
                    image[78:95, 140:220] = nest()[78:95, 140:220]
                result, count = self.advance(value, image, 3)
                self.assertEqual(count, 1)
                self.assertLess(result['tracks'][0]['ellipse'][0][1], 110)

    def test_wider_search_rejects_a_bright_patch_with_different_appearance(self):
        known = self.known()
        track = known['tracks'][0]
        image = nest()
        shape = np.zeros_like(image)
        cv2.ellipse(shape, (180, 95), (20, 25), 0, 0, 360, 1, -1)
        y, x = np.nonzero(shape)
        image[y, x] = np.clip(track['brightness'] + (x - 180) * 1.7, 0, 255)
        # The distractor would pass the geometry and brightness prefilter alone.
        with patch('ha_service.egg_tracker.matches_shell', return_value=True):
            self.assertEqual(self.advance(known, image, 3, detections=[])[1], 1)
        self.assertEqual(self.advance(known, image, 3, detections=[])[1], 0)
        self.assertEqual(self.advance(None, image, 5, detections=[])[1], 0)

    def test_legacy_track_learns_shell_profile_on_a_strict_match(self):
        known = self.known()
        del known['tracks'][0]['shell_profile']
        self.assertTrue(egg_tracker.valid_tracker(known))
        result, count = self.advance(known, nest([(180, 180)]))
        self.assertEqual(count, 1)
        self.assertIn('shell_profile', result['tracks'][0])

    def test_static_shell_like_patch_does_not_prevent_collection(self):
        background = nest([(180, 95)])
        background[78:95, 140:220] = nest()[78:95, 140:220]
        before = background.copy()
        before[145:215, 145:215] = nest([(180, 180)])[145:215, 145:215]
        self.assertEqual(self.detect(background), [])
        known, count = self.advance(None, before, 3)
        self.assertEqual(count, 1)
        track = known['tracks'][0]
        texture = egg_tracker.texture(background)
        # Without the change restriction the patch has enough shell-like support.
        self.assertTrue(egg_tracker.relocated_shell(background, texture, texture < 8, track,
                                                     np.zeros_like(background, dtype=bool), True,
                                                     np.ones_like(background, dtype=bool)))
        restored = egg_tracker.pause(json.loads(json.dumps(known)))
        pending, count = self.advance(restored, background, 2)
        self.assertEqual(count, 1)
        self.assertEqual(pending['tracks'][0]['absent'], 2)
        self.assertEqual(pending['relocation_baseline'], known['relocation_baseline'])
        self.assertEqual(self.advance(pending, background)[1], 0)

    def test_legacy_track_without_relocation_baseline_can_still_be_collected(self):
        known = self.known()
        del known['relocation_baseline']
        self.assertTrue(egg_tracker.valid_tracker(known))
        partial = nest([(180, 95)])
        partial[78:95, 140:220] = nest()[78:95, 140:220]
        self.assertEqual(self.advance(known, partial, 3, detections=[])[1], 0)

    def test_invalid_relocation_baseline_is_rejected(self):
        known = self.known()
        for invalid in (None, 'AAAA', '?' * len(known['relocation_baseline'])):
            self.assertFalse(egg_tracker.valid_tracker(dict(known, relocation_baseline=invalid)))

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
        for field, bad in (('brightness', float('nan')), ('shell_profile', [float('nan'), 3]),
                           ('hits', 4), ('absent', 3),
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

    def test_covered_relocation_does_not_collect_or_recount(self):
        self.shots(nest([(180, 180)]))
        partial = nest([(180, 95)])
        partial[78:95, 140:220] = nest()[78:95, 140:220]
        self.assertEqual(self.detect(partial), [])
        self.assertEqual(self.shots(partial, 8), [])
        self.assertEqual(self.state.state['eggs'], 1)
        self.assertEqual(self.shots(nest([(180, 95)])), [])
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 1)
        self.assertEqual(self.shots(nest()), ['1 egg collected'])

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

    def test_replacement_track_without_count_increase_is_not_production(self):
        self.shots(nest())
        self.shots(nest([(180, 180)]))
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 1)
        # A new location does not prove removal plus a different egg. Without
        # an observed count increase this is movement, not new production.
        self.shots(nest([(340, 220)]), 5)
        self.assertEqual(self.state.state['eggs'], 1)
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 1)
        self.shots(nest([(350, 224)]), 5)
        self.assertEqual(self.state.daily_snapshot()['counts']['unknown'], 1)

    def test_moved_egg_after_revisit_does_not_emit_second_laying(self):
        self.shots(nest())
        for unused in range(3):
            self.state.observe('hen_a', 0)
        self.assertEqual(self.shots(nest([(180, 180)])), ['Hen_a laid an Egg'])
        original = self.state.state['egg_tracking']['tracks'][0]['id']
        for unused in range(3):
            self.state.observe('hen_a', 0)
        # The egg moves beyond the association gate while hidden by the hen.
        # On departure its old track retires as the replacement confirms.
        events = self.shots(nest([(340, 220)]))
        self.assertEqual(events, ['Hen_a left'])
        self.assertEqual(self.state.state['eggs'], 1)
        self.assertNotEqual(self.state.state['egg_tracking']['tracks'][0]['id'], original)
        self.assertTrue(self.state.state['egg_tracking']['tracks'][0]['counted'])
        self.assertEqual(self.state.daily_snapshot()['counts'], {'unknown': 0, 'hen_a': 1})
        self.assertEqual(self.shots(nest([(340, 220)]), 10), [])
        self.state = NestState(self.root)
        self.assertEqual(self.shots(nest([(340, 220)])), [])
        self.assertEqual(self.state.daily_snapshot()['counts']['hen_a'], 1)
        self.assertEqual(self.shots(nest()), ['1 egg collected'])
        # A real collection followed by a fresh laying still counts.
        for unused in range(3):
            self.state.observe('hen_a', 0)
        self.assertEqual(self.shots(nest([(340, 220)])), ['Hen_a laid an Egg'])
        self.assertEqual(self.state.daily_snapshot()['counts']['hen_a'], 2)

    def test_moved_old_egg_and_additional_egg_count_only_the_increase(self):
        self.shots(nest())
        for unused in range(3):
            self.state.observe('hen_a', 0)
        self.shots(nest([(180, 180)]))
        for unused in range(3):
            self.state.observe('hen_b', 0)
        self.assertEqual(self.shots(nest([(340, 220), (470, 150)])),
                         ['Hen_b laid an Egg'])
        self.assertEqual(self.state.state['eggs'], 2)
        self.assertEqual(self.state.daily_snapshot()['counts'],
                         {'unknown': 0, 'hen_a': 1, 'hen_b': 1})

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
                tracking = runtime.last_observation['egg_tracking']
                self.assertNotIn('relocation_baseline', tracking if layout == 'none' else tracking[first])
                if layout != 'none':
                    self.assertIsNone(runtime.last_observation['zones']['center']['egg_count'])
                    self.assertNotIn('center', runtime.last_observation['egg_tracking'])


class SplitTrackerTests(unittest.TestCase):
    def test_relocation_search_covers_the_full_side_but_not_its_neighbor(self):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as folder:
            state = SplitNest(Path(folder), 'vertical')

            def shots(image, n=5):
                views = {'left': image[:, :320], 'right': image[:, 320:]}
                zones = {'center': {'label': 'no_chicken', 'egg_count': None}}
                for zone, view in views.items():
                    eggs = detect_eggs(view, size=(320, 360))
                    zones[zone] = {'label': 'no_chicken', 'egg_count': len(eggs), 'egg_detections': eggs}
                return [event for unused in range(n)
                        for event in state.observe_zones(zones, egg_images=views)]

            shots(nest())
            shots(nest([(65, 280)]))
            left = state.regions['left']
            original = left.state['egg_tracking']['tracks'][0]['id']
            partial = nest([(250, 80)])
            partial[63:80, 210:290] = nest()[63:80, 210:290]
            self.assertEqual(detect_eggs(partial[:, :320], size=(320, 360)), [])
            self.assertEqual(shots(partial), [])
            self.assertEqual(state.snapshot()['zones']['left']['egg_count'], 1)
            self.assertEqual(shots(nest([(250, 80)])), [])
            self.assertEqual(state.regions['left'].state['egg_tracking']['tracks'][0]['id'], original)
            self.assertEqual(shots(nest([(390, 80)])), ['Left: 1 egg collected'])
            self.assertEqual(state.snapshot()['zones']['left']['egg_count'], 0)
            self.assertEqual(state.snapshot()['zones']['right']['egg_count'], 1)

    def test_moved_egg_after_revisit_is_not_new_production_in_either_layout(self):
        for layout, sides in (('vertical', ('left', 'right')),
                              ('horizontal', ('top', 'bottom'))):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory(dir='/tmp/opencode') as folder:
                state = SplitNest(Path(folder), layout)
                first, second = sides

                def shots(image, label='no_chicken', n=5, diagnostic=None):
                    detections = detect_eggs(image) if label == 'no_chicken' else []
                    zones = {first: {'label': label, 'egg_count': len(detections),
                                      'egg_detections': detections, 'presence_diagnostic': diagnostic},
                             second: {'label': 'no_chicken', 'egg_count': 0},
                             'center': {'label': 'no_chicken', 'egg_count': None}}
                    events = []
                    for unused in range(n):
                        events += state.observe_zones(zones, egg_images={first: image})
                    return events

                shots(nest())
                shots(nest(), 'hen_a', 3)
                egg = nest([(180, 180)])
                self.assertEqual(shots(egg, n=4, diagnostic='small_body'), [])
                self.assertEqual(shots(egg, n=1, diagnostic='small_body'),
                                 [first.capitalize() + ': Hen_a laid an Egg'])
                shots(nest(), 'hen_a', 3)
                self.assertEqual(shots(nest([(340, 220)])),
                                 [first.capitalize() + ': Hen_a left'])
                self.assertEqual(state.daily_snapshot()['counts']['hen_a'], 1)
                self.assertEqual(state.snapshot()['zones'][first]['egg_count'], 1)
                self.assertIsNone(state.snapshot()['zones']['center']['egg_count'])

    def test_body_fragment_and_occupied_divider_break_absence_streaks(self):
        with tempfile.TemporaryDirectory(dir='/tmp/opencode') as folder:
            state = SplitNest(Path(folder), 'vertical')
            image = nest([(180, 180)])[:, :320]
            empty = nest()[:, :320]

            def shot(view=None, center_label='no_chicken', obscured=False):
                detections = detect_eggs(view, size=(320, 360)) if view is not None else []
                zones = {'left': {'label': 'no_chicken', 'egg_count': len(detections),
                                  'egg_detections': detections, 'obscured': obscured,
                                  'obscured_reason': 'body_fragment' if obscured else None},
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
