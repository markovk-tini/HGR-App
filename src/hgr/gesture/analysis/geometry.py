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


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def normalize_range(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return clamp01((value - low) / (high - low))


def unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-8:
        return np.zeros_like(vector, dtype=np.float32)
    return (vector / norm).astype(np.float32)


def signed_angle_deg_2d(vector: np.ndarray) -> float:
    return math.degrees(math.atan2(float(vector[1]), float(vector[0])))
