from __future__ import annotations

import math
from typing import Dict

import numpy as np

from ..models import PalmReading, SpreadReading
from .geometry import clamp01, distance, signed_angle_deg_2d, unit


def _spread_state(distance_ratio: float, together_at: float, apart_at: float) -> SpreadReading:
    together_strength = clamp01((apart_at - distance_ratio) / max(apart_at - together_at, 1e-6))
    apart_strength = clamp01((distance_ratio - together_at) / max(apart_at - together_at, 1e-6))
    if distance_ratio <= together_at:
        state = "together"
    elif distance_ratio >= apart_at:
        state = "apart"
    else:
        state = "neutral"
    return SpreadReading(
        name="",
        distance=float(distance_ratio),
        apart_strength=float(apart_strength),
        together_strength=float(together_strength),
        state=state,
    )


def analyze_hand_shape(landmarks: np.ndarray) -> tuple[PalmReading, Dict[str, SpreadReading], float]:
    wrist = landmarks[0]
    index_mcp = landmarks[5]
    middle_mcp = landmarks[9]
    pinky_mcp = landmarks[17]
    palm_center = np.mean(landmarks[[0, 5, 9, 13, 17]], axis=0).astype(np.float32)
    palm_width = max(distance(index_mcp, pinky_mcp), 1e-6)
    palm_height = max(distance(wrist, middle_mcp), 1e-6)
    palm_scale = float(max((palm_width + palm_height) * 0.5, 1e-6))

    up = unit(middle_mcp - wrist)
    normal = unit(np.cross(index_mcp - wrist, pinky_mcp - wrist))

    roll_deg = -signed_angle_deg_2d(up[:2])
    pitch_deg = math.degrees(math.asin(max(-1.0, min(1.0, float(normal[1])))))
    yaw_deg = math.degrees(math.asin(max(-1.0, min(1.0, float(normal[0])))))

    spreads = {
        "thumb_index": _spread_state(min(distance(landmarks[4], landmarks[5]), distance(landmarks[4], landmarks[6])) / palm_scale, 0.34, 0.58),
        "index_middle": _spread_state(distance(landmarks[8], landmarks[12]) / palm_scale, 0.32, 0.46),
        "middle_ring": _spread_state(distance(landmarks[12], landmarks[16]) / palm_scale, 0.30, 0.50),
        "ring_pinky": _spread_state(distance(landmarks[16], landmarks[20]) / palm_scale, 0.30, 0.48),
    }
    spreads = {
        key: SpreadReading(
            name=key,
            distance=value.distance,
            apart_strength=value.apart_strength,
            together_strength=value.together_strength,
            state=value.state,
        )
        for key, value in spreads.items()
    }

    pose_confidence = clamp01(0.62 + 0.20 * abs(float(normal[2])) + 0.18 * clamp01((palm_width / max(palm_height, 1e-6)) - 0.6))
    return (
        PalmReading(
            center=palm_center,
            scale=palm_scale,
            roll_deg=float(roll_deg),
            pitch_deg=float(pitch_deg),
            yaw_deg=float(yaw_deg),
            normal=normal,
        ),
        spreads,
        float(pose_confidence),
    )
