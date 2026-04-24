"""Sounddevice-compatible audio source backed by phone-posted PCM chunks.

The phone's browser captures audio via AudioWorklet, resamples to a
fixed sample rate, and POSTs 16-bit signed little-endian mono PCM to
the `/audio` endpoint. Server-side handler pushes into this buffer;
the voice pipeline reads from it via a `sounddevice.InputStream`-shaped
API so no new codepath is needed in the existing whisper runners.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Tuple

import numpy as np


_DEFAULT_SAMPLE_RATE = 48000


class PhoneAudioSource:
    """Thread-safe PCM queue that mimics `sd.InputStream.read(frames)`.

    The voice pipeline calls `read(frames)` expecting a
    `(np.ndarray shape=(frames, 1) dtype=float32, overflow: bool)`
    tuple. We buffer pushed PCM in a deque of int16 arrays and assemble
    exactly the requested number of samples on demand — blocking up to
    a configurable timeout if not enough have arrived yet.

    Samples arrive as Int16 but the pipeline works in Float32 for
    consistency with sounddevice's default dtype; conversion happens in
    read() so the push path stays fast.
    """

    def __init__(
        self,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        max_buffer_seconds: float = 2.5,
    ) -> None:
        self._sample_rate = int(sample_rate)
        self._max_samples = max(1024, int(sample_rate * max_buffer_seconds))
        # Buffer is a deque of 1D int16 arrays; reads concatenate across
        # chunks as needed.
        self._buffer: "deque[np.ndarray]" = deque()
        self._total_samples = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._closed = False
        self._push_count = 0
        self._last_push_at = 0.0

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def push_count(self) -> int:
        return self._push_count

    @property
    def seconds_since_last_push(self) -> float:
        if self._last_push_at <= 0.0:
            return float("inf")
        return time.monotonic() - self._last_push_at

    def push_pcm_int16(self, raw_bytes: bytes) -> None:
        """Append a chunk of raw 16-bit signed LE mono PCM."""
        if self._closed or not raw_bytes:
            return
        try:
            chunk = np.frombuffer(raw_bytes, dtype=np.int16)
        except Exception:
            return
        if chunk.size == 0:
            return
        with self._cond:
            self._buffer.append(chunk)
            self._total_samples += chunk.size
            self._push_count += 1
            self._last_push_at = time.monotonic()
            # Drop oldest chunks if we're above max buffer — prevents
            # unbounded growth when the reader has stalled.
            while self._total_samples > self._max_samples and self._buffer:
                oldest = self._buffer.popleft()
                self._total_samples -= oldest.size
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._buffer.clear()
            self._total_samples = 0
            self._cond.notify_all()

    # ------------------------------------------------------------------
    # sounddevice.InputStream-shaped surface
    # ------------------------------------------------------------------

    def read(self, frames: int, timeout: float = 1.0) -> Tuple[np.ndarray, bool]:
        """Block until `frames` samples are available; return (data, overflow).

        `data` is shape (frames, 1) float32 in [-1.0, 1.0], matching
        sd.InputStream's default dtype. `overflow` is always False here
        (we drop samples on overflow silently in push, mirroring
        sounddevice's behavior under backpressure).
        """
        frames = int(max(1, frames))
        deadline = time.monotonic() + max(0.001, float(timeout))
        with self._cond:
            while not self._closed and self._total_samples < frames:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
            if self._closed:
                # Return silence when closed so callers' dtype
                # expectations don't break.
                return np.zeros((frames, 1), dtype=np.float32), False
            # Assemble exactly `frames` samples from the front of the
            # deque. The tail of the last chunk we pop is stashed back
            # at the head of the deque so the next read sees a clean
            # alignment.
            parts: list[np.ndarray] = []
            need = frames
            while need > 0 and self._buffer:
                head = self._buffer[0]
                if head.size <= need:
                    parts.append(head)
                    need -= head.size
                    self._buffer.popleft()
                    self._total_samples -= head.size
                else:
                    parts.append(head[:need])
                    leftover = head[need:]
                    self._buffer[0] = leftover
                    self._total_samples -= need
                    need = 0
            assembled = (
                np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
            )
            if assembled.size < frames:
                # Didn't accumulate enough within timeout. Pad with
                # silence so the caller's fixed-shape expectations
                # still hold.
                padding = np.zeros(frames - assembled.size, dtype=np.int16)
                assembled = np.concatenate([assembled, padding])
        # Convert int16 to float32 in [-1.0, 1.0] and reshape to
        # (frames, 1) so we match sd.InputStream's default channel-last
        # layout.
        float_arr = assembled.astype(np.float32) / 32768.0
        return float_arr.reshape(-1, 1), False

    # Context-manager surface so the voice pipeline can swap us in
    # where it currently uses `with sd.InputStream(...) as stream:`
    def __enter__(self) -> "PhoneAudioSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Don't close on __exit__ — the source is owned by the phone
        # server and survives individual voice sessions.
        return False
