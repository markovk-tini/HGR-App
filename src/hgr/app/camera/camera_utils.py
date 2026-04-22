
from __future__ import annotations

import platform
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2


@dataclass(frozen=True)
class CameraInfo:
    index: int
    backend: int
    backend_name: str
    display_name: str


_BACKEND_NAMES = {
    getattr(cv2, "CAP_AVFOUNDATION", -99999): "AVFoundation",
    getattr(cv2, "CAP_DSHOW", -99998): "DirectShow",
    getattr(cv2, "CAP_MSMF", -99997): "Media Foundation",
    getattr(cv2, "CAP_ANY", 0): "Default",
}


def backend_name(backend: int) -> str:
    return _BACKEND_NAMES.get(backend, f"Backend {backend}")


def _qt_video_device_names() -> List[str]:
    """Return the friendly names Qt reports for video capture devices.

    PySide6's QMediaDevices exposes the real device labels (e.g. "Iriun
    Webcam", "Integrated Camera", "USB Video Device") that OpenCV's
    VideoCapture does not. On Windows + DirectShow these typically
    enumerate in the same order as OpenCV's integer indices, so we can
    zip them together when the counts match. Falls back silently to an
    empty list if Qt's multimedia module is unavailable.
    """
    try:
        from PySide6.QtMultimedia import QMediaDevices
    except Exception:
        return []
    try:
        return [str(dev.description() or "").strip() for dev in QMediaDevices.videoInputs()]
    except Exception:
        return []


def _backend_candidates() -> List[int]:
    system = platform.system()

    if system == "Darwin":
        # On macOS, avoid CAP_ANY fallback because it tends to duplicate AVFoundation probing
        # and produces extra invalid-index noise/crashes in this app flow.
        if hasattr(cv2, "CAP_AVFOUNDATION"):
            return [cv2.CAP_AVFOUNDATION]
        return [cv2.CAP_ANY]

    if system == "Windows":
        backends: List[int] = []
        if hasattr(cv2, "CAP_DSHOW"):
            backends.append(cv2.CAP_DSHOW)
        if hasattr(cv2, "CAP_MSMF"):
            backends.append(cv2.CAP_MSMF)
        unique: List[int] = []
        for backend in backends:
            if backend not in unique:
                unique.append(backend)
        return unique

    return [cv2.CAP_ANY]


def _candidate_indices(max_index: int) -> List[int]:
    if max_index <= 0:
        return []
    if platform.system() == "Darwin":
        # macOS/OpenCV AVFoundation has been unstable here when probing out-of-range indices.
        # Probe only index 0 during app discovery/preflight.
        return [0]
    return list(range(max_index))


@contextmanager
def _quiet_opencv_probe():
    get_level = getattr(cv2, "getLogLevel", None)
    set_level = getattr(cv2, "setLogLevel", None)
    previous_level = None
    if callable(get_level) and callable(set_level):
        try:
            previous_level = int(get_level())
            set_level(0)
        except Exception:
            previous_level = None
    try:
        yield
    finally:
        if previous_level is not None and callable(set_level):
            try:
                set_level(previous_level)
            except Exception:
                pass


def try_open_camera(index: int, backend: int, read_attempts: int = 10) -> Optional[cv2.VideoCapture]:
    with _quiet_opencv_probe():
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            return None

        for _ in range(read_attempts):
            ok, _ = cap.read()
            if ok:
                return cap
            time.sleep(0.03)

        cap.release()
        return None


def request_camera_access_main_thread(max_index: int = 4) -> tuple[bool, str]:
    system = platform.system()
    if system != "Darwin":
        return True, "Camera permission prompt is not required on this platform."

    for backend in _backend_candidates():
        cap = try_open_camera(0, backend, read_attempts=12)
        if cap is not None:
            cap.release()
            return True, "Camera access confirmed on camera 0."

    return False, (
        "macOS camera access was not granted yet. Approve camera access when prompted, "
        "or enable it in System Settings > Privacy & Security > Camera for Terminal or your packaged app, then try again."
    )


