from __future__ import annotations

from typing import Optional

import cv2
from PySide6.QtCore import QPoint, Qt, Signal, QEvent, QTimer
from PySide6.QtGui import QColor, QCursor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...config.app_config import AppConfig


class MiniLiveViewer(QWidget):
    enlarge_requested = Signal()
    toggle_gestures_requested = Signal()
    close_requested = Signal()

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._worker: Optional[object] = None
        self._last_frame = None
        self._drag_offset: Optional[QPoint] = None
        self._user_positioned = False
        self._hover_active = False
        self._gestures_enabled = True

        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.resize(360, 250)

        self._build_ui()
        self.apply_theme(config)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.header = QFrame()
        self.header.setObjectName("miniHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        header_layout.setSpacing(6)

        self.title_label = QLabel("Touchless")
        self.title_label.setObjectName("miniTitle")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)

        self.enlarge_button = QPushButton("Enlarge")
        self.enlarge_button.setObjectName("miniButton")
        self.enlarge_button.clicked.connect(self.enlarge_requested.emit)
        header_layout.addWidget(self.enlarge_button)

        self.gesture_toggle_button = QPushButton("Gestures On")
        self.gesture_toggle_button.setObjectName("miniButton")
        self.gesture_toggle_button.clicked.connect(self.toggle_gestures_requested.emit)
        header_layout.addWidget(self.gesture_toggle_button)

        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("miniButton")
        self.close_button.clicked.connect(self._handle_close)
        header_layout.addWidget(self.close_button)
        layout.addWidget(self.header)

        self.video_label = QLabel("Press START to begin live gesture tracking.")
        self.video_label.setObjectName("miniVideo")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setWordWrap(True)
        self.video_label.setMinimumSize(220, 140)
        layout.addWidget(self.video_label, 1)

        self.gesture_chip = QLabel("Gesture: neutral")
        self.gesture_chip.setObjectName("miniChip")
        self.gesture_chip.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.gesture_chip)

        for widget in (
            self,
            self.header,
            self.title_label,
            self.enlarge_button,
            self.gesture_toggle_button,
            self.close_button,
            self.video_label,
            self.gesture_chip,
        ):
            widget.setAttribute(Qt.WA_Hover, True)
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        self._set_hover_active(False)

    def apply_theme(self, config: AppConfig) -> None:
        self.config = config
        hover = QColor(self.config.primary_color).lighter(118)
        hover.setAlpha(175)
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: rgba(7,19,29,0.98);
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.22);
                border-radius: 14px;
            }}
            QFrame#miniHeader {{
                background-color: rgba(9,24,36,0.92);
                border: 1px solid rgba(255,255,255,0.04);
                border-radius: 10px;
            }}
            QLabel#miniTitle {{
                color: {self.config.text_color};
                font-weight: 800;
            }}
            QLabel#miniVideo {{
                background-color: rgba(0,0,0,0.18);
                border-radius: 12px;
                border: 1px solid rgba(255,255,255,0.08);
                color: {self.config.text_color};
            }}
            QLabel#miniChip {{
                background-color: rgba(9,42,58,0.92);
                color: {self.config.accent_color};
                border-radius: 12px;
                padding: 6px 8px;
                font-weight: 800;
            }}
            QPushButton#miniButton {{
                background-color: rgba(255,255,255,0.07);
                color: {self.config.text_color};
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 6px 10px;
                font-weight: 800;
            }}
            QPushButton#miniButton:hover {{
                background-color: {hover.name(QColor.HexArgb)};
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton#miniButton:pressed {{
                background-color: {self.config.accent_color};
                color: #001B24;
                border: 1px solid {self.config.accent_color};
            }}
            """
        )

    def set_gestures_enabled(self, enabled: bool) -> None:
        self._gestures_enabled = bool(enabled)
        self.gesture_toggle_button.setText("Gestures On" if self._gestures_enabled else "Gestures Off")

    def attach_to_worker(self, worker: Optional[object]) -> None:
        if self._worker is worker:
            return
        self.detach_from_worker()
        self._worker = worker
        if self._worker is None:
            self._set_idle_state()
            return
        try:
            self._worker.debug_frame_ready.connect(self._on_worker_debug_frame)
        except Exception:
            self._worker = None
            self._set_idle_state()

    def detach_from_worker(self) -> None:
        if self._worker is not None:
            try:
                self._worker.debug_frame_ready.disconnect(self._on_worker_debug_frame)
            except Exception:
                pass
        self._worker = None
        self._set_idle_state()

    def show_overlay(self) -> None:
        if not self._user_positioned:
            self._move_to_default_corner()
        self.show()
        self.raise_()
        self._sync_hover_state()

    def _move_to_default_corner(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        margin = 22
        self.move(
            geometry.right() - self.width() - margin,
            geometry.bottom() - self.height() - margin,
        )

    def _on_worker_debug_frame(self, frame, payload) -> None:
        self._last_frame = frame.copy() if frame is not None else None
        self._render_frame()
        self.gesture_chip.setText(str(payload.get("gesture_chip", "Gesture: neutral")))

    def _set_idle_state(self) -> None:
        self._last_frame = None
        self.video_label.clear()
        self.video_label.setText("Press START to begin live gesture tracking.")
        self.gesture_chip.setText("Gesture: neutral")

    def _render_frame(self) -> None:
        if self._last_frame is None:
            return
        frame_rgb = cv2.cvtColor(self._last_frame, cv2.COLOR_BGR2RGB)
        height, width, channels = frame_rgb.shape
        image = QImage(frame_rgb.data, width, height, channels * width, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        self.video_label.setPixmap(
            pixmap.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _handle_close(self) -> None:
        self.hide()
        self.close_requested.emit()

    def _set_hover_active(self, active: bool) -> None:
        if self._hover_active == active:
            return
        self._hover_active = active
        self.header.setVisible(active)

    def _sync_hover_state(self) -> None:
        cursor_pos = QCursor.pos()
        local_pos = self.mapFromGlobal(cursor_pos)
        self._set_hover_active(self.rect().contains(local_pos))

    def eventFilter(self, obj, event):  # noqa: N802
        if obj in (
            self,
            self.header,
            self.title_label,
            self.enlarge_button,
            self.gesture_toggle_button,
            self.close_button,
            self.video_label,
            self.gesture_chip,
        ) and event.type() in {
            QEvent.Enter,
            QEvent.Leave,
            QEvent.HoverEnter,
            QEvent.HoverLeave,
            QEvent.HoverMove,
            QEvent.MouseMove,
            QEvent.Show,
        }:
            QTimer.singleShot(0, self._sync_hover_state)
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._user_positioned = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render_frame()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        QTimer.singleShot(0, self._sync_hover_state)
