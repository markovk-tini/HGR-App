from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Dict

import numpy as np

from ...gesture.recognition.engine import GestureRecognitionEngine
from ...gesture.rendering.overlay import draw_hand_overlay
from ..classifiers.gesture_types import DynamicMotionFrame, DynamicMotionState


@dataclass(frozen=True)
class LegacyFeatures:
    palm_center: np.ndarray
    palm_scale: float
    open_scores: Dict[str, float]
    states: Dict[str, str]
    fine_states: Dict[str, str]
    finger_state_confidences: Dict[str, float]
    finger_state_biases: Dict[str, float]
    finger_count_open: int
    spread_ratios: Dict[str, float]
    spread_states: Dict[str, str]
    spread_together_strengths: Dict[str, float]
    spread_apart_strengths: Dict[str, float]


@dataclass
class BackendResult:
    found: bool
    handedness: str | None
    raw_gesture: str
    stable_gesture: str
    confidence: float
    features: LegacyFeatures | None
    candidate_scores: Dict[str, float]
    landmarks: np.ndarray | None
    annotated_frame: np.ndarray
    stable_count: int
    dynamic_candidate_scores: Dict[str, float]
    dynamic_state: DynamicMotionState
    motion_frame: DynamicMotionFrame | None


def _coarse_state(fine_state: str) -> str:
    if fine_state in {"fully_open", "partially_curled"}:
        return "open"
    return "closed"


