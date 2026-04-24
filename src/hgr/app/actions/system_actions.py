from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Callable, Optional

from ...utils.subprocess_utils import launch_external


class SystemActions:
    def __init__(self, open_settings_callback: Optional[Callable[[], None]] = None):
        self.open_settings_callback = open_settings_callback

    def open_google_chrome(self) -> str:
        system = platform.system()
        try:
            if system == "Windows":
                # ShellExecuteW via App Paths registry — no cmd.exe spawn
                # (that's the dropper pattern Norton SONAR flags).
                if launch_external("chrome"):
                    return "Opened Google Chrome"
                return "Could not open Google Chrome"
            if system == "Darwin":
                subprocess.Popen(["open", "-a", "Google Chrome"])
            else:
                subprocess.Popen(["google-chrome"])
            return "Opened Google Chrome"
        except Exception:
            return "Could not open Google Chrome"

    def launch_chrome_or_youtube(self) -> str:
        return self.open_google_chrome()

    def open_system_settings(self) -> str:
        system = platform.system()
        try:
            if system == "Windows":
                # The `ms-settings:` URI is handled by ShellExecuteW's
                # registered protocol handler, same as when Explorer opens it.
                if launch_external("ms-settings:"):
                    return "Opened device settings"
                return "Could not open device settings"
            if system == "Darwin":
                subprocess.Popen(["open", "-a", "System Settings"])
            else:
                subprocess.Popen(["xdg-open", "settings://"])
            return "Opened device settings"
        except Exception:
            return "Could not open device settings"

    def open_files_manager(self) -> str:
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.Popen(["open", str(Path.home())])
                return "Opened Finder"
            elif system == "Windows":
                subprocess.Popen(["explorer"])
                return "Opened Files"
            else:
                subprocess.Popen(["xdg-open", str(Path.home())])
                return "Opened Files"
        except Exception:
            return "Could not open file manager"

    def open_settings(self) -> str:
        if self.open_settings_callback is not None:
            self.open_settings_callback()
            return "Opened Touchless settings"
        return "No settings callback configured"
