"""Settings panel for the Custom Gestures Beta feature.

Drops into the existing settings nav at SECTION_CUSTOM_GESTURE. Calls
out to dedicated wizard / recorder / sandbox dialogs in sibling modules.

Camera handling: the main GestureWorker owns the webcam. Recording and
sandbox dialogs subscribe to its `raw_frame_ready` signal — no camera
contention, just frame-stream sharing.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# Stylesheet for the scroll bars used inside the panel + the cards list.
# Mirrors the camera-panel scroll styling. The triple-selector for
# QScrollArea / its child QWidget / the qt_scrollarea_viewport is
# important — Qt creates an internal viewport widget that doesn't
# inherit a transparent background otherwise, and the page ends up
# painted with the OS default (white on Win 11).
_SCROLLBAR_STYLE = """
QScrollArea, QScrollArea > QWidget, QScrollArea QWidget#qt_scrollarea_viewport {{
    background: transparent;
    border: none;
}}
QScrollArea QScrollBar:vertical {{
    background: rgba(255,255,255,0.04);
    width: 10px;
    margin: 6px 3px 6px 3px;
    border-radius: 5px;
}}
QScrollArea QScrollBar::handle:vertical {{
    background: {accent};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollArea QScrollBar::handle:vertical:hover {{
    background: {accent};
    border: 1px solid rgba(255,255,255,0.25);
}}
QScrollArea QScrollBar::add-line:vertical,
QScrollArea QScrollBar::sub-line:vertical {{
    height: 0px;
    background: transparent;
}}
"""

# Allow imports from the standalone custom_gestures module.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from hgr.custom_gestures.action import describe as describe_action
from hgr.custom_gestures.description import format_gesture_summary
from hgr.custom_gestures.registry import CustomGesture, GestureRegistry


_HOW_IT_WORKS_HTML = (
    "<p style='margin-top:0;'>Custom Gestures lets you record your own hand "
    "pose and bind it to any keystroke, hotkey, text snippet, URL, or shell "
    "command. Once saved, the gesture works alongside the built-in Touchless "
    "controls.</p>"
    "<p><b>How to use it:</b></p>"
    "<ol style='margin-top:2px; padding-left:18px;'>"
    "<li><b>Click Create New Gesture.</b> Give it a name and (optional) "
    "description.</li>"
    "<li><b>Set timing.</b> Hold-to-activate (default 1s) is how long you "
    "must keep the pose before the action fires. Cooldown (default 2s) "
    "prevents back-to-back firing while you keep holding.</li>"
    "<li><b>Pick an action.</b> Press a key, fire a hotkey combo, type "
    "text, open a URL, or run a shell command. The form will ask for the "
    "specific value once you choose.</li>"
    "<li><b>Click Start to record.</b> The camera opens. Hold your pose "
    "and click <b>Begin Recording</b>. Touchless captures 100 frames "
    "(~10 seconds) — let your hand drift naturally during this so the "
    "classifier learns your real range. The live finger-state readout "
    "in the top-right shows what the system is perceiving.</li>"
    "<li><b>Save when done.</b> Touchless prints a Hand Pose summary so "
    "you can verify the recording captured what you intended.</li>"
    "</ol>"
    "<p><b>Limitations of the current model (Beta):</b></p>"
    "<ul style='margin-top:2px; padding-left:18px;'>"
    "<li><b>Static poses only.</b> The classifier matches single-frame "
    "shapes. Swipes, waves, and motion-based gestures aren't supported "
    "yet.</li>"
    "<li><b>Avoid heavy occlusion.</b> Poses where one finger is hidden "
    "behind another (interlocked fingers, pinky tucked behind thumb) "
    "produce noisy landmark predictions and unreliable matches. Pick "
    "poses where every fingertip is visible to the camera.</li>"
    "<li><b>One hand at a time.</b> Custom gestures are matched on the "
    "primary tracked hand only.</li>"
    "</ul>"
)


class CustomGesturesPanel(QWidget):
    """Container widget holding the description, the create button, the
    saved-gesture cards list, and the sandbox button. Emits a signal
    when it needs the parent window to open one of the dialogs that
    consumes the live camera stream."""

    open_create_requested = Signal()
    open_sandbox_requested = Signal()
    open_edit_requested = Signal(str)  # emits gesture name to edit

    def __init__(
        self,
        config,
        accent_color: str,
        registry_path_provider: Optional[Callable[[], Path]] = None,
        worker_provider: Optional[Callable[[], object]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._accent_color = accent_color
        self._registry_path_provider = registry_path_provider
        self._worker_provider = worker_provider
        self._registry = GestureRegistry()
        self._cards: List["GestureCard"] = []

        # Outer layout: just hosts the page-level scroll area.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.NoFrame)
        page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page_scroll.setStyleSheet(_SCROLLBAR_STYLE.format(accent=self._accent_color))
        # CSS-only `qt_scrollarea_viewport` targeting wasn't beating the
        # OS default background on Win 11 — set the viewport's bg
        # explicitly via API so it actually takes effect.
        page_scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(page_scroll)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        page_scroll.setWidget(inner)
        root = QVBoxLayout(inner)
        # Right padding leaves space for the scrollbar.
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(12)

        root.addWidget(self._build_how_it_works_card())
        root.addWidget(self._build_actions_card())
        self._cards_card = self._build_cards_card()
        root.addWidget(self._cards_card)
        root.addStretch(1)

        self.refresh_cards()

    # --- card builders ---------------------------------------------------

    def _inner_card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        box = QFrame()
        box.setObjectName("innerCard")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        if title:
            t = QLabel(title)
            t.setObjectName("cardTitle")
            t.setStyleSheet("font-size: 16px; font-weight: 700;")
            layout.addWidget(t)
        return box, layout

    def _build_how_it_works_card(self) -> QFrame:
        box, layout = self._inner_card("How it works")
        body = QLabel(_HOW_IT_WORKS_HTML)
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setStyleSheet("color: #DCE9F2; font-size: 13px; line-height: 1.4;")
        body.setOpenExternalLinks(False)
        layout.addWidget(body)
        return box

    def _build_actions_card(self) -> QFrame:
        box, layout = self._inner_card("")
        row = QHBoxLayout()
        row.setSpacing(10)

        self.create_button = QPushButton("+  Create New Gesture")
        self.create_button.setMinimumHeight(38)
        self.create_button.setStyleSheet(
            f"QPushButton {{"
            f"  background: {self._accent_color};"
            f"  color: #0B1620;"
            f"  font-weight: 700;"
            f"  font-size: 14px;"
            f"  border: none;"
            f"  border-radius: 8px;"
            f"  padding: 8px 18px;"
            f"}}"
            f"QPushButton:hover {{ background: #FFFFFF; }}"
        )
        self.create_button.clicked.connect(self.open_create_requested)
        row.addWidget(self.create_button)

        self.sandbox_button = QPushButton("Sandbox  (test gestures live)")
        self.sandbox_button.setMinimumHeight(38)
        self.sandbox_button.setStyleSheet(
            "QPushButton {"
            "  background: rgba(255,255,255,0.06);"
            "  color: #E5F6FF;"
            "  font-weight: 600;"
            "  border: 1px solid rgba(255,255,255,0.12);"
            "  border-radius: 8px;"
            "  padding: 8px 18px;"
            "}"
            "QPushButton:hover { background: rgba(255,255,255,0.10); }"
        )
        self.sandbox_button.clicked.connect(self.open_sandbox_requested)
        row.addWidget(self.sandbox_button)

        row.addStretch(1)
        layout.addLayout(row)
        return box

    def _build_cards_card(self) -> QFrame:
        box, self._cards_layout = self._inner_card("Saved gestures")
        self._empty_label = QLabel(
            "No custom gestures yet. Click <b>Create New Gesture</b> to "
            "record your first one."
        )
        self._empty_label.setStyleSheet("color: #9FB3C2; font-style: italic;")
        self._empty_label.setWordWrap(True)
        self._cards_layout.addWidget(self._empty_label)

        # Inner scroll area so a long list of gestures stays bounded
        # rather than pushing every other card off-screen.
        cards_scroll = QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setFrameShape(QFrame.NoFrame)
        cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cards_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        cards_scroll.setStyleSheet(_SCROLLBAR_STYLE.format(accent=self._accent_color))
        cards_scroll.viewport().setStyleSheet("background: transparent;")
        cards_scroll.setMinimumHeight(220)
        cards_scroll.setMaximumHeight(420)

        self._cards_container = QWidget()
        self._cards_container.setStyleSheet("background: transparent;")
        self._cards_container_layout = QVBoxLayout(self._cards_container)
        self._cards_container_layout.setContentsMargins(0, 0, 8, 0)
        self._cards_container_layout.setSpacing(8)
        self._cards_container_layout.addStretch(1)
        cards_scroll.setWidget(self._cards_container)
        self._cards_layout.addWidget(cards_scroll)
        return box

    # --- public API ------------------------------------------------------

    def _ping_worker_reload(self) -> None:
        """Tell the running GestureWorker to re-read custom gestures so
        new / edited / deleted gestures take effect live without an app
        restart. Safe no-op when no worker is running."""
        if self._worker_provider is None:
            return
        try:
            worker = self._worker_provider()
        except Exception:
            return
        if worker is None:
            return
        try:
            worker.reload_custom_gestures()
        except Exception:
            pass

    def refresh_cards(self) -> None:
        """Reload the registry from disk and rebuild the cards list.
        Also pings the live GestureWorker so any add / edit / delete
        propagates to the running pipeline immediately."""
        self._ping_worker_reload()
        self._registry = GestureRegistry()
        self._registry.load()
        # Clear existing cards.
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        gestures = self._registry.list()
        if not gestures:
            self._empty_label.show()
            return
        self._empty_label.hide()
        # Insert each new card BEFORE the trailing stretch so cards
        # stack from the top of the scroll area instead of expanding.
        insert_index = max(0, self._cards_container_layout.count() - 1)
        for g in gestures:
            card = GestureCard(
                g,
                on_delete=self._on_delete_gesture,
                on_edit=self._on_edit_gesture,
                parent=self._cards_container,
            )
            self._cards.append(card)
            self._cards_container_layout.insertWidget(insert_index, card)
            insert_index += 1

    def _on_edit_gesture(self, name: str) -> None:
        self.open_edit_requested.emit(name)

    def _on_delete_gesture(self, name: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete gesture",
            f"Delete custom gesture '{name}'? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self._registry.load()
        self._registry.remove(name)
        self._registry.save()
        self.refresh_cards()


class GestureCard(QFrame):
    """Expandable card for one saved gesture: header with name + action +
    delete; click expands to show description, how-to summary, action
    detail. Video-clip preview is a future addition."""

    def __init__(
        self,
        gesture: CustomGesture,
        on_delete: Callable[[str], None],
        on_edit: Optional[Callable[[str], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._gesture = gesture
        self._on_delete = on_delete
        self._on_edit = on_edit
        self._expanded = False

        self.setObjectName("gestureCard")
        self.setStyleSheet(
            "QFrame#gestureCard {"
            "  background: rgba(255,255,255,0.04);"
            "  border: 1px solid rgba(255,255,255,0.08);"
            "  border-radius: 8px;"
            "}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # Top row: name (expanding) + edit/delete (fixed). The thumbnail
        # the user picked while recording lives inside the expanded
        # details below — the collapsed card stays compact so a list
        # of many custom gestures scrolls cleanly.
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        self._toggle_button = QPushButton(f"▶  {gesture.name}")
        self._toggle_button.setStyleSheet(
            "QPushButton {"
            "  background: transparent;"
            "  color: #E5F6FF;"
            "  font-weight: 700;"
            "  font-size: 14px;"
            "  text-align: left;"
            "  padding: 4px 0;"
            "  border: none;"
            "}"
            "QPushButton:hover { color: #FFFFFF; }"
        )
        self._toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._toggle_button.clicked.connect(self._toggle)
        top_row.addWidget(self._toggle_button, 1)

        edit_button = QPushButton("Edit")
        edit_button.setFixedWidth(64)
        edit_button.setStyleSheet(
            "QPushButton {"
            "  background: transparent;"
            "  color: #DCE9F2;"
            "  border: 1px solid rgba(220,233,242,0.35);"
            "  border-radius: 6px;"
            "  padding: 4px 10px;"
            "  font-size: 12px;"
            "}"
            "QPushButton:hover {"
            "  background: rgba(255,255,255,0.10);"
            "  color: #FFFFFF;"
            "}"
        )
        if self._on_edit is not None:
            edit_button.clicked.connect(lambda: self._on_edit(self._gesture.name))
        else:
            edit_button.setEnabled(False)
        top_row.addWidget(edit_button, 0)

        delete_button = QPushButton("Delete")
        delete_button.setFixedWidth(78)
        delete_button.setStyleSheet(
            "QPushButton {"
            "  background: transparent;"
            "  color: #C9818D;"
            "  border: 1px solid rgba(201,129,141,0.35);"
            "  border-radius: 6px;"
            "  padding: 4px 10px;"
            "  font-size: 12px;"
            "}"
            "QPushButton:hover {"
            "  background: rgba(201,129,141,0.15);"
            "  color: #FFFFFF;"
            "}"
        )
        delete_button.clicked.connect(lambda: self._on_delete(self._gesture.name))
        top_row.addWidget(delete_button, 0)

        root.addLayout(top_row)

        # Second row: the action description, full width but small text.
        # Prefix with the bound hand so the user sees at-a-glance which
        # hand fires this custom gesture in the live pipeline.
        if gesture.handedness in ("Left", "Right"):
            hand_prefix = f"[{gesture.handedness} hand] "
        else:
            hand_prefix = "[Either hand] "
        action_label = QLabel(hand_prefix + describe_action(gesture.action))
        action_label.setStyleSheet("color: #9FB3C2; font-size: 12px;")
        action_label.setWordWrap(True)
        root.addWidget(action_label)

        self._details = QLabel()
        self._details.setWordWrap(True)
        self._details.setStyleSheet(
            "color: #DCE9F2;"
            " font-family: Consolas, 'Courier New', monospace;"
            " font-size: 12px;"
            " background: rgba(0,0,0,0.20);"
            " padding: 10px;"
            " border-radius: 6px;"
        )
        self._details.setText(format_gesture_summary(gesture))
        self._details.hide()
        root.addWidget(self._details)

        # Thumbnail (only shown when the card is expanded). Larger than
        # the old collapsed-card thumbnail because there's more room
        # inside the dropdown.
        self._expanded_thumb_label = QLabel()
        self._expanded_thumb_label.setObjectName("gestureCardExpandedThumb")
        self._expanded_thumb_label.setAlignment(Qt.AlignCenter)
        self._expanded_thumb_label.setFixedHeight(180)
        self._expanded_thumb_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._expanded_thumb_label.setStyleSheet(
            "QLabel#gestureCardExpandedThumb {"
            "  background: rgba(0,0,0,0.30);"
            "  border: 1px solid rgba(255,255,255,0.10);"
            "  border-radius: 8px;"
            "  color: rgba(229,246,255,0.55);"
            "  font-size: 12px;"
            "  padding: 8px;"
            "}"
        )
        self._expanded_thumb_label.hide()
        root.addWidget(self._expanded_thumb_label)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._toggle_button.setText(
            f"{'▼' if self._expanded else '▶'}  {self._gesture.name}"
        )
        self._details.setVisible(self._expanded)
        if self._expanded:
            # Lazy-load the thumbnail the first time the card is
            # expanded so a list of many custom gestures doesn't pay
            # the disk cost up-front.
            self._refresh_expanded_thumbnail()
        self._expanded_thumb_label.setVisible(self._expanded)

    def _refresh_expanded_thumbnail(self) -> None:
        """Load the user-picked thumbnail into the expanded slot. Falls
        back to a friendly placeholder when the gesture has no image."""
        try:
            from hgr.custom_gestures.registry import GestureRegistry
            registry = GestureRegistry()
            registry.load()
            path = registry.thumbnail_path(self._gesture)
        except Exception:
            path = None
        if path is not None:
            pix = QPixmap(str(path))
            if not pix.isNull():
                self._expanded_thumb_label.setPixmap(
                    pix.scaled(
                        self._expanded_thumb_label.width() or 240,
                        self._expanded_thumb_label.height() or 180,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
                return
        self._expanded_thumb_label.setText("No image picked for this gesture.")

