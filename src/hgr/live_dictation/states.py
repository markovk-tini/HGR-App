from __future__ import annotations

from enum import Enum


class DictationState(Enum):
    OFF = "off"
    LISTENING = "listening"
    SPEAKING = "speaking"
    FINALIZING = "finalizing"
    ERROR = "error"
