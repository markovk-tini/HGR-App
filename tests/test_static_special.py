from __future__ import annotations

import unittest

from hgr.core.classifiers.static_registry import classify_static, score_static_candidates
from hgr.core.features.static_features import extract_static_features

from .helpers import make_pose


class StaticSpecialTest(unittest.TestCase):
    def test_mute_classifies(self) -> None:
        features = extract_static_features(make_pose('mute'))
        prediction = classify_static(features)
        self.assertEqual(prediction.raw_gesture, 'mute')

    def test_finger_spacing_scores_track_together_and_apart(self) -> None:
        together = score_static_candidates(extract_static_features(make_pose('finger_together')))
        apart = score_static_candidates(extract_static_features(make_pose('finger_apart')))
        self.assertGreater(together['finger_together'], together['finger_apart'])
        self.assertGreater(apart['finger_apart'], apart['finger_together'])

    def test_open_hand_does_not_score_as_volume_pose(self) -> None:
        scores = score_static_candidates(extract_static_features(make_pose('open_hand')))
        self.assertLess(scores['volume_pose'], 0.35)

    def test_two_with_fingers_apart_does_not_score_as_volume_pose(self) -> None:
        scores = score_static_candidates(extract_static_features(make_pose('two')))
        self.assertLess(scores['volume_pose'], 0.40)

    def test_folded_thumb_four_reads_thumb_index_together(self) -> None:
        features = extract_static_features(make_pose('four'))
        self.assertEqual(features.spread_states['thumb_index'], 'together')
        self.assertEqual(features.spread_states['ring_pinky'], 'together')
