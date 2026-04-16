from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Sequence

import numpy as np

from .dynamic_swipe_left import score_swipe_left
from .dynamic_swipe_right import score_swipe_right
from .gesture_types import DynamicDetectionContext, DynamicMotionFrame, DynamicMotionState, DynamicObservation, clamp01
from ..features.static_features import StaticFeatures


@dataclass
class DynamicHistory:
    frames: Deque[DynamicMotionFrame] = field(default_factory=lambda: deque(maxlen=18))
    anchor_index: int | None = None
    anchor_frame: DynamicMotionFrame | None = None
    path_length: float = 0.0
    speed_ewma: float = 0.0

    def reset(self) -> None:
        self.frames.clear()
        self.anchor_index = None
        self.anchor_frame = None
        self.path_length = 0.0
        self.speed_ewma = 0.0

    def push(self, frame: DynamicMotionFrame) -> None:
        if self.frames and frame.motion_from_previous is not None:
            self.path_length += float(np.linalg.norm(frame.motion_from_previous))
        self.speed_ewma = 0.85 * self.speed_ewma + 0.15 * frame.motion_speed if self.frames else frame.motion_speed
        self.frames.append(frame)
        if self.anchor_frame is None and frame.landmarks_present:
            self.anchor_index = frame.frame_index
            self.anchor_frame = frame
        elif self.anchor_frame is not None and frame.raw_gesture == 'neutral' and frame.motion_speed <= 1e-4:
            self.anchor_index = frame.frame_index
            self.anchor_frame = frame

    def snapshot(self) -> DynamicMotionState:
        latest = self.frames[-1] if self.frames else None
        latest_velocity = latest.motion_from_previous if latest is not None else None
        latest_speed = latest.motion_speed if latest is not None else 0.0
        displacement_from_anchor = latest.motion_from_anchor if latest is not None else None
        displacement_from_previous = latest.motion_from_previous if latest is not None else None
        horizontal_progress = 0.0
        vertical_progress = 0.0
        depth_progress = 0.0
        current_velocity = None
        if latest is not None and latest_velocity is not None and latest.palm_scale > 1e-6:
            current_velocity = latest_velocity / latest.palm_scale
        if displacement_from_anchor is not None and latest is not None and latest.palm_scale > 1e-6:
            horizontal_progress = float(displacement_from_anchor[0] / latest.palm_scale)
            vertical_progress = float(displacement_from_anchor[1] / latest.palm_scale)
            depth_progress = float((-displacement_from_anchor[2]) / latest.palm_scale)
        normalized_path_length = 0.0
        if latest is not None and latest.palm_scale > 1e-6:
            normalized_path_length = float(self.path_length / latest.palm_scale)
        return DynamicMotionState(
            frame_index=latest.frame_index if latest is not None else 0,
            sample_count=len(self.frames),
            has_anchor=self.anchor_frame is not None,
            anchor_index=self.anchor_index,
            latest_index=latest.frame_index if latest is not None else None,
            latest_raw_gesture=latest.raw_gesture if latest is not None else 'neutral',
            latest_confidence=latest.confidence if latest is not None else 0.0,
            current_velocity=current_velocity,
            current_speed=latest_speed,
            path_length=normalized_path_length,
            displacement_from_anchor=displacement_from_anchor,
            displacement_from_previous=displacement_from_previous,
            horizontal_progress=horizontal_progress,
            vertical_progress=vertical_progress,
            depth_progress=depth_progress,
            swipe_bias=float(clamp01(abs(horizontal_progress) - 0.55 * abs(vertical_progress) - 0.45 * max(0.0, depth_progress))),
            play_bias=float(clamp01(depth_progress - 0.55 * abs(horizontal_progress) - 0.55 * abs(vertical_progress))),
            volume_anchor_bias=float(max(0.0, 1.0 - latest_speed) if latest is not None else 0.0),
        )

    def history(self) -> Sequence[DynamicMotionFrame]:
        return tuple(self.frames)


class DynamicGestureScaffold:
    def __init__(self, history: DynamicHistory | None = None, detectors: Sequence[object] | None = None):
        self.history = history or DynamicHistory()
        self.detectors = tuple(detectors or (score_swipe_left, score_swipe_right))
        self._last_state = self.history.snapshot()

    def reset(self) -> None:
        self.history.reset()
        self._last_state = self.history.snapshot()

    def update(
        self,
        features: StaticFeatures,
        observation: DynamicObservation,
        motion_frame: DynamicMotionFrame,
    ) -> Dict[str, float]:
        self.history.push(motion_frame)
        self._last_state = self.history.snapshot()
        if not self.detectors:
            return {}

        context = DynamicDetectionContext(
            features=features,
            observation=observation,
            frame=motion_frame,
            state=self._last_state,
            history=self.history.history(),
        )
        scores: Dict[str, float] = {}
        for detector in self.detectors:
            try:
                detector_scores = detector(context)
            except Exception:
                continue
            for name, score in detector_scores.items():
                scores[name] = max(scores.get(name, 0.0), float(score))
        return scores

    def snapshot(self) -> DynamicMotionState:
        return self._last_state
