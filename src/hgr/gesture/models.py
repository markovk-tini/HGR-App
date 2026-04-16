from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Literal

import numpy as np


FingerStateName = Literal["fully_open", "partially_curled", "mostly_curled", "closed"]
SpreadStateName = Literal["together", "neutral", "apart"]


@dataclass(frozen=True)
class HandBounds:
    x: float
    y: float
    width: float
    height: float
    center_x: float
    center_y: float
    area: float


@dataclass(frozen=True)
class TrackedHand:
    landmarks: np.ndarray
    handedness: str
    handedness_confidence: float
    bbox: HandBounds


@dataclass(frozen=True)
class FingerReading:
    name: str
    state: FingerStateName
    openness: float
    curl: float
    confidence: float
    occluded: bool
    bend_base: float
    bend_proximal: float
    bend_distal: float
    palm_distance: float
    reach: float
    spread_hint: float = 0.0

    @property
    def extended(self) -> bool:
        return self.state == "fully_open"


@dataclass(frozen=True)
class SpreadReading:
    name: str
    distance: float
    apart_strength: float
    together_strength: float
    state: SpreadStateName


@dataclass(frozen=True)
class PalmReading:
    center: np.ndarray
    scale: float
    roll_deg: float
    pitch_deg: float
    yaw_deg: float
    normal: np.ndarray


@dataclass(frozen=True)
class HandReading:
    handedness: str
    handedness_confidence: float
    bbox: HandBounds
    palm: PalmReading
    fingers: Dict[str, FingerReading]
    spreads: Dict[str, SpreadReading]
    landmarks: np.ndarray
    finger_count_extended: int
    occlusion_score: float
    shape_confidence: float
    debug_values: Dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class GestureCandidate:
    label: str
    score: float
    kind: Literal["static", "dynamic"] = "static"


@dataclass(frozen=True)
class GesturePrediction:
    raw_label: str
    stable_label: str
    confidence: float
    candidates: tuple[GestureCandidate, ...]
    dynamic_label: str
    dynamic_candidates: tuple[GestureCandidate, ...]


@dataclass(frozen=True)
class GestureFrameResult:
    found: bool
    frame_index: int
    tracked_hand: TrackedHand | None
    hand_reading: HandReading | None
    prediction: GesturePrediction
    annotated_frame: np.ndarray
