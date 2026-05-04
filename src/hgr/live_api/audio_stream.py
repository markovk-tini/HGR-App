"""Microphone capture for the Live API session.

Wraps `sounddevice.InputStream` (already a project dependency — see
`requirements.txt`). Audio is captured as 16-bit mono PCM at the
configured sample rate and forwarded as raw bytes via a callback.
The callback runs on the sounddevice audio thread; consumers must
not block.

The stream is started on demand and fully released on `stop()` so
the existing local voice-command pipeline can grab the mic again
when Live API mode is off.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np

from .live_api_logger import LiveApiLogger


AudioCallback = Callable[[bytes], None]


class AudioStream:
    """Continuously captures mic audio and emits PCM16 mono chunks."""

    def __init__(
        self,
        *,
        sample_rate: int,
        chunk_ms: int,
        on_chunk: AudioCallback,
        logger: LiveApiLogger,
        device: Optional[int | str] = None,
    ) -> None:
        self._sample_rate = int(sample_rate)
        self._chunk_ms = int(chunk_ms)
        self._on_chunk = on_chunk
        self._logger = logger
        self._device = device

        self._frames_per_chunk = max(1, int(self._sample_rate * self._chunk_ms / 1000))
        self._stream = None
        self._lock = threading.Lock()
        self._running = False
        self._chunk_counter = 0
        self._error_counter = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return True
            try:
                import sounddevice as sd
            except Exception as exc:
                self._logger.exception("audio_import_failed", exc)
                return False

            try:
                stream = sd.RawInputStream(
                    samplerate=self._sample_rate,
                    blocksize=self._frames_per_chunk,
                    channels=1,
                    dtype="int16",
                    device=self._device,
                    callback=self._sd_callback,
                )
                stream.start()
            except Exception as exc:
                self._logger.exception("audio_start_failed", exc, device=str(self._device))
                return False

            self._stream = stream
            self._running = True
            self._chunk_counter = 0
            self._error_counter = 0
            self._logger.event(
                "audio_started",
                sample_rate=self._sample_rate,
                chunk_ms=self._chunk_ms,
                frames_per_chunk=self._frames_per_chunk,
                device=str(self._device),
            )
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                self._logger.exception("audio_stop_failed", exc)
            try:
                stream.close()
            except Exception:
                pass
        self._logger.event(
            "audio_stopped",
            chunks=self._chunk_counter,
            errors=self._error_counter,
        )

    # sounddevice callback contract: (indata: cffi buffer, frames: int, time, status)
    def _sd_callback(self, indata, frames, time_info, status) -> None:  # pragma: no cover
        if status:
            self._error_counter += 1
            try:
                self._logger.warning("audio_stream_status", status=str(status))
            except Exception:
                pass
        if not self._running:
            return
        try:
            # `indata` is a cffi buffer for RawInputStream; bytes() copies it.
            data = bytes(indata)
        except Exception as exc:
            self._error_counter += 1
            try:
                self._logger.exception("audio_buffer_copy_failed", exc)
            except Exception:
                pass
            return
        self._chunk_counter += 1
        try:
            self._on_chunk(data)
        except Exception as exc:
            self._error_counter += 1
            try:
                self._logger.exception("audio_consumer_failed", exc)
            except Exception:
                pass


def estimate_rms(pcm16_bytes: bytes) -> float:
    """Return a rough RMS for diagnostics. Pure utility — no network use."""
    if not pcm16_bytes:
        return 0.0
    arr = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)) / 32768.0)

# Author: Konstantin Markov
