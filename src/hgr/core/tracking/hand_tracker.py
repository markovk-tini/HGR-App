from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .mediapipe_runtime import HandRuntime, load_mediapipe_hand_runtime


@dataclass
class TrackingResult:
    found: bool
    landmarks: np.ndarray | None
    handedness: str | None
    hand_landmarks: object | None
    annotated_frame: np.ndarray


class HandTracker:
    def __init__(self, min_detection_confidence: float = 0.68, min_tracking_confidence: float = 0.68):
        self.runtime: HandRuntime = load_mediapipe_hand_runtime()
        self.hands = self.runtime.hands_module.Hands(
            static_image_mode=False,
            model_complexity=1,
            max_num_hands=1,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )

    def close(self) -> None:
        self.hands.close()

    def process(self, frame_bgr: np.ndarray) -> TrackingResult:
        frame = frame_bgr.copy()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self.hands.process(rgb)
        if not getattr(result, 'multi_hand_landmarks', None):
            return TrackingResult(False, None, None, None, frame)

        hand_landmarks = result.multi_hand_landmarks[0]
        landmarks = np.array([[lm.x, lm.y, lm.z] for lm in hand_landmarks.landmark], dtype=np.float32)
        handedness = None
        try:
            if result.multi_handedness:
                handedness = result.multi_handedness[0].classification[0].label
        except Exception:
            handedness = None

        if self.runtime.drawing_utils is not None and self.runtime.hand_connections is not None:
            self.runtime.drawing_utils.draw_landmarks(frame, hand_landmarks, self.runtime.hand_connections)
        return TrackingResult(True, landmarks, handedness, hand_landmarks, frame)
