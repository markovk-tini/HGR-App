from __future__ import annotations

from typing import Optional

import cv2
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from ...config.app_config import AppConfig


class LiveViewWindow(QMainWindow):
    minimize_requested = Signal()
    toggle_gestures_requested = Signal()

    def __init__(self, config: AppConfig, worker: Optional[object] = None):
        super().__init__()
        self.config = config
        self._worker: Optional[object] = None
        self._last_frame = None
        self._volume_level = 0.0
        self._volume_muted = False
        self._volume_active = False
        self._gestures_enabled = True

        self.setWindowTitle("Touchless Live View")
        self.setMinimumSize(980, 680)
        self.resize(1120, 780)

        self._build_ui()
        self.apply_theme(config)
        self.attach_to_worker(worker)

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("debugRoot")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("debugHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(10, 6, 10, 6)
        header_layout.setSpacing(8)

        self.title_label = QLabel("Touchless Gesture Live View")
        self.title_label.setObjectName("debugHeaderTitle")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)

        self.min_button = QPushButton("Minimize")
        self.min_button.setObjectName("debugHeaderButton")
        self.min_button.clicked.connect(self._handle_minimize)

        self.gesture_toggle_button = QPushButton("Gestures On")
        self.gesture_toggle_button.setObjectName("debugHeaderButton")
        self.gesture_toggle_button.clicked.connect(self.toggle_gestures_requested.emit)

        self.close_button = QPushButton("Close")
        self.close_button.setObjectName("debugHeaderButton")
        self.close_button.clicked.connect(self._handle_close)

        header_layout.addWidget(self.min_button)
        header_layout.addWidget(self.gesture_toggle_button)
        header_layout.addWidget(self.close_button)
        outer.addWidget(self.header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        outer.addWidget(body, 1)

        video_wrap = QFrame()
        video_wrap.setObjectName("debugVideoWrap")
        video_layout = QVBoxLayout(video_wrap)
        video_layout.setContentsMargins(10, 10, 10, 10)
        video_layout.setSpacing(8)

        self.video_label = QLabel("Press START in the app to begin live gesture tracking.")
        self.video_label.setObjectName("videoLabel")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setWordWrap(True)
        self.video_label.setMinimumSize(280, 180)
        video_layout.addWidget(self.video_label, 1)

        self.gesture_chip = QLabel("Gesture: neutral")
        self.gesture_chip.setObjectName("gestureChip")
        self.gesture_chip.setAlignment(Qt.AlignCenter)
        video_layout.addWidget(self.gesture_chip, 0, Qt.AlignCenter)
        body_layout.addWidget(video_wrap, 1)

        self.side_card = QFrame()
        self.side_card.setObjectName("debugSideCard")
        self.side_card.setMinimumWidth(380)
        side_layout = QVBoxLayout(self.side_card)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(8)

        self.info_labels: list[QLabel] = []
        for text in (
            "Camera: waiting",
            "Handedness: -",
            "Gesture raw/stable: neutral / neutral",
            "Confidence: 0.00",
            "FPS: 0.0",
            "Box: -",
            "Palm: -",
            "Dynamic: neutral",
            "Candidates: -",
            "Thumb: -",
            "Index: -",
            "Middle: -",
            "Ring: -",
            "Pinky: -",
            "Spreads: -",
            "Reasoning: no hand in frame",
            "Volume control: unavailable",
            "Volume level: -",
            "Spotify control: -",
            "Spotify info: -",
            "Chrome mode: off",
            "Chrome control: -",
            "YouTube mode: off",
            "YouTube control: -",
            "Voice mode: ready",
            "Voice control: -",
            "Voice heard: -",
            "Mouse mode: off",
            "Mouse control: -",
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setObjectName("debugInfoLabel")
            side_layout.addWidget(label)
            self.info_labels.append(label)

        self.volume_bar_card = QFrame()
        self.volume_bar_card.setObjectName("volumeCard")
        volume_card_layout = QVBoxLayout(self.volume_bar_card)
        volume_card_layout.setContentsMargins(10, 10, 10, 10)
        volume_card_layout.setSpacing(6)

        self.volume_bar_title = QLabel("System Volume")
        self.volume_bar_title.setObjectName("volumeTitle")
        volume_card_layout.addWidget(self.volume_bar_title)

        self.volume_bar_bg = QFrame()
        self.volume_bar_bg.setObjectName("volumeBarBg")
        self.volume_bar_bg.setFixedHeight(18)
        volume_bg_layout = QHBoxLayout(self.volume_bar_bg)
        volume_bg_layout.setContentsMargins(0, 0, 0, 0)
        volume_bg_layout.setSpacing(0)

        self.volume_bar_fill = QFrame(self.volume_bar_bg)
        self.volume_bar_fill.setObjectName("volumeBarFill")
        self.volume_bar_fill.setFixedWidth(0)
        volume_bg_layout.addWidget(self.volume_bar_fill, 0, Qt.AlignLeft)
        volume_bg_layout.addStretch(1)
        volume_card_layout.addWidget(self.volume_bar_bg)

        self.volume_bar_text = QLabel("Volume inactive")
        self.volume_bar_text.setObjectName("volumeText")
        volume_card_layout.addWidget(self.volume_bar_text)
        side_layout.addWidget(self.volume_bar_card)

        note = QLabel(
            "This app live view mirrors the active Touchless runtime. Use it to watch the live hand skeleton, finger-state reasoning, gesture status, system-volume feedback, app control state, and voice state while the app is running."
        )
        note.setWordWrap(True)
        note.setObjectName("debugNote")
        side_layout.addWidget(note)
        side_layout.addStretch(1)

        body_layout.addWidget(self.side_card)

    def apply_theme(self, config: AppConfig) -> None:
        self.config = config
        button_hover = QColor(self.config.primary_color).lighter(118)
        button_hover.setAlpha(175)
        self.setStyleSheet(
            f"""
            QWidget#debugRoot {{
                background-color: rgba(7, 19, 29, 0.98);
                color: {self.config.text_color};
            }}
            QFrame#debugHeader {{
                background-color: rgba(9, 24, 36, 0.95);
                border-bottom: 1px solid rgba(29, 233, 182, 0.22);
            }}
            QLabel#debugHeaderTitle {{
                color: {self.config.text_color};
                font-size: 15px;
                font-weight: 800;
            }}
            QPushButton#debugHeaderButton {{
                background-color: rgba(255,255,255,0.07);
                color: {self.config.text_color};
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 6px 10px;
                min-width: 72px;
                font-weight: 800;
            }}
            QPushButton#debugHeaderButton:hover {{
                background-color: {button_hover.name(QColor.HexArgb)};
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton#debugHeaderButton:pressed {{
                background-color: {self.config.accent_color};
                color: #001B24;
                border: 1px solid {self.config.accent_color};
            }}
            QFrame#debugVideoWrap, QFrame#debugSideCard, QFrame#volumeCard {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(29, 233, 182, 0.22);
                border-radius: 16px;
            }}
            QLabel#videoLabel {{
                background-color: rgba(0,0,0,0.16);
                border-radius: 12px;
                border: 1px solid rgba(255,255,255,0.08);
                color: {self.config.text_color};
            }}
            QLabel#gestureChip {{
                background-color: rgba(9, 42, 58, 0.90);
                color: {self.config.accent_color};
                border-radius: 12px;
                padding: 6px 10px;
                font-weight: 800;
            }}
            QLabel#debugInfoLabel {{
                color: {self.config.text_color};
                background: transparent;
            }}
            QLabel#debugNote {{
                color: rgba(229,246,255,0.88);
                background: transparent;
            }}
            QLabel#volumeTitle {{
                color: {self.config.accent_color};
                font-weight: 800;
                background: transparent;
            }}
            QFrame#volumeBarBg {{
                background-color: rgba(255,255,255,0.10);
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 9px;
            }}
            QFrame#volumeBarFill {{
                background-color: {self.config.accent_color};
                border-radius: 9px;
            }}
            QLabel#volumeText {{
                color: {self.config.text_color};
                background: transparent;
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

    def show_window(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _handle_minimize(self) -> None:
        self.hide()
        self.minimize_requested.emit()

    def _handle_close(self) -> None:
        self.hide()

    def _on_worker_debug_frame(self, frame, payload) -> None:
        self._last_frame = frame.copy() if frame is not None else None
        self._render_frame()

        self.gesture_chip.setText(str(payload.get("gesture_chip", "Gesture: neutral")))

        info_lines = list(payload.get("info_lines", []))
        for index, label in enumerate(self.info_labels):
            if index < len(info_lines):
                label.setText(str(info_lines[index]))
            else:
                label.setText("-")

        self._volume_level = float(payload.get("volume_level_scalar", 0.0) or 0.0)
        self._volume_muted = bool(payload.get("volume_muted", False))
        self._volume_active = bool(payload.get("volume_active", False))
        self._update_volume_widgets()

    def _set_idle_state(self) -> None:
        self._last_frame = None
        self.video_label.clear()
        self.video_label.setText("Press START in the app to begin live gesture tracking.")
        self.gesture_chip.setText("Gesture: neutral")
        defaults = (
            "Camera: waiting",
            "Handedness: -",
            "Gesture raw/stable: neutral / neutral",
            "Confidence: 0.00",
            "FPS: 0.0",
            "Box: -",
            "Palm: -",
            "Dynamic: neutral",
            "Candidates: -",
            "Thumb: -",
            "Index: -",
            "Middle: -",
            "Ring: -",
            "Pinky: -",
            "Spreads: -",
            "Reasoning: no hand in frame",
            "Volume control: idle",
            "Volume level: -",
            "Spotify control: -",
            "Spotify info: -",
            "Chrome mode: off",
            "Chrome control: -",
            "YouTube mode: off",
            "YouTube control: -",
            "Voice mode: ready",
            "Voice control: -",
            "Voice heard: -",
            "Mouse mode: off",
            "Mouse control: -",
        )
        for index, label in enumerate(self.info_labels):
            label.setText(defaults[index] if index < len(defaults) else "-")
        self._volume_level = 0.0
        self._volume_muted = False
        self._volume_active = False
        self._gestures_enabled = True
        self._update_volume_widgets()

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

    def _update_volume_widgets(self) -> None:
        level = max(0.0, min(1.0, float(self._volume_level)))
        bar_width = max(0, int(self.volume_bar_bg.contentsRect().width() * level))
        self.volume_bar_fill.setFixedWidth(bar_width)
        prefix = "Active" if self._volume_active else "Idle"
        mute_suffix = " [muted]" if self._volume_muted else ""
        self.volume_bar_text.setText(f"{prefix}: {int(round(level * 100))}%{mute_suffix}")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._render_frame()
        self._update_volume_widgets()
