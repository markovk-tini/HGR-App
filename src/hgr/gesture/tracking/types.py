from __future__ import annotations

import numpy as np

from ..models import HandBounds


def build_bounds(landmarks: np.ndarray, padding: float = 0.12) -> HandBounds:
    min_xy = landmarks[:, :2].min(axis=0)
    max_xy = landmarks[:, :2].max(axis=0)
    size = np.maximum(max_xy - min_xy, 1e-6)
    center = (min_xy + max_xy) * 0.5
    padded_size = size * (1.0 + padding)
    padded_min = center - padded_size * 0.5
    return HandBounds(
        x=float(padded_min[0]),
        y=float(padded_min[1]),
        width=float(padded_size[0]),
        height=float(padded_size[1]),
        center_x=float(center[0]),
        center_y=float(center[1]),
        area=float(padded_size[0] * padded_size[1]),
    )
