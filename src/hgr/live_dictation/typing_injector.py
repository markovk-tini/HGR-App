"""Low-level text injection via Win32 SendInput.

We deliberately avoid higher-level libraries (pyautogui, keyboard) so
dictation works inside applications that filter synthetic input. Each
character is sent as a UTF-16 KEYEVENTF_UNICODE scancode event, which
is accepted by every standard Windows edit control and by Electron /
Chromium text fields. Newlines are sent as VK_RETURN.

Thread-safe: SendInput serializes internally. Callers may invoke
:func:`type_text` from any thread.
"""
from __future__ import annotations

import ctypes
import logging
import platform
from ctypes import wintypes

log = logging.getLogger(__name__)


_AVAILABLE = platform.system() == "Windows"


if _AVAILABLE:
    _INPUT_KEYBOARD = 1
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_UNICODE = 0x0004
    _VK_RETURN = 0x0D
    _ULONG_PTR = ctypes.c_size_t

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", _MOUSEINPUT),
            ("ki", _KEYBDINPUT),
            ("hi", _HARDWAREINPUT),
        ]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("i",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("i", _INPUT_UNION),
        ]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _SendInput = _user32.SendInput
    _SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _SendInput.restype = wintypes.UINT

    def _make_unicode(scan: int, key_up: bool) -> _INPUT:
        ev = _INPUT()
        ev.type = _INPUT_KEYBOARD
        ev.ki.wVk = 0
        ev.ki.wScan = scan & 0xFFFF
        flags = _KEYEVENTF_UNICODE | (_KEYEVENTF_KEYUP if key_up else 0)
        ev.ki.dwFlags = flags
        ev.ki.time = 0
        ev.ki.dwExtraInfo = 0
        return ev

    def _make_vk(vk: int, key_up: bool) -> _INPUT:
        ev = _INPUT()
        ev.type = _INPUT_KEYBOARD
        ev.ki.wVk = vk
        ev.ki.wScan = 0
        ev.ki.dwFlags = _KEYEVENTF_KEYUP if key_up else 0
        ev.ki.time = 0
        ev.ki.dwExtraInfo = 0
        return ev


def available() -> bool:
    return _AVAILABLE


def type_text(text: str) -> bool:
    """Inject ``text`` into the focused window.

    Returns True on full success, False otherwise. Newlines map to
    Enter (VK_RETURN); all other characters are sent as UTF-16 code
    units with KEYEVENTF_UNICODE so non-ASCII works transparently.
    Thread-safe.
    """
    if not _AVAILABLE or not text:
        return False

    events: list[_INPUT] = []
    for ch in text:
        if ch in ("\n", "\r"):
            events.append(_make_vk(_VK_RETURN, False))
            events.append(_make_vk(_VK_RETURN, True))
            continue
        encoded = ch.encode("utf-16-le")
        for i in range(0, len(encoded), 2):
            unit = int.from_bytes(encoded[i:i + 2], "little")
            events.append(_make_unicode(unit, False))
            events.append(_make_unicode(unit, True))

    if not events:
        return False

    arr = (_INPUT * len(events))(*events)
    sent = _SendInput(len(events), arr, ctypes.sizeof(_INPUT))
    if sent != len(events):
        err = ctypes.get_last_error()
        log.warning("SendInput: %d/%d events accepted (GetLastError=%d)",
                    sent, len(events), err)
        return False
    return True
