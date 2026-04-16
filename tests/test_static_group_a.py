from __future__ import annotations

import unittest

from hgr.core.classifiers.static_registry import classify_static
from hgr.core.features.static_features import extract_static_features

from .helpers import make_landmarks, make_pose


class StaticGroupATest(unittest.TestCase):
    def test_group_a_gestures_classify_under_rotation(self) -> None:
        for gesture in ('open_hand', 'fist', 'zero'):
            for rotation in (0.0, 18.0, -20.0):
                with self.subTest(gesture=gesture, rotation=rotation):
                    features = extract_static_features(make_pose(gesture, rotation_degrees=rotation))
                    prediction = classify_static(features)
                    self.assertEqual(prediction.raw_gesture, gesture)
                    self.assertGreaterEqual(prediction.confidence, 0.58)


    def test_zero_beats_mute_for_ok_sign_shape(self) -> None:
        features = extract_static_features(make_pose('zero'))
        prediction = classify_static(features)
        self.assertEqual(prediction.raw_gesture, 'zero')
        self.assertGreater(prediction.candidate_scores['zero'], prediction.candidate_scores['mute'])


    def test_fist_beats_zero_when_no_thumb_index_loop(self) -> None:
        features = extract_static_features(make_pose('fist'))
        prediction = classify_static(features)
        self.assertEqual(prediction.raw_gesture, 'fist')
        self.assertGreater(prediction.candidate_scores['fist'], prediction.candidate_scores['zero'])


    def test_closed_hand_with_thumb_open_does_not_force_zero(self) -> None:
        from .helpers import make_landmarks
        features = extract_static_features(
            make_landmarks(
                {'index': 'closed', 'middle': 'closed', 'ring': 'closed', 'pinky': 'closed'},
                thumb_state='open',
                spread='together',
            )
        )
        prediction = classify_static(features)
        self.assertNotEqual(prediction.raw_gesture, 'zero')

    def test_open_hand_with_tight_fingers_stays_open_hand(self) -> None:
        features = extract_static_features(make_pose('open_hand', spread='together'))
        prediction = classify_static(features)
        self.assertEqual(prediction.raw_gesture, 'open_hand')

    def test_curled_hand_does_not_force_fist(self) -> None:
        features = extract_static_features(
            make_landmarks(
                {'index': 'curled', 'middle': 'curled', 'ring': 'curled', 'pinky': 'curled'},
                thumb_state='closed',
                spread='normal',
            )
        )
        prediction = classify_static(features)
        self.assertNotEqual(prediction.raw_gesture, 'fist')

    def test_claw_pose_does_not_classify_as_fist(self) -> None:
        features = extract_static_features(make_pose('claw'))
        prediction = classify_static(features)
        self.assertNotEqual(prediction.raw_gesture, 'fist')
