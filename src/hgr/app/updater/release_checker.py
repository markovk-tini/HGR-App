"""GitHub Releases poller.

Runs the HTTP fetch on a Qt worker thread so the main thread never
blocks on network I/O. Emits `update_available(ReleaseInfo)` when
the latest release tag parses to a version newer than the running
app's `__version__`. Emits `no_update()` for same/older. Emits
`check_failed(reason)` if the fetch errored — the caller can
silently ignore that (offline laptops, GitHub rate limit, etc.)
without ever bothering the user.

Why GitHub Releases API specifically:
- It's free, requires no auth for public repos at modest poll
  rates (60 unauthenticated req/hr/IP, way more than we need),
  and returns a structured JSON we can parse without scraping.
- The release `body` field is markdown — it's exactly what the
  user types into "release notes" on the GitHub Releases UI, so
  the maintainer's existing release-writing workflow doubles as
  the changelog source.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from ... import __version__ as RUNNING_VERSION

GITHUB_RELEASES_LATEST_URL = (
    "https://api.github.com/repos/markovk-tini/HGR-App/releases/latest"
)
INSTALLER_ASSET_NAME = "Touchless_Installer.exe"
HTTP_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class ReleaseInfo:
    version: str            # e.g. "1.0.2" (tag with leading 'v' stripped)
    body: str               # Markdown release notes from GitHub
    download_url: str       # Direct link to the .exe asset
    html_url: str           # GitHub release page (fallback)
    size_bytes: int = 0     # Asset content-length, 0 if unknown


def _parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Convert '1.0.2' → (1, 0, 2). Tolerates leading 'v' and
    suffixes like '-beta'. Returns (0,) if unparseable, which makes
    any real version compare as 'newer' (no false positive)."""
    cleaned = re.sub(r"^v", "", str(version_str or "").strip(), flags=re.IGNORECASE)
    cleaned = re.split(r"[-+]", cleaned, maxsplit=1)[0]
    parts = re.findall(r"\d+", cleaned)
    if not parts:
        return (0,)
    return tuple(int(p) for p in parts)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_version_tuple(remote) > _parse_version_tuple(local)


class _CheckWorker(QObject):
    finished = Signal()
    update_available = Signal(object)   # ReleaseInfo
    no_update = Signal()
    check_failed = Signal(str)

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                GITHUB_RELEASES_LATEST_URL,
                headers={
                    # Bare github.com requests get user-agent-blocked.
                    "User-Agent": "Touchless-Updater/1.0",
                    "Accept": "application/vnd.github+json",
                },
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            self.check_failed.emit(f"network: {exc!s}")
            self.finished.emit()
            return
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.check_failed.emit(f"parse: {exc!s}")
            self.finished.emit()
            return
        except Exception as exc:  # pragma: no cover — belt and suspenders
            self.check_failed.emit(f"unexpected: {type(exc).__name__}")
            self.finished.emit()
            return

        try:
            tag = str(data.get("tag_name") or "").strip()
            body = str(data.get("body") or "").strip()
            html_url = str(data.get("html_url") or "").strip()
            assets = data.get("assets") or []
            asset_url = ""
            asset_size = 0
            for asset in assets:
                if str(asset.get("name") or "").strip().lower() == INSTALLER_ASSET_NAME.lower():
                    asset_url = str(asset.get("browser_download_url") or "").strip()
                    try:
                        asset_size = int(asset.get("size") or 0)
                    except (TypeError, ValueError):
                        asset_size = 0
                    break
        except Exception as exc:
            self.check_failed.emit(f"shape: {type(exc).__name__}")
            self.finished.emit()
            return

        if not tag:
            self.check_failed.emit("missing tag_name in release payload")
            self.finished.emit()
            return

        if not _is_newer(tag, RUNNING_VERSION):
            self.no_update.emit()
            self.finished.emit()
            return

        # Newer tag exists. If we have no installer asset URL, fall
        # back to the html_url — the dialog can offer "Open release
        # page" instead of an in-place download. We still treat
        # this as an update_available signal.
        info = ReleaseInfo(
            version=re.sub(r"^v", "", tag, flags=re.IGNORECASE),
            body=body,
            download_url=asset_url,
            html_url=html_url,
            size_bytes=asset_size,
        )
        self.update_available.emit(info)
        self.finished.emit()


class ReleaseChecker(QObject):
    """Public facade. Owns its own QThread so callers don't have to.

    Usage:
        self._checker = ReleaseChecker(parent=self)
        self._checker.update_available.connect(self._on_update_available)
        self._checker.start()
    """

    update_available = Signal(object)   # ReleaseInfo
    no_update = Signal()
    check_failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _CheckWorker | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self._worker = _CheckWorker()
        self._worker.moveToThread(self._thread)
        self._worker.update_available.connect(self.update_available)
        self._worker.no_update.connect(self.no_update)
        self._worker.check_failed.connect(self.check_failed)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _cleanup(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
