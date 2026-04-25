"""Qt dialog for the auto-update flow.

Two visual states in one widget:
  1. Prompt — title with new version, expandable "What's new" panel
     populated from the GitHub release body, big centered Download
     button, smaller Later button on the bottom right.
  2. Download — same layout but the buttons are replaced by a
     progress bar + status line. On success the dialog closes itself
     and the Updater handles the relaunch.

The "What's new" panel renders the release body as plain text inside
a QTextBrowser. Markdown rendering is enabled so headings/lists from
the GitHub release notes look reasonable without external deps.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .release_checker import ReleaseInfo
from ... import __version__ as RUNNING_VERSION


class UpdateDialog(QDialog):
    """Modal-ish update prompt. Emits one of:
       - download_requested(ReleaseInfo): user clicked Download
       - dismissed(): user clicked Later or closed the dialog
    The Updater listens for download_requested and drives the rest.
    """

    download_requested = Signal(object)   # ReleaseInfo
    dismissed = Signal()

    def __init__(self, info: ReleaseInfo, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._info = info
        self._showing_changelog = False
        self.setWindowTitle("Touchless Update Available")
        self.setMinimumWidth(460)
        self.setSizeGripEnabled(False)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel(f"Touchless {self._info.version} is available!")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        subtitle_parts = [f"You're currently running {RUNNING_VERSION}."]
        size_mb = self._info.size_bytes / (1024 * 1024) if self._info.size_bytes else 0.0
        if self._info.update_kind == "app-zip":
            if size_mb > 0:
                subtitle_parts.append(f"App update — {size_mb:.0f} MB.")
            else:
                subtitle_parts.append("App update — small download.")
        else:
            if size_mb > 0:
                subtitle_parts.append(f"Full update — {size_mb:.0f} MB.")
            else:
                # Full installer is hosted externally (Cloudflare etc.)
                # and the developer didn't include a size marker.
                # Don't fake a number — be honest about the unknown.
                subtitle_parts.append("Full update — large download.")
        subtitle = QLabel(" ".join(subtitle_parts))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 12px;")
        layout.addWidget(subtitle)

        # Centered Download button. Big and obvious — it's the primary action.
        self.download_button = QPushButton("Download Update")
        self.download_button.setMinimumHeight(40)
        self.download_button.setStyleSheet(
            "QPushButton { font-size: 14px; font-weight: 600; padding: 8px 18px; }"
        )
        self.download_button.clicked.connect(self._on_download_clicked)
        download_row = QHBoxLayout()
        download_row.addStretch(1)
        download_row.addWidget(self.download_button)
        download_row.addStretch(1)
        layout.addLayout(download_row)

        # Click-to-show changelog toggle. Hidden by default per the
        # spec: dialog stays compact until the user opts to read.
        self.toggle_button = QPushButton("Click to show what's new ▾")
        self.toggle_button.setFlat(True)
        self.toggle_button.setStyleSheet(
            "QPushButton { color: rgba(255,255,255,0.75); font-size: 12px; "
            "background: transparent; border: none; padding: 4px; } "
            "QPushButton:hover { color: white; }"
        )
        self.toggle_button.clicked.connect(self._on_toggle_changelog)
        toggle_row = QHBoxLayout()
        toggle_row.addStretch(1)
        toggle_row.addWidget(self.toggle_button)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        # Changelog body — collapsed by default.
        self.changelog = QTextBrowser()
        self.changelog.setOpenExternalLinks(True)
        self.changelog.setMinimumHeight(180)
        self.changelog.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body_md = self._info.body.strip() or "_No release notes were attached to this release._"
        try:
            self.changelog.setMarkdown(body_md)
        except Exception:
            # Older Qt without setMarkdown — fall back to plain text.
            self.changelog.setPlainText(body_md)
        self.changelog.setVisible(False)
        layout.addWidget(self.changelog)

        # Progress UI — hidden until Download is clicked.
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 12px;")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        # Later button on the bottom right per the user spec.
        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        self.later_button = QPushButton("Later")
        self.later_button.setStyleSheet("padding: 6px 14px;")
        self.later_button.clicked.connect(self._on_later_clicked)
        bottom_row.addWidget(self.later_button)
        layout.addLayout(bottom_row)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def _on_toggle_changelog(self) -> None:
        self._showing_changelog = not self._showing_changelog
        self.changelog.setVisible(self._showing_changelog)
        self.toggle_button.setText(
            "Hide release notes ▴" if self._showing_changelog else "Click to show what's new ▾"
        )
        # Let the layout recompute. Without this the dialog grows but
        # leaves a void where the buttons used to sit.
        self.adjustSize()

    def _on_download_clicked(self) -> None:
        # Switch to "downloading" state — disable the buttons (so the
        # user doesn't double-tap), reveal the progress bar, emit
        # the signal that the Updater listens for.
        self.download_button.setEnabled(False)
        self.later_button.setEnabled(False)
        self.toggle_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.status_label.setVisible(True)
        self.status_label.setText("Starting download...")
        self.download_requested.emit(self._info)

    def _on_later_clicked(self) -> None:
        self.dismissed.emit()
        self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API name
        # Treat window-X same as Later only when we're not mid-download.
        if self.download_button.isEnabled():
            self.dismissed.emit()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Updater hooks
    # ------------------------------------------------------------------

    def set_progress(self, percent: int, status_text: str = "") -> None:
        if percent >= 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(max(0, min(100, int(percent))))
        else:
            # Indeterminate — used for "extracting", "launching", etc.
            self.progress_bar.setRange(0, 0)
        if status_text:
            self.status_label.setText(status_text)

    def set_failure(self, message: str) -> None:
        # Allow the user to retry or bail.
        self.status_label.setStyleSheet(
            "color: #ff8080; font-size: 12px;"
        )
        self.status_label.setText(message)
        self.progress_bar.setVisible(False)
        self.download_button.setEnabled(True)
        self.download_button.setText("Retry Download")
        self.later_button.setEnabled(True)
        self.toggle_button.setEnabled(True)
