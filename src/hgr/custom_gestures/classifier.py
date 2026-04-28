from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

import numpy as np

from .recorder import normalize_landmarks
from .registry import CustomGesture, GestureRegistry


# Feature space: landmarks are normalized so wrist=origin and
# wrist->middle-finger-MCP = unit length. Typical hand feature vectors
# have norm ~10-15 in this space, so small rotations move points by
# more than a glance would suggest. Empirical calibration:
#   - 0 (identical):                    score 1.00
#   - ~0.3 (natural jitter):            score 0.94
#   - ~0.7 (small tilt within aug range): score 0.86
#   - ~1.5 (same gesture, different hand orientation outside aug): score 0.70
#   - ~3.0 (finger-spread change):      score 0.40
#   - ~5.0+ (totally different pose):   score 0.00
_SCORE_ZERO_DISTANCE = 5.0

# Structural features (fingertip spacing + wrist-to-fingertip extension)
# come last in the feature vector. They directly encode finger-grouping
# and finger-extension patterns — raw-landmark Euclidean smears these
# across 63 dims and loses the signal, so we boost their effective
# contribution. Weighting goes into Euclidean as w² on the squared diff,
# so weight=5 amplifies structural-feature variance 25×.
#
# Why so aggressive: rotation augmentation (±16° around z, ±12° x/y) can
# shift fingertips laterally by ~1 unit, which means a query with
# meaningfully spread fingers can land geographically close to a rotated
# "fingers-together" sample in landmark space. Without dominant structural
# weighting the classifier picks the rotated variant as its best match
# even though spacing/extension clearly disagree.
#
# Empirical:
#   - 1.0 (none): peace-sign still scored above threshold
#   - 2.5: peace-sign rejected; fingers-spread-but-extended still matched
#   - 5.0: fingers-spread-but-extended cleanly rejected, but pinch poses
#          (OK sign) became too brittle — small natural drift in the
#          thumb-index contact got amplified 25× in squared distance.
#   - 4.0: pinch poses tolerate natural drift, finger-grouping/extension
#          rejection still solid (peace-sign + extension diff dominates).
_LANDMARK_FEATURE_LEN = 63
_SPACING_FEATURE_LEN = 3
_EXTENSION_FEATURE_LEN = 5
_JOINT_ANGLE_FEATURE_LEN = 10
_STRUCTURAL_FEATURE_LEN = (
    _SPACING_FEATURE_LEN + _EXTENSION_FEATURE_LEN + _JOINT_ANGLE_FEATURE_LEN
)
_STRUCTURAL_WEIGHT = 4.0

# Default minimum gap between best and second-best gesture scores. If
# multiple gestures land within this window of each other, neither is
# fired — the user is between two registered poses, and forcing one to
# win would just produce the wrong action. Tunable per-classifier.
_DEFAULT_CONFIDENCE_MARGIN = 0.05


def _distance_to_score(distance: float) -> float:
    """Convert Euclidean distance to a 0..1 score (1 = identical)."""
    if distance <= 0.0:
        return 1.0
    return max(0.0, 1.0 - distance / _SCORE_ZERO_DISTANCE)


@dataclass(frozen=True)
class MatchResult:
    gesture: CustomGesture
    score: float  # 1.0 = identical, 0.0 = very different (see _distance_to_score)
    distance: float  # raw weighted Euclidean distance
    sample_index: int  # which stored sample was the best match
    runner_up_name: Optional[str] = None  # name of the second-best gesture (if any)
    runner_up_score: float = 0.0


