from __future__ import annotations

import sys
from pathlib import Path


def app_base_path() -> Path:
    """Return the runtime base directory for source and PyInstaller builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    return app_base_path().joinpath(*parts)
