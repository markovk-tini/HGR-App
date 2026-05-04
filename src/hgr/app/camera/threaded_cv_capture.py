"""Reader-thread wrapper around cv2.VideoCapture.

Why this exists:
`cv2.VideoCapture.read()` is synchronous — it blocks until the next
camera frame arrives, which on a 30 fps camera is up to 33 ms of dead
main-thread time per call. With the gesture loop's per-cycle work at
~5-7 ms, the cap.read blocking caps the loop at the camera's frame
rate AND blocks every other Qt event from firing during the wait.
With heavy main-thread paint pressure, FPS collapses below the
camera rate.

The fix mirrors the pattern in FfmpegMjpegCapture: a daemon thread
loops cv2 reads and stashes the latest frame; main-thread `.read()`
returns the latest fresh frame, blocking only briefly via an event
when no fresh frame has arrived since the last consume.

This module is a drop-in stand-in for cv2.VideoCapture wherever the
engine consumes one — same `read() / isOpened() / release() / get() /
set()` surface.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional, Tuple

import cv2
import numpy as np


class ThreadedCvCapture:
    """Async wrapper for cv2.VideoCapture. Drops blocking-read latency
    from main thread. Same API surface the engine consumes."""

    def __init__(self, inner: cv2.VideoCapture) -> None:
        self._inner = inner
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        # See FfmpegMjpegCapture for the same fields — used for
        # end-to-end pipeline latency measurement.
        self._latest_frame_ts: float = 0.0
        self._last_consumed_ts: float = 0.0
        self._fresh_frame_event = threading.Event()
        self._stop_event = threading.Event()
        self._read_error = False
        self._closed = False
        self._reader_thread: Optional[threading.Thread] = None
        if self._inner.isOpened():
            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="ThreadedCvCapture",
                daemon=True,
            )
            self._reader_thread.start()

    def _reader_loop(self) -> None:
        consecutive_failures = 0
        while not self._stop_event.is_set():
            try:
                ok, frame = self._inner.read()
            except Exception:
                self._read_error = True
                # Wake any waiter so they can observe the error
                # promptly instead of hitting the 100 ms timeout.
                self._fresh_frame_event.set()
                return
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 30:
                    self._read_error = True
                    self._fresh_frame_event.set()
                    return
                # Brief pause so we don't busy-spin if the camera
                # stalls for a moment.
                time.sleep(0.005)
                continue
            consecutive_failures = 0
            decoded_at = time.monotonic()
            with self._frame_lock:
                self._latest_frame = frame
                self._latest_frame_ts = decoded_at
            self._fresh_frame_event.set()

    def isOpened(self) -> bool:  # noqa: N802 (cv2 API parity)
        if self._closed or self._read_error:
            return False
        return bool(self._inner.isOpened())

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._closed:
            return False, None
        with self._frame_lock:
            frame = self._latest_frame
            ts = self._latest_frame_ts
            self._latest_frame = None
            self._fresh_frame_event.clear()
        if frame is not None:
            self._last_consumed_ts = ts
            return True, frame
        if not self._fresh_frame_event.wait(timeout=0.002):
            return False, None
        with self._frame_lock:
            frame = self._latest_frame
            ts = self._latest_frame_ts
            self._latest_frame = None
            self._fresh_frame_event.clear()
        if frame is None:
            return False, None
        self._last_consumed_ts = ts
        return True, frame

    def get(self, prop_id: int) -> float:
        try:
            return float(self._inner.get(prop_id))
        except Exception:
            return 0.0

    def set(self, prop_id: int, value: Any) -> bool:
        try:
            return bool(self._inner.set(prop_id, value))
        except Exception:
            return False

    def grab(self) -> bool:
        with self._frame_lock:
            return self._latest_frame is not None

    def retrieve(self) -> Tuple[bool, Optional[np.ndarray]]:
        return self.read()

    def release(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._fresh_frame_event.set()
        thread = self._reader_thread
        self._reader_thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        try:
            self._inner.release()
        except Exception:
            pass

# Author: Konstantin Markov
