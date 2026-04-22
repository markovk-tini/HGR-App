from __future__ import annotations

import math
import numpy as np


def distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    ba = a - b
    bc = c - b
    denom = float(np.linalg.norm(ba) * np.linalg.norm(bc))
    if denom <= 1e-8:
        return 180.0
    cosine = float(np.dot(ba, bc) / denom)
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def clip01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def normalize_range(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return clip01((value - low) / (high - low))
