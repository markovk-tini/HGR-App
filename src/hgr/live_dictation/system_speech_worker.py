"""Fallback ASR worker backed by Windows System.Speech (via PowerShell).

Wraps the existing :class:`LiveDictationStreamer`, which spawns a
PowerShell host that streams JSON lines for ``hypothesis`` (partial),
``final`` (endpointed utterance), ``ready``, ``stopped`` and ``error``
events. We translate those into the same callback shape the sherpa
backend exposes so :class:`DictationController` doesn't care which one
is active.

This backend is always available on Windows and needs no model
download, so it's the default until sherpa-onnx models are installed.
"""
from __future__ import annotations

import logging
import platform
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class SystemSpeechWorker:
    def __init__(
        self,
        on_partial: Callable[[str], None],
        on_endpoint: Callable[[str], None],
        on_state: Callable[[str, str | None], None],
    ) -> None:
        self._on_partial = on_partial
        self._on_endpoint = on_endpoint
        self._on_state = on_state
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._streamer = None

    @staticmethod
    def available() -> tuple[bool, str | None]:
        if platform.system() != "Windows":
            return False, "system.speech backend is Windows-only"
        try:
            from ..debug.live_dictation_streamer import LiveDictationStreamer
        except Exception as exc:
            return False, f"live dictation streamer import failed: {exc}"
        streamer = LiveDictationStreamer()
        if not streamer.available:
            return False, streamer.message
        return True, None

    def start(self) -> bool:
        ok, err = self.available()
        if not ok:
            self._on_state("error", err)
            return False
        if self._thread and self._thread.is_alive():
            return True

        from ..debug.live_dictation_streamer import (
            LiveDictationEvent,
            LiveDictationStreamer,
        )

        self._stop_event.clear()
        self._streamer = LiveDictationStreamer()

        def _event_cb(ev: LiveDictationEvent) -> None:
            name = (ev.event or "").lower()
            text = (ev.text or "").strip()
            if name == "ready":
                self._on_state("listening", None)
            elif name == "hypothesis":
                if text:
                    self._on_state("speaking", None)
                    self._on_partial(text)
            elif name == "final":
                # System.Speech has already endpointed the utterance.
                self._on_endpoint(text)
                self._on_state("listening", None)
            elif name == "rejected":
                # Low-confidence — finalize what we have to flush preview.
                self._on_endpoint(text)
                self._on_state("listening", None)
            elif name == "stopped":
                self._on_state("off", None)
            elif name == "error":
                self._on_state("error", text or "unknown error")

        def _run() -> None:
            try:
                self._streamer.stream(
                    stop_event=self._stop_event,
                    event_callback=_event_cb,
                )
            except Exception as exc:
                log.exception("system.speech worker crashed")
                self._on_state("error", f"system.speech worker: {exc}")
            finally:
                self._on_state("off", None)

        self._thread = threading.Thread(
            target=_run, name="hgr-system-speech-asr", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._streamer = None
