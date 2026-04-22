from __future__ import annotations

import ctypes
import json
import platform
import re
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from .foreground_window import enumerate_visible_windows, find_chrome_youtube_windows
from .text_input_controller import TextInputController

_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1
_VK_MEDIA_PLAY_PAUSE = 0xB3

_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_KEY_L = 0x4C
_VK_F = 0x46
_VK_T = 0x54
_VK_I = 0x49
_VK_C = 0x43
_VK_J = 0x4A
_VK_K = 0x4B
_VK_L = 0x4C
_VK_N = 0x4E
_VK_P = 0x50
_VK_OEM_COMMA = 0xBC
_VK_OEM_PERIOD = 0xBE
_VK_TAB = 0x09
_VK_RETURN = 0x0D
_VK_UP = 0x26
_VK_DOWN = 0x28

_SW_RESTORE = 9
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004

_SKIP_TEMPLATE_DIRNAME = "youtube_skip"
_SKIP_MATCH_THRESHOLD = 0.75
_SKIP_CLICK_COOLDOWN_SECONDS = 0.8
_TAB_SWITCH_SETTLE_SECONDS = 0.12
_WINDOW_FOCUS_SETTLE_SECONDS = 0.08
_BACKGROUND_TAB_SEARCH_STEPS = 18
_RECENT_YOUTUBE_WINDOW_SECONDS = 6.0
_CAPTIONS_FEEDBACK_SETTLE_SECONDS = 0.28
_CAPTIONS_OCR_TIMEOUT_SECONDS = 3.0
_UIA_ACTION_TIMEOUT_SECONDS = 2.2
_YOUTUBE_SCRIPT_RESULT_PREFIX = "HGR_YT_ACTION_"
_YOUTUBE_SCRIPT_RESULT_PATTERN = re.compile(rf"{_YOUTUBE_SCRIPT_RESULT_PREFIX}([A-Z_]+)__", re.IGNORECASE)
_YOUTUBE_SCRIPT_TITLE_TIMEOUT_SECONDS = 1.2


