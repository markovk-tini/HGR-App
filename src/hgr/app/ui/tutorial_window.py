from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...config.app_config import AppConfig
from ...debug.chrome_controller import ChromeController
from ...debug.mouse_gesture import MouseGestureTracker
from ...debug.voice_command_listener import VoiceCommandListener
from ...gesture.recognition.engine import GestureRecognitionEngine
from ...gesture.rendering.overlay import HAND_CONNECTIONS
from ...voice.command_processor import VoiceCommandProcessor
from ...debug.spotify_controller import SpotifyController
from ...gesture.ui.test_window import SpotifyWheelOverlay
from ..camera.camera_utils import open_camera_by_index, open_preferred_or_first_available
from ...voice.command_processor import VoiceCommandContext
from ..integration.noop_engine import GestureWorker


@dataclass(frozen=True)
class _StepDefinition:
    key: str
    title: str
    description: str
    progress_template: str


class SwipeInstructionWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._left_count = 0
        self._right_count = 0
        self._accent = QColor("#1DE9B6")
        self._text = QColor("#F4FAFF")
        self.setMinimumHeight(200)

    def apply_theme(self, accent: str, text: str) -> None:
        self._accent = QColor(accent)
        self._text = QColor(text)
        self.update()

    def set_counts(self, left_count: int, right_count: int) -> None:
        self._left_count = int(left_count)
        self._right_count = int(right_count)
        self.update()

    def _draw_hand(self, painter: QPainter, center: QPointF, scale: float, color: QColor) -> None:
        palm = [
            QPointF(center.x() - 18 * scale, center.y() + 28 * scale),
            QPointF(center.x() - 28 * scale, center.y() + 4 * scale),
            QPointF(center.x() - 18 * scale, center.y() - 30 * scale),
            QPointF(center.x() + 18 * scale, center.y() - 30 * scale),
            QPointF(center.x() + 28 * scale, center.y() + 4 * scale),
            QPointF(center.x() + 18 * scale, center.y() + 28 * scale),
        ]
        finger_bases = (-18, -6, 6, 18)
        tip_offsets = (-36, -48, -48, -36)

        pen = QPen(color, 2.6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        path = QPainterPath()
        path.moveTo(palm[0])
        for point in palm[1:]:
            path.lineTo(point)
        path.closeSubpath()
        painter.drawPath(path)

        wrist = QPointF(center.x(), center.y() + 28 * scale)
        for base_x, tip_y in zip(finger_bases, tip_offsets):
            base = QPointF(center.x() + base_x * scale, center.y() - 6 * scale)
            tip = QPointF(center.x() + base_x * scale, center.y() + tip_y * scale)
            painter.drawLine(wrist, base)
            painter.drawLine(base, tip)
            painter.drawEllipse(tip, 2.2 * scale, 2.2 * scale)

        thumb_mid = QPointF(center.x() - 28 * scale, center.y() + 6 * scale)
        thumb_tip = QPointF(center.x() - 48 * scale, center.y() + 18 * scale)
        painter.drawLine(wrist, thumb_mid)
        painter.drawLine(thumb_mid, thumb_tip)
        painter.drawEllipse(thumb_tip, 2.2 * scale, 2.2 * scale)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), Qt.transparent)

        card = self.rect().adjusted(6, 6, -6, -6)
        painter.setBrush(QColor(255, 255, 255, 10))
        painter.setPen(QPen(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 50), 1.2))
        painter.drawRoundedRect(card, 18, 18)
        title_font = QFont("Segoe UI", 13)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(self._accent)
        painter.drawText(
            QRectF(card.left() + 18, card.top() + 20, card.width() - 36, 28),
            Qt.AlignCenter,
            "Swipe Practice",
        )

        count_font = QFont("Segoe UI", 12)
        count_font.setBold(True)
        painter.setFont(count_font)
        painter.setPen(QColor(self._text.red(), self._text.green(), self._text.blue(), 230))
        painter.drawText(
            QRectF(card.left() + 18, card.top() + 64, card.width() - 36, 28),
            Qt.AlignCenter,
            f"Left: {self._left_count}/3    Right: {self._right_count}/3",
        )

        hint_font = QFont("Segoe UI", 10)
        painter.setFont(hint_font)
        painter.setPen(QColor(self._text.red(), self._text.green(), self._text.blue(), 185))
        painter.drawText(
            QRectF(card.left() + 24, card.top() + 108, card.width() - 48, 52),
            Qt.AlignCenter | Qt.TextWordWrap,
            "Use your right hand and match the swipe direction shown over the live camera view.",
        )


class WheelInstructionWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._accent = QColor("#1DE9B6")
        self._text = QColor("#F4FAFF")
        self.setMinimumHeight(220)

    def apply_theme(self, accent: str, text: str) -> None:
        self._accent = QColor(accent)
        self._text = QColor(text)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), Qt.transparent)

        bounds = QRectF(self.rect()).adjusted(16, 12, -16, -12)
        center = bounds.center()
        radius = min(bounds.width(), bounds.height()) * 0.34
        inner_radius = radius * 0.43

        for index in range(6):
            start_angle = 90 - index * 60
            painter.setPen(QPen(QColor(255, 255, 255, 110), 1.4))
            painter.setBrush(QColor(29, 233, 182, 22 if index % 2 == 0 else 12))
            path = QPainterPath()
            outer = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            inner = QRectF(center.x() - inner_radius, center.y() - inner_radius, inner_radius * 2, inner_radius * 2)
            path.arcMoveTo(outer, start_angle)
            path.arcTo(outer, start_angle, -60)
            path.arcTo(inner, start_angle - 60, 60)
            path.closeSubpath()
            painter.drawPath(path)
            painter.fillPath(path, painter.brush())

        painter.setBrush(QColor(8, 20, 34, 230))
        painter.setPen(QPen(self._accent, 2.0))
        painter.drawEllipse(center, inner_radius, inner_radius)

        title_font = QFont("Segoe UI", 12)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(self._accent)
        painter.drawText(QRectF(center.x() - 90, center.y() - 26, 180, 26), Qt.AlignCenter, "Gesture Wheel")

        hint_font = QFont("Segoe UI", 9)
        painter.setFont(hint_font)
        painter.setPen(QColor(self._text.red(), self._text.green(), self._text.blue(), 210))
        painter.drawText(
            QRectF(center.x() - 100, center.y() + 4, 200, 34),
            Qt.AlignCenter | Qt.TextWordWrap,
            "Hold the pose, move toward a slice, and keep it there to confirm.",
        )


class MousePracticeWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._accent = QColor("#1DE9B6")
        self._text = QColor("#F4FAFF")
        self._targets = [
            (0.20, 0.28),
            (0.74, 0.22),
            (0.66, 0.64),
            (0.28, 0.72),
        ]
        self._active_index = 0
        self._cursor_position: tuple[float, float] | None = None
        self._mode_enabled = False
        self._status_text = "Hold left-hand three to turn mouse mode on."
        self.setMinimumHeight(240)

    def apply_theme(self, accent: str, text: str) -> None:
        self._accent = QColor(accent)
        self._text = QColor(text)
        self.update()

    def reset(self) -> None:
        self._active_index = 0
        self._cursor_position = None
        self._mode_enabled = False
        self._status_text = "Hold left-hand three to turn mouse mode on."
        self.update()

    @property
    def completed(self) -> bool:
        return self._active_index >= len(self._targets)

    def set_mode_enabled(self, enabled: bool) -> None:
        self._mode_enabled = bool(enabled)
        if self.completed:
            self._status_text = "Nice work. Mouse mode is ready to use."
        elif self._mode_enabled:
            self._status_text = "Move the cursor and left-click each target in order."
        else:
            self._status_text = "Hold left-hand three to turn mouse mode on."
        self.update()

    def set_cursor_position(self, position: tuple[float, float] | None) -> None:
        self._cursor_position = position
        self.update()

    def register_click(self, position: tuple[float, float] | None) -> bool:
        if not self._mode_enabled or position is None or self.completed:
            return False
        target = self._targets[self._active_index]
        if math.hypot(position[0] - target[0], position[1] - target[1]) <= 0.12:
            self._active_index += 1
            if self.completed:
                self._status_text = "Nice work. Mouse mode is ready to use."
            else:
                self._status_text = "Great. Keep clicking the highlighted target."
            self.update()
            return True
        self._status_text = "Close, but click inside the glowing target."
        self.update()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), Qt.transparent)

        card = self.rect().adjusted(8, 8, -8, -8)
        painter.setBrush(QColor(255, 255, 255, 10))
        painter.setPen(QPen(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 55), 1.2))
        painter.drawRoundedRect(card, 18, 18)

        arena = QRectF(card.left() + 12, card.top() + 12, card.width() - 24, card.height() - 64)
        painter.setBrush(QColor(255, 255, 255, 7))
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0))
        painter.drawRoundedRect(arena, 16, 16)

        for index, (tx, ty) in enumerate(self._targets):
            point = QPointF(arena.left() + tx * arena.width(), arena.top() + ty * arena.height())
            if index < self._active_index:
                fill = QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 85)
                pen = QPen(self._accent, 2.2)
            elif index == self._active_index:
                fill = QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 50)
                pen = QPen(self._accent, 3.2)
            else:
                fill = QColor(255, 255, 255, 12)
                pen = QPen(QColor(255, 255, 255, 70), 1.6)
            painter.setBrush(fill)
            painter.setPen(pen)
            painter.drawEllipse(point, 22, 22)
            painter.setPen(QPen(QColor(self._text.red(), self._text.green(), self._text.blue(), 180), 1.0))
            painter.drawText(QRectF(point.x() - 18, point.y() - 12, 36, 24), Qt.AlignCenter, str(index + 1))

        if self._cursor_position is not None:
            cx = arena.left() + self._cursor_position[0] * arena.width()
            cy = arena.top() + self._cursor_position[1] * arena.height()
            painter.setPen(QPen(self._accent, 2.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), 9, 9)
            painter.drawLine(QPointF(cx - 15, cy), QPointF(cx + 15, cy))
            painter.drawLine(QPointF(cx, cy - 15), QPointF(cx, cy + 15))

        status_font = QFont("Segoe UI", 9)
        status_font.setBold(True)
        painter.setFont(status_font)
        painter.setPen(self._accent if self._mode_enabled else QColor(self._text.red(), self._text.green(), self._text.blue(), 210))
        painter.drawText(
            QRectF(card.left() + 14, card.bottom() - 38, card.width() - 28, 28),
            Qt.AlignCenter | Qt.TextWordWrap,
            self._status_text,
        )


