from __future__ import annotations

import ctypes
import platform
import subprocess
import time
from ctypes import wintypes


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_BACK = 0x08
VK_H = 0x48
VK_LWIN = 0x5B
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_TAB = 0x09
SW_RESTORE = 9
ASFW_ANY = 0xFFFFFFFF
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (("ki", KEYBDINPUT),)


class INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("union", INPUT_UNION),
    )


class TextInputController:
    def __init__(self) -> None:
        self._available = platform.system() == "Windows"
        self._message = "text input ready" if self._available else "text input unavailable on this platform"
        self._user32 = ctypes.windll.user32 if self._available else None
        self._kernel32 = ctypes.windll.kernel32 if self._available else None
        self._target_hwnd: int | None = None
        self._last_external_hwnd: int | None = None
        self._own_pid: int = int(self._kernel32.GetCurrentProcessId()) if self._kernel32 is not None else 0
        if self._available and self._user32 is not None:
            try:
                self._user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
                self._user32.SendInput.restype = wintypes.UINT
                self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
                self._user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
                self._user32.AttachThreadInput.restype = wintypes.BOOL
                self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
                self._user32.SetForegroundWindow.restype = wintypes.BOOL
                self._user32.SetFocus.argtypes = [wintypes.HWND]
                self._user32.SetFocus.restype = wintypes.HWND
                self._user32.SetActiveWindow.argtypes = [wintypes.HWND]
                self._user32.SetActiveWindow.restype = wintypes.HWND
                self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                self._user32.ShowWindow.restype = wintypes.BOOL
                self._user32.BringWindowToTop.argtypes = [wintypes.HWND]
                self._user32.BringWindowToTop.restype = wintypes.BOOL
                self._user32.IsWindow.argtypes = [wintypes.HWND]
                self._user32.IsWindow.restype = wintypes.BOOL
                self._user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
                self._user32.AllowSetForegroundWindow.restype = wintypes.BOOL
                self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
                self._user32.IsWindowVisible.restype = wintypes.BOOL
                self._user32.IsIconic.argtypes = [wintypes.HWND]
                self._user32.IsIconic.restype = wintypes.BOOL
                self._user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                self._user32.GetClassNameW.restype = ctypes.c_int
                self._user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                self._user32.GetWindowTextW.restype = ctypes.c_int
                self._user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
                self._user32.EnumWindows.restype = wintypes.BOOL
                self._user32.OpenClipboard.argtypes = [wintypes.HWND]
                self._user32.OpenClipboard.restype = wintypes.BOOL
                self._user32.CloseClipboard.argtypes = []
                self._user32.CloseClipboard.restype = wintypes.BOOL
                self._user32.EmptyClipboard.argtypes = []
                self._user32.EmptyClipboard.restype = wintypes.BOOL
                self._user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
                self._user32.SetClipboardData.restype = wintypes.HANDLE
                self._kernel32.GetCurrentThreadId.argtypes = []
                self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
                self._kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
                self._kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
                self._kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
                self._kernel32.GlobalLock.restype = wintypes.LPVOID
                self._kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
                self._kernel32.GlobalUnlock.restype = wintypes.BOOL
            except Exception:
                pass

    @property
    def available(self) -> bool:
        return self._available

    @property
    def message(self) -> str:
        return self._message

    def capture_target_window(self) -> bool:
        if not self._available or self._user32 is None:
            self._message = "text input unavailable on this platform"
            return False
        self.remember_active_window()
        hwnd = self._foreground_window()
        if hwnd > 0 and not self._is_own_window(hwnd):
            self._target_hwnd = hwnd
            self._last_external_hwnd = hwnd
            self._message = "dictation target captured"
            return True
        fallback = int(self._last_external_hwnd or 0)
        if fallback > 0:
            self._target_hwnd = fallback
            self._message = "dictation target captured"
            return True
        if int(self._target_hwnd or 0) > 0:
            self._message = "dictation target captured"
            return True
        self._message = "could not capture dictation target"
        return False

    def clear_target_window(self) -> None:
        self._target_hwnd = None

    def remember_active_window(self) -> bool:
        if not self._available or self._user32 is None:
            return False
        hwnd = self._foreground_window()
        if hwnd <= 0 or self._is_own_window(hwnd):
            return False
        self._last_external_hwnd = hwnd
        return True

    def focus_target_window(self) -> bool:
        ok = self._restore_target_window()
        if ok:
            self._message = "dictation target focused"
        else:
            self._message = "could not focus dictation target"
        return ok
    def insert_text(self, text: str) -> bool:
        if not self._available or self._user32 is None:
            self._message = "text input unavailable on this platform"
            return False
        payload = str(text or "")
        if not payload:
            self._message = "dictation text missing"
            return False
        if not self._restore_target_window():
            self._message = "could not focus dictation target"
            return False
        time.sleep(0.06)
        if self._paste_text(payload):
            self._message = f"inserted dictated text ({len(payload)} chars)"
            return True

        inputs: list[INPUT] = []
        for char in payload:
            if char == "\n":
                inputs.extend(self._vk_inputs(VK_RETURN))
            elif char == "\t":
                inputs.extend(self._vk_inputs(VK_TAB))
            else:
                inputs.extend(self._unicode_inputs(char))
        if not inputs:
            self._message = "dictation text missing"
            return False

        array_type = INPUT * len(inputs)
        sent = int(self._user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT)))
        if sent != len(inputs):
            self._message = "could not insert dictated text"
            return False
        self._message = f"inserted dictated text ({len(payload)} chars)"
        return True


    def remove_text(self, char_count: int) -> bool:
        if not self._available or self._user32 is None:
            self._message = "text input unavailable on this platform"
            return False
        count = max(0, int(char_count))
        if count <= 0:
            self._message = "nothing to remove"
            return True
        if not self._restore_target_window():
            self._message = "could not focus dictation target"
            return False

        inputs: list[INPUT] = []
        for _index in range(count):
            inputs.extend(self._vk_inputs(VK_BACK))
        array_type = INPUT * len(inputs)
        sent = int(self._user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT)))
        if sent != len(inputs):
            self._message = "could not update dictated text"
            return False
        self._message = f"updated dictated text (-{count} chars)"
        return True

    def replace_text(self, previous_text: str, new_text: str) -> bool:
        prior = str(previous_text or "")
        replacement = str(new_text or "")
        if prior and not self.remove_text(len(prior)):
            return False
        if replacement:
            return self.insert_text(replacement)
        self._message = "updated dictated text"
        return True

    def toggle_windows_dictation(self) -> bool:
        if not self._available or self._user32 is None:
            self._message = "windows dictation unavailable on this platform"
            return False
        try:
            # Sending Win+H as separate events is more reliable for the OS voice typing flyout
            # than batching the whole shortcut into one SendInput array.
            if not self._send_key_down(VK_LWIN):
                return False
            time.sleep(0.03)
            if not self._send_key_down(VK_H):
                self._send_key_up(VK_LWIN)
                return False
            time.sleep(0.03)
            if not self._send_key_up(VK_H):
                self._send_key_up(VK_LWIN)
                return False
            time.sleep(0.03)
            if not self._send_key_up(VK_LWIN):
                return False
        except Exception:
            self._message = "could not send keyboard shortcut"
            return False
        self._message = "sent keyboard shortcut"
        return True

    def start_windows_dictation(self) -> bool:
        if not self._available or self._user32 is None:
            self._message = "windows dictation unavailable on this platform"
            return False

        # 1) If an external window is already foreground, use it directly.
        self.remember_active_window()
        foreground = self._foreground_window()
        if foreground > 0 and not self._is_own_window(foreground):
            self._target_hwnd = foreground
            self._last_external_hwnd = foreground
            time.sleep(0.05)
            if not self.toggle_windows_dictation():
                return False
            self._message = "dictation active at the current cursor"
            return True

        # 2) Poll briefly — the user may be switching focus, or the HGR overlay
        # may momentarily be foreground.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            time.sleep(0.05)
            self.remember_active_window()
            fg = self._foreground_window()
            if fg > 0 and not self._is_own_window(fg):
                self._target_hwnd = fg
                self._last_external_hwnd = fg
                if not self.toggle_windows_dictation():
                    return False
                self._message = "dictation active at the current cursor"
                return True

        # 3) Try to restore a previously remembered external window.
        target_hwnd = int(self._last_external_hwnd or self._target_hwnd or 0)
        if target_hwnd > 0:
            try:
                if not bool(self._user32.IsWindow(wintypes.HWND(target_hwnd))):
                    target_hwnd = 0
            except Exception:
                target_hwnd = 0
        if target_hwnd > 0:
            self._target_hwnd = target_hwnd
            if self._restore_target_window():
                time.sleep(0.15)
                if not self.toggle_windows_dictation():
                    return False
                self._message = "dictation active at the current cursor"
                return True

        # 4) Look for an already-open external text-editor window.
        existing = self._find_external_text_window()
        if existing > 0:
            self._target_hwnd = existing
            self._last_external_hwnd = existing
            if self._restore_target_window():
                time.sleep(0.15)
                if not self.toggle_windows_dictation():
                    return False
                self._message = "dictation active at the current cursor"
                return True

        # 5) Nothing clicked anywhere — launch a fresh Notepad and dictate there.
        if not self._launch_notepad_for_dictation(timeout=4.0):
            self._message = "could not open notepad for dictation"
            return False
        time.sleep(0.25)
        if not self.toggle_windows_dictation():
            return False
        self._message = "dictation active in Notepad"
        return True

    def _find_external_text_window(self) -> int:
        if not self._available or self._user32 is None:
            return 0
        preferred_classes = {
            "notepad",
            "edit",
            "richedit",
            "richedit20w",
            "richeditd2dpt",
            "richedit50w",
            "opusapp",  # Word
            "wordpadclass",
            "chrome_widgetwin_1",  # Chromium-based (Chrome, Edge, VS Code)
            "mozillawindowclass",
            "applicationframewindow",  # Modern Win11 Notepad wrapper
        }
        found: list[tuple[int, int]] = []  # (priority, hwnd)

        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd, _lparam):
            try:
                if not bool(self._user32.IsWindowVisible(wintypes.HWND(hwnd))):
                    return True
                if bool(self._user32.IsIconic(wintypes.HWND(hwnd))):
                    return True
                if self._is_own_window(int(hwnd)):
                    return True
                class_name = self._window_class_name(int(hwnd)).lower()
                title = self._window_text(int(hwnd))
                if not title and class_name not in preferred_classes:
                    return True
                priority = 1 if class_name in preferred_classes else 3
                if class_name == "notepad" or "notepad" in title.lower():
                    priority = 0
                found.append((priority, int(hwnd)))
            except Exception:
                pass
            return True

        try:
            self._user32.EnumWindows(EnumProc(_callback), 0)
        except Exception:
            return 0
        if not found:
            return 0
        found.sort(key=lambda item: item[0])
        return found[0][1]

    def _window_class_name(self, hwnd: int) -> str:
        if not self._available or self._user32 is None or hwnd <= 0:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(256)
            self._user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
            return str(buf.value or "")
        except Exception:
            return ""

    def _window_text(self, hwnd: int) -> str:
        if not self._available or self._user32 is None or hwnd <= 0:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            self._user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
            return str(buf.value or "")
        except Exception:
            return ""

    def _launch_notepad_for_dictation(self, *, timeout: float = 4.0) -> bool:
        launched = False
        for cmd in (["notepad.exe"], ["cmd", "/c", "start", "", "notepad.exe"]):
            try:
                subprocess.Popen(cmd, shell=False)
                launched = True
                break
            except Exception:
                continue
        if not launched:
            return False
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            time.sleep(0.1)
            hwnd = self._foreground_window()
            if hwnd > 0 and not self._is_own_window(hwnd):
                self._target_hwnd = hwnd
                self._last_external_hwnd = hwnd
                return True
            candidate = self._find_external_text_window()
            if candidate > 0:
                self._target_hwnd = candidate
                self._last_external_hwnd = candidate
                self._restore_target_window()
                time.sleep(0.15)
                return True
        return False

    def stop_windows_dictation(self) -> bool:
        if not self._available or self._user32 is None:
            self._message = "windows dictation unavailable on this platform"
            return False
        self._restore_target_window()
        time.sleep(0.12)
        if not self.toggle_windows_dictation():
            return False
        self._message = "dictation stopped"
        return True

    def _foreground_window(self) -> int:
        if not self._available or self._user32 is None:
            return 0
        try:
            return int(self._user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def _restore_target_window(self) -> bool:
        if not self._available or self._user32 is None or self._kernel32 is None:
            return False
        target_hwnd = int(self._target_hwnd or self._last_external_hwnd or 0)
        if target_hwnd <= 0:
            return False
        try:
            if not bool(self._user32.IsWindow(wintypes.HWND(target_hwnd))):
                self._message = "dictation target is no longer available"
                return False
        except Exception:
            pass

        foreground = self._foreground_window()
        if foreground == target_hwnd:
            return True

        current_tid = int(self._kernel32.GetCurrentThreadId())
        target_tid = self._window_thread_id(target_hwnd)
        foreground_tid = self._window_thread_id(foreground) if foreground > 0 else 0
        attached: list[tuple[int, int]] = []
        pairs = []
        for a, b in ((current_tid, foreground_tid), (current_tid, target_tid), (target_tid, foreground_tid)):
            if a and b and a != b and (a, b) not in pairs:
                pairs.append((a, b))
        try:
            try:
                self._user32.AllowSetForegroundWindow(ASFW_ANY)
            except Exception:
                pass
            for a, b in pairs:
                try:
                    if bool(self._user32.AttachThreadInput(a, b, True)):
                        attached.append((a, b))
                except Exception:
                    pass
            try:
                self._user32.ShowWindow(wintypes.HWND(target_hwnd), SW_RESTORE)
            except Exception:
                pass
            # Nudge foreground permission with an Alt tap before requesting focus.
            self._tap_alt()
            try:
                self._user32.BringWindowToTop(wintypes.HWND(target_hwnd))
            except Exception:
                pass
            try:
                self._user32.SetForegroundWindow(wintypes.HWND(target_hwnd))
            except Exception:
                pass
            try:
                self._user32.SetActiveWindow(wintypes.HWND(target_hwnd))
            except Exception:
                pass
            try:
                self._user32.SetFocus(wintypes.HWND(target_hwnd))
            except Exception:
                pass
        finally:
            for a, b in reversed(attached):
                try:
                    self._user32.AttachThreadInput(a, b, False)
                except Exception:
                    pass
        time.sleep(0.08)
        return self._foreground_window() == target_hwnd

    def _tap_alt(self) -> None:
        if not self._available or self._user32 is None:
            return
        inputs = [
            self._keyboard_input(virtual_key=VK_MENU, flags=0),
            self._keyboard_input(virtual_key=VK_MENU, flags=KEYEVENTF_KEYUP),
        ]
        array_type = INPUT * len(inputs)
        try:
            self._user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT))
        except Exception:
            pass

    def _send_shortcut(self, *virtual_keys: int) -> bool:
        if not self._available or self._user32 is None:
            self._message = "text input unavailable on this platform"
            return False
        keys = [int(key) for key in virtual_keys if int(key) > 0]
        if not keys:
            return False
        try:
            for key in keys:
                if not self._send_key_down(key):
                    return False
                time.sleep(0.015)
            for key in reversed(keys):
                if not self._send_key_up(key):
                    return False
                time.sleep(0.015)
        except Exception:
            self._message = "could not send keyboard shortcut"
            return False
        self._message = "sent keyboard shortcut"
        return True

    def _send_key_down(self, virtual_key: int) -> bool:
        if not self._available or self._user32 is None:
            return False
        inp = INPUT * 1
        sent = int(self._user32.SendInput(1, inp(self._keyboard_input(virtual_key=int(virtual_key), flags=0)), ctypes.sizeof(INPUT)))
        return sent == 1

    def _send_key_up(self, virtual_key: int) -> bool:
        if not self._available or self._user32 is None:
            return False
        inp = INPUT * 1
        sent = int(self._user32.SendInput(1, inp(self._keyboard_input(virtual_key=int(virtual_key), flags=KEYEVENTF_KEYUP)), ctypes.sizeof(INPUT)))
        return sent == 1

    def _window_thread_id(self, hwnd: int) -> int:
        if not self._available or self._user32 is None or hwnd <= 0:
            return 0
        try:
            pid = wintypes.DWORD()
            return int(self._user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid)) or 0)
        except Exception:
            return 0

    def _is_own_window(self, hwnd: int) -> bool:
        if not self._available or self._user32 is None or hwnd <= 0:
            return False
        try:
            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            return int(pid.value) == int(self._own_pid)
        except Exception:
            return False

    def _paste_text(self, text: str) -> bool:
        if not self._available or self._user32 is None or self._kernel32 is None:
            return False
        data = (str(text) + "\0").encode("utf-16-le")
        handle = None
        locked = None
        try:
            if not self._user32.OpenClipboard(None):
                return False
            self._user32.EmptyClipboard()
            handle = self._kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                self._user32.CloseClipboard()
                return False
            locked = self._kernel32.GlobalLock(handle)
            if not locked:
                self._user32.CloseClipboard()
                return False
            ctypes.memmove(locked, data, len(data))
            self._kernel32.GlobalUnlock(handle)
            locked = None
            if not self._user32.SetClipboardData(CF_UNICODETEXT, handle):
                self._user32.CloseClipboard()
                return False
            handle = None
            self._user32.CloseClipboard()
            time.sleep(0.03)
            return self._send_shortcut(0x11, 0x56)
        except Exception:
            try:
                self._user32.CloseClipboard()
            except Exception:
                pass
            return False
        finally:
            if locked is not None:
                try:
                    self._kernel32.GlobalUnlock(handle)
                except Exception:
                    pass

    def _unicode_inputs(self, text: str) -> list[INPUT]:
        data = text.encode("utf-16-le")
        units = [int.from_bytes(data[index:index + 2], "little") for index in range(0, len(data), 2)]
        payload: list[INPUT] = []
        for unit in units:
            payload.append(self._keyboard_input(scan_code=unit, flags=KEYEVENTF_UNICODE))
            payload.append(self._keyboard_input(scan_code=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        return payload

    def _vk_inputs(self, virtual_key: int) -> list[INPUT]:
        return [
            self._keyboard_input(virtual_key=virtual_key, flags=0),
            self._keyboard_input(virtual_key=virtual_key, flags=KEYEVENTF_KEYUP),
        ]

    def _keyboard_input(self, *, virtual_key: int = 0, scan_code: int = 0, flags: int = 0) -> INPUT:
        return INPUT(
            type=INPUT_KEYBOARD,
            union=INPUT_UNION(
                ki=KEYBDINPUT(
                    wVk=int(virtual_key),
                    wScan=int(scan_code),
                    dwFlags=int(flags),
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )
