"""Screen-context capture for the Live API session.

Reuses the existing PIL ImageGrab pipeline already in
`src/hgr/debug/youtube_controller.py` (Pillow is in requirements.txt).
TODO: extend to multi-monitor — for the prototype we capture the
primary virtual screen and crop on demand later.

Image data is base64-encoded JPEG bytes ready to be sent as a
content part to the Realtime model. The capture uses a worker
thread loop scheduled from `LiveApiManager`, so the UI never
blocks on a screenshot.
"""
from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .live_api_logger import LiveApiLogger
from ..debug.foreground_window import get_foreground_window_info


@dataclass
class ScreenFrame:
    captured_at: float
    width: int
    height: int
    jpeg_bytes: bytes
    active_window_title: str
    active_window_process: str

    @property
    def b64(self) -> str:
        return base64.b64encode(self.jpeg_bytes).decode("ascii")


class ScreenContext:
    """Captures + compresses screenshots on demand."""

    def __init__(
        self,
        *,
        max_width: int,
        jpeg_quality: int,
        logger: LiveApiLogger,
        debug_save_dir: Optional[Path] = None,
    ) -> None:
        self._max_width = max(320, int(max_width))
        self._jpeg_quality = max(20, min(95, int(jpeg_quality)))
        self._logger = logger
        self._debug_save_dir = debug_save_dir
        self._capture_count = 0

    def capture(self) -> Optional[ScreenFrame]:
        started = time.time()
        try:
            from PIL import ImageGrab
        except Exception as exc:
            self._logger.exception("screen_capture_pillow_missing", exc)
            return None

        try:
            # all_screens=True captures the full virtual desktop on
            # multi-monitor setups so the model sees windows on any
            # monitor, not just the one with the active window.
            img = ImageGrab.grab(all_screens=True)
        except Exception as exc:
            self._logger.exception("screen_capture_grab_failed", exc)
            return None

        try:
            if img.width > self._max_width:
                ratio = self._max_width / float(img.width)
                new_size = (self._max_width, max(1, int(round(img.height * ratio))))
                img = img.resize(new_size)
            if img.mode != "RGB":
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=self._jpeg_quality, optimize=False)
            jpeg_bytes = buf.getvalue()
        except Exception as exc:
            self._logger.exception("screen_capture_encode_failed", exc)
            return None

        info = get_foreground_window_info()
        title = "" if info is None else (info.title or "")
        process = "" if info is None else (info.process_name or "")

        frame = ScreenFrame(
            captured_at=started,
            width=img.width,
            height=img.height,
            jpeg_bytes=jpeg_bytes,
            active_window_title=title,
            active_window_process=process,
        )
        self._capture_count += 1
        elapsed_ms = round((time.time() - started) * 1000.0, 2)
        self._logger.event(
            "screen_capture",
            seq=self._capture_count,
            width=frame.width,
            height=frame.height,
            jpeg_kb=round(len(jpeg_bytes) / 1024.0, 2),
            window_title=title,
            window_process=process,
            elapsed_ms=elapsed_ms,
        )

        if self._debug_save_dir is not None:
            try:
                self._debug_save_dir.mkdir(parents=True, exist_ok=True)
                out = self._debug_save_dir / f"screen_{int(started)}_{self._capture_count}.jpg"
                out.write_bytes(jpeg_bytes)
            except Exception as exc:
                self._logger.exception("screen_capture_debug_save_failed", exc)

        return frame

# Author: Konstantin Markov
