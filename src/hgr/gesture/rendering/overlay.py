from __future__ import annotations

import cv2
import numpy as np

from ..models import GestureFrameResult


HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (0, 9), (9, 10), (10, 11), (11, 12),
    (0, 13), (13, 14), (14, 15), (15, 16),
    (0, 17), (17, 18), (18, 19), (19, 20),
    (5, 9), (9, 13), (13, 17),
)


def _to_pixel(point: np.ndarray, width: int, height: int) -> tuple[int, int]:
    return int(point[0] * width), int(point[1] * height)


def draw_hand_overlay(frame_bgr: np.ndarray, result: GestureFrameResult) -> np.ndarray:
    frame = frame_bgr.copy()
    if not result.found or result.tracked_hand is None or result.hand_reading is None:
        return frame

    height, width = frame.shape[:2]
    hand = result.tracked_hand
    reading = result.hand_reading
    label = result.prediction.stable_label if result.prediction.stable_label != "neutral" else result.prediction.raw_label
    active = label != "neutral"
    color = (52, 224, 117) if active else (44, 76, 255)

    x1 = int(hand.bbox.x * width)
    y1 = int(hand.bbox.y * height)
    x2 = int((hand.bbox.x + hand.bbox.width) * width)
    y2 = int((hand.bbox.y + hand.bbox.height) * height)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
    banner = f"{hand.handedness} | {label}"
    cv2.putText(frame, banner, (x1, max(22, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 1, cv2.LINE_AA)

    for a, b in HAND_CONNECTIONS:
        cv2.line(
            frame,
            _to_pixel(hand.landmarks[a], width, height),
            _to_pixel(hand.landmarks[b], width, height),
            (236, 240, 241),
            1,
            cv2.LINE_AA,
        )

    finger_colors = {
        "fully_open": (52, 224, 117),
        "partially_curled": (35, 198, 255),
        "mostly_curled": (0, 190, 255),
        "closed": (44, 76, 255),
    }
    tip_indices = {"thumb": 4, "index": 8, "middle": 12, "ring": 16, "pinky": 20}
    for name, tip_index in tip_indices.items():
        finger = reading.fingers[name]
        cv2.circle(frame, _to_pixel(hand.landmarks[tip_index], width, height), 3, finger_colors[finger.state], -1, cv2.LINE_AA)

    secondary_hand = getattr(result, "secondary_tracked_hand", None)
    secondary_reading = getattr(result, "secondary_hand_reading", None)
    if secondary_hand is not None and secondary_reading is not None:
        secondary_color = (200, 160, 40)
        sx1 = int(secondary_hand.bbox.x * width)
        sy1 = int(secondary_hand.bbox.y * height)
        sx2 = int((secondary_hand.bbox.x + secondary_hand.bbox.width) * width)
        sy2 = int((secondary_hand.bbox.y + secondary_hand.bbox.height) * height)
        cv2.rectangle(frame, (sx1, sy1), (sx2, sy2), secondary_color, 1)
        cv2.putText(
            frame,
            f"{secondary_hand.handedness} (2nd)",
            (sx1, max(22, sy1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            secondary_color,
            1,
            cv2.LINE_AA,
        )
        for a, b in HAND_CONNECTIONS:
            cv2.line(
                frame,
                _to_pixel(secondary_hand.landmarks[a], width, height),
                _to_pixel(secondary_hand.landmarks[b], width, height),
                (200, 200, 200),
                1,
                cv2.LINE_AA,
            )
        for name, tip_index in tip_indices.items():
            finger = secondary_reading.fingers[name]
            cv2.circle(
                frame,
                _to_pixel(secondary_hand.landmarks[tip_index], width, height),
                3,
                finger_colors[finger.state],
                -1,
                cv2.LINE_AA,
            )
    return frame
