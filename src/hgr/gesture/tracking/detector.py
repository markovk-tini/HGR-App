from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ..models import TrackedHand
from .runtime import HandRuntime, load_hand_runtime
from .smoothing import AdaptiveLandmarkSmoother
from .types import build_bounds


@dataclass(frozen=True)
class DetectionResult:
    tracked_hand: TrackedHand | None
    frame_bgr: np.ndarray


class HandDetector:
    def __init__(
        self,
        *,
        min_detection_confidence: float = 0.72,
        min_tracking_confidence: float = 0.72,
        max_num_hands: int = 1,
        smoother: AdaptiveLandmarkSmoother | None = None,
    ) -> None:
        self.runtime: HandRuntime = load_hand_runtime()
        self.hands = self.runtime.hands_module.Hands(
            static_image_mode=False,
            model_complexity=1,
            max_num_hands=max_num_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self.smoother = smoother or AdaptiveLandmarkSmoother()

    def close(self) -> None:
        self.hands.close()

    def reset(self) -> None:
        self.smoother.reset()

    def process(self, frame_bgr: np.ndarray) -> DetectionResult:
        frame = frame_bgr.copy()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self.hands.process(rgb)
        if not getattr(result, "multi_hand_landmarks", None):
            self.reset()
            return DetectionResult(tracked_hand=None, frame_bgr=frame)

        hand_landmarks = result.multi_hand_landmarks[0]
        landmarks = np.array([[lm.x, lm.y, lm.z] for lm in hand_landmarks.landmark], dtype=np.float32)
        landmarks = self.smoother.update(landmarks)

        handedness = "Unknown"
        handedness_confidence = 0.0
        if getattr(result, "multi_handedness", None):
            try:
                classification = result.multi_handedness[0].classification[0]
                handedness = str(getattr(classification, "label", "Unknown") or "Unknown")
                handedness_confidence = float(getattr(classification, "score", 0.0))
            except Exception:
                handedness = "Unknown"
                handedness_confidence = 0.0

        tracked_hand = TrackedHand(
            landmarks=landmarks,
            handedness=handedness,
            handedness_confidence=handedness_confidence,
            bbox=build_bounds(landmarks),
        )
        return DetectionResult(tracked_hand=tracked_hand, frame_bgr=frame)