class TutorialWindow(QDialog):
    tutorial_closed = Signal(bool, bool, bool)
    gesture_guide_requested = Signal(bool)

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._camera_index: Optional[int] = None
        self._launched_from_settings = False
        self._auto_start_on_done = False
        self._camera_info = None
        self._cap = None
        self._engine: GestureRecognitionEngine | None = None
        self._worker: GestureWorker | None = None
        self._owns_worker = False
        self._shared_worker_connected = False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._last_dynamic_label = "neutral"
        self._swipe_counts = {"swipe_left": 0, "swipe_right": 0}
        self._hold_started: dict[str, float] = {}
        self._hold_last_fired: dict[str, float] = {}
        self._close_emitted = False
        self._closing_programmatically = False
        self._voice_listener = VoiceCommandListener()
        self._chrome_controller = ChromeController()
        self._spotify_controller = SpotifyController()
        self._voice_processor = VoiceCommandProcessor(
            chrome_controller=self._chrome_controller,
            spotify_controller=self._spotify_controller,
        )
        self._tutorial_wheel_overlay = SpotifyWheelOverlay(config)
        self._voice_overlay = None
        self._voice_queue: queue.Queue[tuple[int, dict]] = queue.Queue()
        self._voice_request_id = 0
        self._voice_listening = False
        self._voice_heard_text = ""
        self._voice_status = "ready"
        self._mouse_tracker = MouseGestureTracker()
        self._spotify_open_hold_seconds = 1.0
        self._spotify_static_cooldown_seconds = 1.5
        self._spotify_play_pause_hold_seconds = 0.5
        self._wheel_hold_seconds = 1.0
        self._wheel_cooldown_seconds = 1.5
        self._tutorial_nav_cooldown_seconds = 1.5
        self._visual_hold_started: dict[str, float] = {}
        self._visual_green_until: dict[str, float] = {}
        self._visual_edge_active: dict[str, bool] = {}
        self._gesture_flash_seconds = 1.0
        self._completion_feedback_until = 0.0
        self._completion_feedback_duration = 2.0
        self._completion_feedback_step = -1
        self._play_pause_ready_for_next = True
        self._swipe_goal_index = 0
        self._nav_swipe_cooldown_until = 0.0
        self._spotify_toggle_count = 0
        self._mouse_stage = "enable"
        self._tutorial_wheel_anchor = None
        self._tutorial_wheel_selected_key: str | None = None
        self._tutorial_wheel_selected_since = 0.0
        self._tutorial_wheel_cursor_offset: tuple[float, float] | None = None
        self._last_spotify_tutorial_action = "-"
        self._last_tutorial_play_pause_text = ""
        self._last_voice_success_text = ""
        self._prime_voice_runtime_async()
        self._practice_steps = (
            _StepDefinition("swipes", "Part 1/6: Right and Left Swipes", "", ""),
            _StepDefinition("spotify_open", "Part 2/6: Open Spotify", "", ""),
            _StepDefinition("play_pause", "Part 3/6: Pause/Play", "", ""),
            _StepDefinition("gesture_wheel", "Part 4/6: Gesture Wheel", "", ""),
            _StepDefinition("mouse_mode", "Part 5/6: Mouse Control", "", ""),
            _StepDefinition("voice_command", "Part 6/6: Voice Command", "", ""),
        )
        self._step_index = 0
        self._completed_steps: set[int] = set()
        self._step_completed = self._step_index in self._completed_steps
        self._show_completion_page = False
        self._build_ui()
        self.apply_theme(config)
        self._reset_for_step()

    def _prime_voice_runtime_async(self) -> None:
        def _worker() -> None:
            try:
                self._voice_listener.prewarm()
            except Exception:
                pass

        threading.Thread(
            target=_worker,
            name="hgr-tutorial-voice-prewarm",
            daemon=True,
        ).start()

    def _build_ui(self) -> None:
        self.setWindowTitle("HGR Tutorial")
        self.setModal(False)
        self.resize(1180, 820)
        self.setMinimumSize(1040, 760)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("tutorialHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(12)

        title_wrap = QVBoxLayout()
        title_wrap.setSpacing(4)
        self.hero_label = QLabel("HGR Tutorial")
        self.hero_label.setObjectName("tutorialHero")
        self.hero_subtitle = QLabel("Practice the main controls one step at a time with live hand tracking.")
        self.hero_subtitle.setObjectName("tutorialSubtitle")
        self.hero_subtitle.setWordWrap(True)
        title_wrap.addWidget(self.hero_label)
        title_wrap.addWidget(self.hero_subtitle)
        header_layout.addLayout(title_wrap, 1)

        self.progress_badge = QLabel("Step 1 of 6")
        self.progress_badge.setObjectName("tutorialBadge")
        header_layout.addWidget(self.progress_badge, 0, Qt.AlignTop)
        root.addWidget(header)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(18, 18, 18, 18)
        body_layout.setSpacing(18)
        root.addWidget(body, 1)

        video_card = QFrame()
        video_card.setObjectName("tutorialCard")
        video_layout = QVBoxLayout(video_card)
        video_layout.setContentsMargins(16, 16, 16, 16)
        video_layout.setSpacing(10)

        self.camera_label = QLabel("Camera: waiting")
        self.camera_label.setObjectName("tutorialMeta")
        video_layout.addWidget(self.camera_label)

        self.video_label = QLabel("The tutorial will show your live hand skeleton here.")
        self.video_label.setObjectName("tutorialVideo")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setWordWrap(True)
        self.video_label.setMinimumSize(480, 360)
        video_layout.addWidget(self.video_label, 1)

        self.gesture_chip = QLabel("Gesture: neutral")
        self.gesture_chip.setObjectName("tutorialChip")
        self.gesture_chip.setAlignment(Qt.AlignCenter)
        video_layout.addWidget(self.gesture_chip, 0, Qt.AlignCenter)
        body_layout.addWidget(video_card, 7)

        info_card = QFrame()
        info_card.setObjectName("tutorialCard")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(18, 18, 18, 18)
        info_layout.setSpacing(12)
        body_layout.addWidget(info_card, 5)

        self.step_title = QLabel("")
        self.step_title.setObjectName("tutorialStepTitle")
        self.step_desc = QLabel("")
        self.step_desc.setObjectName("tutorialStepDesc")
        self.step_desc.setWordWrap(True)
        info_layout.addWidget(self.step_title)
        info_layout.addWidget(self.step_desc)

        self.instruction_box = QLabel("")
        self.instruction_box.setObjectName("tutorialInstructionBox")
        self.instruction_box.setWordWrap(True)
        self.instruction_box.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        info_layout.addWidget(self.instruction_box)

        self.practice_stack = QStackedWidget()
        self.swipe_widget = SwipeInstructionWidget()
        self.wheel_widget = WheelInstructionWidget()
        self.mouse_widget = MousePracticeWidget()
        self.generic_practice = QLabel("")
        self.generic_practice.setObjectName("tutorialPracticeLabel")
        self.generic_practice.setWordWrap(True)
        self.generic_practice.setAlignment(Qt.AlignCenter)
        self.practice_stack.addWidget(self.swipe_widget)
        self.practice_stack.addWidget(self.generic_practice)
        self.practice_stack.addWidget(self.wheel_widget)
        self.practice_stack.addWidget(self.mouse_widget)
        info_layout.addWidget(self.practice_stack, 1)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("tutorialProgress")
        self.progress_label.setWordWrap(True)
        info_layout.addWidget(self.progress_label)

        self.completion_check_label = QLabel("✓")
        self.completion_check_label.setObjectName("tutorialCompletionCheck")
        self.completion_check_label.setAlignment(Qt.AlignCenter)
        self.completion_check_label.hide()
        info_layout.addWidget(self.completion_check_label)

        self.completion_text_label = QLabel("Completed!")
        self.completion_text_label.setObjectName("tutorialCompletionText")
        self.completion_text_label.setAlignment(Qt.AlignCenter)
        self.completion_text_label.hide()
        info_layout.addWidget(self.completion_text_label)

        self.note_label = QLabel("")
        self.note_label.setObjectName("tutorialNote")
        self.note_label.setWordWrap(True)
        info_layout.addWidget(self.note_label)

        self.voice_preview_label = QLabel("")
        self.voice_preview_label.setObjectName("tutorialVoicePreview")
        self.voice_preview_label.setWordWrap(True)
        self.note_label.hide()
        self.voice_preview_label.hide()
        info_layout.addWidget(self.voice_preview_label)
        info_layout.addStretch(1)

        footer = QFrame()
        footer.setObjectName("tutorialFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 14, 18, 14)
        footer_layout.setSpacing(10)

        self.guide_button = QPushButton("Open Gesture Guide")
        self.guide_button.clicked.connect(self._open_guide)
        self.leave_button = QPushButton("Leave Tutorial")
        self.leave_button.clicked.connect(lambda: self._finish_and_close(False))
        self.prev_button = QPushButton("Previous")
        self.prev_button.clicked.connect(self._go_previous)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self._go_next)

        footer_layout.addWidget(self.guide_button)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.leave_button)
        footer_layout.addWidget(self.prev_button)
        footer_layout.addWidget(self.next_button)
        root.addWidget(footer)

    def apply_theme(self, config: AppConfig) -> None:
        self.config = config
        self.swipe_widget.apply_theme(config.accent_color, config.text_color)
        self.wheel_widget.apply_theme(config.accent_color, config.text_color)
        self.mouse_widget.apply_theme(config.accent_color, config.text_color)
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {self.config.surface_color};
                color: {self.config.text_color};
            }}
            QFrame#tutorialHeader {{
                background-color: rgba(255,255,255,0.03);
                border-bottom: 1px solid rgba(29,233,182,0.22);
            }}
            QFrame#tutorialCard, QFrame#tutorialFooter {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(29,233,182,0.22);
                border-radius: 18px;
            }}
            QLabel#tutorialHero {{
                color: {self.config.accent_color};
                font-size: 26px;
                font-weight: 900;
            }}
            QLabel#tutorialSubtitle {{
                color: {self.config.text_color};
                font-size: 14px;
            }}
            QLabel#tutorialBadge {{
                background-color: rgba(9,42,58,0.92);
                color: {self.config.accent_color};
                border-radius: 13px;
                padding: 8px 12px;
                font-weight: 800;
            }}
            QLabel#tutorialMeta, QLabel#tutorialProgress, QLabel#tutorialVoicePreview {{
                color: {self.config.text_color};
                background: transparent;
            }}
            QLabel#tutorialStepTitle {{
                color: {self.config.accent_color};
                font-size: 22px;
                font-weight: 900;
            }}
            QLabel#tutorialStepDesc {{
                color: {self.config.text_color};
                font-size: 14px;
            }}
            QLabel#tutorialInstructionBox {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(29,233,182,0.18);
                border-radius: 16px;
                color: {self.config.text_color};
                padding: 14px 16px;
                font-size: 14px;
            }}
            QLabel#tutorialVideo {{
                background-color: rgba(0,0,0,0.16);
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.08);
                color: {self.config.text_color};
                padding: 14px;
            }}
            QLabel#tutorialChip {{
                background-color: rgba(9,42,58,0.92);
                color: {self.config.accent_color};
                border-radius: 12px;
                padding: 8px 12px;
                font-weight: 800;
            }}
            QLabel#tutorialPracticeLabel {{
                background-color: rgba(255,255,255,0.04);
                border: 1px solid rgba(29,233,182,0.18);
                border-radius: 16px;
                color: {self.config.text_color};
                padding: 18px;
                font-size: 15px;
            }}
            QLabel#tutorialNote {{
                color: {self.config.accent_color};
                font-size: 13px;
                font-weight: 700;
            }}
            QPushButton {{
                background-color: {self.config.primary_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 10px 16px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton:pressed {{
                background-color: {self.config.accent_color};
                color: #001B24;
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton:disabled {{
                color: rgba(229,246,255,0.45);
                border: 1px solid rgba(255,255,255,0.12);
                background-color: rgba(255,255,255,0.05);
            }}
            """
        )

    def _reset_tutorial_progress(self) -> None:
        self._completed_steps.clear()
        self._show_completion_page = False
        self._swipe_counts = {"swipe_left": 0, "swipe_right": 0}
        self._swipe_goal_index = 0
        self._spotify_toggle_count = 0
        self._mouse_stage = "enable"
        self._step_completed = False
        self._completion_feedback_until = 0.0
        self._completion_feedback_step = -1
        self._play_pause_ready_for_next = True

    def configure_session(
        self,
        *,
        camera_index: Optional[int],
        launched_from_settings: bool,
        auto_start_on_done: bool,
    ) -> None:
        self._camera_index = camera_index
        self._launched_from_settings = bool(launched_from_settings)
        self._auto_start_on_done = bool(auto_start_on_done)
        self._step_index = 0
        self._reset_tutorial_progress()
        self._close_emitted = False
        self._closing_programmatically = False
        self._reset_for_step()
        self._start_session()

    def _resolve_parent_worker(self) -> GestureWorker | None:
        parent = self.parent()
        worker = getattr(parent, "_worker", None)
        if worker is not None and getattr(worker, "is_running", False):
            return worker
        return None

    def _connect_worker(self, worker: GestureWorker) -> None:
        if self._shared_worker_connected and self._worker is worker:
            return
        self._disconnect_worker()
        self._worker = worker
        try:
            worker.debug_frame_ready.connect(self._on_worker_debug_frame)
            worker.command_detected.connect(self._on_worker_command_detected)
            worker.camera_selected.connect(self._on_worker_camera_selected)
            worker.running_state_changed.connect(self._on_worker_running_state_changed)
            worker.error_occurred.connect(self._on_worker_error)
            self._shared_worker_connected = True
            self._sync_worker_tutorial_context()
        except Exception:
            self._worker = None
            self._shared_worker_connected = False

    def _disconnect_worker(self) -> None:
        if self._worker is not None and self._shared_worker_connected:
            try:
                self._worker.set_tutorial_context(False)
            except Exception:
                pass
            try:
                self._worker.debug_frame_ready.disconnect(self._on_worker_debug_frame)
            except Exception:
                pass
            try:
                self._worker.command_detected.disconnect(self._on_worker_command_detected)
            except Exception:
                pass
            try:
                self._worker.camera_selected.disconnect(self._on_worker_camera_selected)
            except Exception:
                pass
            try:
                self._worker.running_state_changed.disconnect(self._on_worker_running_state_changed)
            except Exception:
                pass
            try:
                self._worker.error_occurred.disconnect(self._on_worker_error)
            except Exception:
                pass
        self._shared_worker_connected = False
        self._worker = None

    def _sync_worker_tutorial_context(self) -> None:
        if self._worker is None:
            return
        tutorial_key = self._practice_steps[self._step_index].key
        if self._show_completion_page:
            tutorial_key = "voice_command"
        try:
            self._worker.set_tutorial_context(True, tutorial_key)
        except Exception:
            pass

    def _start_session(self) -> None:
        self._stop_session()
        shared_worker = self._resolve_parent_worker()
        if shared_worker is not None:
            self._owns_worker = False
            self._connect_worker(shared_worker)
            camera_info = getattr(shared_worker, "_camera_info", None)
            if camera_info is not None:
                self.camera_label.setText(f"Camera: {camera_info.display_name}")
            else:
                self.camera_label.setText("Camera: shared app session")
            self.video_label.setText("Waiting for the live app frame...")
            self.gesture_chip.setText("Gesture: neutral")
            return

        self._owns_worker = True
        owned_worker = GestureWorker(self.config, camera_index_override=self._camera_index, parent=self)
        self._connect_worker(owned_worker)
        self.camera_label.setText("Camera: starting tutorial runtime...")
        self.video_label.setText("Starting tutorial camera and runtime...")
        self.gesture_chip.setText("Gesture: starting")
        owned_worker.start()

    def _stop_session(self) -> None:
        self._timer.stop()
        owned_worker = self._worker if self._owns_worker else None
        self._disconnect_worker()
        if owned_worker is not None:
            try:
                owned_worker.stop()
            except Exception:
                pass
        if self._voice_overlay is not None:
            self._voice_overlay.hide_overlay()
        self._tutorial_wheel_overlay.hide_overlay()
        self._cap = None
        self._engine = None
        self._camera_info = None
        self._owns_worker = False

    def _voice_overlay_widget(self):
        if self._voice_overlay is None:
            from ...gesture.ui.voice_status_overlay import VoiceStatusOverlay

            self._voice_overlay = VoiceStatusOverlay(self.config)
        return self._voice_overlay

    def _on_worker_camera_selected(self, text: str) -> None:
        self.camera_label.setText(f"Camera: {text}")

    def _on_worker_running_state_changed(self, is_running: bool) -> None:
        if not is_running and self._owns_worker:
            self.video_label.setText("Tutorial runtime stopped.")
            self.gesture_chip.setText("Gesture: runtime stopped")

    def _on_worker_error(self, message: str) -> None:
        self.video_label.setText(str(message or "Tutorial runtime error"))
        self.gesture_chip.setText("Gesture: runtime error")

    def _on_worker_command_detected(self, command: str) -> None:
        text = str(command or "").strip().lower()
        if not text:
            return
        if "spotify" in text:
            self._last_spotify_tutorial_action = text
        if self._practice_steps[self._step_index].key == "voice_command" and "youtube" in text and "chrome" in text:
            self._last_voice_success_text = text

    def _tutorial_nav_from_payload(self, payload: dict, now: float) -> None:
        if payload.get("mouse_mode_enabled"):
            return
        if self._swipe_goal_index < 6 or now < self._nav_swipe_cooldown_until:
            return
        label = str(payload.get("dynamic_label", "neutral") or "neutral")
        if label == "swipe_left" and (self._show_completion_page or self._step_index > 0):
            self._nav_swipe_cooldown_until = now + self._tutorial_nav_cooldown_seconds
            self._visual_green_until["tutorial_nav"] = now + self._gesture_flash_seconds
            self._go_previous()
        elif label == "swipe_right" and (self._show_completion_page or self._step_index < len(self._practice_steps) - 1 or (self._step_index == len(self._practice_steps) - 1 and self._step_completed)):
            self._nav_swipe_cooldown_until = now + self._tutorial_nav_cooldown_seconds
            self._visual_green_until["tutorial_nav"] = now + self._gesture_flash_seconds
            self._go_next()

    def _reset_for_step(self) -> None:
        self._step_completed = True if self._show_completion_page else (self._step_index in self._completed_steps)
        self._hold_started.clear()
        self._hold_last_fired.clear()
        self._visual_hold_started.clear()
        self._visual_edge_active.clear()
        self._last_dynamic_label = "neutral"
        self._voice_listening = False
        self._voice_status = "ready"
        self._voice_heard_text = ""
        self._last_spotify_tutorial_action = "-"
        self._last_tutorial_play_pause_text = ""
        self._last_voice_success_text = ""
        self._tutorial_wheel_overlay.hide_overlay()
        self._tutorial_wheel_anchor = None
        self._tutorial_wheel_selected_key = None
        self._tutorial_wheel_selected_since = 0.0
        self._tutorial_wheel_cursor_offset = None
        while True:
            try:
                self._voice_queue.get_nowait()
            except queue.Empty:
                break
        self._mouse_tracker.reset()
        self.mouse_widget.reset()
        self._play_pause_ready_for_next = True
        if self._voice_overlay is not None:
            self._voice_overlay.hide_overlay()
        self._apply_step_content()
        self._sync_worker_tutorial_context()

    def _apply_step_content(self) -> None:
        step = self._practice_steps[self._step_index]
        if self._show_completion_page:
            self.progress_badge.setText("Tutorial Completed")
            self.step_title.setText("Tutorial Completed!")
            self.step_desc.setText("You finished the guided tutorial.")
            self.instruction_box.setText(
                "Please view the gesture guide for all remaining gestures and functions this app can provide. "
                "If you would like to be done, swipe right to start the app. You can also click the Finish button at the bottom right."
            )
            self.note_label.clear()
            self.voice_preview_label.clear()
            self.practice_stack.setCurrentWidget(self.generic_practice)
            self.generic_practice.setText("Tutorial Completed!\n\nOpen the gesture guide for more controls, or swipe right / click Finish to start the app.")
            self.progress_label.setText("Tutorial completed! Swipe right to start the app.")
            self.prev_button.setEnabled(True)
            self.next_button.setEnabled(True)
            self.next_button.setText("Finish")
            self._update_completion_feedback(time.monotonic())
            return

        self.progress_badge.setText(f"Step {self._step_index + 1} of {len(self._practice_steps)}")
        self.step_title.setText(step.title)

        what_is_map = {
            "swipes": (
                "You can use these gestures to skip a song, go back to previous song and in chrome forward page and back page. "
                "in the tutorial, once you moved on from part 1, you can control the previous and next page with the swipes as well "
                "(next is right swipe and prev is left swipe)"
            ),
            "spotify_open": "you can use this gesture to open the spotify app.",
            "play_pause": "You can use this gesture to pause or play any music or video playing on your computer.",
            "gesture_wheel": "You can use this gesture to open the gesture wheel and choose one of the Spotify controls.",
            "mouse_mode": "You can use this part to learn turning mouse mode on, moving your cursor, and clicking targets.",
            "voice_command": "You can use this gesture to activate voice commands and speak a command like opening youtube on google chrome.",
        }
        instruction_map = {
            "swipes": (
                "How to do it: Start with your right hand opened with palm facing your monitor and more towards the left side. "
                "Then keeping your palm facing the monitor swipe to your right! To use swipe left do the same thing in vice versa. "
                "You can also use the skeleton hands to help guide your positioning.\n\n"
                "To complete this part: Complete three right swipes then three left swipes. "
                "Your hand will turn green when the corrct getsure is detected."
            ),
            "spotify_open": (
                "How to do it: With your right palm facing towards your monitor make a number two by keeping your index and middle "
                "fingers open and spread apart while thre remaining fingers are closed. Hold this for one second.\n\n"
                "To complete this part: Open the spotify app by creating the correct number two gesture. "
                "Your hand will turn green when the corrct getsure is detected."
            ),
            "play_pause": (
                "How to do it: Start with your right palm facing towards the monitor and create a fist.\n\n"
                "To compete this part: Properly triggere pause/play command twice."
            ),
            "gesture_wheel": (
                "How to do it: Use your right hand to make the gesture wheel pose, hold it for one second, then move your hand "
                "toward one slice and hold there until it activates.\n\n"
                "To complete this part: Open the gesture wheel and hold on any slice until it activates."
            ),
            "mouse_mode": (
                "How to do it: Turn mouse mode on with your left hand, then use your right hand to move and click the tutorial circles.\n\n"
                "To complete this part: Turn mouse mode on, click each target in order, then turn mouse mode off."
            ),
            "voice_command": (
                "How to do it: Hold left-hand one to activate voice command and say \"Open youtube on google chrome\".\n\n"
                "To complete this part: Trigger voice command and successfully open youtube on google chrome."
            ),
        }
        self.step_desc.setText(what_is_map.get(step.key, step.description))
        self.instruction_box.setText(instruction_map.get(step.key, step.description))
        self.note_label.clear()
        self.voice_preview_label.clear()

        if step.key == "swipes":
            self.practice_stack.setCurrentWidget(self.swipe_widget)
            self.swipe_widget.set_counts(self._swipe_counts["swipe_left"], self._swipe_counts["swipe_right"])
            status = f"Right: {self._swipe_counts['swipe_right']}/3        Left: {self._swipe_counts['swipe_left']}/3"
            self.progress_label.setText(status if not self._step_completed else "Completed! Swipe right to move on!")
        elif step.key == "gesture_wheel":
            self.practice_stack.setCurrentWidget(self.wheel_widget)
            self.progress_label.setText("Waiting for gesture wheel pose." if not self._step_completed else "Completed! Swipe right to move on!")
        elif step.key == "mouse_mode":
            self.practice_stack.setCurrentWidget(self.mouse_widget)
            if self._step_completed:
                self.progress_label.setText("Completed! Swipe right to move on!")
            elif self._mouse_stage == "enable":
                self.progress_label.setText("Mouse mode off. Turn it on to begin.")
            elif self._mouse_stage == "practice":
                self.progress_label.setText("Mouse mode on. Clear all tutorial targets.")
            else:
                self.progress_label.setText("Targets cleared. Turn mouse mode off to finish.")
        else:
            self.practice_stack.setCurrentWidget(self.generic_practice)
            prompt_map = {
                "spotify_open": "Right-hand two gesture",
                "play_pause": "Right-hand fist gesture",
                "voice_command": "Left-hand one, then say the full command",
            }
            self.generic_practice.setText(prompt_map.get(step.key, step.description))
            if step.key == "play_pause":
                self.progress_label.setText((f"fist detections {self._spotify_toggle_count}/2" if not self._step_completed else "Completed! Swipe right to move on!"))
            elif step.key == "spotify_open":
                self.progress_label.setText("waiting for right-hand two" if not self._step_completed else "Completed! Swipe right to move on!")
            elif step.key == "voice_command":
                self.progress_label.setText("Waiting for left-hand one and the voice command." if not self._step_completed else "Completed! Swipe right to move on!")
            else:
                self.progress_label.setText(step.progress_template)

        self.prev_button.setEnabled(self._step_index > 0)
        self.next_button.setEnabled(self._step_index in self._completed_steps)
        self.next_button.setText("Next")
        self._update_completion_feedback(time.monotonic())

    def _complete_step(self, note: str | None = None) -> None:
        self._step_completed = True
        self._completed_steps.add(self._step_index)
        self.next_button.setEnabled(True)
        self._completion_feedback_until = time.monotonic() + self._completion_feedback_duration
        self._completion_feedback_step = self._step_index
        if note:
            self.progress_label.setText(note)
        else:
            self.progress_label.setText("Completed! Swipe right to move on!")

    def _update_completion_feedback(self, now: float) -> None:
        visible = self._step_completed and not self._show_completion_page
        if not visible:
            self.completion_check_label.hide()
            self.completion_text_label.hide()
            return
        self.completion_check_label.setStyleSheet("color: rgb(29, 233, 182); font-size: 52px; font-weight: 900;")
        self.completion_text_label.setStyleSheet("color: rgb(29, 233, 182); font-size: 18px; font-weight: 800;")
        self.completion_check_label.show()
        self.completion_text_label.show()

    def _go_previous(self) -> None:
        if self._show_completion_page:
            self._show_completion_page = False
            self._reset_for_step()
            return
        if self._step_index <= 0:
            return
        self._step_index -= 1
        self._reset_for_step()

    def _go_next(self) -> None:
        if self._show_completion_page:
            self._finish_and_close(True)
            return
        if not self._step_completed:
            return
        if self._step_index >= len(self._practice_steps) - 1:
            self._show_completion_page = True
            self._reset_for_step()
            return
        self._step_index += 1
        self._reset_for_step()

    def _open_guide(self) -> None:
        self._closing_programmatically = True
        self._stop_session()
        self.gesture_guide_requested.emit(self._launched_from_settings)
        self.close()

    def _finish_and_close(self, completed: bool) -> None:
        if self._close_emitted:
            self.close()
            return
        self._close_emitted = True
        self._closing_programmatically = True
        self._stop_session()
        self.tutorial_closed.emit(bool(completed), self._auto_start_on_done, self._launched_from_settings)
        self.close()

    def _render_frame(self, frame) -> None:
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = frame_rgb.shape
        image = QImage(frame_rgb.data, width, height, channels * width, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        self.video_label.setPixmap(pixmap.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _hold_ready(self, key: str, active: bool, threshold: float, now: float, cooldown: float = 0.55) -> bool:
        if not active:
            self._hold_started.pop(key, None)
            return False
        start = self._hold_started.setdefault(key, now)
        if now - self._hold_last_fired.get(key, -1e9) < cooldown:
            return False
        if now - start >= threshold:
            self._hold_last_fired[key] = now
            self._hold_started.pop(key, None)
            self._visual_green_until[key] = max(self._visual_green_until.get(key, 0.0), now + self._gesture_flash_seconds)
            return True
        return False

    def _gesture_active(self, result, label: str, *, handedness: str | None, min_confidence: float = 0.56) -> bool:
        if not result.found or result.tracked_hand is None:
            return False
        if handedness is not None and str(result.tracked_hand.handedness or "").lower() != handedness.lower():
            return False
        prediction = result.prediction
        return prediction.stable_label == label or (prediction.raw_label == label and prediction.confidence >= min_confidence)

    def _flash_on_edge(self, key: str, active: bool, now: float) -> None:
        previous = self._visual_edge_active.get(key, False)
        if active and not previous:
            self._visual_green_until[key] = max(self._visual_green_until.get(key, 0.0), now + self._gesture_flash_seconds)
        self._visual_edge_active[key] = bool(active)

    def _visual_ready(self, key: str, active: bool, now: float, threshold: float = 0.6) -> bool:
        self._flash_on_edge(key, active, now)
        return now < self._visual_green_until.get(key, 0.0)

    def _tutorial_nav_from_swipe(self, prediction, now: float) -> None:
        if self._mouse_tracker.mode_enabled:
            return
        if self._swipe_goal_index < 6 or now < self._nav_swipe_cooldown_until:
            return
        label = str(getattr(prediction, "dynamic_label", "neutral") or "neutral")
        if label == "swipe_left" and (self._show_completion_page or self._step_index > 0):
            self._nav_swipe_cooldown_until = now + self._tutorial_nav_cooldown_seconds
            self._visual_green_until["tutorial_nav"] = now + self._gesture_flash_seconds
            self._go_previous()
        elif label == "swipe_right" and (self._show_completion_page or self._step_index < len(self._practice_steps) - 1 or (self._step_index == len(self._practice_steps) - 1 and self._step_completed)):
            self._nav_swipe_cooldown_until = now + self._tutorial_nav_cooldown_seconds
            self._visual_green_until["tutorial_nav"] = now + self._gesture_flash_seconds
            self._go_next()

    def _draw_user_skeleton_overlay(self, frame, result, color) -> None:
        if not result.found or result.tracked_hand is None:
            return
        height, width = frame.shape[:2]
        pts = result.tracked_hand.landmarks
        for a, b in HAND_CONNECTIONS:
            pa = (int(pts[a][0] * width), int(pts[a][1] * height))
            pb = (int(pts[b][0] * width), int(pts[b][1] * height))
            cv2.line(frame, pa, pb, color, 3, cv2.LINE_AA)
        for index in range(len(pts)):
            px = int(pts[index][0] * width)
            py = int(pts[index][1] * height)
            cv2.circle(frame, (px, py), 5, color, -1, cv2.LINE_AA)

    def _draw_demo_hand(self, frame, center, pose, *, scale=1.0, tilt_deg=0.0, color=(255,255,255)) -> None:
        cx, cy = center
        theta = math.radians(tilt_deg)
        rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]], dtype=np.float32)

        def tr(pt):
            xy = (rot @ (np.array(pt, dtype=np.float32) * float(scale))).astype(np.float32)
            return int(round(cx + xy[0])), int(round(cy + xy[1]))

        open_map = {
            "open_hand": (1.0, 1.0, 1.0, 1.0, 1.0),
            "one": (0.25, 1.0, 0.2, 0.2, 0.2),
            "two": (0.25, 1.0, 1.0, 0.2, 0.2),
            "fist": (0.2, 0.2, 0.2, 0.2, 0.2),
            "wheel_pose": (1.0, 1.0, 0.2, 0.2, 1.0),
            "left_three": (0.25, 1.0, 1.0, 1.0, 0.2),
        }
        openness = open_map.get(pose, open_map["open_hand"])

        pts = np.zeros((21, 2), dtype=np.float32)
        pts[0] = np.array([0.0, 56.0])
        pts[5] = np.array([-30.0, 10.0])
        pts[9] = np.array([-8.0, 4.0])
        pts[13] = np.array([16.0, 6.0])
        pts[17] = np.array([38.0, 12.0])

        pts[1] = np.array([-18.0, 36.0])
        pts[2] = np.array([-32.0, 26.0])
        thumb_len = 24.0 * openness[0]
        pts[3] = np.array([-42.0 - 6.0 * openness[0], 20.0 - 7.0 * openness[0]])
        pts[4] = np.array([-48.0 - thumb_len, 20.0 - 9.0 * openness[0]]) if openness[0] >= 0.6 else np.array([-26.0, 34.0])

        finger_specs = [
            (5, 6, 7, 8, -30.0, 40.0, 32.0, 24.0, openness[1]),
            (9, 10, 11, 12, -8.0, 46.0, 36.0, 26.0, openness[2]),
            (13, 14, 15, 16, 16.0, 42.0, 32.0, 24.0, openness[3]),
            (17, 18, 19, 20, 38.0, 36.0, 28.0, 20.0, openness[4]),
        ]
        for mcp, pip, dip, tip, x, l1, l2, l3, op in finger_specs:
            pts[pip] = np.array([x, pts[mcp][1] - max(18.0, l1 * max(op, 0.32))])
            if op >= 0.65:
                pts[dip] = np.array([x, pts[pip][1] - l2])
                pts[tip] = np.array([x, pts[dip][1] - l3])
            else:
                pts[dip] = np.array([x + 8.0, pts[pip][1] + 8.0])
                pts[tip] = np.array([x + 18.0, pts[dip][1] + 10.0])

        pts_px = [tr(pt) for pt in pts]
        palm_outline_idx = (0, 5, 9, 13, 17, 0)
        palm_outline = np.array([pts_px[i] for i in palm_outline_idx], dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(frame, [palm_outline], True, color, 2, cv2.LINE_AA)
        for a, b in HAND_CONNECTIONS:
            cv2.line(frame, pts_px[a], pts_px[b], color, 2, cv2.LINE_AA)
        for px, py in pts_px:
            cv2.circle(frame, (px, py), max(2, int(round(3.0 * scale / 1.6))), color, -1, cv2.LINE_AA)
    def _draw_demo_overlay(self, frame, step_key: str) -> None:
        height, width = frame.shape[:2]
        accent = (182, 233, 29)
        white = (245, 250, 255)
        main_center = (int(width * 0.50), int(height * 0.30))
        if step_key == "swipes":
            right_phase = self._swipe_goal_index < 3
            left_center = (int(width * 0.28), int(height * 0.30))
            right_center = (int(width * 0.72), int(height * 0.30))
            if right_phase:
                self._draw_demo_hand(frame, left_center, "open_hand", scale=0.82, tilt_deg=0.0, color=white)
                self._draw_demo_hand(frame, right_center, "open_hand", scale=0.82, tilt_deg=10.0, color=white)
                cv2.arrowedLine(frame, (left_center[0] + 54, left_center[1]), (right_center[0] - 54, right_center[1]), accent, 4, cv2.LINE_AA, tipLength=0.20)
                cv2.putText(frame, "Swipe right", (int(width * 0.42), int(height * 0.50)), cv2.FONT_HERSHEY_SIMPLEX, 0.88, accent, 2, cv2.LINE_AA)
            else:
                self._draw_demo_hand(frame, right_center, "open_hand", scale=0.82, tilt_deg=0.0, color=white)
                self._draw_demo_hand(frame, left_center, "open_hand", scale=0.82, tilt_deg=-10.0, color=white)
                cv2.arrowedLine(frame, (right_center[0] - 54, right_center[1]), (left_center[0] + 54, left_center[1]), accent, 4, cv2.LINE_AA, tipLength=0.20)
                cv2.putText(frame, "Swipe left", (int(width * 0.43), int(height * 0.50)), cv2.FONT_HERSHEY_SIMPLEX, 0.88, accent, 2, cv2.LINE_AA)
        elif step_key == "spotify_open":
            self._draw_demo_hand(frame, main_center, "two", scale=1.00, color=white)
        elif step_key == "play_pause":
            self._draw_demo_hand(frame, main_center, "fist", scale=1.00, color=white)
        elif step_key == "gesture_wheel":
            self._draw_demo_hand(frame, main_center, "wheel_pose", scale=0.96, color=white)
        elif step_key == "mouse_mode":
            self._draw_demo_hand(frame, main_center, "left_three", scale=0.96, color=white)
        elif step_key == "voice_command":
            self._draw_demo_hand(frame, main_center, "one", scale=1.00, color=white)
            cv2.putText(frame, "Voice", (int(width * 0.46), int(height * 0.50)), cv2.FONT_HERSHEY_SIMPLEX, 0.88, accent, 2, cv2.LINE_AA)
    def _drain_voice_queue(self) -> None:
        while True:
            try:
                request_id, payload = self._voice_queue.get_nowait()
            except queue.Empty:
                break
            if request_id != self._voice_request_id:
                continue
            event = str(payload.get("event", ""))
            if event == "status":
                self._voice_status = str(payload.get("status", "ready"))
                command_text = str(payload.get("command_text", "") or "").strip()
                if command_text:
                    self._voice_heard_text = command_text
                if self._voice_status == "listening":
                    self._voice_overlay_widget().show_listening()
                elif self._voice_status == "recognizing":
                    self._voice_overlay_widget().show_processing("Recognizing...")
                elif self._voice_status == "processing":
                    self._voice_overlay_widget().show_processing(
                        "Processing command...",
                        command_text=command_text or self._voice_heard_text,
                    )
            elif event == "result":
                success = bool(payload.get("success", False))
                heard_text = str(payload.get("heard_text", "") or "").strip()
                self._voice_heard_text = heard_text
                self._voice_listening = False
                self._voice_status = "ready"
                if success:
                    self._voice_overlay_widget().show_result("Executing command", command_text=heard_text, duration=1.9)
                else:
                    self._voice_overlay_widget().show_result("Command not understood", command_text=heard_text, duration=1.9)
                normalized = heard_text.lower()
                if self._practice_steps[self._step_index].key == "voice_command" and "youtube" in normalized and "chrome" in normalized:
                    self._complete_step(f"Voice command detected. Part {self._step_index + 1}/6 completed!")

    def _start_voice_practice(self) -> None:
        if self._voice_listening:
            return
        self._voice_listening = True
        self._voice_status = "listening"
        self._voice_heard_text = ""
        self._voice_request_id += 1
        request_id = self._voice_request_id
        self._voice_overlay_widget().show_listening()

        def _status_callback(status: str) -> None:
            self._voice_queue.put((request_id, {"event": "status", "status": status}))

        def _worker() -> None:
            result = self._voice_listener.listen(
                max_seconds=6.0,
                status_callback=_status_callback,
                transcript_mode="command",
            )
            heard_text = result.heard_text
            if heard_text:
                self._voice_queue.put((request_id, {"event": "status", "status": "processing", "command_text": heard_text}))
                execution = self._voice_processor.execute(
                    heard_text,
                    context=VoiceCommandContext(preferred_app="chrome"),
                )
                display_text = execution.display_text or heard_text
            else:
                display_text = heard_text
            self._voice_queue.put(
                (
                    request_id,
                    {
                        "event": "result",
                        "success": bool(result.success),
                        "heard_text": display_text,
                    },
                )
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_worker_debug_frame(self, frame, payload) -> None:
        monotonic_now = time.monotonic()
        current_step_key = self._practice_steps[self._step_index].key
        visual_ready = self._update_step_progress_from_payload(payload, monotonic_now)
        self._tutorial_nav_from_payload(payload, monotonic_now)

        display = frame.copy() if frame is not None else None
        if display is not None:
            result = payload.get("result")
            if result is not None:
                skeleton_color = (80, 235, 120) if visual_ready else (70, 70, 255)
                self._draw_user_skeleton_overlay(display, result, skeleton_color)
            self._draw_demo_overlay(display, current_step_key)
            self._render_frame(display)
        if current_step_key != "gesture_wheel":
            self._tutorial_wheel_overlay.hide_overlay()

        dynamic_label = str(payload.get("dynamic_label", "neutral") or "neutral")
        stable_label = str(payload.get("stable_label", "neutral") or "neutral")
        raw_label = str(payload.get("raw_label", "neutral") or "neutral")
        banner = stable_label if stable_label != "neutral" else raw_label
        if self._practice_steps[self._step_index].key == "mouse_mode" and payload.get("mouse_mode_enabled"):
            self.gesture_chip.setText("Mouse mode active")
        elif dynamic_label != "neutral":
            self.gesture_chip.setText(f"Dynamic: {dynamic_label.replace('_', ' ')}")
        else:
            self.gesture_chip.setText(f"Gesture: {banner}")

        info_lines = payload.get("info_lines") or []
        if info_lines:
            self.camera_label.setText(str(info_lines[0]))
        self._update_completion_feedback(monotonic_now)

    def _update_step_progress_from_payload(self, payload: dict, now: float) -> bool:
        result = payload.get("result")
        if result is None:
            return False

        step = self._practice_steps[self._step_index]
        handedness = str(payload.get("handedness", "") or "").lower()
        dynamic_label = str(payload.get("dynamic_label", "neutral") or "neutral")
        stable_label = str(payload.get("stable_label", "neutral") or "neutral")
        raw_label = str(payload.get("raw_label", "neutral") or "neutral")
        confidence = float(payload.get("confidence", 0.0) or 0.0)

        if step.key == "swipes":
            if self._swipe_goal_index >= 6:
                self._swipe_counts["swipe_right"] = min(3, self._swipe_counts["swipe_right"])
                self._swipe_counts["swipe_left"] = min(3, self._swipe_counts["swipe_left"])
                self.swipe_widget.set_counts(self._swipe_counts["swipe_left"], self._swipe_counts["swipe_right"])
                self.progress_label.setText(
                    f"Right: {self._swipe_counts['swipe_right']}/3        Left: {self._swipe_counts['swipe_left']}/3"
                )
                return now < self._visual_green_until.get("swipes", 0.0)

            expected = "swipe_right" if self._swipe_goal_index < 3 else "swipe_left"
            accepted_swipe = False
            if dynamic_label != self._last_dynamic_label and handedness == "right" and dynamic_label == expected:
                self._swipe_goal_index = min(6, self._swipe_goal_index + 1)
                self._swipe_counts[dynamic_label] = min(3, self._swipe_counts[dynamic_label] + 1)
                self.swipe_widget.set_counts(self._swipe_counts["swipe_left"], self._swipe_counts["swipe_right"])
                self._visual_green_until["swipes"] = max(self._visual_green_until.get("swipes", 0.0), now + self._gesture_flash_seconds)
                accepted_swipe = True
            self._last_dynamic_label = dynamic_label
            self.progress_label.setText(
                f"Right: {self._swipe_counts['swipe_right']}/3        Left: {self._swipe_counts['swipe_left']}/3"
            )
            visual_ready = accepted_swipe or now < self._visual_green_until.get("swipes", 0.0)
            if self._swipe_goal_index >= 6:
                self._complete_step("Both swipes detected! Swipe right to move on!")
            return visual_ready

        if step.key == "spotify_open":
            active = handedness == "right" and (
                stable_label == "two" or (raw_label == "two" and confidence >= 0.56)
            )
            self._flash_on_edge("spotify_open", active, now)
            visual_ready = now < self._visual_green_until.get("spotify_open", 0.0)
            hold_fired = self._hold_ready(
                "tutorial_spotify_open",
                active,
                self._spotify_open_hold_seconds,
                now,
                cooldown=self._spotify_static_cooldown_seconds,
            )
            if hold_fired:
                visual_ready = True
                self._complete_step("Completed! Swipe right to move on!")
            self.progress_label.setText("Detected right hand two!" if active else "waiting for right-hand two")
            return visual_ready

        if step.key == "play_pause":
            active = handedness == "right" and (
                stable_label == "fist" or (raw_label == "fist" and confidence >= 0.52)
            )
            self._flash_on_edge("play_pause", active, now)
            if not active:
                self._play_pause_ready_for_next = True
            visual_ready = now < self._visual_green_until.get("play_pause", 0.0)
            if self._play_pause_ready_for_next and self._hold_ready(
                "tutorial_play_pause_count",
                active,
                self._spotify_play_pause_hold_seconds,
                now,
                cooldown=self._spotify_static_cooldown_seconds,
            ):
                self._spotify_toggle_count = min(2, self._spotify_toggle_count + 1)
                self._play_pause_ready_for_next = False
                visual_ready = True
            self.progress_label.setText(f"fist detections {self._spotify_toggle_count}/2")
            if self._spotify_toggle_count >= 2:
                self._complete_step("Completed! Swipe right to move on!")
            return visual_ready

        if step.key == "gesture_wheel":
            active = handedness == "right" and (
                stable_label in {"wheel_pose", "chrome_wheel_pose"} or (raw_label in {"wheel_pose", "chrome_wheel_pose"} and confidence >= 0.50)
            )
            wheel_visible = bool(payload.get("wheel_visible"))
            self._flash_on_edge("gesture_wheel", active, now)
            visual_ready = now < self._visual_green_until.get("gesture_wheel", 0.0)
            self.progress_label.setText(
                "Gesture wheel detected!" if (active or wheel_visible) else "Waiting for gesture wheel pose."
            )
            if active and not self._step_completed:
                self._visual_green_until["gesture_wheel"] = max(self._visual_green_until.get("gesture_wheel", 0.0), now + self._gesture_flash_seconds)
                visual_ready = True
                self._complete_step("Completed! Swipe right to move on!")
            self._tutorial_wheel_overlay.hide_overlay()
            return visual_ready

        if step.key == "mouse_mode":
            mouse_mode_enabled = bool(payload.get("mouse_mode_enabled"))
            cursor_position = payload.get("mouse_cursor_position")
            left_click = bool(payload.get("mouse_left_click"))
            left_three_active = handedness == "left" and (
                stable_label == "three" or (raw_label == "three" and confidence >= 0.56)
            )
            self.mouse_widget.set_mode_enabled(mouse_mode_enabled)
            self.mouse_widget.set_cursor_position(cursor_position)
            if left_click:
                self.mouse_widget.register_click(cursor_position)
            if self._mouse_stage == "enable":
                self.progress_label.setText(
                    "Detected left-hand three!" if left_three_active else "Mouse mode off. Turn it on to begin."
                )
                self._flash_on_edge("mouse_enable", left_three_active, now)
                visual_ready = now < self._visual_green_until.get("mouse_enable", 0.0)
                if self._hold_ready(
                    "mouse_enable_hold",
                    left_three_active,
                    self._mouse_tracker.toggle_hold_seconds,
                    now,
                    cooldown=self._mouse_tracker.toggle_cooldown_seconds,
                ):
                    visual_ready = True
                if mouse_mode_enabled:
                    self._mouse_stage = "practice"
            elif self._mouse_stage == "practice":
                self.progress_label.setText("Mouse mode on. Clear all tutorial targets.")
                visual_ready = False
                if self.mouse_widget.completed:
                    self._mouse_stage = "disable"
            else:
                self.progress_label.setText(
                    "Detected left-hand three!" if left_three_active else "Targets cleared. Turn mouse mode off to finish."
                )
                self._flash_on_edge("mouse_disable", left_three_active, now)
                visual_ready = now < self._visual_green_until.get("mouse_disable", 0.0)
                if self._hold_ready(
                    "mouse_disable_hold",
                    left_three_active,
                    self._mouse_tracker.toggle_hold_seconds,
                    now,
                    cooldown=self._mouse_tracker.toggle_cooldown_seconds,
                ):
                    visual_ready = True
                if not mouse_mode_enabled and self.mouse_widget.completed:
                    self._complete_step("Completed! Swipe right to move on!")
            return visual_ready

        if step.key == "voice_command":
            left_one_active = handedness == "left" and (
                stable_label == "one" or (raw_label == "one" and confidence >= 0.56)
            )
            voice_listening = bool(payload.get("voice_listening"))
            voice_heard = str(payload.get("voice_heard_text", "") or "").lower()
            voice_control = str(payload.get("voice_control_text", "") or "").lower()
            self._flash_on_edge("voice_command", left_one_active, now)
            visual_ready = now < self._visual_green_until.get("voice_command", 0.0)
            self.progress_label.setText(
                "Detected left-hand one!" if left_one_active or voice_listening else "Waiting for left-hand one and the voice command."
            )
            if (
                left_one_active
                and not voice_listening
                and self._worker is not None
                and self._hold_ready("tutorial_worker_voice_command", True, 0.6, now, cooldown=1.5)
            ):
                try:
                    self._worker._start_voice_command()
                except Exception:
                    pass
                visual_ready = True
            if "youtube" in self._last_voice_success_text and "chrome" in self._last_voice_success_text:
                self._complete_step("Completed! Swipe right to move on!")
            elif "youtube" in voice_heard and "chrome" in voice_heard and ("execut" in voice_control or "chrome open" in voice_control):
                self._complete_step("Completed! Swipe right to move on!")
            return visual_ready or voice_listening

        return False

    def _update_step_progress(self, result, now: float) -> bool:
        step = self._practice_steps[self._step_index]
        handedness = str(result.tracked_hand.handedness or "") if result.found and result.tracked_hand is not None else ""
        dynamic_label = result.prediction.dynamic_label
        visual_ready = False

        if step.key == "swipes":
            expected = "swipe_right" if self._swipe_goal_index < 3 else "swipe_left"
            accepted_swipe = False
            if dynamic_label != self._last_dynamic_label and handedness.lower() == "right" and dynamic_label == expected:
                self._swipe_goal_index += 1
                self._swipe_counts[dynamic_label] += 1
                self.swipe_widget.set_counts(self._swipe_counts["swipe_left"], self._swipe_counts["swipe_right"])
                self._visual_green_until["swipes"] = max(self._visual_green_until.get("swipes", 0.0), now + 1.0)
                accepted_swipe = True
            self._last_dynamic_label = dynamic_label
            target = "Swipe right" if self._swipe_goal_index < 3 else "Swipe left"
            self.progress_label.setText(f"Swipe right {self._swipe_counts['swipe_right']}/3, swipe left {self._swipe_counts['swipe_left']}/3.")
            visual_ready = accepted_swipe or now < self._visual_green_until.get("swipes", 0.0)
            if self._swipe_goal_index >= 6:
                self._complete_step("Completed! Swipe right to move on!")
            return visual_ready

        if step.key == "spotify_open":
            active = bool(result.found and result.tracked_hand is not None and str(result.tracked_hand.handedness or "").lower() == "right" and result.prediction.stable_label == "two")
            visual_ready = now < self._visual_green_until.get("spotify_open", 0.0)
            self.progress_label.setText("Detected right-hand two!" if active else "Waiting for right-hand two.")
            if self._hold_ready("spotify_open", active, self._spotify_open_hold_seconds, now, cooldown=self._spotify_static_cooldown_seconds):
                self._complete_step("Completed! Swipe right to move on!")
                visual_ready = True
            return visual_ready

        if step.key == "play_pause":
            active = bool(result.found and result.tracked_hand is not None and str(result.tracked_hand.handedness or "").lower() == "right" and result.prediction.stable_label == "fist")
            if not active:
                self._play_pause_ready_for_next = True
            visual_ready = now < self._visual_green_until.get("play_pause", 0.0)
            self.progress_label.setText("Detected right-hand fist!" if active else f"Fist detections: {self._spotify_toggle_count}/2.")
            if self._play_pause_ready_for_next and self._hold_ready("play_pause", active, self._spotify_play_pause_hold_seconds, now, cooldown=self._spotify_static_cooldown_seconds):
                self._spotify_toggle_count = min(2, self._spotify_toggle_count + 1)
                self._play_pause_ready_for_next = False
                self.progress_label.setText(f"Fist detections: {self._spotify_toggle_count}/2.")
                if self._spotify_toggle_count >= 2:
                    self._complete_step("Completed! Swipe right to move on!")
                visual_ready = True
            return visual_ready

        if step.key == "gesture_wheel":
            active = bool(
                result.found and result.tracked_hand is not None and str(result.tracked_hand.handedness or "").lower() == "right" and (
                    result.prediction.stable_label == "wheel_pose" or (result.prediction.raw_label == "wheel_pose" and result.prediction.confidence >= 0.56)
                )
            )
            visual_ready = self._visual_ready("gesture_wheel", active, now, self._wheel_hold_seconds)
            self.progress_label.setText("Wheel pose detected!" if active else "Waiting for wheel pose.")
            items = (
                ("add_playlist", "Add Playlist", 90.0),
                ("remove_playlist", "Remove Playlist", 38.57),
                ("add_queue", "Add Queue", 347.14),
                ("remove_queue", "Remove Queue", 295.71),
                ("like", "Add to Liked", 244.29),
                ("remove_liked", "Remove from Liked", 192.86),
                ("shuffle", "Shuffle", 141.43),
            )
            if active:
                if self._tutorial_wheel_anchor is None:
                    self._tutorial_wheel_anchor = result.hand_reading.palm.center.copy()
                    self._tutorial_wheel_selected_key = None
                    self._tutorial_wheel_selected_since = now
                offset = (result.hand_reading.palm.center - self._tutorial_wheel_anchor) / max(result.hand_reading.palm.scale, 1e-6)
                self._tutorial_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
                selection_key = self._tutorial_wheel_selection_key(float(offset[0]), float(offset[1]), items)
                if selection_key != self._tutorial_wheel_selected_key:
                    self._tutorial_wheel_selected_key = selection_key
                    self._tutorial_wheel_selected_since = now
                selected_label = "Move to a slice"
                if self._tutorial_wheel_selected_key is not None:
                    selected_label = self._tutorial_wheel_label(self._tutorial_wheel_selected_key, items)
                self._tutorial_wheel_overlay.set_wheel(
                    items=items,
                    selected_key=self._tutorial_wheel_selected_key,
                    selection_progress=(
                        min(1.0, max(0.0, (now - self._tutorial_wheel_selected_since) / 1.0))
                        if self._tutorial_wheel_selected_key is not None else 0.0
                    ),
                    status_text=selected_label,
                    cursor_offset=self._tutorial_wheel_cursor_offset,
                )
                self._tutorial_wheel_overlay.show_overlay()
                if self._tutorial_wheel_selected_key is not None and (now - self._tutorial_wheel_selected_since) >= 1.0:
                    self._complete_step("Completed! Swipe right to move on!")
            else:
                self._tutorial_wheel_anchor = None
                self._tutorial_wheel_selected_key = None
                self._tutorial_wheel_selected_since = 0.0
                self._tutorial_wheel_cursor_offset = None
                self._tutorial_wheel_overlay.hide_overlay()
            return visual_ready

        if step.key == "mouse_mode":
            if result.found and result.hand_reading is not None and result.tracked_hand is not None:
                update = self._mouse_tracker.update(
                    hand_reading=result.hand_reading,
                    prediction=result.prediction,
                    hand_handedness=result.tracked_hand.handedness,
                    cursor_seed=None,
                    now=now,
                )
            else:
                update = self._mouse_tracker.update(hand_reading=None, prediction=None, hand_handedness=None, cursor_seed=None, now=now)
            self.mouse_widget.set_mode_enabled(update.mode_enabled)
            self.mouse_widget.set_cursor_position(update.cursor_position)
            if update.left_click:
                self.mouse_widget.register_click(update.cursor_position)
            if self._mouse_stage == "enable":
                mouse_enable_active = bool(result.found and result.tracked_hand is not None and str(result.tracked_hand.handedness or "").lower() == "left" and result.prediction.stable_label == "three")
                self.progress_label.setText("Detected left-hand three!" if mouse_enable_active else "Mouse mode off. Turn it on to begin.")
                visual_ready = self._visual_ready("mouse_enable", mouse_enable_active, now, self._mouse_tracker.toggle_hold_seconds)
                if update.mode_enabled:
                    self._mouse_stage = "practice"
            elif self._mouse_stage == "practice":
                self.progress_label.setText("Mouse mode on. Clear all tutorial targets.")
                visual_ready = update.mode_enabled
                if self.mouse_widget.completed:
                    self._mouse_stage = "disable"
            else:
                mouse_disable_active = bool(result.found and result.tracked_hand is not None and str(result.tracked_hand.handedness or "").lower() == "left" and result.prediction.stable_label == "three")
                self.progress_label.setText("Detected left-hand three!" if mouse_disable_active else "Targets cleared. Turn mouse mode off to finish.")
                visual_ready = self._visual_ready("mouse_disable", mouse_disable_active, now, self._mouse_tracker.toggle_hold_seconds)
                if not update.mode_enabled and self.mouse_widget.completed:
                    self._complete_step(f"Mouse mode practice completed. Part {self._step_index + 1}/6 completed!")
            return visual_ready

        if step.key == "voice_command":
            left_one_active = bool(
                result.found and result.tracked_hand is not None
                and str(result.tracked_hand.handedness or "").lower() == "left"
                and result.prediction.stable_label == "one"
            )
            self._flash_on_edge("voice_command", left_one_active, now)
            visual_ready = now < self._visual_green_until.get("voice_command", 0.0)
            self.progress_label.setText("Detected left-hand one!" if left_one_active or self._voice_listening else "Waiting for left-hand one and the voice command.")
            if self._hold_ready("voice_command", left_one_active and not self._voice_listening, 0.6, now, cooldown=1.5):
                if self._worker is not None:
                    try:
                        self._worker._start_voice_command()
                    except Exception:
                        self._start_voice_practice()
                else:
                    self._start_voice_practice()
                visual_ready = True
            return visual_ready or self._voice_listening

        return visual_ready

    def _tutorial_wheel_selection_key(self, dx: float, dy: float, items: tuple[tuple[str, str, float], ...]) -> str | None:
        radius = math.hypot(dx, dy)
        if radius < 0.28 or radius > 1.25:
            return None
        angle = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        slice_span = 360.0 / max(1, len(items))
        half_slice = slice_span / 2.0
        for key, _label, target_angle in items:
            delta = abs((angle - target_angle + 180.0) % 360.0 - 180.0)
            if delta <= max(0.0, half_slice - 1.5):
                return key
        return None

    def _tutorial_wheel_label(self, key: str, items: tuple[tuple[str, str, float], ...]) -> str:
        for item_key, label, _angle in items:
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _tick(self) -> None:
        if self._cap is None or self._engine is None:
            return
        ok, frame = self._cap.read()
        if not ok:
            return
        frame = cv2.flip(frame, 1)
        result = self._engine.process_frame(frame)
        monotonic_now = time.monotonic()
        self._drain_voice_queue()
        visual_ready = self._update_step_progress(result, monotonic_now)
        self._tutorial_nav_from_swipe(result.prediction, monotonic_now)

        display = result.annotated_frame.copy()
        self._draw_demo_overlay(display, self._practice_steps[self._step_index].key)
        self._draw_user_skeleton_overlay(display, result, (80, 235, 120) if visual_ready else (70, 70, 255))
        self._render_frame(display)

        step_key = self._practice_steps[self._step_index].key
        if step_key == "mouse_mode":
            mouse_mode_text = "On" if self._mouse_tracker.mode_enabled else "Off"
            cv2.putText(display, f"Mouse Mode: {mouse_mode_text}", (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.86, (182, 233, 29), 2, cv2.LINE_AA)
        banner = result.prediction.stable_label if result.prediction.stable_label != "neutral" else result.prediction.raw_label
        if step_key == "mouse_mode" and self._mouse_tracker.mode_enabled:
            self.gesture_chip.setText("Mouse mode active")
        elif result.prediction.dynamic_label != "neutral":
            self.gesture_chip.setText(f"Dynamic: {result.prediction.dynamic_label.replace('_', ' ')}")
        else:
            self.gesture_chip.setText(f"Gesture: {banner}")
        if self._voice_overlay is not None:
            self._voice_overlay.tick(monotonic_now)
        self._update_completion_feedback(monotonic_now)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._stop_session()
        if not self._closing_programmatically and not self._close_emitted:
            self._close_emitted = True
            self.tutorial_closed.emit(False, self._auto_start_on_done, self._launched_from_settings)
        super().closeEvent(event)