class GestureClassifier:
    """KNN matcher over saved custom gestures. Uses weighted Euclidean
    distance on normalized landmark features + structural features
    (spacing / extension / joint angles), and returns the single best
    match whose score clears the threshold AND beats its runner-up by at
    least `confidence_margin`.

    Why Euclidean and not cosine: cosine ignores absolute per-landmark
    position, so two poses with the same overall direction but different
    finger spread (e.g., "four fingers together" vs "four fingers apart
    pointing up") score ~0.97 either way — the small lateral differences
    get swamped by the dominant up-direction. Euclidean keeps those
    differences visible.

    Why a confidence margin: if the user's hand is between two registered
    poses, both might score above threshold. Without a margin we'd fire
    whichever happened to win that frame — usually the wrong action.

    Threshold default 0.88: identical recordings score ~1.0, the same pose
    held with natural jitter scores 0.92-0.98, similar-but-distinct poses
    (finger spread changes) typically drop to 0.6-0.8. Raise for stricter
    matching, lower for more forgiving.
    """

    def __init__(
        self,
        registry: Optional[GestureRegistry] = None,
        *,
        gestures: Optional[Iterable[CustomGesture]] = None,
        threshold: float = 0.88,
        confidence_margin: float = _DEFAULT_CONFIDENCE_MARGIN,
    ) -> None:
        if registry is None and gestures is None:
            raise ValueError("either registry or gestures must be provided")
        self._registry = registry
        self._explicit_gestures: Optional[List[CustomGesture]] = (
            list(gestures) if gestures is not None else None
        )
        self._threshold = float(threshold)
        self._confidence_margin = max(0.0, float(confidence_margin))
        self._matrix: Optional[np.ndarray] = None
        self._sample_to_gesture_idx: List[int] = []
        self._sample_to_local_idx: List[int] = []
        self._gestures: List[CustomGesture] = []

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def confidence_margin(self) -> float:
        return self._confidence_margin

    def reload(self) -> None:
        """Rebuild the sample matrix from the current source. If `gestures`
        was passed at construction, that list is used; otherwise the
        registry's in-memory state is used. The classifier deliberately
        does NOT force a registry reload from disk — call registry.load()
        first if you want fresh-from-disk state."""
        if self._explicit_gestures is not None:
            gestures = self._explicit_gestures
        elif self._registry is not None:
            gestures = self._registry.list()
        else:
            gestures = []
        self._gestures = list(gestures)
        self._sample_to_gesture_idx = []
        self._sample_to_local_idx = []
        rows: List[List[float]] = []
        for g_idx, g in enumerate(self._gestures):
            for s_idx, sample in enumerate(g.samples):
                rows.append(sample.features)
                self._sample_to_gesture_idx.append(g_idx)
                self._sample_to_local_idx.append(s_idx)
        if not rows:
            self._matrix = None
            return
        matrix = np.asarray(rows, dtype=np.float32)
        # Apply the structural-feature weight in-place so classify() is a
        # plain unweighted Euclidean — simpler and faster than weighting on
        # every call.
        structural_start = _LANDMARK_FEATURE_LEN
        structural_end = structural_start + _STRUCTURAL_FEATURE_LEN
        if matrix.shape[1] >= structural_end:
            matrix[:, structural_start:structural_end] *= _STRUCTURAL_WEIGHT
        self._matrix = matrix

    def _match_from_features(
        self, features: np.ndarray
    ) -> Optional[MatchResult]:
        if self._matrix is None:
            self.reload()
        if self._matrix is None or self._matrix.size == 0:
            return None
        # Apply the same structural weight to the query so matrix rows and
        # query live in the same weighted space.
        q = np.array(features, dtype=np.float32, copy=True)
        structural_start = _LANDMARK_FEATURE_LEN
        structural_end = structural_start + _STRUCTURAL_FEATURE_LEN
        if q.shape[0] >= structural_end:
            q[structural_start:structural_end] *= _STRUCTURAL_WEIGHT
        diffs = self._matrix - q
        distances = np.linalg.norm(diffs, axis=1)
        best_idx = int(np.argmin(distances))
        best_distance = float(distances[best_idx])
        score = _distance_to_score(best_distance)
        if score < self._threshold:
            return None
        best_g_idx = self._sample_to_gesture_idx[best_idx]
        best_g = self._gestures[best_g_idx]

        # Confidence-margin check: best score must beat the best score of
        # any OTHER gesture by at least confidence_margin. Find the best
        # competitor by skipping samples that belong to the winning gesture.
        runner_up_score = 0.0
        runner_up_name: Optional[str] = None
        if len(self._gestures) > 1:
            sample_g_idx = np.asarray(self._sample_to_gesture_idx)
            other_mask = sample_g_idx != best_g_idx
            if np.any(other_mask):
                other_distances = distances[other_mask]
                ru_local = int(np.argmin(other_distances))
                ru_distance = float(other_distances[ru_local])
                runner_up_score = _distance_to_score(ru_distance)
                # Map back to the gesture: take the indices of all "other"
                # samples and find the one ru_local picked.
                other_indices = np.flatnonzero(other_mask)
                ru_g_idx = int(sample_g_idx[other_indices[ru_local]])
                runner_up_name = self._gestures[ru_g_idx].name

        if (
            self._confidence_margin > 0.0
            and runner_up_name is not None
            and (score - runner_up_score) < self._confidence_margin
        ):
            return None

        local_idx = self._sample_to_local_idx[best_idx]
        return MatchResult(
            gesture=best_g,
            score=score,
            distance=best_distance,
            sample_index=local_idx,
            runner_up_name=runner_up_name,
            runner_up_score=runner_up_score,
        )

    def classify(self, landmarks: np.ndarray) -> Optional[MatchResult]:
        """Classify a live (21, 3) landmark array. Returns the best match
        above threshold, or None."""
        return self._match_from_features(normalize_landmarks(landmarks))

    def classify_raw(
        self, feature_vector: Sequence[float]
    ) -> Optional[MatchResult]:
        """Alternate entry point when the caller already has a normalized
        63-dim feature vector (e.g., reusing an existing pipeline's output)."""
        return self._match_from_features(
            np.asarray(feature_vector, dtype=np.float32)
        )