def list_available_cameras(max_index: int = 8) -> List[CameraInfo]:
    discovered: List[CameraInfo] = []
    consecutive_misses = 0
    stop_after_misses = 2 if platform.system() == "Windows" else max_index
    qt_names = _qt_video_device_names()

    for index in _candidate_indices(max_index):
        found_for_index = False
        for backend in _backend_candidates():
            cap = try_open_camera(index, backend)
            if cap is None:
                continue
            cap.release()
            # Prefer Qt's friendly device name at the matching index (e.g.
            # "Iriun Webcam"), and only fall back to "Camera N (Backend)"
            # when Qt either couldn't enumerate or returned fewer entries.
            friendly = qt_names[index] if index < len(qt_names) else ""
            if friendly:
                display = f"{friendly} (Camera {index})"
            else:
                display = f"Camera {index} ({backend_name(backend)})"
            discovered.append(
                CameraInfo(
                    index=index,
                    backend=backend,
                    backend_name=backend_name(backend),
                    display_name=display,
                )
            )
            found_for_index = True
            break
        if platform.system() == "Windows":
            if found_for_index:
                consecutive_misses = 0
            else:
                consecutive_misses += 1
                if discovered and consecutive_misses >= stop_after_misses:
                    break

    return discovered


def find_first_available_camera(max_index: int = 8) -> Tuple[Optional[int], Optional[cv2.VideoCapture]]:
    cameras = list_available_cameras(max_index)
    if not cameras:
        return None, None
    selected = cameras[0]
    cap = try_open_camera(selected.index, selected.backend)
    if cap is None:
        return None, None
    return selected.index, cap


def open_camera_by_index(index: int, max_index: int = 8) -> Tuple[Optional[CameraInfo], Optional[cv2.VideoCapture]]:
    # On macOS this app only supports index 0 for direct camera access in order to avoid
    # unstable AVFoundation probing of non-existent indices.
    if platform.system() == "Darwin" and index != 0:
        return None, None

    for backend in _backend_candidates():
        cap = try_open_camera(index, backend)
        if cap is not None:
            info = CameraInfo(
                index=index,
                backend=backend,
                backend_name=backend_name(backend),
                display_name=f"Camera {index} ({backend_name(backend)})",
            )
            return info, cap
    return None, None


def try_open_camera_url(url: str, read_attempts: int = 12) -> Optional[cv2.VideoCapture]:
    """Open an IP-webcam-style stream URL (MJPEG / RTSP / HTTP) and verify a frame arrives.

    Returns a `cv2.VideoCapture` on success, or None. Blocks for up to a few
    seconds while waiting for the first frame — callers that need
    responsiveness (e.g. a Test button in Settings) should run this on a
    worker thread.
    """
    clean = str(url or "").strip()
    if not clean:
        return None
    with _quiet_opencv_probe():
        try:
            cap = cv2.VideoCapture(clean)
        except Exception:
            return None
        if not cap.isOpened():
            cap.release()
            return None
        for _ in range(read_attempts):
            ok, _ = cap.read()
            if ok:
                return cap
            time.sleep(0.08)
        cap.release()
        return None


def open_phone_camera_url(url: str) -> Tuple[Optional[CameraInfo], Optional[cv2.VideoCapture]]:
    cap = try_open_camera_url(url)
    if cap is None:
        return None, None
    info = CameraInfo(
        index=-1,
        backend=0,
        backend_name="Phone",
        display_name=f"Phone Camera ({url})",
    )
    return info, cap


def open_preferred_or_first_available(preferred_index: Optional[int], max_index: int = 8) -> Tuple[Optional[CameraInfo], Optional[cv2.VideoCapture]]:
    if preferred_index is not None:
        info, cap = open_camera_by_index(preferred_index, max_index=max_index)
        if info is not None and cap is not None:
            return info, cap

    cameras = list_available_cameras(max_index)
    if not cameras:
        return None, None

    selected = cameras[0]
    cap = try_open_camera(selected.index, selected.backend)
    if cap is None:
        return None, None
    return selected, cap
