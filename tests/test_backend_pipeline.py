from __future__ import annotations

import unittest

import numpy as np

from hgr.core.arbitration.smoother import GestureSmoother
from hgr.core.pipeline.gesture_backend import GestureBackend
from hgr.core.tracking.landmark_smoother import LandmarkSmoother

from .helpers import make_pose


class DummyTracker:
    def close(self) -> None:
        return


class BackendPipelineTest(unittest.TestCase):
    def test_backend_process_landmarks_stabilizes_after_required_frames(self) -> None:
        backend = GestureBackend(
            tracker=DummyTracker(),
            landmark_smoother=LandmarkSmoother(alpha=1.0),
            gesture_smoother=GestureSmoother(required_frames=2),
        )
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        first = backend.process_landmarks(make_pose('one'), frame, 'Right')
        second = backend.process_landmarks(make_pose('one'), frame, 'Right')
        self.assertEqual(first.raw_gesture, 'one')
        self.assertEqual(first.stable_gesture, 'neutral')
        self.assertEqual(second.stable_gesture, 'one')
        self.assertEqual(second.dynamic_candidate_scores, {})

    def test_backend_reset_when_no_hand_clears_stability(self) -> None:
        backend = GestureBackend(
            tracker=DummyTracker(),
            landmark_smoother=LandmarkSmoother(alpha=1.0),
            gesture_smoother=GestureSmoother(required_frames=2),
        )
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        backend.process_landmarks(make_pose('fist'), frame, 'Right')
        backend.reset()
        result = backend.process_landmarks(make_pose('open_hand'), frame, 'Right')
        self.assertEqual(result.stable_gesture, 'neutral')

    def test_backend_claw_pose_recovers_from_closed_state(self) -> None:
        backend = GestureBackend(
            tracker=DummyTracker(),
            landmark_smoother=LandmarkSmoother(alpha=1.0),
            gesture_smoother=GestureSmoother(required_frames=2),
        )
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        backend.process_landmarks(make_pose('fist'), frame, 'Right', timestamp=1.0)
        first_claw = backend.process_landmarks(make_pose('claw'), frame, 'Right', timestamp=1.1)
        second_claw = backend.process_landmarks(make_pose('claw'), frame, 'Right', timestamp=1.2)
        self.assertNotEqual(second_claw.raw_gesture, 'fist')
        closed_count = sum(
            1 for name in ('index', 'middle', 'ring', 'pinky') if second_claw.features is not None and second_claw.features.states[name] == 'closed'
        )
        self.assertLess(closed_count, 4)
