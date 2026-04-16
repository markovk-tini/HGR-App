from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Dict, Protocol

import numpy as np

from ..features.static_features import StaticFeatures


StaticGestureScores = Dict[str, float]
DynamicGestureScores = Dict[str, float]


class StaticGestureDetector(Protocol):
    def __call__(self, features: StaticFeatures) -> StaticGestureScores: ...


class DynamicGestureDetector(Protocol):
    label: str

    def __call__(self, context: "DynamicDetectionContext") -> DynamicGestureScores: ...


@dataclass(frozen=True)
class GesturePrediction:
    raw_gesture: str
    confidence: float
    candidate_scores: StaticGestureScores
    debug_signals: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DynamicObservation:
    raw_gesture: str
    confidence: float
    landmarks_present: bool
    frame_index: int
    timestamp: float
    handedness: str | None = None


@dataclass(frozen=True)
class DynamicMotionFrame:
    frame_index: int
    timestamp: float
    raw_gesture: str
    confidence: float
    handedness: str | None
    landmarks_present: bool
    palm_center: np.ndarray | None
    palm_scale: float
    centroid: np.ndarray | None
    tip_centroid: np.ndarray | None
    previous_centroid: np.ndarray | None
    anchor_centroid: np.ndarray | None
    motion_from_previous: np.ndarray | None
    motion_from_anchor: np.ndarray | None
    motion_speed: float
    path_length: float
    depth_delta: float
    horizontal_delta: float
    vertical_delta: float


@dataclass(frozen=True)
class DynamicMotionState:
    frame_index: int
    sample_count: int
    has_anchor: bool
    anchor_index: int | None
    latest_index: int | None
    latest_raw_gesture: str
    latest_confidence: float
    current_velocity: np.ndarray | None
    current_speed: float
    path_length: float
    displacement_from_anchor: np.ndarray | None
    displacement_from_previous: np.ndarray | None
    horizontal_progress: float
    vertical_progress: float
    depth_progress: float
    swipe_bias: float
    play_bias: float
    volume_anchor_bias: float


@dataclass(frozen=True)
class DynamicDetectionContext:
    features: StaticFeatures
    observation: DynamicObservation
    frame: DynamicMotionFrame
    state: DynamicMotionState
    history: Sequence[DynamicMotionFrame]


DynamicScoreProvider = Callable[[StaticFeatures, DynamicObservation], StaticGestureScores]
DynamicDetectorProvider = Callable[[DynamicDetectionContext], DynamicGestureScores]


def avg(values) -> float:
    vals = list(values)
    return sum(vals) / max(1, len(vals))


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def closed(score: float) -> float:
    return clamp01(1.0 - score)
