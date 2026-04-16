from __future__ import annotations

import unittest

from hgr.core.features.static_features import extract_static_features

from .helpers import make_pose


class StaticFeaturesTest(unittest.TestCase):
    def test_open_hand_reports_all_fingers_open(self) -> None:
        features = extract_static_features(make_pose('open_hand'))
        self.assertEqual(features.finger_count_open, 5)
        self.assertEqual(features.states['index'], 'open')
        self.assertEqual(features.states['thumb'], 'open')

    def test_rotation_preserves_open_feature_signal(self) -> None:
        base = extract_static_features(make_pose('open_hand'))
        rotated = extract_static_features(make_pose('open_hand', rotation_degrees=25.0))
        self.assertLess(abs(base.open_scores['index'] - rotated.open_scores['index']), 0.10)
        self.assertLess(abs(base.open_scores['middle'] - rotated.open_scores['middle']), 0.10)

    def test_volume_pose_resolves_folded_outer_fingers(self) -> None:
        features = extract_static_features(make_pose('volume_pose'))
        self.assertEqual(features.states['index'], 'open')
        self.assertEqual(features.states['middle'], 'open')
        self.assertEqual(features.states['ring'], 'closed')
        self.assertEqual(features.states['pinky'], 'closed')

    def test_claw_pose_does_not_mark_every_non_thumb_finger_closed(self) -> None:
        features = extract_static_features(make_pose('claw'))
        closed_count = sum(1 for name in ('index', 'middle', 'ring', 'pinky') if features.states[name] == 'closed')
        self.assertLess(closed_count, 4)
