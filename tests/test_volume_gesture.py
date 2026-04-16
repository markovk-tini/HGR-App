from __future__ import annotations

import unittest
from types import SimpleNamespace

from hgr.core.classifiers.static_registry import score_static_candidates
from hgr.core.features.static_features import extract_static_features
from hgr.debug.volume_gesture import VolumeGestureTracker

from .helpers import make_landmarks, make_pose, translate_landmarks


class VolumeGestureTest(unittest.TestCase):
    def test_volume_pose_scores_high(self) -> None:
        features = extract_static_features(make_pose('volume_pose'))
        scores = score_static_candidates(features)
        self.assertGreater(scores['volume_pose'], 0.70)

    def test_apart_two_scores_below_volume_pose_gate(self) -> None:
        features = extract_static_features(
            make_landmarks(
                {'index': 'open', 'middle': 'open', 'ring': 'closed', 'pinky': 'closed'},
                thumb_state='closed',
                spread='apart',
            )
        )
        scores = score_static_candidates(features)
        self.assertLess(scores['volume_pose'], 0.50)

    def test_open_hand_does_not_activate_volume_tracker(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=1, smoothing=1.0)
        open_hand = make_pose('open_hand')
        features = extract_static_features(open_hand)
        scores = score_static_candidates(features)

        first = tracker.update(
            features=features,
            landmarks=open_hand,
            candidate_scores=scores,
            stable_gesture='open_hand',
            current_level=0.50,
            current_muted=False,
            now=1.0,
        )
        second = tracker.update(
            features=features,
            landmarks=open_hand,
            candidate_scores=scores,
            stable_gesture='open_hand',
            current_level=0.50,
            current_muted=False,
            now=1.1,
        )
        self.assertFalse(first.active)
        self.assertFalse(second.active)
        self.assertEqual(second.status, 'idle')

    def test_two_pose_does_not_activate_volume_tracker(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=1, smoothing=1.0)
        pose = make_pose('two')
        features = extract_static_features(pose)
        scores = score_static_candidates(features)

        tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores=scores,
            stable_gesture='two',
            current_level=0.50,
            current_muted=False,
            now=1.0,
        )
        update = tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores=scores,
            stable_gesture='two',
            current_level=0.50,
            current_muted=False,
            now=1.1,
        )
        self.assertFalse(update.active)
        self.assertEqual(update.status, 'idle')

    def test_closed_curled_shell_does_not_activate_volume_tracker(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=1, smoothing=1.0)
        pose = make_landmarks(
            {'index': 'curled', 'middle': 'curled', 'ring': 'curled', 'pinky': 'curled'},
            thumb_state='closed',
            spread='normal',
        )
        features = extract_static_features(pose)
        scores = score_static_candidates(features)

        tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores=scores,
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.0,
        )
        update = tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores=scores,
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.1,
        )
        self.assertFalse(update.active)
        self.assertEqual(update.status, 'idle')

    def test_volume_tracker_uses_relative_anchor_with_small_motion(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=1, smoothing=1.0)
        base = make_pose('volume_pose')

        first_features = extract_static_features(base)
        first_scores = score_static_candidates(first_features)
        tracker.update(
            features=first_features,
            landmarks=base,
            candidate_scores=first_scores,
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.0,
        )
        update = tracker.update(
            features=first_features,
            landmarks=base,
            candidate_scores=first_scores,
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.1,
        )
        self.assertTrue(update.active)

        moved = translate_landmarks(base, dy=-0.045)
        moved_features = extract_static_features(moved)
        moved_scores = score_static_candidates(moved_features)
        changed = tracker.update(
            features=moved_features,
            landmarks=moved,
            candidate_scores=moved_scores,
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.2,
        )
        self.assertIsNotNone(changed.level)
        self.assertGreater(changed.level, 0.50)

    def test_volume_tracker_ignores_small_jitter_around_anchor(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=1, smoothing=1.0)
        base = make_pose('volume_pose')
        features = extract_static_features(base)
        scores = score_static_candidates(features)

        tracker.update(
            features=features,
            landmarks=base,
            candidate_scores=scores,
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.0,
        )
        armed = tracker.update(
            features=features,
            landmarks=base,
            candidate_scores=scores,
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.1,
        )
        self.assertTrue(armed.active)

        jitter = translate_landmarks(base, dy=-0.004)
        jitter_features = extract_static_features(jitter)
        jitter_update = tracker.update(
            features=jitter_features,
            landmarks=jitter,
            candidate_scores=score_static_candidates(jitter_features),
            stable_gesture='neutral',
            current_level=0.50,
            current_muted=False,
            now=1.2,
        )
        self.assertAlmostEqual(jitter_update.level or 0.0, 0.50, places=3)

    def test_volume_tracker_requests_mute_toggle(self) -> None:
        tracker = VolumeGestureTracker()
        features = extract_static_features(make_pose('open_hand'))
        update = tracker.update(
            features=features,
            landmarks=make_pose('open_hand'),
            candidate_scores=score_static_candidates(features),
            stable_gesture='mute',
            current_level=0.50,
            current_muted=False,
            now=5.0,
        )
        self.assertTrue(update.trigger_mute_toggle)

    def test_volume_tracker_can_block_mute_toggle_after_swipe(self) -> None:
        tracker = VolumeGestureTracker()
        features = extract_static_features(make_pose('open_hand'))
        update = tracker.update(
            features=features,
            landmarks=make_pose('open_hand'),
            candidate_scores=score_static_candidates(features),
            stable_gesture='mute',
            current_level=0.50,
            current_muted=False,
            now=5.0,
            allow_mute_toggle=False,
        )
        self.assertFalse(update.trigger_mute_toggle)

    def test_volume_tracker_holds_level_when_pinky_opens(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=2, smoothing=1.0, hold_seconds=1.5)
        base = make_pose('volume_pose')
        base_features = extract_static_features(base)
        base_scores = score_static_candidates(base_features)

        tracker.update(
            features=base_features,
            landmarks=base,
            candidate_scores=base_scores,
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=1.0,
        )
        armed = tracker.update(
            features=base_features,
            landmarks=base,
            candidate_scores=base_scores,
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=1.1,
        )
        self.assertTrue(armed.active)
        self.assertTrue(armed.overlay_visible)

        pinky_hold = make_landmarks(
            {'index': 'open', 'middle': 'open', 'ring': 'closed', 'pinky': 'open'},
            thumb_state='closed',
            spread='together',
        )
        hold_features = extract_static_features(pinky_hold)
        hold_update = tracker.update(
            features=hold_features,
            landmarks=pinky_hold,
            candidate_scores=score_static_candidates(hold_features),
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=1.2,
        )
        self.assertEqual(hold_update.status, 'holding')
        self.assertAlmostEqual(hold_update.level or 0.0, 0.62, places=3)

        moved_while_locked = translate_landmarks(pinky_hold, dy=-0.20)
        locked_features = extract_static_features(moved_while_locked)
        locked_update = tracker.update(
            features=locked_features,
            landmarks=moved_while_locked,
            candidate_scores=score_static_candidates(locked_features),
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=2.0,
        )
        self.assertEqual(locked_update.status, 'holding')
        self.assertAlmostEqual(locked_update.level or 0.0, 0.62, places=3)

        reset_update = tracker.update(
            features=base_features,
            landmarks=base,
            candidate_scores=base_scores,
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=2.8,
        )
        self.assertTrue(reset_update.active)
        self.assertAlmostEqual(reset_update.level or 0.0, 0.62, places=3)

        moved_after_hold = translate_landmarks(base, dy=-0.05)
        moved_features = extract_static_features(moved_after_hold)
        moved_update = tracker.update(
            features=moved_features,
            landmarks=moved_after_hold,
            candidate_scores=score_static_candidates(moved_features),
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=2.9,
        )
        self.assertGreater(moved_update.level or 0.0, 0.62)

    def test_pinky_hold_does_not_activate_without_active_volume_pose(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=2, smoothing=1.0, hold_seconds=1.5)
        pinky_hold = make_landmarks(
            {'index': 'open', 'middle': 'open', 'ring': 'closed', 'pinky': 'open'},
            thumb_state='closed',
            spread='together',
        )
        features = extract_static_features(pinky_hold)
        scores = score_static_candidates(features)
        first = tracker.update(
            features=features,
            landmarks=pinky_hold,
            candidate_scores=scores,
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=1.0,
        )
        second = tracker.update(
            features=features,
            landmarks=pinky_hold,
            candidate_scores=scores,
            stable_gesture='neutral',
            current_level=0.62,
            current_muted=False,
            now=1.1,
        )
        self.assertFalse(first.active)
        self.assertFalse(second.active)
        self.assertNotEqual(second.status, 'holding')

    def test_volume_tracker_accepts_relaxed_mostly_curled_outer_fingers(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=1, smoothing=1.0)
        features = SimpleNamespace(
            palm_scale=0.10,
            open_scores={
                'thumb': 0.38,
                'index': 0.80,
                'middle': 0.83,
                'ring': 0.44,
                'pinky': 0.40,
            },
            states={
                'thumb': 'closed',
                'index': 'open',
                'middle': 'open',
                'ring': 'closed',
                'pinky': 'closed',
            },
            fine_states={
                'thumb': 'mostly_curled',
                'index': 'fully_open',
                'middle': 'fully_open',
                'ring': 'mostly_curled',
                'pinky': 'mostly_curled',
            },
            finger_count_open=2,
            spread_states={'index_middle': 'together'},
            spread_together_strengths={'index_middle': 0.84},
            spread_apart_strengths={'index_middle': 0.08},
        )
        pose = make_pose('volume_pose')
        first = tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores={'volume_pose': 0.0},
            stable_gesture='neutral',
            current_level=0.43,
            current_muted=False,
            now=1.0,
        )
        second = tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores={'volume_pose': 0.0},
            stable_gesture='neutral',
            current_level=0.43,
            current_muted=False,
            now=1.1,
        )
        self.assertFalse(first.active)
        self.assertTrue(second.active)

    def test_volume_tracker_accepts_partially_curled_primary_fingers(self) -> None:
        tracker = VolumeGestureTracker(confirm_frames=2, release_frames=1, smoothing=1.0)
        features = SimpleNamespace(
            palm_scale=0.10,
            open_scores={
                'thumb': 0.34,
                'index': 0.57,
                'middle': 0.65,
                'ring': 0.48,
                'pinky': 0.52,
            },
            states={
                'thumb': 'closed',
                'index': 'open',
                'middle': 'open',
                'ring': 'closed',
                'pinky': 'closed',
            },
            fine_states={
                'thumb': 'mostly_curled',
                'index': 'partially_curled',
                'middle': 'partially_curled',
                'ring': 'mostly_curled',
                'pinky': 'mostly_curled',
            },
            finger_count_open=0,
            spread_states={'index_middle': 'neutral'},
            spread_ratios={'index_middle': 0.28},
            spread_together_strengths={'index_middle': 0.40},
            spread_apart_strengths={'index_middle': 0.17},
        )
        pose = make_pose('volume_pose')
        first = tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores={'volume_pose': 0.0},
            stable_gesture='neutral',
            current_level=0.43,
            current_muted=False,
            now=1.0,
        )
        second = tracker.update(
            features=features,
            landmarks=pose,
            candidate_scores={'volume_pose': 0.0},
            stable_gesture='neutral',
            current_level=0.43,
            current_muted=False,
            now=1.1,
        )
        self.assertFalse(first.active)
        self.assertTrue(second.active)