class GestureBackend:
    def __init__(self, tracker=None, landmark_smoother=None, gesture_smoother=None, dynamic_scaffold=None):
        self.engine = GestureRecognitionEngine(
            stable_frames_required=getattr(gesture_smoother, "required_frames", 3) if gesture_smoother is not None else 3,
        )

    def close(self) -> None:
        self.engine.close()

    def reset(self) -> None:
        self.engine.reset()

    def _legacy_features(self, hand_reading) -> LegacyFeatures:
        fine_states = {name: finger.state for name, finger in hand_reading.fingers.items()}
        states = {name: _coarse_state(finger.state) for name, finger in hand_reading.fingers.items()}
        open_scores = {name: float(finger.openness) for name, finger in hand_reading.fingers.items()}
        confidences = {name: float(finger.confidence) for name, finger in hand_reading.fingers.items()}
        biases = {name: float(finger.openness - finger.curl) for name, finger in hand_reading.fingers.items()}
        return LegacyFeatures(
            palm_center=hand_reading.palm.center,
            palm_scale=float(hand_reading.palm.scale),
            open_scores=open_scores,
            states=states,
            fine_states=fine_states,
            finger_state_confidences=confidences,
            finger_state_biases=biases,
            finger_count_open=sum(1 for finger in hand_reading.fingers.values() if finger.extended),
            spread_ratios={name: float(spread.distance) for name, spread in hand_reading.spreads.items()},
            spread_states={name: spread.state for name, spread in hand_reading.spreads.items()},
            spread_together_strengths={name: float(spread.together_strength) for name, spread in hand_reading.spreads.items()},
            spread_apart_strengths={name: float(spread.apart_strength) for name, spread in hand_reading.spreads.items()},
        )

    def _motion_from_history(self, result, timestamp: float) -> tuple[DynamicMotionState, DynamicMotionFrame | None]:
        history = list(self.engine.dynamic_recognizer.history)
        if not result.found or not history or result.hand_reading is None:
            return (
                DynamicMotionState(
                    frame_index=0,
                    sample_count=0,
                    has_anchor=False,
                    anchor_index=None,
                    latest_index=None,
                    latest_raw_gesture="neutral",
                    latest_confidence=0.0,
                    current_velocity=None,
                    current_speed=0.0,
                    path_length=0.0,
                    displacement_from_anchor=None,
                    displacement_from_previous=None,
                    horizontal_progress=0.0,
                    vertical_progress=0.0,
                    depth_progress=0.0,
                    swipe_bias=0.0,
                    play_bias=0.0,
                    volume_anchor_bias=0.0,
                ),
                None,
            )

        latest = history[-1]
        previous = history[-2] if len(history) >= 2 else None
        anchor = history[0]
        displacement_from_anchor = latest.center - anchor.center
        displacement_from_previous = latest.center - previous.center if previous is not None else None
        scale = max(latest.scale, 1e-6)
        path_length = 0.0
        for prev, current in zip(history, history[1:]):
            path_length += float(np.linalg.norm((current.center - prev.center) / max(current.scale, 1e-6)))

        current_velocity = None
        current_speed = 0.0
        if previous is not None:
            dt = max(latest.timestamp - previous.timestamp, 1e-6)
            current_velocity = displacement_from_previous / scale
            current_speed = float(np.linalg.norm(current_velocity) / dt)

        horizontal_progress = float(displacement_from_anchor[0] / scale)
        vertical_progress = float(displacement_from_anchor[1] / scale)
        depth_progress = float((-displacement_from_anchor[2]) / scale)
        dynamic_state = DynamicMotionState(
            frame_index=result.frame_index,
            sample_count=len(history),
            has_anchor=True,
            anchor_index=max(0, result.frame_index - len(history) + 1),
            latest_index=result.frame_index,
            latest_raw_gesture=result.prediction.raw_label,
            latest_confidence=result.prediction.confidence,
            current_velocity=current_velocity,
            current_speed=current_speed,
            path_length=float(path_length),
            displacement_from_anchor=displacement_from_anchor,
            displacement_from_previous=displacement_from_previous,
            horizontal_progress=horizontal_progress,
            vertical_progress=vertical_progress,
            depth_progress=depth_progress,
            swipe_bias=float(max(0.0, abs(horizontal_progress) - 0.55 * abs(vertical_progress) - 0.45 * max(0.0, depth_progress))),
            play_bias=float(max(0.0, depth_progress - 0.55 * abs(horizontal_progress) - 0.55 * abs(vertical_progress))),
            volume_anchor_bias=0.0,
        )
        motion_frame = DynamicMotionFrame(
            frame_index=result.frame_index,
            timestamp=timestamp,
            raw_gesture=result.prediction.raw_label,
            confidence=result.prediction.confidence,
            handedness=result.tracked_hand.handedness if result.tracked_hand is not None else None,
            landmarks_present=True,
            palm_center=result.hand_reading.palm.center,
            palm_scale=result.hand_reading.palm.scale,
            centroid=latest.center,
            tip_centroid=np.mean(result.hand_reading.landmarks[[8, 12, 16, 20]], axis=0),
            previous_centroid=previous.center if previous is not None else None,
            anchor_centroid=anchor.center,
            motion_from_previous=displacement_from_previous,
            motion_from_anchor=displacement_from_anchor,
            motion_speed=current_speed,
            path_length=float(path_length),
            depth_delta=float(displacement_from_previous[2]) if displacement_from_previous is not None else 0.0,
            horizontal_delta=float(displacement_from_previous[0]) if displacement_from_previous is not None else 0.0,
            vertical_delta=float(displacement_from_previous[1]) if displacement_from_previous is not None else 0.0,
        )
        return dynamic_state, motion_frame

    def _to_backend_result(self, engine_result, timestamp: float) -> BackendResult:
        annotated = draw_hand_overlay(engine_result.annotated_frame, engine_result)
        dynamic_state, motion_frame = self._motion_from_history(engine_result, timestamp)
        features = self._legacy_features(engine_result.hand_reading) if engine_result.hand_reading is not None else None
        return BackendResult(
            found=engine_result.found,
            handedness=engine_result.tracked_hand.handedness if engine_result.tracked_hand is not None else None,
            raw_gesture=engine_result.prediction.raw_label,
            stable_gesture=engine_result.prediction.stable_label,
            confidence=engine_result.prediction.confidence,
            features=features,
            candidate_scores=self.engine.last_static_scores,
            landmarks=engine_result.tracked_hand.landmarks if engine_result.tracked_hand is not None else None,
            annotated_frame=annotated,
            stable_count=self.engine.stable_count,
            dynamic_candidate_scores=self.engine.last_dynamic_scores,
            dynamic_state=dynamic_state,
            motion_frame=motion_frame,
        )

    def process(self, frame_bgr) -> BackendResult:
        timestamp = time.monotonic()
        return self._to_backend_result(self.engine.process_frame(frame_bgr, timestamp=timestamp), timestamp)

    def process_landmarks(
        self,
        landmarks: np.ndarray,
        annotated_frame: np.ndarray | None = None,
        handedness: str | None = None,
        timestamp: float | None = None,
    ) -> BackendResult:
        timestamp = time.monotonic() if timestamp is None else float(timestamp)
        frame = annotated_frame if annotated_frame is not None else np.zeros((1, 1, 3), dtype=np.uint8)
        return self._to_backend_result(
            self.engine.process_landmarks(landmarks, frame_bgr=frame, handedness=handedness, timestamp=timestamp),
            timestamp,
        )
