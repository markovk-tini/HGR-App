"""Prevent two Touchless processes from running concurrently.

Two Touchless instances would fight over the camera, microphone,
and any QR-paired phone server (the second app can't bind to the
already-listening port). The user typically hits this by double-
clicking the desktop shortcut while one is already running, or
when the auto-updater respawns and the previous process exits a
second too late.

Strategy: a Win32 named mutex held for the lifetime of the
process. If the mutex already exists, a Touchless is already up;
the new instance bails out immediately and tries to focus the
existing window via FindWindow + SetForegroundWindow.

Why a Win32 mutex (not Qt's QSharedMemory): mutexes are reliably
torn down when the holding process exits — even if it crashed —
because the kernel cleans up on handle close. QSharedMemory leaks
its segment when the process is killed unexpectedly, leaving the
"already running" check stuck in the True state until reboot.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# A mutex name globally unique to Touchless. The "Local\\" prefix
# scopes it to the current Windows session — different users on
# the same machine each get their own Touchless instance, which
# matches our per-user install model.
_MUTEX_NAME = "Local\\Touchless_SingleInstance_2C4EE680"
_ERROR_ALREADY_EXISTS = 183
_SW_SHOWNORMAL = 1
_SW_RESTORE = 9


_handle: int | None = None


def acquire() -> bool:
    """Try to acquire the single-instance lock. Returns True if
    this is the only Touchless instance, False if another is
    already running. Caller should exit on False."""
    global _handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.GetLastError.restype = wintypes.DWORD
        # Initial owner = False so we can probe the GetLastError
        # afterwards reliably.
        _handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if not _handle:
            # Mutex creation outright failed — be permissive and
            # let the app continue rather than blocking launch.
            return True
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            # Mutex existed before our call: another Touchless is
            # already running. Try to focus its window so the
            # user knows where it went, then signal "don't start".
            _focus_existing_window()
            return False
        return True
    except Exception:
        return True


def _focus_existing_window() -> None:
    """Best-effort raise of the running Touchless's main window."""
    try:
        user32 = ctypes.windll.user32
        # Touchless titles its main window "Touchless" (see
        # MainWindow.setWindowTitle). FindWindow with a NULL class
        # name searches by window title only.
        hwnd = user32.FindWindowW(None, "Touchless")
        if not hwnd:
            return
        # If the window is minimized, restore it. Then bring to top.
        user32.ShowWindow(hwnd, _SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
