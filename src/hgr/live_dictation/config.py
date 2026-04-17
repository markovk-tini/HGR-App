"""Runtime configuration for the live dictation subsystem."""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def resolve_sherpa_model_dir() -> str | None:
    """Locate a sherpa-onnx streaming transducer model directory.

    Precedence:
      1. ``HGR_DICTATION_MODEL_DIR`` env var (explicit override)
      2. ``models/dictation`` relative to the repo root

    Returns the absolute path if found, else None.
    """
    env = os.environ.get("HGR_DICTATION_MODEL_DIR", "").strip()
    if env and os.path.isdir(env):
        return os.path.abspath(env)

    here = os.path.dirname(os.path.abspath(__file__))
    # hgr/live_dictation/config.py -> go up 3 -> repo root, then models/dictation
    candidates = [
        os.path.abspath(os.path.join(here, "..", "..", "..", "models", "dictation")),
        os.path.abspath(os.path.join(here, "..", "..", "..", "..", "models", "dictation")),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c

    return None


def sherpa_backend_available() -> tuple[bool, str | None]:
    """Return (ok, reason_if_not) for the sherpa-onnx backend."""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception as exc:
        return False, f"sherpa_onnx not installed ({exc})"
    try:
        import sounddevice  # noqa: F401
    except Exception as exc:
        return False, f"sounddevice not installed ({exc})"
    model_dir = resolve_sherpa_model_dir()
    if not model_dir:
        return False, "sherpa-onnx model dir not found (set HGR_DICTATION_MODEL_DIR)"
    return True, None
