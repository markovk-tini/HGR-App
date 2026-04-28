"""GPU-accelerated hand tracker via MediaPipe Tasks API HandLandmarker.

This module wraps `mediapipe.tasks.vision.HandLandmarker` and exposes
a small `Hands` adapter whose surface looks identical to
`mediapipe.solutions.hands.Hands` — same constructor kwargs, same
`.process(rgb)` return shape (`multi_hand_landmarks` /
`multi_handedness` duck-types) — so `HandDetector` doesn't notice
which path it's running on. The runtime loader (runtime.py) picks
this when `gpu_mode` is True AND the GPU delegate is available.

Why a duck-typed adapter instead of refactoring HandDetector to
speak Tasks-API natively: HandDetector is 270 lines of MediaPipe-
specific landmark wrangling, smoothing, dual-hand de-dup, and
handedness reconciliation. Refactoring it touches every gesture
test and risks regressions. A small adapter at the seam keeps the
blast radius to one file.

Why MediaPipe Tasks API and not raw onnxruntime-directml: same
exact models as `solutions.hands`. Accuracy is identical when the
GPU delegate engages, and falls back to CPU through MediaPipe's
own machinery on machines that can't reach a Vulkan / OpenGL ES
context. No anchor decoding, no NMS, no ROI rotation logic for us
to maintain — MediaPipe owns all of that. The trade-off is the
delegate sometimes silently runs on CPU on Windows, which is fine
because we only lose the speedup, not the gestures.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np


_MODEL_FILENAME = "hand_landmarker.task"


def _resolve_task_asset() -> Path | None:
    """Locate the bundled hand_landmarker.task. Tries the
    PyInstaller-bundled location first (sys._MEIPASS / assets /
    models) so the installed exe finds it, then falls back to the
    repo-root location (assets/models) so `python run_app.py`
    works during development."""
    try:
        from ...utils.runtime_paths import app_base_path
    except Exception:
        app_base_path = None

    candidates: list[Path] = []
    if app_base_path is not None:
        try:
            candidates.append(app_base_path() / "assets" / "models" / _MODEL_FILENAME)
        except Exception:
            pass
    try:
        # Source-mode: src/hgr/gesture/tracking/this_file → 4 parents up = repo root.
        repo_root = Path(__file__).resolve().parents[4]
        candidates.append(repo_root / "assets" / "models" / _MODEL_FILENAME)
    except Exception:
        pass
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------
# Duck-typed result wrappers — shape-compatible with what
# `mediapipe.solutions.hands.Hands().process(rgb)` returns. The
# rest of the codebase reads these attributes by name, so as long
# as we expose `.x/.y/.z` on landmark points and `.label/.score`
# on handedness classifications, the existing detector pipeline
# Just Works.
# ---------------------------------------------------------------------


class _LandmarkPoint:
    __slots__ = ("x", "y", "z")

    def __init__(self, lm) -> None:
        self.x = float(lm.x)
        self.y = float(lm.y)
        self.z = float(getattr(lm, "z", 0.0))


class _NormalizedLandmarkList:
    __slots__ = ("landmark",)

    def __init__(self, lm_list) -> None:
        self.landmark = [_LandmarkPoint(p) for p in lm_list]


class _Classification:
    __slots__ = ("label", "score")

    def __init__(self, category) -> None:
        # Tasks API's Category exposes `category_name` and `score`;
        # solutions.hands' Classification exposes `label` and
        # `score`. We pick category_name because the legacy
        # solutions.hands uses "Left"/"Right" for `label` and
        # Tasks API matches that string.
        self.label = str(getattr(category, "category_name", "") or "")
        self.score = float(getattr(category, "score", 0.0))


class _ClassificationList:
    __slots__ = ("classification",)

    def __init__(self, categories) -> None:
        if categories:
            self.classification = [_Classification(categories[0])]
        else:
            self.classification = []


class _HandsResult:
    __slots__ = ("multi_hand_landmarks", "multi_handedness")

    def __init__(self, tasks_result) -> None:
        landmark_groups = getattr(tasks_result, "hand_landmarks", None) or []
        handedness_groups = getattr(tasks_result, "handedness", None) or []
        self.multi_hand_landmarks = [_NormalizedLandmarkList(g) for g in landmark_groups]
        self.multi_handedness = [_ClassificationList(c) for c in handedness_groups]


# ---------------------------------------------------------------------
# Hands adapter: same constructor signature as
# mediapipe.solutions.hands.Hands; .process(rgb) calls
# HandLandmarker.detect_for_video and re-shapes the result.
# ---------------------------------------------------------------------


class _TasksApiHands:
    """Drop-in replacement for `mediapipe.solutions.hands.Hands`."""

    def __init__(
        self,
        *,
        static_image_mode: bool = False,
        model_complexity: int = 1,  # noqa: ARG002 — Tasks API uses the bundled model regardless
        max_num_hands: int = 2,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        task_path = _resolve_task_asset()
        if task_path is None:
            raise FileNotFoundError(
                f"hand_landmarker.task not found. Expected at "
                f"<bundle>/assets/models/{_MODEL_FILENAME} or repo "
                f"root assets/models/{_MODEL_FILENAME}."
            )

        # GPU delegate. When MediaPipe can't reach a Vulkan / OpenGL
        # ES context (some Windows builds) it falls back to CPU
        # internally — no exception, just slower. That's fine: the
        # user opted into GPU Mode, accuracy is unchanged, and our
        # outer fallback catches actual construction errors.
        delegate_gpu = getattr(mp_python.BaseOptions.Delegate, "GPU", None)
        if delegate_gpu is None:
            raise RuntimeError("BaseOptions.Delegate.GPU not available in this mediapipe build")
        base_options = mp_python.BaseOptions(
            model_asset_path=str(task_path),
            delegate=delegate_gpu,
        )

        # IMAGE running mode for static_image_mode=True (used during
        # custom-gesture recording on still frames) and VIDEO mode
        # for the real-time path. VIDEO mode requires monotonic
        # timestamps which we generate from time.monotonic_ns.
        if static_image_mode:
            running_mode = mp_vision.RunningMode.IMAGE
        else:
            running_mode = mp_vision.RunningMode.VIDEO

        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=running_mode,
            num_hands=int(max_num_hands),
            min_hand_detection_confidence=float(min_detection_confidence),
            min_hand_presence_confidence=float(min_detection_confidence),
            min_tracking_confidence=float(min_tracking_confidence),
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._static_image_mode = bool(static_image_mode)
        self._timestamp_origin_ns = time.monotonic_ns()
        self._last_timestamp_ms = -1

    # ------- public surface (matches solutions.hands.Hands) -------

    def process(self, rgb_frame: np.ndarray):
        """Run hand detection on an HxWx3 uint8 RGB frame.
        Returns an object with multi_hand_landmarks and
        multi_handedness attributes (or with both empty when no
        hand is in frame)."""
        import mediapipe as mp

        # The Tasks API takes mp.Image. The frame must be RGB
        # (callers in HandDetector already cv2.cvtColor BGR->RGB),
        # uint8, contiguous. Make a contiguous copy if upstream
        # passed a non-contiguous slice — cheap relative to MediaPipe
        # inference cost and saves a confusing C++-side error.
        if not rgb_frame.flags["C_CONTIGUOUS"]:
            rgb_frame = np.ascontiguousarray(rgb_frame)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        if self._static_image_mode:
            tasks_result = self._landmarker.detect(image)
        else:
            # Strictly-monotonic millisecond timestamps. Real-time
            # camera frames typically arrive at 16.7 ms cadence;
            # the +1 floor below guards against two frames landing
            # in the same millisecond bucket (which Tasks-API
            # rejects with a "non-monotonic timestamp" error).
            now_ns = time.monotonic_ns() - self._timestamp_origin_ns
            ts_ms = max(self._last_timestamp_ms + 1, int(now_ns // 1_000_000))
            self._last_timestamp_ms = ts_ms
            tasks_result = self._landmarker.detect_for_video(image, ts_ms)

        return _HandsResult(tasks_result)

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# A tiny module-shaped object that exposes the .Hands attribute the
# rest of the code reads. HandDetector accesses
# `self.runtime.hands_module.Hands(...)`, so we make hands_module
# look like a module by exposing the Hands attribute (and the
# HAND_CONNECTIONS constant for any drawing code, reused from
# solutions.hands so we stay consistent on the wire).
# ---------------------------------------------------------------------


class _HandsModuleShim:
    Hands = _TasksApiHands

    def __init__(self) -> None:
        try:
            import mediapipe as mp

            self.HAND_CONNECTIONS = getattr(mp.solutions.hands, "HAND_CONNECTIONS", None)
        except Exception:
            self.HAND_CONNECTIONS = None


def build_tasks_gpu_runtime() -> object | None:
    """Build the hands_module shim if the Tasks API + GPU delegate
    are available AND the .task asset is present. Returns None on
    any failure so the caller can fall back to CPU MediaPipe.
    Surfaces the actual error to stderr once so a user reporting
    "GPU mode does nothing" has something concrete to share."""
    try:
        from mediapipe.tasks import python as mp_python  # noqa: F401
        from mediapipe.tasks.python import vision as mp_vision  # noqa: F401
    except Exception as exc:
        try:
            sys.stderr.write(
                f"[tasks_runtime] mediapipe.tasks not importable: "
                f"{type(exc).__name__}: {exc!s}\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
        return None

    if _resolve_task_asset() is None:
        try:
            sys.stderr.write(
                "[tasks_runtime] hand_landmarker.task asset not found at "
                "<bundle>/assets/models/ or repo assets/models/. GPU path "
                "unavailable; falling back to CPU MediaPipe.\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
        return None

    return _HandsModuleShim()
