"""OpenCV-compatible shim that yields frames received over the WebSocket.

The engine's existing camera path consumes a `cv2.VideoCapture`: it calls
`.read()` repeatedly, expects `(ok, frame)`, and releases when done. This
class implements that surface on top of a thread-safe "latest frame"
slot maintained by `PhoneCameraServer`. Consuming the latest frame (as
opposed to draining a queue) keeps gesture tracking at the server's
push rate without lag accumulating when the PC briefly stalls.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Tuple

import cv2
import numpy as np


class PhoneCameraCapture:
    def __init__(self, wait_seconds: float = 0.05) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._frame_age_hint = 0.0  # monotonic ts of latest frame
        self._last_read_stamp = 0.0
        self._closed = False
        self._opened = True
        self._wait_seconds = float(wait_seconds)
        self._wait_event = threading.Event()

    def push_jpeg(self, jpeg_bytes: bytes) -> None:
        """Server-side hook: decode a JPEG payload and publish as the latest frame."""
        if self._closed:
            return
        if not jpeg_bytes:
            return
        try:
            buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            return
        if frame is None or frame.size == 0:
            return
        with self._lock:
            self._frame = frame
            self._frame_age_hint = time.monotonic()
        self._wait_event.set()

    def has_fresh_frame(self) -> bool:
        with self._lock:
            return self._frame is not None and self._frame_age_hint > self._last_read_stamp

    def isOpened(self) -> bool:  # noqa: N802 (cv2 API parity)
        return not self._closed

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:  # noqa: D401
        """Return the newest frame, blocking briefly if none has arrived yet.

        Returning quickly when frames haven't arrived avoids starving the
        engine tick — the engine's `_tick()` tolerates an occasional
        missed read by returning early.
        """
        if self._closed:
            return False, None
        deadline = time.monotonic() + self._wait_seconds
        while True:
            with self._lock:
                frame = self._frame
            if frame is not None:
                with self._lock:
                    self._last_read_stamp = self._frame_age_hint
                return True, frame.copy()
            if time.monotonic() >= deadline:
                return False, None
            # Short wait for the next push.
            self._wait_event.wait(timeout=max(0.002, deadline - time.monotonic()))
            self._wait_event.clear()

    def set(self, *_args, **_kwargs) -> bool:  # cv2.VideoCapture.set no-op for us
        return True

    def get(self, prop_id: int) -> float:
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            with self._lock:
                return float(self._frame.shape[1]) if self._frame is not None else 0.0
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            with self._lock:
                return float(self._frame.shape[0]) if self._frame is not None else 0.0
        return 0.0

    def grab(self) -> bool:
        return self.has_fresh_frame()

    def retrieve(self) -> Tuple[bool, Optional[np.ndarray]]:
        return self.read()

    def release(self) -> None:
        self._closed = True
        self._wait_event.set()
        with self._lock:
            self._frame = None
