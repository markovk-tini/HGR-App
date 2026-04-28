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
    reachable on this machine — the caller falls back to CPU
    MediaPipe transparently."""
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

    # Path 1 — MediaPipe Tasks API HandLandmarker with GPU delegate.
    # Same models as solutions.hands so accuracy is identical;
    # speedup comes from MediaPipe's Vulkan / OpenGL ES delegate
    # when reachable. Construction failure (delegate can't reach a
    # GPU context, .task asset missing, etc.) → return None and
    # let the caller fall back to CPU MediaPipe.
    if probe.mediapipe_tasks_importable and probe.tasks_gpu_delegate_present:
        try:
            from .tasks_runtime import build_tasks_gpu_runtime

            hands_module = build_tasks_gpu_runtime()
            if hands_module is not None:
                try:
                    sys.stderr.write(
                        "[hand_runtime] gpu_mode active: MediaPipe Tasks GPU delegate "
                        "selected. Inference accuracy matches CPU MediaPipe; speedup "
                        "depends on whether the delegate can reach a GPU context on "
                        "this machine (it transparently runs on CPU otherwise).\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass
                hand_connections = getattr(hands_module, "HAND_CONNECTIONS", None)
                return HandRuntime(
                    hands_module=hands_module,
                    drawing_utils=None,
                    hand_connections=hand_connections,
                    backend="mediapipe-tasks-gpu",
                )
        except Exception as exc:
            try:
                sys.stderr.write(
                    f"[hand_runtime] Tasks-API GPU path construction failed: "
                    f"{type(exc).__name__}: {exc!s}. Falling back to MediaPipe CPU.\n"
                )
                sys.stderr.flush()
            except Exception:
                pass

    # Path 2 — onnxruntime-directml on a custom palm-detect +
    # landmark pipeline. Reserved for future release if too many
    # users report Tasks-API GPU silently running on CPU.
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
