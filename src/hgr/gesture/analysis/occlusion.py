from __future__ import annotations

import numpy as np

from .geometry import clamp01, distance


def estimate_finger_occlusion(
    landmarks: np.ndarray,
    chain: tuple[int, int, int, int],
    palm_scale: float,
    neighboring_tips: list[np.ndarray],
) -> float:
    mcp, pip, dip, tip = chain
    scale = max(float(palm_scale), 1e-6)
    segment_lengths = [
        distance(landmarks[mcp], landmarks[pip]) / scale,
        distance(landmarks[pip], landmarks[dip]) / scale,
        distance(landmarks[dip], landmarks[tip]) / scale,
    ]
    compression = clamp01((0.11 - min(segment_lengths[1], segment_lengths[2])) / 0.07)
    overlap = 0.0
    for other_tip in neighboring_tips:
        overlap = max(overlap, clamp01((0.11 - distance(landmarks[tip], other_tip) / scale) / 0.09))
    depth_disagreement = clamp01((abs(float(landmarks[tip][2] - landmarks[pip][2])) - 0.10) / 0.18)
    return clamp01(0.45 * compression + 0.35 * overlap + 0.20 * depth_disagreement)
