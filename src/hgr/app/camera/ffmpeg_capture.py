"""FFmpeg-backed camera capture for Lite Mode on Windows.

Why this module exists:
OpenCV's `cv2.VideoCapture` on Windows uses DirectShow / Media Foundation
through a thin wrapper that does not reliably honour `CAP_PROP_FOURCC`
requests on common consumer webcams (Razer Kiyo, many cheap 1080p
webcams, etc.). Those drivers latch the negotiated stream format on
the first frame read and silently stay on YUY2 (uncompressed) — even
when we use OpenCV 4.5+'s 3-arg constructor that's supposed to apply
params before init. The result is a hard ~30 fps ceiling at 720p over
USB 2.0 because YUY2's bandwidth saturates the bus, plus a soft ~10 fps
ceiling at 1080p for the same reason.

ffmpeg's DirectShow input *does* honour `-vcodec mjpeg` reliably, so a
capture pipeline of:

    ffmpeg -f dshow -vcodec mjpeg -framerate <fps> -video_size <wxh> \\
           -i video=<friendly_name> \\
           -f rawvideo -pix_fmt bgr24 pipe:1

talks to the camera in MJPG, decodes the JPEG frames inside ffmpeg's
process (free CPU core), and pipes raw BGR24 bytes back to us at a
fixed frame size we can read deterministically. This unlocks 60 fps at
720p on cameras that advertise it but were stuck at 30 fps under
OpenCV, and 30 fps at 1080p on cameras that were stuck at ~10 fps.

The class below exposes the subset of the `cv2.VideoCapture` interface
the rest of this codebase touches: `isOpened()`, `read()`,
`release()`, `get(prop_id)`, `set(prop_id, value)`. The engine code
treats us as a drop-in replacement for the OpenCV capture object.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


def locate_ffmpeg() -> str | None:
    """Find the ffmpeg executable. Prefers a copy that lives next to
    the running interpreter / packaged exe (PyInstaller layout) over
    PATH, since the installer ships ffmpeg there and we want the
    bundled one to win on user machines that have an older system
    ffmpeg installed."""
    exe_name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    candidates: list[Path] = []
    try:
        candidates.append(Path(sys.executable).resolve().with_name(exe_name))
    except Exception:
        pass
    try:
        candidates.append(Path.cwd() / exe_name)
    except Exception:
        pass
    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except Exception:
            pass
    return shutil.which("ffmpeg") or shutil.which(exe_name)


def list_dshow_video_devices(ffmpeg_path: str | None = None) -> list[str]:
    """Ask ffmpeg for the DirectShow video device list. Returns the
    friendly names ffmpeg expects after `-i video=`. The names are
    case- and whitespace-sensitive, so we surface them verbatim."""
    if not sys.platform.startswith("win"):
        return []
    path = ffmpeg_path or locate_ffmpeg()
    if not path:
        return []
    try:
        completed = subprocess.run(
            [path, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=6.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    # ffmpeg writes the device list to stderr. Lines look like:
    #   [dshow @ 0x...] "Razer Kiyo" (video)
    #   [dshow @ 0x...]   Alternative name "@device_pnp_..."
    # We want only the friendly-name rows tagged (video).
    text = (completed.stderr or "") + (completed.stdout or "")
    pattern = re.compile(r'"([^"]+)"\s*\(video\)', re.IGNORECASE)
    devices: list[str] = []
    for match in pattern.finditer(text):
        name = match.group(1).strip()
        if name and name not in devices:
            devices.append(name)
    return devices


def resolve_dshow_device_for_index(index: int, qt_name_hint: str = "") -> str | None:
    """Best-effort mapping from a CameraInfo.index to the DirectShow
    friendly name ffmpeg wants. Tries an exact / case-insensitive match
    against the Qt-reported display name first, then falls back to the
    Nth device in ffmpeg's enumerated list. Returns None when nothing
    plausibly maps — caller should then fall back to the OpenCV path."""
    devices = list_dshow_video_devices()
    if not devices:
        return None
    hint = (qt_name_hint or "").strip()
    if hint:
        # Strip our "(Camera N)" suffix from the Qt-side display name
        # before comparing — DirectShow doesn't include that bracket.
        hint_clean = re.sub(r"\s*\(Camera\s+\d+\)\s*$", "", hint).strip()
        for candidate in devices:
            if candidate.lower() == hint_clean.lower():
                return candidate
        for candidate in devices:
            if candidate and (candidate.lower() in hint_clean.lower() or hint_clean.lower() in candidate.lower()):
                return candidate
    if 0 <= int(index) < len(devices):
        return devices[int(index)]
    return None


class FfmpegMjpegCapture:
    """A `cv2.VideoCapture`-shaped wrapper around an ffmpeg subprocess.

    Lifecycle:
      cap = FfmpegMjpegCapture(device_name="Razer Kiyo")
      if cap.isOpened():
          ok, frame = cap.read()  # frame is HxWx3 BGR np.ndarray
      cap.release()

    Threading model: ffmpeg writes raw BGR frames to stdout. A daemon
    reader thread on our side reads exactly W*H*3 bytes per frame off
    the pipe and stores the latest one under a lock; `read()` returns
    that latest frame. We keep just the latest — same single-frame
    "drop stale" behaviour we'd get from `cap.set(BUFFERSIZE, 1)` on
    the OpenCV path. This matches what every other consumer in the
    engine expects (gesture loop wants the freshest frame, not a
    queue of pending ones)."""

    def __init__(
        self,
        device_name: str,
        *,
        width: int = 1280,
        height: int = 720,
        fps: int = 60,
        ffmpeg_path: str | None = None,
        startup_timeout_seconds: float = 8.0,
    ) -> None:
        self._device_name = device_name
        self._width = int(width)
        self._height = int(height)
        self._fps = int(fps)
        self._ffmpeg_path = ffmpeg_path or locate_ffmpeg()
        # Bumped startup_timeout from 4s to 8s. After we
        # release-and-reopen on Windows DSHOW, the camera driver can
        # take 2-5s to actually free the device on first toggle —
        # 4s caught some real-world cases of "camera technically
        # available but driver not done flushing yet" and reported
        # them as ffmpeg failing, when in fact ffmpeg just hadn't
        # gotten a chance to negotiate the format yet.
        self._startup_timeout = float(startup_timeout_seconds)
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._first_frame_event = threading.Event()
        self._opened = False
        self._read_error = False
        # Capture ffmpeg's stderr to a memory buffer + a stderr
        # mirror so we can surface the actual failure reason on
        # startup. The previous configuration discarded stderr to
        # DEVNULL, which made "ffmpeg cap startup failed" a black
        # box — the user couldn't see whether the camera was busy,
        # the format was rejected, or the device name was wrong.
        self._stderr_log: list[str] = []
        self._stderr_thread: Optional[threading.Thread] = None
        self._opened = self._start()

    @property
    def device_name(self) -> str:
        return self._device_name

    def _start(self) -> bool:
        if not self._ffmpeg_path:
            return False
        if not sys.platform.startswith("win"):
            # The dshow input is Windows-only. No fallback platform
            # for this module yet — caller will fall through to OpenCV.
            return False
        if not self._device_name:
            return False
        # rtbufsize=32M: ffmpeg keeps a large input MJPG buffer so it
        # can decode + write BGR to pipe at full camera rate without
        # being stalled by the consumer. Smaller values (3M, 12M)
        # tested measurably worse — the OS pipe between ffmpeg's
        # stdout and our reader is tiny (~64KB on Windows, less than
        # one BGR frame), so without enough rtbufsize ffmpeg's BGR
        # write back-pressures the camera capture and the consumer
        # ends up waiting ~22 ms per cap.read() call. The latency
        # buffering this introduces (~200 ms of pipelined frames) is
        # hidden by the consumer always reading the freshest frame
        # from latest_frame and dropping older ones — see
        # FfmpegMjpegCapture.read.
        cmd = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-rtbufsize", "32M",
            "-f", "dshow",
            "-vcodec", "mjpeg",
            "-framerate", str(self._fps),
            "-video_size", f"{self._width}x{self._height}",
            "-i", f"video={self._device_name}",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1",
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                bufsize=0,
            )
        except Exception as exc:
            self._stderr_log.append(f"Popen failed: {exc!s}")
            self._proc = None
            return False
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"ffmpeg-cap-{self._device_name[:24]}",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            name=f"ffmpeg-stderr-{self._device_name[:18]}",
            daemon=True,
        )
        self._stderr_thread.start()
        # Give ffmpeg a few seconds to negotiate format and emit the
        # first frame. If we don't see anything by the deadline, give
        # up so the caller can fall back to OpenCV without leaving the
        # user staring at a blank live view. We surface ffmpeg's own
        # stderr on failure so the user can see whether the device
        # was busy, the format was rejected, etc.
        if self._first_frame_event.wait(timeout=self._startup_timeout):
            return True
        self._teardown_proc()
        try:
            tail = "".join(self._stderr_log[-12:]).strip()
        except Exception:
            tail = ""
        if tail:
            try:
                sys.stderr.write(f"[ffmpeg_capture] startup stderr tail: {tail}\n")
                sys.stderr.flush()
            except Exception:
                pass
        return False

    def _stderr_loop(self) -> None:
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            for line in iter(self._proc.stderr.readline, b""):
                if not line:
                    break
                try:
                    text = line.decode("utf-8", errors="replace")
                except Exception:
                    continue
                self._stderr_log.append(text)
                if len(self._stderr_log) > 200:
                    del self._stderr_log[:100]
        except Exception:
            return

    def _reader_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        frame_bytes = self._width * self._height * 3
        while not self._stop_event.is_set():
            try:
                chunk = self._read_exact(self._proc.stdout, frame_bytes)
            except Exception:
                self._read_error = True
                self._first_frame_event.set()
                return
            if chunk is None:
                # ffmpeg ended (process exit / EOF on pipe). Mark
                # failure so the engine can drop us and try again.
                self._read_error = True
                self._first_frame_event.set()
                return
            try:
                # `np.frombuffer` over an immutable `bytes` object
                # makes the result `writeable=False`. Several
                # OpenCV operations inside MediaPipe end up doing
                # an *internal* copy on read-only inputs, which
                # actually pushed `engine=` from ~19 ms to ~30 ms
                # in testing — slower than just doing one
                # explicit copy here ourselves. So we copy into a
                # writable buffer up front. This is the only copy
                # in the camera→engine path now (we already
                # dropped the engine-side and overlay-side
                # copies).
                frame = np.frombuffer(chunk, dtype=np.uint8).reshape(
                    (self._height, self._width, 3)
                ).copy()
            except Exception:
                continue
            with self._frame_lock:
                self._latest_frame = frame
            self._first_frame_event.set()

    @staticmethod
    def _read_exact(stream, size: int) -> bytes | None:
        """Read exactly `size` bytes from a binary stream. Returns
        None on EOF (process exit). Required because pipe reads
        otherwise return short blocks at high frame rates."""
        buf = bytearray()
        while len(buf) < size:
            chunk = stream.read(size - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def isOpened(self) -> bool:  # cv2.VideoCapture API parity
        if not self._opened:
            return False
        if self._read_error:
            return False
        if self._proc is None or self._proc.poll() is not None:
            return False
        return True

    def read(self):  # cv2.VideoCapture API parity
        if not self.isOpened():
            return False, None
        with self._frame_lock:
            frame = self._latest_frame
            self._latest_frame = None
        if frame is None:
            # No new frame since the last read. Wait briefly so
            # callers polling at MediaPipe inference rate (typically
            # 35-60 Hz) don't busy-spin when ffmpeg's stream is
            # paused or temporarily stalled.
            woke = self._first_frame_event.wait(timeout=0.1)
            if not woke:
                return False, None
            with self._frame_lock:
                frame = self._latest_frame
                self._latest_frame = None
            if frame is None:
                return False, None
        return True, frame

    def release(self) -> None:  # cv2.VideoCapture API parity
        self._stop_event.set()
        self._teardown_proc()
        thread = self._reader_thread
        self._reader_thread = None
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=1.0)
            except Exception:
                pass
        with self._frame_lock:
            self._latest_frame = None
        self._opened = False

    def _teardown_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except Exception:
            pass
        for stream_name in ("stdout", "stderr", "stdin"):
            stream = getattr(proc, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    # cv2.VideoCapture-style get/set so the rest of the engine can
    # query frame size & fps without knowing whether we're a real
    # cv2 cap or an ffmpeg-backed one.

    def get(self, prop_id: int) -> float:
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if prop_id == cv2.CAP_PROP_FPS:
            return float(self._fps)
        if prop_id == cv2.CAP_PROP_FOURCC:
            try:
                return float(cv2.VideoWriter_fourcc(*"MJPG"))
            except Exception:
                return 0.0
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE") and prop_id == cv2.CAP_PROP_BUFFERSIZE:
            return 1.0
        return 0.0

    def set(self, prop_id: int, value: float) -> bool:  # noqa: ARG002
        # Settings cannot be changed after the ffmpeg process is
        # started; the engine's other code paths set BUFFERSIZE etc.
        # which we already enforce ourselves, so silently no-op
        # rather than raising.
        return False

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
