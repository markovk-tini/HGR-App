from __future__ import annotations

import numpy as np


class AdaptiveLandmarkSmoother:
    def __init__(self, alpha: float = 0.58, min_alpha: float = 0.18, max_alpha: float = 0.76):
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

        # Per-landmark outlier rejection. At extreme yaw / poor
        # lighting the landmark model occasionally produces 1-3
        # wildly-misplaced landmarks while the rest stay accurate
        # (e.g., thumb tip snapping across the palm during a
        # rotation pose). Vanilla EMA still blends the bad landmark
        # with prior state, but if the observed position is far
        # enough off, the blend produces visible scatter.
        #
        # Detect: any landmark whose 2D motion this frame is >3x the
        # median motion AND >0.04 normalized (the absolute floor
        # keeps a mostly-still frame from flagging normal sub-pixel
        # wobble as outliers). Replace each outlier's delta with the
        # median-of-good delta so the outlier follows the consensus
        # motion of the rest of the hand instead of teleporting.
        # Z is ignored for the outlier check — z is noisier than
        # x/y on the OpenCV-Zoo export and we don't want a single
        # axis dominating the threshold.
        distances_2d = np.linalg.norm(delta[:, :2], axis=1)
        median_motion = float(np.median(distances_2d))
        threshold = max(0.04, 3.0 * median_motion)
        outliers = distances_2d > threshold
        if outliers.any() and not outliers.all():
            consensus = np.median(delta[~outliers], axis=0)
            delta[outliers] = consensus

        weighted = delta.copy()
        weighted[:, 2] *= 0.60
        motion = float(np.median(np.linalg.norm(weighted, axis=1)))
        adaptive_alpha = self.min_alpha + (self.max_alpha - self.min_alpha) * min(1.0, motion / 0.040)
        adaptive_alpha = 0.52 * adaptive_alpha + 0.48 * self.alpha
        adaptive_alpha = max(self.min_alpha, min(self.max_alpha, adaptive_alpha))
        self._state = self._state + adaptive_alpha * delta
        return self._state.copy()
