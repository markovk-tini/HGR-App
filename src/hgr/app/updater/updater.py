"""Download the new installer and hand off to it.

Flow when the user clicks Download in the UpdateDialog:
  1. Open a streaming HTTP request to the GitHub asset URL.
  2. Write to a stable temp path under TEMP\\Touchless_Update\\
     (stable so a retry after a partial download can resume cleanly).
  3. Stream chunks, emit progress every ~256KB so the dialog's
     progress bar stays smooth without flooding the event loop.
  4. On success, launch the .exe with Inno Setup's silent flags
     (/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS), then exit
     the running app immediately so the installer can replace its
     files without "in use" errors. Inno Setup will relaunch
     Touchless on completion via the [Run] section.

Why ShellExecuteW for the launch instead of subprocess.Popen:
  the new installer needs to elevate to admin (UAC) to write to
  Program Files. Popen can't trigger UAC; ShellExecuteW with verb
  "runas" can. That's the same dropper-friendly pattern we already
  use for opening external apps from voice commands.
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

    def start_download(self, info: ReleaseInfo) -> None:
        if not info.download_url:
            self.failed.emit(
                "No installer attached to this release. Please update manually from the GitHub release page."
            )
            return
        # Stable temp folder so partial downloads survive retries
        # within a session. Cleaned up by the OS on reboot.
        target_dir = Path(tempfile.gettempdir()) / "Touchless_Update"
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

    def launch_installer_and_exit(self, installer_path: str) -> bool:
        """Launch the downloaded .exe with Inno Setup silent flags
        and exit the current app. Returns False if the launch failed
        (in which case the caller should keep the app running and
        report the error)."""
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
            # ShellExecuteW with verb 'runas' triggers UAC. The app
            # may itself be running unelevated; the installer needs
            # admin rights to write to Program Files.
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", installer_path, params, None, 1
            )
            if ret <= 32:
                # ShellExecuteW returns ≤32 on failure (e.g. user
                # declined UAC, file not found, etc.).
                return False
        except Exception:
            return False

        # Schedule app exit on the next event loop tick. Doing it
        # synchronously here would race with the installer reading
        # the running .exe — we want our process to terminate
        # before /CLOSEAPPLICATIONS times out and force-kills us,
        # so we exit promptly but cleanly.
        QTimer.singleShot(0, self._quit_app)
        return True

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
