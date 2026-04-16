from __future__ import annotations

import unittest

from hgr.core.classifiers.static_registry import classify_static
from hgr.core.features.static_features import extract_static_features

from .helpers import make_pose


class StaticGroupBTest(unittest.TestCase):
    def test_group_b_gestures_classify(self) -> None:
        for gesture in ('one', 'two', 'three', 'four'):
            with self.subTest(gesture=gesture):
                features = extract_static_features(make_pose(gesture))
                prediction = classify_static(features)
                self.assertEqual(prediction.raw_gesture, gesture)
                self.assertGreaterEqual(prediction.confidence, 0.58)

    def test_four_beats_open_hand_when_thumb_is_folded(self) -> None:
        features = extract_static_features(make_pose('four'))
        prediction = classify_static(features)
        self.assertEqual(prediction.raw_gesture, 'four')
        self.assertGreater(prediction.candidate_scores['four'], prediction.candidate_scores['open_hand'])
