from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class HandRuntime:
    hands_module: object
    drawing_utils: object | None
    hand_connections: object | None
    backend: str = "mediapipe-cpu"   # "mediapipe-cpu" | "mediapipe-tasks-gpu" | "onnx-directml"


def _load_mediapipe_cpu_runtime() -> HandRuntime:
    """Load the legacy mediapipe.solutions.hands path. This is the
    path Touchless has always used and the safe fallback for every
    GPU attempt that doesn't reach the GPU."""
    import mediapipe as mp

    last_error: Exception | None = None
    try:
        hands_module = mp.solutions.hands
        drawing_utils = getattr(mp.solutions, "drawing_utils", None)
        hand_connections = getattr(hands_module, "HAND_CONNECTIONS", None)
        return HandRuntime(hands_module, drawing_utils, hand_connections, backend="mediapipe-cpu")
    except Exception as exc:
        last_error = exc

    try:
        from mediapipe.python.solutions import drawing_utils  # type: ignore
        from mediapipe.python.solutions import hands as hands_module  # type: ignore

        hand_connections = getattr(hands_module, "HAND_CONNECTIONS", None)
        return HandRuntime(hands_module, drawing_utils, hand_connections, backend="mediapipe-cpu")
    except Exception as exc:
        last_error = exc

    version = getattr(mp, "__version__", "unknown")
    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown import error"
    raise ImportError(
        "Unable to load MediaPipe Hands for gesture tracking. "
        f"Installed mediapipe version: {version}. Last import error: {detail}"
    )


def _try_load_gpu_runtime() -> HandRuntime | None:
    """Attempt the GPU-accelerated path. Returns None if GPU isn't
    reachable on this machine (the caller falls back to CPU). The
    actual Tasks-API HandLandmarker construction is intentionally
    NOT implemented in this session — Touchless v1.0.9 ships the
    GPU foundation (this seam, the gpu_probe, the Settings toggle)
    so v1.0.10 can drop in `mediapipe.tasks.vision.HandLandmarker`
    with `BaseOptions(delegate=Delegate.GPU)` here without touching
    any other file. Right now this function logs a one-time notice
    that GPU was requested and returns None so we transparently use
    CPU.

    The Tasks-API wrapper (next session) will need to:
      - resolve the path to the bundled hand_landmarker.task asset
      - construct HandLandmarker with VIDEO running mode and GPU delegate
      - return a HandRuntime where `hands_module` is a small adapter
        whose .Hands(...) constructor returns a duck-typed object
        whose .process(rgb) yields multi_hand_landmarks /
        multi_handedness shaped results matching solutions.hands
    """
    from .gpu_probe import probe_gpu_paths

    probe = probe_gpu_paths()
    if not probe.has_any_gpu_path:
        try:
            sys.stderr.write(
                "[hand_runtime] gpu_mode requested but no GPU inference path is reachable. "
                "Falling back to MediaPipe CPU.\n"
                f"{probe.diagnostic()}\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
        return None

    # Path 1 — MediaPipe Tasks API GPU delegate. STUB for now; we
    # validated the imports + delegate enum exist (probe), but the
    # real HandLandmarker construction is parked for the next
    # session along with .task asset bundling.
    if probe.mediapipe_tasks_importable and probe.tasks_gpu_delegate_present:
        try:
            sys.stderr.write(
                "[hand_runtime] gpu_mode requested. MediaPipe Tasks GPU delegate detected, "
                "but the GPU runtime adapter isn't wired up in this build yet. "
                "Falling back to MediaPipe CPU until v1.0.10.\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
        return None

    # Path 2 — onnxruntime-directml. Not installed by default in
    # the v1.0.9 wheel set; full ONNX port is the v1.0.10+ escalation
    # only if Tasks-API GPU silently falls back on too many users.
    return None


def load_hand_runtime(*, prefer_gpu: bool = False) -> HandRuntime:
    """Pick a hand-tracking runtime. When prefer_gpu is True we try
    the GPU path first; on any failure we transparently fall back
    to the CPU MediaPipe runtime so gesture detection keeps working.

    Callers that want CPU unconditionally (low_fps mode etc.) pass
    prefer_gpu=False or omit the kwarg. The GestureWorker reads
    config.gpu_mode and threads it through here.
    """
    if prefer_gpu:
        gpu_runtime = _try_load_gpu_runtime()
        if gpu_runtime is not None:
            return gpu_runtime
    return _load_mediapipe_cpu_runtime()
