from __future__ import annotations

import platform
import subprocess


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
