from __future__ import annotations

import os
from typing import Callable, Optional

from .sapi_stream import SapiStreamer
from .whisper_stream import DictationEvent, WhisperStreamer


LiveDictationEvent = DictationEvent


class LiveDictationStreamer:
    def __init__(self, *, preferred_microphone_name: Optional[str] = None) -> None:
        self._whisper: Optional[WhisperStreamer] = None
        self._sapi: Optional[SapiStreamer] = None
        self._active_backend: Optional[str] = None
        self._preferred_mic_name = (preferred_microphone_name or "").strip() or None

        force = os.getenv("HGR_DICTATION_BACKEND", "").strip().lower()

        if force != "sapi":
            whisper = WhisperStreamer(preferred_microphone_name=self._preferred_mic_name)
            if whisper.available:
                self._whisper = whisper
        if self._whisper is None:
            self._sapi = SapiStreamer()

        self._active_backend = (
            self._whisper.backend if self._whisper is not None else (self._sapi.backend if self._sapi else None)
        )

    @property
    def available(self) -> bool:
        if self._whisper is not None:
            return self._whisper.available
        if self._sapi is not None:
            return self._sapi.available
        return False

    @property
    def message(self) -> str:
        if self._whisper is not None:
            return self._whisper.message
        if self._sapi is not None:
            return self._sapi.message
        return "dictation unavailable"

    @property
    def backend(self) -> Optional[str]:
        return self._active_backend

    def stream(
        self,
        *,
        stop_event,
        event_callback: Callable[[DictationEvent], None],
    ) -> bool:
        if self._whisper is not None:
            return self._whisper.stream(stop_event=stop_event, event_callback=event_callback)
        if self._sapi is not None:
            return self._sapi.stream(stop_event=stop_event, event_callback=event_callback)
        return False
