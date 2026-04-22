from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HandRuntime:
    hands_module: object
    drawing_utils: object | None
    hand_connections: object | None


def load_hand_runtime() -> HandRuntime:
    import mediapipe as mp

    last_error: Exception | None = None

    try:
        hands_module = mp.solutions.hands
        drawing_utils = getattr(mp.solutions, "drawing_utils", None)
        hand_connections = getattr(hands_module, "HAND_CONNECTIONS", None)
        return HandRuntime(hands_module, drawing_utils, hand_connections)
    except Exception as exc:
        last_error = exc

    try:
        from mediapipe.python.solutions import drawing_utils  # type: ignore
        from mediapipe.python.solutions import hands as hands_module  # type: ignore

        hand_connections = getattr(hands_module, "HAND_CONNECTIONS", None)
        return HandRuntime(hands_module, drawing_utils, hand_connections)
    except Exception as exc:
        last_error = exc

    version = getattr(mp, "__version__", "unknown")
    detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown import error"
    raise ImportError(
        "Unable to load MediaPipe Hands for gesture tracking. "
        f"Installed mediapipe version: {version}. Last import error: {detail}"
    )
