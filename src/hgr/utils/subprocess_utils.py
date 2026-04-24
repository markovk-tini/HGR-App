from __future__ import annotations

import platform
import subprocess
from typing import Iterable, Optional


def launch_external(
    target: str,
    *,
    args: Optional[Iterable[str]] = None,
) -> bool:
    """Launch an external app / URI via the OS's native handler.

    On Windows, uses the Win32 ShellExecuteW API instead of
    `subprocess.Popen(["cmd", "/c", "start", "", target])` which triggers
    Norton SONAR and other behavioral-AV engines: an unsigned PyInstaller
    binary spawning `cmd.exe` to launch other executables is a classic
    dropper fingerprint. ShellExecuteW goes straight to the OS's own
    "open this" code path, the same one Explorer uses when you double-
    click a file or URI, so no suspicious shell process appears in the
    process tree.

    The `target` can be:
      - An app name registered in the Windows App Paths registry
        (e.g. "chrome", "spotify") — ShellExecuteW resolves it.
      - An absolute file or folder path.
      - A URI scheme (e.g. "https://example.com", "ms-settings:",
        "mailto:x@y").

    On macOS falls back to `open` / `open -a`, on Linux to `xdg-open`.

    Returns True on best-effort success, False otherwise.
    """
    system = platform.system()
    args_list = list(args or ())
    if system == "Windows":
        try:
            import ctypes

            params: Optional[str] = None
            if args_list:
                quoted = [f'"{a}"' if (" " in a or "\t" in a) else a for a in args_list]
                params = " ".join(quoted)
            # SW_SHOWNORMAL = 1. ShellExecuteW returns > 32 on success.
            result = ctypes.windll.shell32.ShellExecuteW(
                None, "open", target, params, None, 1
            )
            return int(result) > 32
        except Exception:
            return False
    if system == "Darwin":
        try:
            if args_list:
                subprocess.Popen(["open", "-a", target, *args_list])
            else:
                subprocess.Popen(["open", target])
            return True
        except Exception:
            return False
    # Linux / other
    try:
        if args_list:
            subprocess.Popen([target, *args_list])
        else:
            subprocess.Popen(["xdg-open", target])
        return True
    except Exception:
        return False


def hidden_subprocess_kwargs() -> dict:
    """Return subprocess kwargs that prevent a console window from flashing.

    In a PyInstaller --windowed build the main exe has no console, so any
    console-mode child (`whisper-cli.exe`, `powershell.exe`, `ffmpeg.exe`, ...)
    causes Windows to spawn a brand new console window for the child. That is
    why the installer-built app briefly flashes a "System32..." PowerShell /
    cmd window on every voice-pipeline subprocess call, while `python
    run_app.py` from a terminal stays clean (the child inherits the parent's
    existing console).

    Unpack via `**hidden_subprocess_kwargs()` into any `subprocess.run` /
    `subprocess.Popen` that should stay invisible. No-op on non-Windows.
    """
    if platform.system() != "Windows":
        return {}
    try:
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE
        return {"creationflags": creationflags, "startupinfo": startupinfo}
    except Exception:
        return {}
