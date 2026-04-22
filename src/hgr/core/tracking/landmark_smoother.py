from __future__ import annotations

import numpy as np


class LandmarkSmoother:
    def __init__(self, alpha: float = 0.55, min_alpha: float = 0.18, max_alpha: float = 0.72):
        self.alpha = float(alpha)
        self.min_alpha = float(min_alpha)
        self.max_alpha = float(max_alpha)
        self._state: np.ndarray | None = None

    def reset(self) -> None:
        self._state = None

    def update(self, landmarks: np.ndarray) -> np.ndarray:
        current = landmarks.astype(np.float32)
        if self._state is None:
            self._state = current.copy()
            return self._state.copy()

        delta = current - self._state
        weighted = delta.copy()
        weighted[:, 2] *= 0.65
        motion = float(np.median(np.linalg.norm(weighted, axis=1)))
        adaptive_alpha = self.min_alpha + (self.max_alpha - self.min_alpha) * min(1.0, motion / 0.040)
        adaptive_alpha = 0.55 * adaptive_alpha + 0.45 * self.alpha
        adaptive_alpha = max(self.min_alpha, min(self.max_alpha, adaptive_alpha))

        self._state = adaptive_alpha * current + (1.0 - adaptive_alpha) * self._state
        return self._state.copy()