class YouTubeController:
    """Controls the currently playing YouTube tab via Chrome.

    Detection is a title scan for Chrome windows whose caption contains
    'YouTube'. A VolumeController is used to distinguish 'YouTube tab open
    but paused' from 'YouTube currently emitting audio' when the caller
    wants the stricter signal.
    """

    def __init__(self, volume_controller=None) -> None:
        self._volume_controller = volume_controller
        self._is_windows = platform.system() == "Windows"
        self._message = "YouTube idle"
        self._peak_cache_until = 0.0
        self._peak_cache_value: float | None = None
        self._tab_cache_until = 0.0
        self._tab_cache_value = False
        self._playing_cache_until = 0.0
        self._playing_cache_value = False
        self._skip_click_cooldown_until = 0.0
        self._last_youtube_hwnd = 0
        self._last_youtube_seen_until = 0.0
        self._text_input = TextInputController() if self._is_windows else None

    @property
    def message(self) -> str:
        return self._message

    def has_youtube_tab(self) -> bool:
        now = time.time()
        if now < self._tab_cache_until:
            return self._tab_cache_value
        try:
            value = bool(find_chrome_youtube_windows())
        except Exception:
            value = False
        if not value:
            value = self._has_recent_youtube_window()
        self._tab_cache_value = value
        self._tab_cache_until = now + 1.0
        return value

    def is_playing(self) -> bool:
        """Title-and-audio check: YouTube tab exists AND chrome is making sound."""
        now = time.time()
        if now < self._playing_cache_until:
            return self._playing_cache_value
        if not self.has_youtube_tab():
            value = False
        elif self._volume_controller is None:
            value = True
        else:
            peak = self._cached_chrome_peak()
            value = True if peak is None else peak > 0.005
        self._playing_cache_value = value
        self._playing_cache_until = now + 0.5
        return value

    def _cached_chrome_peak(self) -> float | None:
        now = time.time()
        if now < self._peak_cache_until and self._peak_cache_value is not None:
            return self._peak_cache_value
        value = self._volume_controller.get_process_audio_peak(["chrome"])
        self._peak_cache_value = value
        self._peak_cache_until = now + 0.25
        return value

    def _send_virtual_key(self, vk: int) -> bool:
        if not self._is_windows:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(wintypes.BYTE(vk), 0, _KEYEVENTF_EXTENDEDKEY, 0)
            user32.keybd_event(wintypes.BYTE(vk), 0, _KEYEVENTF_EXTENDEDKEY | _KEYEVENTF_KEYUP, 0)
            return True
        except Exception as exc:
            self._message = f"YouTube key send failed: {type(exc).__name__}"
            return False

    def _focus_youtube_window(self) -> bool:
        return self._activate_youtube_tab() is not None

    def _window_title(self, hwnd: int) -> str:
        if not self._is_windows or hwnd <= 0:
            return ""
        try:
            user32 = ctypes.windll.user32
            length = int(user32.GetWindowTextLengthW(wintypes.HWND(hwnd)) or 0)
            if length <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
            return str(buf.value or "")
        except Exception:
            return ""

    def _is_window_minimized(self, hwnd: int) -> bool:
        if not self._is_windows or hwnd <= 0:
            return False
        try:
            return bool(ctypes.windll.user32.IsIconic(wintypes.HWND(hwnd)))
        except Exception:
            return False

    def _restore_window(self, hwnd: int) -> bool:
        if not self._is_windows or hwnd <= 0:
            return False
        try:
            return bool(ctypes.windll.user32.ShowWindow(wintypes.HWND(hwnd), _SW_RESTORE))
        except Exception:
            return False

    def _bring_window_to_front(self, hwnd: int) -> bool:
        if not self._is_windows or hwnd <= 0:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.BringWindowToTop(wintypes.HWND(hwnd))
            if user32.SetForegroundWindow(wintypes.HWND(hwnd)):
                time.sleep(_WINDOW_FOCUS_SETTLE_SECONDS)
                return True
            fg = user32.GetForegroundWindow()
            return int(fg) == hwnd if fg else False
        except Exception:
            return False

    def _focus_window_handle(self, hwnd: int, *, restore_if_minimized: bool = True) -> bool:
        if not self._is_windows or hwnd <= 0:
            return False
        if restore_if_minimized and self._is_window_minimized(hwnd):
            self._restore_window(hwnd)
        return self._bring_window_to_front(hwnd)

    def _cycle_chrome_tab(self) -> bool:
        if not self._is_windows:
            return False
        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(wintypes.BYTE(_VK_CONTROL), 0, 0, 0)
            user32.keybd_event(wintypes.BYTE(_VK_TAB), 0, 0, 0)
            user32.keybd_event(wintypes.BYTE(_VK_TAB), 0, _KEYEVENTF_KEYUP, 0)
            user32.keybd_event(wintypes.BYTE(_VK_CONTROL), 0, _KEYEVENTF_KEYUP, 0)
            return True
        except Exception:
            return False

    def _chrome_window_handles(self) -> list[int]:
        try:
            windows = enumerate_visible_windows()
        except Exception:
            windows = []
        handles: list[int] = []
        seen: set[int] = set()
        for window in windows:
            try:
                hwnd = int(window.hwnd)
            except (AttributeError, TypeError, ValueError):
                continue
            process_name = str(getattr(window, "process_name", "") or "").lower()
            if "chrome" not in process_name or hwnd <= 0 or hwnd in seen:
                continue
            seen.add(hwnd)
            handles.append(hwnd)
        recent_hwnd = self._recent_youtube_window_handle()
        if recent_hwnd is not None and recent_hwnd in handles:
            handles.remove(recent_hwnd)
            handles.insert(0, recent_hwnd)
        return handles

    def _remember_youtube_window(self, hwnd: int) -> None:
        try:
            hwnd_value = int(hwnd)
        except (TypeError, ValueError):
            return
        if hwnd_value <= 0:
            return
        self._last_youtube_hwnd = hwnd_value
        self._last_youtube_seen_until = time.time() + _RECENT_YOUTUBE_WINDOW_SECONDS

    def _recent_youtube_window_handle(self) -> int | None:
        now = time.time()
        if now > self._last_youtube_seen_until or self._last_youtube_hwnd <= 0:
            return None
        recent_hwnd = int(self._last_youtube_hwnd)
        try:
            windows = enumerate_visible_windows()
        except Exception:
            return None
        for window in windows:
            try:
                hwnd = int(window.hwnd)
            except (AttributeError, TypeError, ValueError):
                continue
            if hwnd != recent_hwnd:
                continue
            process_name = str(getattr(window, "process_name", "") or "").lower()
            if "chrome" in process_name:
                return recent_hwnd
            break
        return None

    def _has_recent_youtube_window(self) -> bool:
        return self._recent_youtube_window_handle() is not None

    def _activate_background_youtube_tab(self) -> int | None:
        for hwnd in self._chrome_window_handles():
            if not self._focus_window_handle(hwnd, restore_if_minimized=True):
                continue
            title = self._window_title(hwnd)
            if "youtube" in title.lower():
                self._remember_youtube_window(hwnd)
                return hwnd
            seen_titles: set[str] = set()
            normalized = title.strip().lower()
            if normalized:
                seen_titles.add(normalized)
            for _index in range(_BACKGROUND_TAB_SEARCH_STEPS):
                if not self._cycle_chrome_tab():
                    break
                time.sleep(_TAB_SWITCH_SETTLE_SECONDS)
                title = self._window_title(hwnd)
                normalized = title.strip().lower()
                if "youtube" in normalized:
                    self._remember_youtube_window(hwnd)
                    return hwnd
                if normalized and normalized in seen_titles:
                    break
                if normalized:
                    seen_titles.add(normalized)
        return None

    def _activate_youtube_tab(self) -> int | None:
        if not self._is_windows:
            return None
        try:
            handles = find_chrome_youtube_windows()
        except Exception:
            handles = []
        for handle in handles:
            try:
                hwnd = int(handle.hwnd)
            except (AttributeError, TypeError, ValueError):
                continue
            if self._focus_window_handle(hwnd, restore_if_minimized=True):
                self._remember_youtube_window(hwnd)
                return hwnd
        return self._activate_background_youtube_tab()

    def _send_key_to_youtube(self, vk: int, *, shift: bool = False, hwnd: int | None = None) -> bool:
        target_hwnd = hwnd if hwnd is not None else self._activate_youtube_tab()
        if target_hwnd is None:
            self._message = "YouTube tab not focusable"
            return False
        self._remember_youtube_window(target_hwnd)
        try:
            user32 = ctypes.windll.user32
            if shift:
                user32.keybd_event(wintypes.BYTE(_VK_SHIFT), 0, 0, 0)
            user32.keybd_event(wintypes.BYTE(vk), 0, 0, 0)
            user32.keybd_event(wintypes.BYTE(vk), 0, _KEYEVENTF_KEYUP, 0)
            if shift:
                user32.keybd_event(wintypes.BYTE(_VK_SHIFT), 0, _KEYEVENTF_KEYUP, 0)
            return True
        except Exception as exc:
            self._message = f"YouTube key send failed: {type(exc).__name__}"
            return False

    def toggle_playback(self) -> bool:
        ok = self._send_key_to_youtube(_VK_K)
        self._message = "YouTube play/pause" if ok else self._message
        return ok

    def next_track(self) -> bool:
        ok = self._send_key_to_youtube(_VK_N, shift=True)
        self._message = "YouTube next" if ok else self._message
        return ok

    def previous_track(self) -> bool:
        ok = self._send_key_to_youtube(_VK_P, shift=True)
        self._message = "YouTube previous" if ok else self._message
        return ok

    def step_player_volume(self, direction: int, steps: int = 1) -> bool:
        if direction == 0 or steps <= 0:
            return False
        vk = _VK_UP if direction > 0 else _VK_DOWN
        hwnd = self._activate_youtube_tab()
        if hwnd is None:
            self._message = "YouTube tab not focusable"
            return False
        ok_any = False
        for _ in range(int(max(1, min(steps, 10)))):
            if not self._send_key_to_youtube(vk, hwnd=hwnd):
                break
            ok_any = True
        if ok_any:
            self._message = "YouTube volume up" if direction > 0 else "YouTube volume down"
        return ok_any

    def toggle_fullscreen(self) -> bool:
        ok = self._send_key_to_youtube(_VK_F)
        self._message = "YouTube fullscreen" if ok else self._message
        return ok

    def toggle_theater(self) -> bool:
        hwnd = self._activate_youtube_tab()
        if hwnd is None:
            self._message = "YouTube tab not focusable"
            return False
        ok = self._send_key_to_youtube(_VK_T, hwnd=hwnd)
        self._message = "YouTube theater" if ok else self._message
        return ok

    def toggle_mini_player(self) -> bool:
        ok = self._send_key_to_youtube(_VK_I)
        self._message = "YouTube mini-player" if ok else self._message
        return ok

    def toggle_captions(self) -> bool:
        hwnd = self._activate_youtube_tab()
        if hwnd is None:
            self._message = "YouTube tab not focusable"
            return False
        ok = self._invoke_uia_named_control(
            hwnd,
            ("subtitles", "captions", "closed captions", "cc"),
        )
        if not ok:
            ok = self._send_key_to_youtube(_VK_C, hwnd=hwnd)
        if not ok:
            return False
        feedback = self._detect_captions_feedback(hwnd)
        if feedback == "unavailable":
            self._message = "No captions available for this video"
            return False
        self._message = "YouTube captions"
        return True

    def seek_backward(self) -> bool:
        ok = self._send_key_to_youtube(_VK_J)
        self._message = "YouTube -10s" if ok else self._message
        return ok

    def seek_forward(self) -> bool:
        ok = self._send_key_to_youtube(_VK_L)
        self._message = "YouTube +10s" if ok else self._message
        return ok

    def speed_down(self) -> bool:
        hwnd = self._activate_youtube_tab()
        if hwnd is None:
            self._message = "YouTube tab not focusable"
            return False
        ok = self._send_key_to_youtube(_VK_OEM_COMMA, shift=True, hwnd=hwnd)
        self._message = "YouTube slower" if ok else self._message
        return ok

    def speed_up(self) -> bool:
        hwnd = self._activate_youtube_tab()
        if hwnd is None:
            self._message = "YouTube tab not focusable"
            return False
        ok = self._send_key_to_youtube(_VK_OEM_PERIOD, shift=True, hwnd=hwnd)
        self._message = "YouTube faster" if ok else self._message
        return ok

    def like_video(self) -> bool:
        return self._invoke_named_control_action(
            ("like this video", "undo like", "like"),
            success_message="YouTube like",
        )

    def dislike_video(self) -> bool:
        return self._invoke_named_control_action(
            ("dislike this video", "undo dislike", "dislike"),
            success_message="YouTube dislike",
        )

    def share_video(self) -> bool:
        return self._invoke_named_control_action(
            ("share", "share video"),
            success_message="YouTube share",
        )

    def skip_ad(self) -> bool:
        if not self._is_windows:
            self._message = "skip ad: unsupported platform"
            return False
        now = time.time()
        if now < self._skip_click_cooldown_until:
            return False
        hwnd = self._activate_youtube_tab()
        if hwnd is None:
            self._message = "skip ad: no youtube tab"
            return False
        if hwnd <= 0:
            self._message = "skip ad: invalid window handle"
            return False
        if not self._focus_window_handle(hwnd, restore_if_minimized=True):
            self._message = "skip ad: tab not focusable"
            return False
        time.sleep(0.16)

        rect = wintypes.RECT()
        try:
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        except Exception:
            self._message = "skip ad: window rect failed"
            return False
        if rect.right <= rect.left or rect.bottom <= rect.top:
            self._message = "skip ad: window rect empty"
            return False

        screen_bgr = self._capture_window_bgr(rect, context="skip ad")
        if screen_bgr is None:
            return False

        templates = self._load_skip_templates()
        if not templates:
            self._message = "skip ad: no templates in assets/youtube_skip/"
            return False

        try:
            import cv2
        except ImportError:
            self._message = "skip ad: cv2 unavailable"
            return False

        best_score = 0.0
        best_loc = None
        best_size = None
        for tpl in templates:
            if tpl.shape[0] >= screen_bgr.shape[0] or tpl.shape[1] >= screen_bgr.shape[1]:
                continue
            try:
                result = cv2.matchTemplate(screen_bgr, tpl, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
            except Exception:
                continue
            if max_val > best_score:
                best_score = float(max_val)
                best_loc = max_loc
                best_size = (tpl.shape[1], tpl.shape[0])

        if best_loc is None or best_size is None or best_score < _SKIP_MATCH_THRESHOLD:
            self._message = f"skip ad: no match ({best_score:.2f})"
            return False

        cx = rect.left + best_loc[0] + best_size[0] // 2
        cy = rect.top + best_loc[1] + best_size[1] // 2
        if not self._click_at(cx, cy):
            return False
        self._skip_click_cooldown_until = now + _SKIP_CLICK_COOLDOWN_SECONDS
        self._message = f"skip ad ({best_score:.2f})"
        return True

    def _capture_window_bgr(self, rect, *, context: str = "capture"):
        try:
            from PIL import ImageGrab
            import numpy as np
            import cv2
        except ImportError:
            self._message = f"{context}: capture deps missing"
            return None
        try:
            img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
        except Exception as exc:
            self._message = f"{context}: capture failed {type(exc).__name__}"
            return None
        try:
            arr = np.array(img)
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        except Exception as exc:
            self._message = f"{context}: capture decode failed {type(exc).__name__}"
            return None

    def _invoke_named_control_action(self, name_patterns: tuple[str, ...], *, success_message: str) -> bool:
        hwnd = self._activate_youtube_tab()
        if hwnd is None:
            self._message = "YouTube tab not focusable"
            return False
        if self._invoke_uia_named_control(hwnd, name_patterns):
            self._message = str(success_message)
            return True
        self._message = f"{success_message.lower()} unavailable"
        return False

    def _invoke_uia_named_control(self, hwnd: int, name_patterns: tuple[str, ...]) -> bool:
        if not self._is_windows or hwnd <= 0:
            return False
        needles = [str(pattern or "").strip().lower() for pattern in name_patterns if str(pattern or "").strip()]
        if not needles:
            return False
        if not self._focus_window_handle(hwnd, restore_if_minimized=True):
            return False
        try:
            payload = json.dumps(needles)
        except Exception:
            return False
        script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$hwnd = [IntPtr]({int(hwnd)})
$needles = ConvertFrom-Json @'
{payload}
'@
$root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
if ($null -eq $root) {{
    [Console]::Out.Write('NOT_FOUND')
    exit 0
}}
$elements = $root.FindAll(
    [System.Windows.Automation.TreeScope]::Descendants,
    [System.Windows.Automation.Condition]::TrueCondition
)
for ($i = 0; $i -lt $elements.Count; $i++) {{
    $element = $elements.Item($i)
    try {{
        $name = [string]$element.Current.Name
    }} catch {{
        $name = ''
    }}
    if ([string]::IsNullOrWhiteSpace($name)) {{
        continue
    }}
    $nameLower = $name.ToLowerInvariant()
    $matched = $false
    foreach ($needle in $needles) {{
        if (-not [string]::IsNullOrWhiteSpace([string]$needle) -and $nameLower.Contains(([string]$needle).ToLowerInvariant())) {{
            $matched = $true
            break
        }}
    }}
    if (-not $matched) {{
        continue
    }}
    try {{
        $invokePattern = $element.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        if ($invokePattern -is [System.Windows.Automation.InvokePattern]) {{
            $invokePattern.Invoke()
            [Console]::Out.Write('INVOKED')
            exit 0
        }}
    }} catch {{}}
    try {{
        $togglePattern = $element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
        if ($togglePattern -is [System.Windows.Automation.TogglePattern]) {{
            $togglePattern.Toggle()
            [Console]::Out.Write('TOGGLED')
            exit 0
        }}
    }} catch {{}}
    try {{
        $selectionPattern = $element.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
        if ($selectionPattern -is [System.Windows.Automation.SelectionItemPattern]) {{
            $selectionPattern.Select()
            [Console]::Out.Write('SELECTED')
            exit 0
        }}
    }} catch {{}}
    try {{
        $legacyPattern = $element.GetCurrentPattern([System.Windows.Automation.LegacyIAccessiblePattern]::Pattern)
        if ($legacyPattern -is [System.Windows.Automation.LegacyIAccessiblePattern]) {{
            $legacyPattern.DoDefaultAction()
            [Console]::Out.Write('DEFAULT')
            exit 0
        }}
    }} catch {{}}
}}
[Console]::Out.Write('NOT_FOUND')
"""
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=_UIA_ACTION_TIMEOUT_SECONDS,
                check=False,
            )
        except Exception:
            return False
        if completed.returncode != 0:
            return False
        outcome = str(completed.stdout or "").strip().upper()
        return outcome in {"INVOKED", "TOGGLED", "SELECTED", "DEFAULT"}

    def _execute_youtube_script_action(self, hwnd: int, script: str) -> str | None:
        if not self._is_windows or hwnd <= 0 or self._text_input is None:
            return None
        payload = str(script or "").strip()
        if not payload:
            return None
        if not self._focus_window_handle(hwnd, restore_if_minimized=True):
            return None
        self._text_input._target_hwnd = int(hwnd)
        self._text_input._last_external_hwnd = int(hwnd)
        try:
            if not self._text_input._send_shortcut(_VK_CONTROL, _VK_KEY_L):
                return None
            time.sleep(0.04)
            if not self._text_input.insert_text(payload, prefer_paste=False):
                return None
            time.sleep(0.04)
            if not self._text_input._send_shortcut(_VK_RETURN):
                return None
        except Exception:
            return None
        return self._wait_for_script_result(hwnd)

    def _wait_for_script_result(self, hwnd: int) -> str | None:
        deadline = time.time() + _YOUTUBE_SCRIPT_TITLE_TIMEOUT_SECONDS
        while time.time() < deadline:
            title = self._window_title(hwnd)
            match = _YOUTUBE_SCRIPT_RESULT_PATTERN.search(title)
            if match is not None:
                return str(match.group(1) or "").upper()
            time.sleep(0.08)
        return None

    def _captions_script(self) -> str:
        body = (
            "const p=document.getElementById('movie_player');"
            "const tracks=p&&typeof p.getOption==='function'?p.getOption('captions','tracklist'):null;"
            "const b=document.querySelector('button.ytp-subtitles-button');"
            "if(!b||!tracks||!tracks.length){return 'NO_CAPTIONS';}"
            "b.click();"
            "return 'CAPTIONS';"
        )
        return self._wrap_youtube_script(body)

    def _theater_script(self) -> str:
        body = (
            "const b=document.querySelector('button.ytp-size-button');"
            "if(!b){return 'THEATER_FAILED';}"
            "b.click();"
            "return 'THEATER';"
        )
        return self._wrap_youtube_script(body)

    def _like_script(self) -> str:
        return self._button_action_script(
            result_name="LIKE",
            label_patterns=("like this video", "undo like", "like"),
        )

    def _dislike_script(self) -> str:
        return self._button_action_script(
            result_name="DISLIKE",
            label_patterns=("dislike this video", "undo dislike", "dislike"),
        )

    def _share_script(self) -> str:
        return self._button_action_script(
            result_name="SHARE",
            label_patterns=("share",),
        )

    def _button_action_script(self, *, result_name: str, label_patterns: tuple[str, ...]) -> str:
        filters = ",".join(f"'{pattern.lower()}'" for pattern in label_patterns if pattern)
        body = (
            "const buttons=Array.from(document.querySelectorAll('button[aria-label],button[title]'));"
            "const match=buttons.find((button)=>{"
            "const label=((button.getAttribute('aria-label')||button.getAttribute('title')||button.innerText||'')+'').toLowerCase();"
            f"return [{filters}].some((needle)=>label.includes(needle));"
            "});"
            f"if(!match){{return '{result_name}_FAILED';}}"
            "match.click();"
            f"return '{result_name}';"
        )
        return self._wrap_youtube_script(body)

    def _wrap_youtube_script(self, body: str) -> str:
        action_body = str(body or "").strip()
        if not action_body:
            return ""
        return (
            "javascript:(()=>{try{"
            "const __hgrTitle=document.title;"
            f"const __hgrMark=(value)=>{{document.title='{_YOUTUBE_SCRIPT_RESULT_PREFIX}'+value+'__'+__hgrTitle;"
            "setTimeout(()=>{document.title=__hgrTitle;},1600);};"
            f"const __hgrResult=(()=>{{{action_body}}})();"
            "__hgrMark(__hgrResult||'FAILED');"
            "}catch(_error){"
            "const __hgrTitle=document.title;"
            f"document.title='{_YOUTUBE_SCRIPT_RESULT_PREFIX}FAILED__'+__hgrTitle;"
            "setTimeout(()=>{document.title=__hgrTitle;},1600);"
            "}})()"
        )

    def _detect_captions_feedback(self, hwnd: int) -> str | None:
        if not self._is_windows or hwnd <= 0:
            return None
        time.sleep(_CAPTIONS_FEEDBACK_SETTLE_SECONDS)
        rect = wintypes.RECT()
        try:
            ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        except Exception:
            return None
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        screen_bgr = self._capture_window_bgr(rect, context="captions")
        if screen_bgr is None:
            return None
        text = self._ocr_captions_feedback_text(screen_bgr)
        if not text:
            return None
        normalized = " ".join(str(text).lower().split())
        if "unavailable" in normalized and any(token in normalized for token in ("caption", "captions", "subtitle", "subtitles", "cc")):
            return "unavailable"
        return None

    def _ocr_captions_feedback_text(self, screen_bgr) -> str:
        try:
            import cv2
        except ImportError:
            return ""
        try:
            height, width = screen_bgr.shape[:2]
        except Exception:
            return ""
        if height <= 0 or width <= 0:
            return ""
        left = max(0, int(width * 0.18))
        right = min(width, int(width * 0.82))
        top = max(0, int(height * 0.54))
        bottom = min(height, int(height * 0.86))
        if right - left < 24 or bottom - top < 24:
            return ""
        crop = screen_bgr[top:bottom, left:right]
        if crop.size == 0:
            return ""
        try:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
            gray = cv2.GaussianBlur(gray, (0, 0), 0.8)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            processed = 255 - binary
        except Exception:
            return ""
        ok, encoded = cv2.imencode(".png", processed)
        if not ok:
            return ""
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="hgr_youtube_captions_", suffix=".png", delete=False) as temp_file:
                temp_file.write(encoded.tobytes())
                temp_path = Path(temp_file.name)
            return self._ocr_image_path(temp_path)
        except Exception:
            return ""
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _ocr_image_path(self, image_path: Path) -> str:
        if not self._is_windows:
            return ""
        script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
function Await($asyncOp, $resultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    if ($null -eq $method) {
        return $null
    }
    $task = $method.MakeGenericMethod($resultType).Invoke($null, @($asyncOp))
    $task.Wait(-1)
    return $task.Result
}
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($args[0])) ([Windows.Storage.StorageFile])
if ($null -eq $file) { return }
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
if ($null -eq $stream) { return }
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
if ($null -eq $decoder) { return }
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
if ($null -eq $bitmap) { return }
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { return }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
if ($null -ne $result -and $null -ne $result.Text) {
    [Console]::Out.Write($result.Text)
}
"""
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                    str(image_path),
                ],
                capture_output=True,
                text=True,
                timeout=_CAPTIONS_OCR_TIMEOUT_SECONDS,
                check=False,
            )
        except Exception:
            return ""
        if completed.returncode != 0:
            return ""
        return str(completed.stdout or "").strip()

    def _load_skip_templates(self) -> list:
        try:
            import cv2
        except ImportError:
            return []
        templates: list = []
        seen: set[str] = set()
        for directory in self._skip_template_dirs():
            if not directory.exists() or not directory.is_dir():
                continue
            for png in sorted(directory.glob("*.png")):
                key = str(png.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                try:
                    img = cv2.imread(str(png), cv2.IMREAD_COLOR)
                except Exception:
                    img = None
                if img is not None and img.size > 0:
                    templates.append(img)
        return templates

    def _skip_template_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass) / "assets" / _SKIP_TEMPLATE_DIRNAME)
        here = Path(__file__).resolve()
        for candidate in here.parents:
            assets = candidate / "assets" / _SKIP_TEMPLATE_DIRNAME
            dirs.append(assets)
            if (candidate / "assets").exists():
                break
        dirs.append(Path.cwd() / "assets" / _SKIP_TEMPLATE_DIRNAME)
        return dirs

    def _click_at(self, x: int, y: int) -> bool:
        try:
            user32 = ctypes.windll.user32
            user32.SetCursorPos(int(x), int(y))
            time.sleep(0.04)
            user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            return True
        except Exception as exc:
            self._message = f"skip ad: click failed {type(exc).__name__}"
            return False

    def get_volume(self) -> float | None:
        if self._volume_controller is None:
            return None
        target, level = self._volume_controller.get_app_audio_info(["chrome"])
        return level if target is not None else None

    def set_volume(self, scalar: float) -> bool:
        if self._volume_controller is None:
            return False
        return self._volume_controller.set_app_audio_level(["chrome"], scalar)
