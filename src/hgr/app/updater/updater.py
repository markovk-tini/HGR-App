"""Download the new release artifact and hand off to it.

Two update paths, depending on what asset GitHub serves:

A. App-only zip (Touchless_App_Update_<ver>.zip, ~50-150 MB):
   1. Download the zip to a stable temp location.
   2. Write a tiny `_apply_update.bat` helper next to the install
      that waits for Touchless.exe to exit, then unzips over the
      install directory and relaunches the app.
   3. Spawn the helper, exit our process. The helper does its
      work without admin prompts because the per-user install
      lives under %LOCALAPPDATA%\\Programs\\Touchless\\ — fully
      user-writable. UAC never appears.

B. Full installer .exe (~2.4 GB, first install or breaking change):
   1. Download to temp.
   2. Launch with Inno Setup silent flags (/SILENT
      /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS), exit ourselves.
   3. Installer replaces files and relaunches Touchless. UAC may
      fire if the user has an old install in Program Files; new
      installs to LocalAppData skip it.

The path is chosen by the ReleaseInfo.update_kind value.
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .release_checker import ReleaseInfo


_PROGRESS_CHUNK_BYTES = 256 * 1024


class _DownloadWorker(threading.Thread):
    """Plain threading.Thread + Qt-thread-safe signals via callbacks.

    We avoid moving this onto a QThread because the dialog already
    owns its parent thread; a callback-shim is simpler and less
    invasive. Callbacks are scheduled onto the GUI thread by the
    Updater itself via QTimer.singleShot.
    """

    def __init__(
        self,
        url: str,
        target_path: Path,
        on_progress: Callable[[int, int], None],
        on_done: Callable[[Path], None],
        on_failed: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True, name="UpdaterDownload")
        self._url = url
        self._target_path = target_path
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_failed = on_failed
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        try:
            self._target_path.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": "Touchless-Updater/1.0"},
            )
            with urllib.request.urlopen(req, timeout=15.0) as resp:
                total_str = resp.headers.get("Content-Length") or "0"
                try:
                    total = int(total_str)
                except (TypeError, ValueError):
                    total = 0
                downloaded = 0
                last_progress_at = 0
                # Open in 'wb' (not append) — a partial leftover
                # download from a previous failed attempt would
                # otherwise corrupt the target.
                with open(self._target_path, "wb") as fh:
                    while True:
                        if self._cancelled.is_set():
                            self._on_failed("Cancelled")
                            return
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if downloaded - last_progress_at >= _PROGRESS_CHUNK_BYTES:
                            self._on_progress(downloaded, total)
                            last_progress_at = downloaded
            # Final tick so the progress bar lands at 100% before
            # the dialog flips to "launching".
            self._on_progress(downloaded, total or downloaded)
            self._on_done(self._target_path)
        except urllib.error.URLError as exc:
            self._on_failed(f"Network error: {exc!s}")
        except OSError as exc:
            self._on_failed(f"Disk error: {exc!s}")
        except Exception as exc:  # pragma: no cover
            self._on_failed(f"Unexpected: {type(exc).__name__}: {exc!s}")


class Updater(QObject):
    """Drives the download → launch handoff. Owns the download
    thread, marshals callbacks to the GUI thread, and asks the app
    to exit cleanly after launching the installer."""

    progress = Signal(int, str)        # percent (or -1), status text
    failed = Signal(str)
    ready_to_launch = Signal(str)      # path to downloaded installer

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: _DownloadWorker | None = None
        self._target_path: Path | None = None
        self._info: Optional[ReleaseInfo] = None

    def start_download(self, info: ReleaseInfo) -> None:
        if not info.download_url:
            self.failed.emit(
                "No update artifact attached to this release. Please update manually from the GitHub release page."
            )
            return
        self._info = info
        # Stable temp folder so partial downloads survive retries
        # within a session. Cleaned up by the OS on reboot.
        target_dir = Path(tempfile.gettempdir()) / "Touchless_Update"
        if info.update_kind == "app-zip":
            target_path = target_dir / f"Touchless_App_Update_{info.version}.zip"
        else:
            target_path = target_dir / f"Touchless_Installer_{info.version}.exe"
        self._target_path = target_path

        def progress_cb(downloaded: int, total: int) -> None:
            if total > 0:
                pct = int((downloaded * 100) / total)
                mb_done = downloaded / (1024 * 1024)
                mb_total = total / (1024 * 1024)
                msg = f"Downloading... {mb_done:.1f} MB of {mb_total:.1f} MB"
            else:
                pct = -1
                mb_done = downloaded / (1024 * 1024)
                msg = f"Downloading... {mb_done:.1f} MB"
            QTimer.singleShot(0, lambda p=pct, m=msg: self.progress.emit(p, m))

        def done_cb(path: Path) -> None:
            QTimer.singleShot(
                0,
                lambda p=str(path): self._on_download_complete(p),
            )

        def failed_cb(reason: str) -> None:
            QTimer.singleShot(0, lambda r=reason: self.failed.emit(r))

        self._worker = _DownloadWorker(
            url=info.download_url,
            target_path=target_path,
            on_progress=progress_cb,
            on_done=done_cb,
            on_failed=failed_cb,
        )
        self._worker.start()

    def _on_download_complete(self, path: str) -> None:
        self.progress.emit(-1, "Launching installer...")
        # Tiny delay so the user sees "Launching..." before the
        # window vanishes. Without this the app can disappear so
        # fast the user thinks the click did nothing.
        QTimer.singleShot(400, lambda p=path: self.ready_to_launch.emit(p))

    def apply_update_and_exit(self, downloaded_path: str) -> bool:
        """Route to the appropriate handler based on the update kind
        the ReleaseChecker stamped on `self._info`. Returns False on
        any setup failure so the dialog can show an error."""
        if self._info is None:
            return False
        if self._info.update_kind == "app-zip":
            return self._apply_zip_and_exit(downloaded_path)
        return self.launch_installer_and_exit(downloaded_path)

    # Backwards-compat shim — main_window connects to ready_to_launch
    # and may have been wired before the kind-based router existed.
    def launch_installer_and_exit(self, installer_path: str) -> bool:
        """Launch the downloaded .exe with Inno Setup silent flags
        and exit the current app. Used for full-installer updates."""
        if not os.path.exists(installer_path):
            return False
        # Inno Setup flags:
        #   /SILENT — minimal install UI (just a progress dialog)
        #   /CLOSEAPPLICATIONS — close any running Touchless first
        #   /RESTARTAPPLICATIONS — relaunch it after install
        #   /NORESTART — don't reboot Windows even if the installer
        #                thinks it needs to (it doesn't, we don't
        #                touch system DLLs)
        params = "/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /NORESTART"
        try:
            # ShellExecuteW with verb 'runas' triggers UAC if the
            # install dir requires admin (legacy Program Files
            # installs). New per-user installs under LocalAppData
            # don't need elevation, but we use 'runas' anyway so
            # mixed-environment users get prompted only when
            # actually necessary — Windows skips the UAC prompt
            # if the target binary's manifest doesn't require it.
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", installer_path, params, None, 1
            )
            if ret <= 32:
                return False
        except Exception:
            return False
        QTimer.singleShot(0, self._quit_app)
        return True

    def _apply_zip_and_exit(self, zip_path: str) -> bool:
        """Apply an app-only zip update by spawning a small batch
        helper that waits for our process to exit, unzips over the
        install directory, then relaunches the new Touchless.exe.

        No UAC needed: the per-user install dir (%LOCALAPPDATA%\\
        Programs\\Touchless\\) is fully user-writable. The helper
        runs at the same privilege level as the launching app.
        """
        if not os.path.exists(zip_path):
            return False
        install_dir = self._resolve_install_dir()
        if install_dir is None:
            return False

        helper_path = self._write_apply_helper(zip_path, install_dir)
        if helper_path is None:
            return False

        try:
            # Spawn the bat detached from our process so it survives
            # our exit. CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS
            # ensure it's not a child of our (about-to-die) process.
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            DETACHED_PROCESS = 0x00000008
            CREATE_NO_WINDOW = 0x08000000
            import subprocess
            subprocess.Popen(
                ["cmd.exe", "/c", str(helper_path)],
                creationflags=(
                    CREATE_NEW_PROCESS_GROUP
                    | DETACHED_PROCESS
                    | CREATE_NO_WINDOW
                ),
                close_fds=True,
                cwd=str(Path(helper_path).parent),
            )
        except Exception:
            return False

        QTimer.singleShot(0, self._quit_app)
        return True

    def _resolve_install_dir(self) -> Optional[Path]:
        """Where is Touchless.exe installed? In a frozen PyInstaller
        bundle, sys.executable IS Touchless.exe — its parent is the
        install dir. In a dev source-run, we don't have an install
        to upgrade and bail."""
        if not getattr(sys, "frozen", False):
            return None
        try:
            return Path(sys.executable).resolve().parent
        except Exception:
            return None

    def _write_apply_helper(self, zip_path: str, install_dir: Path) -> Optional[Path]:
        """Write the apply-update batch helper next to the zip.

        Why a batch script and not Python: the user's machine has
        no Python interpreter outside the bundle, and we can't run
        the bundle while it's mid-replacement. cmd.exe + PowerShell
        Expand-Archive are universally available on Windows 10+.
        """
        try:
            zip_dir = Path(zip_path).parent
            zip_dir.mkdir(parents=True, exist_ok=True)
            helper = zip_dir / "_apply_update.bat"
            content = (
                "@echo off\r\n"
                "setlocal\r\n"
                f"set \"INSTALL_DIR={install_dir}\"\r\n"
                f"set \"UPDATE_ZIP={zip_path}\"\r\n"
                "rem Wait up to 30s for Touchless.exe to exit before we touch its files.\r\n"
                "set /a count=0\r\n"
                ":waitloop\r\n"
                "tasklist /FI \"IMAGENAME eq Touchless.exe\" 2>nul | find /I \"Touchless.exe\" >nul\r\n"
                "if errorlevel 1 goto extract\r\n"
                "if %count% geq 30 goto extract\r\n"
                "timeout /t 1 /nobreak >nul\r\n"
                "set /a count+=1\r\n"
                "goto waitloop\r\n"
                "\r\n"
                ":extract\r\n"
                "powershell -NoProfile -ExecutionPolicy Bypass -Command "
                "\"Expand-Archive -LiteralPath '%UPDATE_ZIP%' -DestinationPath '%INSTALL_DIR%' -Force\"\r\n"
                "if errorlevel 1 (\r\n"
                "  echo [Touchless Update] Extraction failed. Run the installer manually if needed.\r\n"
                "  pause\r\n"
                "  exit /b 1\r\n"
                ")\r\n"
                "\r\n"
                "rem Relaunch the new Touchless.exe.\r\n"
                "start \"\" \"%INSTALL_DIR%\\Touchless.exe\"\r\n"
                "\r\n"
                "rem Best-effort cleanup. The downloaded zip is small;\r\n"
                "rem if delete fails (rare) the OS will reclaim on reboot.\r\n"
                "del \"%UPDATE_ZIP%\" 2>nul\r\n"
                "endlocal\r\n"
            )
            helper.write_text(content, encoding="cp1252")
            return helper
        except Exception:
            return None

    def _quit_app(self) -> None:
        # Use the Qt application's quit() so any cleanup hooks
        # (config save, audio device release, server shutdown) run
        # before exit. Hard sys.exit as a last-resort fallback.
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
                # Give Qt a moment to flush; if it doesn't exit
                # within 1.5s, force it.
                threading.Timer(1.5, lambda: os._exit(0)).start()
                return
        except Exception:
            pass
        os._exit(0)
