from __future__ import annotations

import ctypes
import csv
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, QEvent, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QColor, QPainter, QPainterPath, QPen, QCursor, QPixmap, QGuiApplication, QImage
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    _HAS_QT_MEDIA = True
except Exception:
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None
    _HAS_QT_MEDIA = False
from ctypes import wintypes

from ..actions.system_actions import SystemActions
from ..camera.camera_utils import CameraInfo, list_available_cameras, request_camera_access_main_thread
from ...config.app_config import (
    ORIGINAL_ACCENT_COLOR,
    ORIGINAL_HELLO_FONT_SIZE,
    ORIGINAL_PRIMARY_COLOR,
    ORIGINAL_SURFACE_COLOR,
    ORIGINAL_TEXT_COLOR,
    AppConfig,
    CURRENT_TUTORIAL_PROMPT_VERSION,
    SAVE_LOCATION_LABELS,
    SAVE_LOCATION_OUTPUT_ORDER,
    SAVE_NAME_DEFAULTS,
    configured_save_directory,
    configured_save_name,
    save_config,
    save_location_config_field,
    save_name_config_field,
)
from ...debug.debug_window import DebugWindow as StandaloneDebugWindow
from ...debug.voice_command_listener import list_input_microphones
from ...voice.save_prompt import SavePromptProcessor
from ..integration.noop_engine import GestureWorker
from ..overlays.overlay import HelloOverlay, ScreenDrawOverlay, DrawingSettingsDialog, CountdownOverlay, CaptureRegionOverlay, RecordingIndicatorOverlay
from .mini_live_viewer import MiniLiveViewer
from .live_view_window import LiveViewWindow
from .tutorial_window import TutorialWindow


SECTION_INSTRUCTIONS = 0
SECTION_GESTURES = 1
SECTION_COLORS = 2
SECTION_CAMERA = 3
SECTION_MICROPHONE = 4
SECTION_SAVE_LOCATIONS = 5
SECTION_TUTORIAL = 6



def _with_alpha(color: QColor, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


if sys.platform.startswith("win"):
    WM_NCHITTEST = 0x0084
    HTLEFT = 10
    HTRIGHT = 11
    HTTOP = 12
    HTTOPLEFT = 13
    HTTOPRIGHT = 14
    HTBOTTOM = 15
    HTBOTTOMLEFT = 16
    HTBOTTOMRIGHT = 17

    class _NativePoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _NativeMessage(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", _NativePoint),
        ]





class WindowControlButton(QAbstractButton):
    def __init__(self, kind: str, title_bar: "TitleBar"):
        super().__init__(title_bar)
        self.kind = kind
        self.title_bar = title_bar
        self._hovered = False
        self._pressed = False
        self.setFixedSize(22, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent; border: none;")

    def set_hovered(self, hovered: bool) -> None:
        if self._hovered != hovered:
            self._hovered = hovered
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._pressed = False
        self.set_hovered(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def _hover_fill(self) -> QColor:
        if self.kind == "close":
            return QColor(220, 68, 68, 235 if self._pressed else 205)
        base = QColor(self.title_bar.parent_window.config.primary_color)
        if self._pressed:
            return _with_alpha(base.lighter(134), 235)
        return _with_alpha(base.lighter(124), 210)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        hover_rect = self.rect().adjusted(1, 1, -1, -1)
        icon_rect = self.rect().adjusted(4, 2, -4, -2)

        if self._hovered or self._pressed:
            path = QPainterPath()
            path.addRoundedRect(hover_rect, 6, 6)
            painter.fillPath(path, self._hover_fill())

        pen = QPen(QColor("#F4FAFF"))
        pen.setWidthF(1.9 if self.kind != "close" else 1.95)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        cx = icon_rect.center().x()
        cy = icon_rect.center().y()

        if self.kind == "min":
            painter.drawLine(cx - 4, cy, cx + 4, cy)
        elif self.kind == "max":
            parent = self.title_bar.parent_window
            if parent.is_custom_maximized:
                painter.drawRect(QRect(cx - 4, cy - 1, 7, 6))
                painter.drawRect(QRect(cx - 1, cy - 4, 7, 6))
            else:
                painter.drawRect(QRect(cx - 4, cy - 4, 8, 8))
        elif self.kind == "close":
            painter.drawLine(cx - 3, cy - 3, cx + 3, cy + 3)
            painter.drawLine(cx + 3, cy - 3, cx - 3, cy + 3)


class TitleBar(QFrame):

    def __init__(self, parent_window: "MainWindow"):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self._drag_offset: Optional[QPoint] = None
        self.setObjectName("titleBar")
        self.setMinimumHeight(36)
        self.setMaximumHeight(36)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(30)
        self._hover_timer.timeout.connect(self._sync_control_hover_state)
        self._hover_timer.start()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(0)
        layout.addStretch(1)

        controls = QWidget(self)
        self.controls = controls
        controls.setStyleSheet("background: transparent;")
        controls.setMouseTracking(True)
        controls.setAttribute(Qt.WA_Hover, True)
        control_layout = QHBoxLayout(controls)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(2)

        self.min_button = WindowControlButton("min", self)
        self.max_button = WindowControlButton("max", self)
        self.close_button = WindowControlButton("close", self)

        self.min_button.clicked.connect(self.parent_window.showMinimized)
        self.max_button.clicked.connect(self.parent_window.toggle_max_restore)
        self.close_button.clicked.connect(self.parent_window.close)

        for widget in (self, self.controls, self.min_button, self.max_button, self.close_button):
            widget.installEventFilter(self)

        control_layout.addWidget(self.min_button)
        control_layout.addWidget(self.max_button)
        control_layout.addWidget(self.close_button)
        layout.addWidget(controls, 0, Qt.AlignRight)

    def refresh(self) -> None:
        self.update()
        self.min_button.update()
        self.max_button.update()
        self.close_button.update()
        QTimer.singleShot(0, self._sync_control_hover_state)

    def _sync_control_hover_state(self) -> None:
        cursor_pos = QCursor.pos()
        for button in (self.min_button, self.max_button, self.close_button):
            hovered = button.isVisible() and button.rect().contains(button.mapFromGlobal(cursor_pos))
            button.set_hovered(hovered)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj in (self, self.controls, self.min_button, self.max_button, self.close_button):
            if event.type() in (
                QEvent.Enter,
                QEvent.Leave,
                QEvent.MouseMove,
                QEvent.HoverMove,
                QEvent.MouseButtonPress,
                QEvent.MouseButtonRelease,
                QEvent.Show,
                QEvent.Resize,
            ):
                QTimer.singleShot(0, self._sync_control_hover_state)
        return super().eventFilter(obj, event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.min_button.set_hovered(False)
        self.max_button.set_hovered(False)
        self.close_button.set_hovered(False)
        super().leaveEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_control_hover_state)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton and not self.parent_window.is_custom_maximized:
            self.parent_window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.parent_window.toggle_max_restore()
            event.accept()


class SettingsNavButton(QPushButton):
    def __init__(self, text: str, page_index: int, parent_window: "MainWindow"):
        super().__init__(text)
        self.page_index = page_index
        self.parent_window = parent_window
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("settingsNavButton")
        self.clicked.connect(lambda: self.parent_window.show_settings_section(self.page_index))


class ColorPickerButton(QPushButton):
    def __init__(self, label: str, color: str, callback):
        super().__init__(label)
        self._color = color
        self._callback = callback
        self.setObjectName("colorPickerButton")
        self.clicked.connect(self._pick_color)
        self._refresh_style()

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self.window(), "Pick Color")
        if color.isValid():
            self._color = color.name()
            self._refresh_style()
            self._callback(self._color)

    def set_color(self, color: str) -> None:
        self._color = color
        self._refresh_style()

    def _refresh_style(self) -> None:
        text_color = "#001B24" if QColor(self._color).lightness() > 170 else "#F4FAFF"
        self.setStyleSheet(
            f"""
            QPushButton#colorPickerButton {{
                background-color: {self._color};
                color: {text_color};
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 800;
                text-align: center;
            }}
            QPushButton#colorPickerButton:hover {{
                border: 1px solid rgba(255,255,255,0.35);
            }}
            """
        )



class StartTutorialDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        if getattr(self.config, "tutorial_prompt_version", 0) < CURRENT_TUTORIAL_PROMPT_VERSION:
            self.config.show_start_instructions_prompt = True
            self.config.tutorial_prompt_version = CURRENT_TUTORIAL_PROMPT_VERSION
            save_config(self.config)
        self.choice: Optional[str] = None
        self.setModal(True)
        self.setWindowTitle("HGR App")
        self.setMinimumWidth(440)
        self.setObjectName("startTutorialDialog")
        self._build_ui()
        self._apply_theme()

    @property
    def do_not_show_again(self) -> bool:
        return self.do_not_show_checkbox.isChecked()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        title = QLabel("Would you like to go through the tutorial?")
        title.setObjectName("startDialogTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        subtitle = QLabel(
            "Yes opens the six-part guided tutorial. No skips straight to starting the app."
        )
        subtitle.setObjectName("startDialogSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self.do_not_show_checkbox = QCheckBox("Please don't show this message again")
        self.do_not_show_checkbox.setObjectName("startDialogCheckbox")
        root.addWidget(self.do_not_show_checkbox)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.no_button = QPushButton("No")
        self.no_button.setObjectName("startDialogButton")
        self.no_button.clicked.connect(self._choose_start)

        self.yes_button = QPushButton("Yes")
        self.yes_button.setObjectName("startDialogButton")
        self.yes_button.clicked.connect(self._choose_tutorial)
        self.yes_button.setDefault(True)

        button_row.addWidget(self.no_button)
        button_row.addWidget(self.yes_button)
        root.addLayout(button_row)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QDialog#startTutorialDialog {{
                background-color: {self.config.surface_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.30);
            }}
            QLabel#startDialogTitle {{
                color: {self.config.accent_color};
                font-size: 22px;
                font-weight: 900;
            }}
            QLabel#startDialogSubtitle {{
                color: {self.config.text_color};
                font-size: 14px;
            }}
            QCheckBox#startDialogCheckbox {{
                color: {self.config.text_color};
                spacing: 10px;
                font-size: 14px;
            }}
            QCheckBox#startDialogCheckbox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid rgba(255,255,255,0.35);
                background: rgba(255,255,255,0.05);
            }}
            QCheckBox#startDialogCheckbox::indicator:checked {{
                background: {self.config.accent_color};
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton#startDialogButton {{
                background-color: {self.config.primary_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 10px 18px;
                min-width: 86px;
                font-weight: 800;
            }}
            QPushButton#startDialogButton:hover {{
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton#startDialogButton:pressed {{
                background-color: {self.config.accent_color};
                color: #001B24;
            }}
            """
        )

    def _choose_tutorial(self) -> None:
        self.choice = "tutorial"
        self.accept()

    def _choose_start(self) -> None:
        self.choice = "start"
        self.accept()


class CameraSelectionDialog(QDialog):
    def __init__(self, config: AppConfig, cameras: list[CameraInfo], prompt_text: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.cameras = cameras
        self.selected_camera_index: Optional[int] = None
        self.setModal(True)
        self.setWindowTitle("Choose Camera")
        self.setMinimumWidth(460)
        self.setObjectName("cameraSelectionDialog")
        self._build_ui(prompt_text)
        self._apply_theme()

    @property
    def remember_choice(self) -> bool:
        return self.remember_checkbox.isChecked()

    def _build_ui(self, prompt_text: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        title = QLabel("Choose the camera HGR App should use")
        title.setObjectName("cameraDialogTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        subtitle = QLabel(prompt_text)
        subtitle.setObjectName("cameraDialogSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self.camera_combo = QComboBox()
        self.camera_combo.setObjectName("cameraDialogCombo")
        for camera in self.cameras:
            self.camera_combo.addItem(camera.display_name, camera.index)
        root.addWidget(self.camera_combo)

        self.remember_checkbox = QCheckBox("Remember this camera for next time")
        self.remember_checkbox.setObjectName("cameraDialogCheckbox")
        root.addWidget(self.remember_checkbox)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("cameraDialogButton")
        cancel_button.clicked.connect(self.reject)
        use_button = QPushButton("Use Camera")
        use_button.setObjectName("cameraDialogButton")
        use_button.setDefault(True)
        use_button.clicked.connect(self._accept_selection)
        row.addWidget(cancel_button)
        row.addWidget(use_button)
        root.addLayout(row)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QDialog#cameraSelectionDialog {{
                background-color: {self.config.surface_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.30);
            }}
            QLabel#cameraDialogTitle {{
                color: {self.config.accent_color};
                font-size: 22px;
                font-weight: 900;
            }}
            QLabel#cameraDialogSubtitle {{
                color: {self.config.text_color};
                font-size: 14px;
            }}
            QComboBox#cameraDialogCombo {{
                background-color: rgba(255,255,255,0.06);
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 10px 12px;
                min-height: 22px;
            }}
            QCheckBox#cameraDialogCheckbox {{
                color: {self.config.text_color};
                spacing: 10px;
                font-size: 14px;
            }}
            QCheckBox#cameraDialogCheckbox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid rgba(255,255,255,0.35);
                background: rgba(255,255,255,0.05);
            }}
            QCheckBox#cameraDialogCheckbox::indicator:checked {{
                background: {self.config.accent_color};
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton#cameraDialogButton {{
                background-color: {self.config.primary_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 10px 18px;
                min-width: 96px;
                font-weight: 800;
            }}
            QPushButton#cameraDialogButton:hover {{
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton#cameraDialogButton:pressed {{
                background-color: {self.config.accent_color};
                color: #001B24;
            }}
            """
        )

    def _accept_selection(self) -> None:
        self.selected_camera_index = self.camera_combo.currentData()
        self.accept()


class GestureSketchWidget(QWidget):
    def __init__(self, gesture_key: str, parent=None):
        super().__init__(parent)
        self.gesture_key = gesture_key
        self.setMinimumSize(210, 210)
        self.setMaximumSize(210, 210)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(10, 28, 39, 210))
        painter.drawRoundedRect(rect, 18, 18)

        guide_pen = QPen(QColor(88, 227, 255, 46))
        guide_pen.setWidthF(1.2)
        painter.setPen(guide_pen)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 18, 18)

        pen = QPen(QColor("#F4FBFF"))
        pen.setWidthF(3.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        cx = rect.center().x()
        base_y = rect.bottom() - 22
        palm_top = rect.top() + 56
        palm_w = 56
        palm_h = 46
        palm_rect = QRect(cx - palm_w // 2, palm_top, palm_w, palm_h)
        painter.drawRoundedRect(palm_rect, 14, 14)
        painter.drawLine(cx - 12, palm_rect.bottom(), cx - 20, base_y)
        painter.drawLine(cx + 12, palm_rect.bottom(), cx + 20, base_y)
        painter.drawLine(cx, palm_rect.bottom(), cx, base_y - 6)

        finger_x_positions = [-18, -6, 10, 24]
        if self.gesture_key == "open_hand":
            finger_lengths = [44, 48, 44, 38]
        elif self.gesture_key == "fist":
            finger_lengths = [14, 14, 14, 14]
        elif self.gesture_key in {"one", "voice_one"}:
            finger_lengths = [48, 14, 14, 14]
        elif self.gesture_key in {"two", "volume_pose"}:
            finger_lengths = [46, 44, 14, 14]
        elif self.gesture_key in {"three", "left_three"}:
            finger_lengths = [46, 44, 40, 14]
        elif self.gesture_key in {"wheel_pose", "chrome_wheel_pose", "mute"}:
            finger_lengths = [44, 14, 14, 40]
        else:
            finger_lengths = [44, 44, 44, 44]

        for offset_x, length in zip(finger_x_positions, finger_lengths):
            x = cx + offset_x
            is_open = length >= 22
            finger_pen = QPen(QColor("#58E3FF") if is_open else QColor(190, 215, 228, 180))
            finger_pen.setWidthF(3.2 if is_open else 2.8)
            finger_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(finger_pen)
            joint_top = palm_rect.top() + 2
            tip_y = palm_rect.top() - length

            if is_open:
                painter.drawLine(x, joint_top, x, tip_y)
                painter.drawEllipse(QRect(x - 2, tip_y - 2, 4, 4))
                mid_y = joint_top - int(length * 0.45)
                painter.drawEllipse(QRect(x - 1, mid_y - 1, 3, 3))
            else:
                curl_y = palm_rect.top() - 6
                painter.drawLine(x, joint_top, x - 2, curl_y)
                painter.drawLine(x - 2, curl_y, x + 5, curl_y + 7)
                painter.drawEllipse(QRect(x + 3, curl_y + 5, 4, 4))

        thumb_start_x = palm_rect.left() + 8
        thumb_start_y = palm_rect.top() + 20
        if self.gesture_key == "open_hand":
            thumb_end = (thumb_start_x - 26, thumb_start_y - 10)
            thumb_open = True
        elif self.gesture_key in {"mute"}:
            thumb_end = (thumb_start_x - 26, thumb_start_y - 4)
            thumb_open = True
        elif self.gesture_key in {"wheel_pose"}:
            thumb_end = (thumb_start_x - 24, thumb_start_y - 2)
            thumb_open = True
        elif self.gesture_key in {"chrome_wheel_pose"}:
            thumb_end = (thumb_start_x - 14, thumb_start_y + 8)
            thumb_open = False
        else:
            thumb_end = (thumb_start_x - 12, thumb_start_y + 10)
            thumb_open = False
        thumb_pen = QPen(QColor("#58E3FF") if thumb_open else QColor(190, 215, 228, 180))
        thumb_pen.setWidthF(3.0 if thumb_open else 2.8)
        thumb_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(thumb_pen)
        painter.drawLine(thumb_start_x, thumb_start_y, thumb_end[0], thumb_end[1])


class GestureMediaWidget(QFrame):
    def __init__(self, *, image_name: str | None = None, video_name: str | None = None, gesture_key: str = "open_hand", parent=None):
        super().__init__(parent)
        self._gesture_key = gesture_key
        self._image_name = image_name
        self._video_name = video_name
        self._loop_timer: QTimer | None = None
        self._player = None
        self._audio = None
        self._video_widget = None
        self._image_label = None
        self._fallback = None

        self.setObjectName("gestureMediaWidget")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMaximumWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        media_path = self._resolve_media_path()
        suffix = media_path.suffix.lower() if media_path is not None else ""
        if media_path is not None and suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            self.setFixedSize(220, 220)
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setFixedSize(220, 220)
            label.setStyleSheet("background: rgba(10, 28, 39, 0.72); border-radius: 14px;")
            pixmap = QPixmap(str(media_path))
            if not pixmap.isNull():
                label.setPixmap(pixmap.scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                label.setText(media_path.name)
            self._image_label = label
            layout.addWidget(label)
        elif media_path is not None and suffix in {".mp4", ".mov", ".m4v", ".avi"} and _HAS_QT_MEDIA:
            video_width = 220
            video_height = 124
            self.setFixedSize(video_width, video_height)
            video_widget = QVideoWidget()
            video_widget.setFixedSize(video_width, video_height)
            video_widget.setStyleSheet("background: rgba(10, 28, 39, 0.72); border-radius: 14px;")
            layout.addWidget(video_widget)
            self._video_widget = video_widget

            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            try:
                self._audio.setMuted(True)
            except Exception:
                try:
                    self._audio.setVolume(0.0)
                except Exception:
                    pass
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(video_widget)
            self._player.setSource(QUrl.fromLocalFile(str(media_path)))
            self._player.mediaStatusChanged.connect(self._handle_media_status)

            self._loop_timer = QTimer(self)
            self._loop_timer.setSingleShot(True)
            self._loop_timer.timeout.connect(self._restart_video)
            self._player.play()
        else:
            self.setFixedSize(220, 220)
            fallback = GestureSketchWidget(gesture_key)
            fallback.setFixedSize(220, 220)
            self._fallback = fallback
            layout.addWidget(fallback, 0, Qt.AlignCenter)

    def _resolve_media_path(self) -> Path | None:
        root = Path(__file__).resolve().parents[4] / "GestureGuide"
        candidate_name = self._image_name or self._video_name
        if not candidate_name:
            return None
        candidate = root / candidate_name
        return candidate if candidate.exists() else None

    def _handle_media_status(self, status) -> None:
        if self._player is None or self._loop_timer is None:
            return
        status_name = getattr(status, "name", str(status))
        if "EndOfMedia" in status_name:
            self._restart_video()

    def _restart_video(self) -> None:
        if self._player is None:
            return
        self._player.setPosition(0)
        self._player.play()

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._player is not None:
            self._player.pause()
        if self._loop_timer is not None:
            self._loop_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._player is not None:
            self._player.play()


class GestureGuideCard(QFrame):
    def __init__(
        self,
        *,
        title: str,
        action: str,
        how_to: str,
        gesture_key: str,
        image_name: str | None = None,
        video_name: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("innerCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(16)

        media = GestureMediaWidget(image_name=image_name, video_name=video_name, gesture_key=gesture_key)
        layout.addWidget(media, 0, Qt.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("gestureCardTitle")
        title_label.setWordWrap(True)

        action_label = QLabel(f"Action: {action}")
        action_label.setObjectName("gestureCardSubtitle")
        action_label.setWordWrap(True)

        how_header = QLabel("How to do it")
        how_header.setObjectName("gestureCardSubtitle")

        detail_label = QLabel(how_to)
        detail_label.setObjectName("gestureCardBody")
        detail_label.setWordWrap(True)
        detail_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        detail_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        desc_scroll = QScrollArea()
        desc_scroll.setFrameShape(QFrame.NoFrame)
        desc_scroll.setWidgetResizable(True)
        desc_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        desc_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        desc_scroll.setMaximumHeight(90)
        desc_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        desc_scroll.setWidget(detail_label)
        desc_scroll.setStyleSheet("background: transparent; border: none;")

        text_layout.addWidget(title_label)
        text_layout.addWidget(action_label)
        text_layout.addSpacing(2)
        text_layout.addWidget(how_header)
        text_layout.addWidget(desc_scroll)
        text_layout.addStretch(1)
        layout.addLayout(text_layout, 1)


class GestureGuideSection(QFrame):
    def __init__(self, title: str, cards: list[GestureGuideCard], parent=None):
        super().__init__(parent)
        self.setObjectName("gestureGuideSection")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        self.header_button = QPushButton(f"▶  {title}")
        self.header_button.setObjectName("gestureGuideSectionButton")
        self.header_button.setCheckable(True)
        self.header_button.setChecked(False)
        self.header_button.clicked.connect(self._toggle_expanded)
        outer.addWidget(self.header_button)

        self.content = QWidget()
        self.content.setVisible(False)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        for card in cards:
            content_layout.addWidget(card)
        outer.addWidget(self.content)

    def _toggle_expanded(self, checked: bool) -> None:
        self.content.setVisible(bool(checked))
        self.header_button.setText(f"{'▼' if checked else '▶'}  {self.header_button.text()[3:]}")


class CaptureMonitorDialog(QWidget):
    selection_made = Signal(QRect)
    canceled = Signal()

    def __init__(self, config: AppConfig, action_label: str, options: list[tuple[str, QRect]], parent=None):
        super().__init__(None)
        self.config = config
        self.selected_region: QRect | None = None
        self._cursor_global: QPoint | None = None
        self._last_left_down = False
        self._last_right_down = False
        self._completed = False
        self.setWindowTitle('Choose Monitor')
        self.setObjectName('captureMonitorDialog')
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)

        self._panel = _CursorHostPanel(self)
        self._panel.setObjectName('captureMonitorDialogPanel')
        self._panel.setMinimumWidth(440)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(22, 20, 22, 18)
        panel_layout.setSpacing(12)

        title = QLabel(f'Choose which monitor to {action_label}')
        title.setObjectName('captureMonitorDialogTitle')
        title.setWordWrap(True)
        panel_layout.addWidget(title)

        subtitle = QLabel("Pick a display below. 'All Monitors' captures the full desktop across every display.")
        subtitle.setObjectName('captureMonitorDialogSubtitle')
        subtitle.setWordWrap(True)
        panel_layout.addWidget(subtitle)

        self._buttons: list[QPushButton] = []
        for label, region in options:
            button = QPushButton(label)
            button.setObjectName('captureMonitorDialogButton')
            button.clicked.connect(lambda _checked=False, region=QRect(region): self._choose(region))
            panel_layout.addWidget(button)
            self._buttons.append(button)

        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        cancel_button = QPushButton('Cancel')
        cancel_button.setObjectName('captureMonitorDialogButton')
        cancel_button.clicked.connect(self._cancel)
        cancel_row.addWidget(cancel_button)
        panel_layout.addLayout(cancel_row)
        self._buttons.append(cancel_button)

        row.addWidget(self._panel, 0)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self.setStyleSheet(
            f"""
            QWidget#captureMonitorDialog {{
                background-color: rgba(2, 10, 18, 96);
            }}
            QFrame#captureMonitorDialogPanel {{
                background-color: {self.config.surface_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.30);
                border-radius: 18px;
            }}
            QLabel#captureMonitorDialogTitle {{
                color: {self.config.accent_color};
                font-size: 22px;
                font-weight: 900;
            }}
            QLabel#captureMonitorDialogSubtitle {{
                color: {self.config.text_color};
                font-size: 14px;
            }}
            QPushButton#captureMonitorDialogButton {{
                background-color: {self.config.primary_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 10px 18px;
                font-weight: 800;
                text-align: left;
            }}
            QPushButton#captureMonitorDialogButton:hover {{
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton#captureMonitorDialogButton:pressed {{
                background-color: {self.config.accent_color};
                color: #001B24;
            }}
            """
        )

    def _screens_union_geometry(self) -> QRect:
        screens = [screen.geometry() for screen in QGuiApplication.screens()]
        if not screens:
            return QRect(0, 0, 1280, 720)
        union = QRect(screens[0])
        for geo in screens[1:]:
            union = union.united(geo)
        return union

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setGeometry(self._screens_union_geometry())
        if self._cursor_global is None:
            geo = self.geometry()
            self._cursor_global = QPoint(geo.center().x(), geo.center().y())
        self.raise_()
        self._panel.refresh_cursor()
        self.update()

    def _update_cursor_from_global(self, global_point: QPoint) -> None:
        geo = self.geometry()
        gx = max(geo.left() + 10, min(geo.right() - 10, int(global_point.x())))
        gy = max(geo.top() + 10, min(geo.bottom() - 10, int(global_point.y())))
        self._cursor_global = QPoint(gx, gy)
        self._panel.refresh_cursor()
        self.update()

    def update_hand_control(self, global_point, *, left_down: bool, right_down: bool) -> None:
        if not self.isVisible():
            return
        if isinstance(global_point, QPoint):
            self._update_cursor_from_global(global_point)
        self._process_hand_clicks(left_down=left_down, right_down=right_down)

    def _process_hand_clicks(self, *, left_down: bool, right_down: bool) -> None:
        if right_down and not self._last_right_down:
            self._last_right_down = True
            self._last_left_down = bool(left_down)
            self._cancel()
            return
        self._last_right_down = bool(right_down)
        if left_down and not self._last_left_down and self._cursor_global is not None:
            local = self.mapFromGlobal(self._cursor_global)
            widget = self.childAt(local)
            while widget is not None and not isinstance(widget, QAbstractButton):
                widget = widget.parentWidget()
            if isinstance(widget, QAbstractButton) and widget.isEnabled():
                widget.click()
                self._last_left_down = True
                self.update()
                return
        self._last_left_down = bool(left_down)
        self.update()

    def handle_debug_frame(self, frame, info) -> None:
        if not self.isVisible() or self._completed or not isinstance(info, dict):
            return

        union_geo = self.geometry()
        global_point = None
        capture_cursor = info.get("utility_capture_cursor_norm")
        if isinstance(capture_cursor, (tuple, list)) and len(capture_cursor) >= 2:
            try:
                cx = max(0.0, min(1.0, float(capture_cursor[0])))
                cy = max(0.0, min(1.0, float(capture_cursor[1])))
                global_point = QPoint(
                    int(round(union_geo.left() + cx * max(union_geo.width() - 1, 1))),
                    int(round(union_geo.top() + cy * max(union_geo.height() - 1, 1))),
                )
            except Exception:
                global_point = None

        left_down = bool(info.get("utility_capture_left_down", False))
        right_down = bool(info.get("utility_capture_right_down", False))

        if global_point is None or (not left_down and not right_down):
            result = info.get("result")
            hand_reading = getattr(result, "hand_reading", None) if result is not None else None
            tracked_hand = getattr(result, "tracked_hand", None) if result is not None else None
            handedness = str(getattr(tracked_hand, "handedness", "") or "")
            if hand_reading is not None and handedness == "Right":
                try:
                    palm_center = getattr(hand_reading.palm, "center", None)
                    if palm_center is not None and len(palm_center) >= 2:
                        px = max(0.0, min(1.0, float(palm_center[0])))
                        py = max(0.0, min(1.0, float(palm_center[1])))
                        global_point = QPoint(
                            int(round(union_geo.left() + px * max(union_geo.width() - 1, 1))),
                            int(round(union_geo.top() + py * max(union_geo.height() - 1, 1))),
                        )
                    fingers = getattr(hand_reading, "fingers", {})
                    def _finger_down(finger) -> bool:
                        if finger is None:
                            return False
                        openness = float(getattr(finger, "openness", 0.0) or 0.0)
                        curl = float(getattr(finger, "curl", 0.0) or 0.0)
                        state = str(getattr(finger, "state", "") or "")
                        return state in {"closed", "mostly_curled"} or openness <= 0.42 or curl >= 0.52
                    if not left_down and not right_down:
                        index_down = _finger_down(fingers.get("index"))
                        middle_down = _finger_down(fingers.get("middle"))
                        left_down = index_down and not middle_down
                        right_down = middle_down and not index_down
                except Exception:
                    pass

        self.update_hand_control(global_point, left_down=left_down, right_down=right_down)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)

    def _choose(self, region: QRect) -> None:
        if self._completed:
            return
        self._completed = True
        self.selected_region = QRect(region.normalized())
        self.selection_made.emit(QRect(self.selected_region))
        self.close()

    def _cancel(self) -> None:
        if self._completed:
            return
        self._completed = True
        self.canceled.emit()
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._completed:
            self._completed = True
            self.canceled.emit()
        super().closeEvent(event)


class _CursorLayerWidget(QWidget):
    """Transparent overlay raised above all siblings; draws only the hand-cursor crosshair."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        panel = self.parent()
        if panel is None:
            return
        host = panel.parent()
        cursor_global = getattr(host, "_cursor_global", None)
        if cursor_global is None:
            return
        local = self.mapFromGlobal(cursor_global)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#F4FAFF"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(local, 10, 10)
        painter.drawLine(local.x() - 15, local.y(), local.x() + 15, local.y())
        painter.drawLine(local.x(), local.y() - 15, local.x(), local.y() + 15)


class _CursorHostPanel(QFrame):
    """QFrame that owns a _CursorLayerWidget child raised above all other children.

    Used by both CaptureMonitorDialog and _HandSelectorBase.  Call refresh_cursor()
    whenever the host's _cursor_global changes so the layer repaints.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cursor_layer = _CursorLayerWidget(self)

    def _sync_layer(self) -> None:
        self._cursor_layer.setGeometry(self.rect())
        self._cursor_layer.raise_()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_layer()
        self._cursor_layer.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_layer()

    def refresh_cursor(self) -> None:
        self._cursor_layer.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)


class _HandSelectorBase(QWidget):
    accepted = Signal()
    canceled = Signal()

    def __init__(self, config: AppConfig, title: str, subtitle: str, parent=None):
        super().__init__(None)
        self.config = config
        self._cursor_global: QPoint | None = None
        self._last_left_down = False
        self._last_right_down = False
        self._hand_clicks_armed = False
        self._raw_clicks_armed = False
        self._completed = False
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)

        self._panel = _CursorHostPanel(self)
        self._panel.setObjectName("handSelectorPanel")
        self._panel.setMinimumWidth(460)
        self._panel_layout = QVBoxLayout(self._panel)
        self._panel_layout.setContentsMargins(22, 20, 22, 18)
        self._panel_layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("handSelectorTitle")
        title_label.setWordWrap(True)
        self._panel_layout.addWidget(title_label)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("handSelectorSubtitle")
        subtitle_label.setWordWrap(True)
        self._panel_layout.addWidget(subtitle_label)

        row.addWidget(self._panel, 0)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self.setStyleSheet(
            f"""
            QWidget {{
                background: transparent;
            }}
            QFrame#handSelectorPanel {{
                background-color: {self.config.surface_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.30);
                border-radius: 18px;
            }}
            QLabel#handSelectorTitle {{
                color: {self.config.accent_color};
                font-size: 22px;
                font-weight: 900;
            }}
            QLabel#handSelectorSubtitle {{
                color: {self.config.text_color};
                font-size: 14px;
            }}
            QPushButton {{
                background-color: {self.config.primary_color};
                color: {self.config.text_color};
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 9px 14px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                border: 1px solid {self.config.accent_color};
            }}
            QPushButton:pressed {{
                background-color: {self.config.accent_color};
                color: #001B24;
            }}
            QLabel {{
                color: {self.config.text_color};
            }}
            """
        )

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._panel_layout

    def _screens_union_geometry(self) -> QRect:
        screens = [screen.geometry() for screen in QGuiApplication.screens()]
        if not screens:
            return QRect(0, 0, 1280, 720)
        union = QRect(screens[0])
        for geo in screens[1:]:
            union = union.united(geo)
        return union

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.setGeometry(self._screens_union_geometry())
        self._hand_clicks_armed = False
        self._raw_clicks_armed = False
        self._last_left_down = False
        self._last_right_down = False
        try:
            panel_rect = self._panel.geometry()
            self._cursor_global = self.mapToGlobal(panel_rect.center())
        except Exception:
            self._cursor_global = None
        self.raise_()
        self._panel.refresh_cursor()
        self.update()

    def _update_cursor_from_global(self, global_point: QPoint) -> None:
        geo = self.geometry()
        gx = max(geo.left() + 10, min(geo.right() - 10, int(global_point.x())))
        gy = max(geo.top() + 10, min(geo.bottom() - 10, int(global_point.y())))
        self._cursor_global = QPoint(gx, gy)
        self._panel.refresh_cursor()
        self.update()

    def _mapped_panel_global(self, source_global: QPoint) -> QPoint | None:
        if source_global is None:
            return None
        geo = self.geometry()
        if geo.width() <= 1 or geo.height() <= 1:
            return None
        panel_rect = self._panel.geometry()
        if panel_rect.width() <= 1 or panel_rect.height() <= 1:
            return None
        nx = (float(source_global.x()) - float(geo.left())) / float(max(geo.width() - 1, 1))
        ny = (float(source_global.y()) - float(geo.top())) / float(max(geo.height() - 1, 1))
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        _sensitivity = 1.4
        nx = max(0.0, min(1.0, 0.5 + (nx - 0.5) * _sensitivity))
        ny = max(0.0, min(1.0, 0.5 + (ny - 0.5) * _sensitivity))
        pad_x = 18
        pad_y = 18
        usable_left = panel_rect.left() + pad_x
        usable_top = panel_rect.top() + pad_y
        usable_width = max(1, panel_rect.width() - pad_x * 2)
        usable_height = max(1, panel_rect.height() - pad_y * 2)
        target_local = QPoint(
            int(round(usable_left + nx * max(usable_width - 1, 1))),
            int(round(usable_top + ny * max(usable_height - 1, 1))),
        )
        return self.mapToGlobal(target_local)

    def update_hand_control(self, global_point, *, left_down: bool, right_down: bool) -> None:
        if not self.isVisible():
            return
        if isinstance(global_point, QPoint):
            mapped_global = self._mapped_panel_global(global_point)
            if mapped_global is not None:
                self._update_cursor_from_global(mapped_global)
        elif self._cursor_global is None:
            try:
                self._cursor_global = self.mapToGlobal(self._panel.geometry().center())
            except Exception:
                pass

        if not self._hand_clicks_armed:
            if not left_down and not right_down:
                self._hand_clicks_armed = True
            self._last_left_down = bool(left_down)
            self._last_right_down = bool(right_down)
            self.update()
            return

        self._process_hand_clicks(left_down=bool(left_down), right_down=bool(right_down))

    def _process_hand_clicks(self, *, left_down: bool, right_down: bool) -> None:
        if right_down and not self._last_right_down:
            self._last_right_down = True
            self._last_left_down = bool(left_down)
            self._cancel()
            return
        self._last_right_down = bool(right_down)

        if left_down and not self._last_left_down and self._cursor_global is not None:
            local = self.mapFromGlobal(self._cursor_global)
            widget = self.childAt(local)
            while widget is not None and not isinstance(widget, QAbstractButton):
                widget = widget.parentWidget()
            if isinstance(widget, QAbstractButton) and widget.isEnabled():
                widget.click()
                self._last_left_down = True
                self.update()
                return
        self._last_left_down = bool(left_down)
        self.update()

    def handle_debug_frame(self, frame, info) -> None:
        if not self.isVisible() or self._completed or not isinstance(info, dict):
            return

        union_geo = self.geometry()
        global_point = None
        utility_left_down = bool(info.get("utility_capture_left_down", False))
        utility_right_down = bool(info.get("utility_capture_right_down", False))
        capture_cursor = info.get("utility_capture_cursor_norm")
        if isinstance(capture_cursor, (tuple, list)) and len(capture_cursor) >= 2:
            try:
                cx = max(0.0, min(1.0, float(capture_cursor[0])))
                cy = max(0.0, min(1.0, float(capture_cursor[1])))
                global_point = QPoint(
                    int(round(union_geo.left() + cx * max(union_geo.width() - 1, 1))),
                    int(round(union_geo.top() + cy * max(union_geo.height() - 1, 1))),
                )
            except Exception:
                global_point = None

        raw_index_down = False
        raw_middle_down = False
        result = info.get("result")
        hand_reading = getattr(result, "hand_reading", None) if result is not None else None
        tracked_hand = getattr(result, "tracked_hand", None) if result is not None else None
        handedness = str(getattr(tracked_hand, "handedness", "") or "")
        if hand_reading is not None and handedness == "Right":
            try:
                palm_center = getattr(hand_reading.palm, "center", None)
                if global_point is None and palm_center is not None and len(palm_center) >= 2:
                    px = max(0.0, min(1.0, float(palm_center[0])))
                    py = max(0.0, min(1.0, float(palm_center[1])))
                    global_point = QPoint(
                        int(round(union_geo.left() + px * max(union_geo.width() - 1, 1))),
                        int(round(union_geo.top() + py * max(union_geo.height() - 1, 1))),
                    )
                fingers = getattr(hand_reading, "fingers", {})

                def _finger_down(finger) -> bool:
                    if finger is None:
                        return False
                    openness = float(getattr(finger, "openness", 0.0) or 0.0)
                    curl = float(getattr(finger, "curl", 0.0) or 0.0)
                    state = str(getattr(finger, "state", "") or "")
                    return state in {"closed", "mostly_curled"} or openness <= 0.42 or curl >= 0.52

                raw_index_down = _finger_down(fingers.get("index"))
                raw_middle_down = _finger_down(fingers.get("middle"))
            except Exception:
                pass

        if not self._raw_clicks_armed:
            if not raw_index_down and not raw_middle_down:
                self._raw_clicks_armed = True
            left_down = False
            right_down = False
        else:
            if utility_left_down or utility_right_down:
                left_down = utility_left_down
                right_down = utility_right_down
            else:
                left_down = raw_index_down and not raw_middle_down
                right_down = raw_middle_down and not raw_index_down

        self.update_hand_control(global_point, left_down=left_down, right_down=right_down)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)

    def _accept(self) -> None:
        if self._completed:
            return
        self._completed = True
        self.accepted.emit()
        self.close()

    def _cancel(self) -> None:
        if self._completed:
            return
        self._completed = True
        self.canceled.emit()
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._completed:
            self._completed = True
            self.canceled.emit()
        super().closeEvent(event)


class _ColorSwatchButton(QPushButton):
    def __init__(self, color: QColor, parent=None):
        super().__init__("", parent)
        self._color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.clicked.connect(self._refresh_style)
        self._refresh_style()

    @property
    def selected_color(self) -> QColor:
        return QColor(self._color)

    def _refresh_style(self) -> None:
        if self.isChecked():
            self.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: {self._color.name()};
                    border: 3px solid #F4FAFF;
                    border-radius: 15px;
                    padding: 0;
                    outline: 2px solid rgba(29,233,182,0.85);
                    outline-offset: 2px;
                }}
                """
            )
        else:
            self.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: {self._color.name()};
                    border: 2px solid rgba(255,255,255,0.20);
                    border-radius: 15px;
                    padding: 0;
                }}
                """
            )

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        super().setChecked(checked)
        self._refresh_style()


class PenOptionsDialog(_HandSelectorBase):
    def __init__(self, config: AppConfig, color: QColor, thickness: int, parent=None):
        super().__init__(
            config,
            "Pen Options",
            "Use your hand-controlled cursor to choose a color and adjust pen thickness.",
            parent=parent,
        )
        self._selected_color = QColor(color)
        self._selected_thickness = int(max(2, thickness))
        palette_box = QFrame(self)
        palette_box.setObjectName("innerCard")
        palette_layout = QVBoxLayout(palette_box)
        palette_layout.setContentsMargins(14, 14, 14, 14)
        palette_layout.setSpacing(10)
        palette_layout.addWidget(QLabel("Pen color"))

        grid = QGridLayout()
        grid.setSpacing(8)
        self._color_buttons: list[_ColorSwatchButton] = []
        palette = [
            QColor(self._selected_color),
            QColor("#1DE9B6"),
            QColor("#40C4FF"),
            QColor("#FFD740"),
            QColor("#FF6E40"),
            QColor("#FF5252"),
            QColor("#FFFFFF"),
            QColor("#0F172A"),
        ]
        deduped: list[QColor] = []
        seen: set[str] = set()
        for value in palette:
            key = value.name().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(value)
        for index, swatch_color in enumerate(deduped):
            button = _ColorSwatchButton(swatch_color, self)
            button.clicked.connect(lambda _checked=False, color=QColor(swatch_color): self._select_color(color))
            grid.addWidget(button, index // 4, index % 4)
            self._color_buttons.append(button)
        palette_layout.addLayout(grid)
        custom_color_btn = QPushButton("Custom Color…")
        custom_color_btn.clicked.connect(self._open_color_wheel)
        palette_layout.addWidget(custom_color_btn, 0, Qt.AlignLeft)
        self.content_layout.addWidget(palette_box)

        thickness_box = QFrame(self)
        thickness_box.setObjectName("innerCard")
        thickness_layout = QHBoxLayout(thickness_box)
        thickness_layout.setContentsMargins(14, 14, 14, 14)
        thickness_layout.setSpacing(10)
        thickness_layout.addWidget(QLabel("Pen thickness"))
        minus_button = QPushButton("−")
        minus_button.clicked.connect(lambda: self._adjust_thickness(-2))
        plus_button = QPushButton("+")
        plus_button.clicked.connect(lambda: self._adjust_thickness(2))
        self._thickness_value = QLabel(str(self._selected_thickness))
        self._thickness_value.setMinimumWidth(44)
        self._thickness_value.setAlignment(Qt.AlignCenter)
        thickness_layout.addWidget(minus_button)
        thickness_layout.addWidget(self._thickness_value)
        thickness_layout.addWidget(plus_button)
        thickness_layout.addStretch(1)
        self.content_layout.addWidget(thickness_box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self._cancel)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self._accept)
        actions.addWidget(cancel_button)
        actions.addWidget(apply_button)
        self.content_layout.addLayout(actions)
        self._select_color(self._selected_color)

    @property
    def selected_color(self) -> QColor:
        return QColor(self._selected_color)

    @property
    def selected_thickness(self) -> int:
        return int(self._selected_thickness)

    def _open_color_wheel(self) -> None:
        picker = HandColorPickerDialog(self.config, self._selected_color, self)
        self._active_color_picker = picker
        picker.color_chosen.connect(self._select_color)
        signal = getattr(self, "_parent_debug_signal", None)
        connected_signal = None
        if signal is not None:
            try:
                signal.connect(picker.handle_debug_frame)
                connected_signal = signal
            except Exception:
                connected_signal = None

        def _picker_closed() -> None:
            if connected_signal is not None:
                try:
                    connected_signal.disconnect(picker.handle_debug_frame)
                except Exception:
                    pass
            self._active_color_picker = None
            try:
                self.show()
                self.raise_()
                self.activateWindow()
            except Exception:
                pass

        picker.accepted.connect(_picker_closed)
        picker.canceled.connect(_picker_closed)
        picker.show()
        picker.raise_()
        picker.activateWindow()

    def _select_color(self, color: QColor) -> None:
        self._selected_color = QColor(color)
        for button in self._color_buttons:
            button.setChecked(button.selected_color.name().lower() == self._selected_color.name().lower())

    def _adjust_thickness(self, delta: int) -> None:
        self._selected_thickness = int(max(2, min(48, self._selected_thickness + int(delta))))
        self._thickness_value.setText(str(self._selected_thickness))


class HandColorPickerDialog(_HandSelectorBase):
    """A fully hand-controllable color picker using a hue/saturation grid + brightness stepper."""

    color_chosen = Signal(QColor)

    def __init__(self, config: AppConfig, initial_color: QColor, parent=None):
        super().__init__(
            config,
            "Custom Color",
            "Use your hand-controlled cursor to pick a color.",
            parent=parent,
        )
        self._current_color = QColor(initial_color) if initial_color.isValid() else QColor("#1DE9B6")
        h, s, v, _ = self._current_color.getHsvF()
        self._hue = max(0.0, min(1.0, float(h) if h >= 0 else 0.0))
        self._sat = max(0.0, min(1.0, float(s)))
        self._val = max(0.0, min(1.0, float(v)))

        palette_box = QFrame(self)
        palette_box.setObjectName("innerCard")
        palette_layout = QVBoxLayout(palette_box)
        palette_layout.setContentsMargins(12, 12, 12, 12)
        palette_layout.setSpacing(8)

        grid = QGridLayout()
        grid.setSpacing(6)
        hue_steps = 12
        sat_steps = 4
        val_levels = [1.0, 0.75, 0.5]
        self._grid_buttons: list[tuple[float, float, float, QPushButton]] = []
        col = 0
        for hi in range(hue_steps):
            hue = hi / hue_steps
            for si in range(sat_steps):
                sat = 0.45 + si * (0.55 / max(1, sat_steps - 1))
                for vi, val in enumerate(val_levels):
                    c = QColor.fromHsvF(hue, sat, val)
                    btn = QPushButton(self)
                    btn.setFixedSize(28, 28)
                    btn.setStyleSheet(
                        f"QPushButton {{background-color:{c.name()};border:2px solid rgba(255,255,255,0.18);border-radius:14px;padding:0;}}"
                        f"QPushButton:hover {{border:2px solid rgba(255,255,255,0.7);}}"
                    )
                    row = si * len(val_levels) + vi
                    grid.addWidget(btn, row, col)
                    self._grid_buttons.append((hue, sat, val, btn))
                    btn.clicked.connect(lambda _c=False, hh=hue, ss=sat, vv=val: self._pick_hsv(hh, ss, vv))
            col += 1

        palette_layout.addLayout(grid)
        self.content_layout.addWidget(palette_box)

        brightness_box = QFrame(self)
        brightness_box.setObjectName("innerCard")
        b_layout = QHBoxLayout(brightness_box)
        b_layout.setContentsMargins(12, 10, 12, 10)
        b_layout.setSpacing(10)
        b_layout.addWidget(QLabel("Brightness"))
        dim_btn = QPushButton("−")
        dim_btn.clicked.connect(lambda: self._step_val(-0.08))
        bright_btn = QPushButton("+")
        bright_btn.clicked.connect(lambda: self._step_val(0.08))
        self._preview_swatch = QPushButton()
        self._preview_swatch.setFixedSize(44, 28)
        self._preview_swatch.setEnabled(False)
        self._val_label = QLabel(f"{int(self._val * 100)}%")
        self._val_label.setMinimumWidth(42)
        self._val_label.setAlignment(Qt.AlignCenter)
        b_layout.addWidget(dim_btn)
        b_layout.addWidget(self._val_label)
        b_layout.addWidget(bright_btn)
        b_layout.addStretch(1)
        b_layout.addWidget(self._preview_swatch)
        self.content_layout.addWidget(brightness_box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self._cancel)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._emit_chosen)
        actions.addWidget(cancel_btn)
        actions.addWidget(apply_btn)
        self.content_layout.addLayout(actions)

        self._refresh_preview()

    def _pick_hsv(self, hue: float, sat: float, val: float) -> None:
        self._hue = hue
        self._sat = sat
        self._val = val
        self._refresh_preview()

    def _step_val(self, delta: float) -> None:
        self._val = max(0.05, min(1.0, self._val + delta))
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        color = QColor.fromHsvF(self._hue, self._sat, self._val)
        self._current_color = color
        self._preview_swatch.setStyleSheet(
            f"QPushButton {{background-color:{color.name()};border:2px solid rgba(255,255,255,0.4);border-radius:6px;padding:0;}}"
        )
        self._val_label.setText(f"{int(self._val * 100)}%")

    def _emit_chosen(self) -> None:
        if self._completed:
            return
        chosen = QColor(self._current_color)
        self.color_chosen.emit(chosen)
        self._completed = True
        self.accepted.emit()
        self.close()


class EraserOptionsDialog(_HandSelectorBase):
    def __init__(self, config: AppConfig, thickness: int, mode: str, parent=None):
        super().__init__(
            config,
            "Eraser Options",
            "Use your hand-controlled cursor to choose eraser mode and thickness.",
            parent=parent,
        )
        self._selected_mode = "stroke" if str(mode).strip().lower() == "stroke" else "normal"
        self._selected_thickness = int(max(6, thickness))

        mode_box = QFrame(self)
        mode_box.setObjectName("innerCard")
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(14, 14, 14, 14)
        mode_layout.setSpacing(10)
        mode_layout.addWidget(QLabel("Eraser mode"))
        _mode_btn_style = """
            QPushButton { background-color: rgba(255,255,255,0.08); color: #F4FAFF;
                          border: 1px solid rgba(255,255,255,0.18); border-radius: 8px;
                          padding: 6px 18px; }
            QPushButton:checked { background-color: rgba(29,233,182,0.85); color: #001B24;
                                  border: 2px solid #1DE9B6; }
        """
        self._normal_button = QPushButton("Normal")
        self._normal_button.setCheckable(True)
        self._normal_button.setStyleSheet(_mode_btn_style)
        self._normal_button.clicked.connect(lambda: self._select_mode("normal"))
        self._stroke_button = QPushButton("Stroke")
        self._stroke_button.setCheckable(True)
        self._stroke_button.setStyleSheet(_mode_btn_style)
        self._stroke_button.clicked.connect(lambda: self._select_mode("stroke"))
        mode_layout.addWidget(self._normal_button)
        mode_layout.addWidget(self._stroke_button)
        mode_layout.addStretch(1)
        self.content_layout.addWidget(mode_box)

        thickness_box = QFrame(self)
        thickness_box.setObjectName("innerCard")
        thickness_layout = QHBoxLayout(thickness_box)
        thickness_layout.setContentsMargins(14, 14, 14, 14)
        thickness_layout.setSpacing(10)
        thickness_layout.addWidget(QLabel("Eraser thickness"))
        minus_button = QPushButton("−")
        minus_button.clicked.connect(lambda: self._adjust_thickness(-4))
        plus_button = QPushButton("+")
        plus_button.clicked.connect(lambda: self._adjust_thickness(4))
        self._thickness_value = QLabel(str(self._selected_thickness))
        self._thickness_value.setMinimumWidth(44)
        self._thickness_value.setAlignment(Qt.AlignCenter)
        thickness_layout.addWidget(minus_button)
        thickness_layout.addWidget(self._thickness_value)
        thickness_layout.addWidget(plus_button)
        thickness_layout.addStretch(1)
        self.content_layout.addWidget(thickness_box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self._cancel)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self._accept)
        actions.addWidget(cancel_button)
        actions.addWidget(apply_button)
        self.content_layout.addLayout(actions)
        self._select_mode(self._selected_mode)

    @property
    def selected_mode(self) -> str:
        return self._selected_mode

    @property
    def selected_thickness(self) -> int:
        return int(self._selected_thickness)

    def _select_mode(self, mode: str) -> None:
        self._selected_mode = "stroke" if str(mode).strip().lower() == "stroke" else "normal"
        self._normal_button.setChecked(self._selected_mode == "normal")
        self._stroke_button.setChecked(self._selected_mode == "stroke")

    def _adjust_thickness(self, delta: int) -> None:
        self._selected_thickness = int(max(6, min(72, self._selected_thickness + int(delta))))
        self._thickness_value.setText(str(self._selected_thickness))


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self._thread = None
        self._worker: Optional[GestureWorker] = None
        self.debugger_window: Optional[StandaloneDebugWindow] = None
        self.mini_live_viewer: Optional[MiniLiveViewer] = None
        self.live_view_window: Optional[LiveViewWindow] = None
        self.tutorial_window: Optional[TutorialWindow] = None
        self.is_custom_maximized = False
        self._restore_geometry = None
        self._discovered_cameras: list[CameraInfo] = []
        self._discovered_microphones: list[str] = []
        self.overlay = HelloOverlay(font_size=self.config.hello_font_size)
        self.draw_overlay = ScreenDrawOverlay(color=self.config.accent_color, thickness=8)
        self.countdown_overlay = CountdownOverlay()
        self.capture_region_overlay = CaptureRegionOverlay()
        self.recording_overlay = RecordingIndicatorOverlay()
        self.capture_region_overlay.selection_finished.connect(self._on_capture_region_selected)
        self.capture_region_overlay.selection_canceled.connect(self._on_capture_region_canceled)
        self._drawing_mode_active = False
        self._erase_mode_active = False
        self._worker_drawing_tool = "hidden"
        self._hold_started: dict[str, float] = {}
        self._hold_last_fired: dict[str, float] = {}
        self._drawing_settings_open = False
        self._hand_selector_dialog: QWidget | None = None
        self._drawing_render_target = "screen"
        self._last_drawing_request_token = 0
        self._last_utility_request_token = 0
        self._utility_screenshot_pending = False
        self._utility_countdown_active = False
        self._utility_countdown_token = 0
        self._capture_region_selection_mode: str | None = None
        self._pending_capture_region: QRect | None = None
        self._screen_recording_active = False
        self._screen_record_region: QRect | None = None
        self._screen_record_writer = None
        self._screen_record_process: subprocess.Popen | None = None
        self._screen_record_path: Path | None = None
        self._screen_record_fps = 12.0
        self._screen_record_frame_size: tuple[int, int] | None = None
        self._screen_record_backend = ""
        self._screen_record_timer = QTimer(self)
        self._screen_record_timer.setInterval(int(round(1000.0 / self._screen_record_fps)))
        self._screen_record_timer.timeout.connect(self._capture_screen_record_frame)
        self._clip_cache_fps = 8.0
        self._clip_cache_segment_seconds = 10.0
        self._clip_cache_max_seconds = 65.0
        self._clip_cache_region: QRect | None = None
        self._clip_cache_segment_writer = None
        self._clip_cache_segment_path: Path | None = None
        self._clip_cache_segment_started_at = 0.0
        self._clip_cache_segment_frame_count = 0
        self._clip_cache_segments: list[dict] = []
        self._clip_cache_process: subprocess.Popen | None = None
        self._clip_cache_backend = ""
        self._clip_cache_list_path: Path | None = None
        self._clip_cache_segment_pattern: Path | None = None
        self._clip_cache_wrap_count = max(3, int(np.ceil(self._clip_cache_max_seconds / self._clip_cache_segment_seconds)) + 1)
        self._clip_cache_timer = QTimer(self)
        self._clip_cache_timer.setInterval(int(round(1000.0 / self._clip_cache_fps)))
        self._clip_cache_timer.timeout.connect(self._capture_clip_cache_frame)
        self._ffmpeg_path = self._locate_ffmpeg_executable("ffmpeg")
        self._ffprobe_path = self._locate_ffmpeg_executable("ffprobe")
        self._ffmpeg_capabilities = self._detect_ffmpeg_capabilities()
        self.actions = SystemActions(open_settings_callback=self.show_settings_page)
        self.actions = SystemActions(open_settings_callback=self.show_settings_page)
        self._settings_nav_buttons: list[SettingsNavButton] = []
        self._camera_combo_lookup: dict[int, int] = {}
        self._microphone_combo_lookup: dict[str, int] = {}
        self._save_location_inputs: dict[str, QLineEdit] = {}
        self._save_name_inputs: dict[str, QLineEdit] = {}
        self._save_prompt_processor = SavePromptProcessor()
        self._pending_post_action_save: dict[str, object] | None = None
        self.setWindowTitle("HGR App")
        self.setMinimumSize(700, 540)
        self.resize(1020, 740)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self._build_ui()
        self._install_button_hover_refresh()
        self.apply_theme()
        QTimer.singleShot(0, self._initial_camera_setup)
        QTimer.singleShot(0, lambda: self.refresh_microphone_inventory(update_status=True, notify=False))

    def _build_ui(self) -> None:
        outer = QWidget()
        outer.setObjectName("rootWindow")
        self.setCentralWidget(outer)

        root = QVBoxLayout(outer)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.title_bar = TitleBar(self)
        root.addWidget(self.title_bar)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("pageStack")
        root.addWidget(self.page_stack, 1)

        self.home_page = self._build_home_page()
        self.settings_page = self._build_settings_page()
        self.page_stack.addWidget(self.home_page)
        self.page_stack.addWidget(self.settings_page)

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("homePage")
        body_layout = QVBoxLayout(page)
        body_layout.setContentsMargins(26, 22, 26, 24)
        body_layout.setSpacing(18)

        hero = QLabel("Hand Gesture Recognition App")
        hero.setAlignment(Qt.AlignCenter)
        hero.setObjectName("heroLabel")
        body_layout.addWidget(hero)

        subtitle = QLabel("Start live gesture and voice control, open Settings, or use Live View to monitor the camera feed.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setObjectName("subtitleLabel")
        body_layout.addWidget(subtitle)

        button_row = QHBoxLayout()
        button_row.setSpacing(14)
        self.start_button = QPushButton("START")
        self.end_button = QPushButton("END")
        self.settings_button = QPushButton("SETTINGS")
        self.start_button.clicked.connect(self.start_engine)
        self.end_button.clicked.connect(self.stop_engine)
        self.settings_button.clicked.connect(self.show_settings_page)
        button_row.addStretch(1)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.end_button)
        button_row.addWidget(self.settings_button)
        button_row.addStretch(1)
        body_layout.addLayout(button_row)

        info_card = QFrame()
        info_card.setObjectName("card")
        self.home_status_card = info_card
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(18, 18, 18, 18)
        info_layout.setSpacing(10)
        info_title = QLabel("Runtime Status")
        info_title.setObjectName("cardTitle")
        info_layout.addWidget(info_title)
        self.camera_label = QLabel("Camera: scanning...")
        self.status_label = QLabel("Status: idle")
        self.last_action_label = QLabel("Last action: none")
        for label in (self.camera_label, self.status_label, self.last_action_label):
            label.setWordWrap(True)
            info_layout.addWidget(label)
        body_layout.addWidget(info_card, 0, Qt.AlignHCenter)
        QTimer.singleShot(0, self._update_home_status_card_width)

        self.debugger_button = QPushButton("LIVE VIEW")
        self.debugger_button.setObjectName("debuggerButton")
        self.debugger_button.clicked.connect(self.open_debugger)
        debug_row = QHBoxLayout()
        debug_row.addStretch(1)
        debug_row.addWidget(self.debugger_button)
        debug_row.addStretch(1)
        body_layout.addLayout(debug_row)

        body_layout.addStretch(1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("settingsPage")

        layout = QHBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)

        left_panel = QFrame()
        left_panel.setObjectName("settingsSidebar")
        left_panel.setFixedWidth(220)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 16, 14, 14)
        left_layout.setSpacing(10)

        settings_title = QLabel("HGR Settings")
        settings_title.setObjectName("settingsTitle")
        left_layout.addWidget(settings_title)

        instructions_button = SettingsNavButton("Instructions", SECTION_INSTRUCTIONS, self)
        gestures_button = SettingsNavButton("Gesture Guide", SECTION_GESTURES, self)
        colors_button = SettingsNavButton("Colors", SECTION_COLORS, self)
        camera_button = SettingsNavButton("Camera", SECTION_CAMERA, self)
        microphone_button = SettingsNavButton("Microphone", SECTION_MICROPHONE, self)
        save_locations_button = SettingsNavButton("Save Locations", SECTION_SAVE_LOCATIONS, self)
        tutorial_button = SettingsNavButton("Tutorial", SECTION_TUTORIAL, self)
        self._settings_nav_buttons = [
            instructions_button,
            gestures_button,
            colors_button,
            camera_button,
            microphone_button,
            save_locations_button,
            tutorial_button,
        ]
        for button in self._settings_nav_buttons:
            left_layout.addWidget(button)

        left_layout.addStretch(1)
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("backButton")
        self.back_button.clicked.connect(self.show_home_page)
        left_layout.addWidget(self.back_button, 0, Qt.AlignLeft)

        self.settings_content_stack = QStackedWidget()
        self.settings_content_stack.setObjectName("settingsContentStack")
        self.settings_content_stack.addWidget(self._build_instructions_panel())
        self.settings_content_stack.addWidget(self._build_gesture_guide_panel())
        self.settings_content_stack.addWidget(self._build_colors_panel())
        self.settings_content_stack.addWidget(self._build_camera_panel())
        self.settings_content_stack.addWidget(self._build_microphone_panel())
        self.settings_content_stack.addWidget(self._build_save_locations_panel())
        self.settings_content_stack.addWidget(self._build_tutorial_panel())

        layout.addWidget(left_panel)
        layout.addWidget(self.settings_content_stack, 1)

        self.show_settings_section(SECTION_INSTRUCTIONS)
        return page

    def _make_content_panel(self, title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("settingsContentPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 20, 20, 20)
        panel_layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("settingsPanelTitle")
        panel_layout.addWidget(title_label)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("settingsPanelSubtitle")
        subtitle_label.setWordWrap(True)
        panel_layout.addWidget(subtitle_label)
        return panel, panel_layout

    def _build_instructions_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Instructions",
            "HGR App lets you control Spotify, Chrome, mouse input, volume, and voice features from a live camera feed. Use this page as the quick start, Gesture Guide for the full control map, and Tutorial for the guided walkthrough.",
        )
        info_box = QFrame()
        info_box.setObjectName("innerCard")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(16, 16, 16, 16)
        info_layout.setSpacing(10)
        items = [
            "1. Press Start to begin live tracking with the selected camera. HGR then reads your hand pose in real time and routes gestures to the active control context.",
            "2. Use Live View whenever you want to watch the hand skeleton, gesture label, voice state, volume state, and current app routing.",
            "3. Right-hand gestures handle Spotify, Chrome, wheels, and volume. Left-hand gestures handle voice, dictation, and mouse mode.",
            "4. Spotify actions work while Spotify is running on this device. Chrome wheel actions only work while Chrome is already open and active.",
            "5. Mouse mode is a separate control mode. Turn it on with the left hand, control the pointer with the right hand, then turn it off again when finished.",
            "6. Tutorial is the best place to learn the main motions. It now uses the same live gesture and voice runtime as the app, so the actions you practice there are the real actions the app will run.",
        ]
        for item in items:
            lbl = QLabel(item)
            lbl.setWordWrap(True)
            info_layout.addWidget(lbl)
        layout.addWidget(info_box, 0)

        spotify_box = QFrame()
        spotify_box.setObjectName("innerCard")
        spotify_layout = QVBoxLayout(spotify_box)
        spotify_layout.setContentsMargins(16, 16, 16, 16)
        spotify_layout.setSpacing(10)
        spotify_title = QLabel("Spotify Authorization")
        spotify_title.setObjectName("settingsSubtitle")
        spotify_layout.addWidget(spotify_title)
        spotify_note = QLabel(
            "If Spotify actions like Add to Liked Songs, Add to Playlist, or Create Playlist are failing, "
            "click Re-authorize Spotify. Your browser will open to Spotify's login page — approve access, "
            "then return here. This refreshes your token with the full permissions HGR needs."
        )
        spotify_note.setWordWrap(True)
        spotify_layout.addWidget(spotify_note)
        self.spotify_reauth_status = QLabel("")
        self.spotify_reauth_status.setWordWrap(True)
        spotify_layout.addWidget(self.spotify_reauth_status)
        self.spotify_reauth_button = QPushButton("Re-authorize Spotify")
        self.spotify_reauth_button.clicked.connect(self._on_spotify_reauth_clicked)
        spotify_layout.addWidget(self.spotify_reauth_button)
        layout.addWidget(spotify_box, 0)

        layout.addStretch(1)
        return panel

    def _on_spotify_reauth_clicked(self) -> None:
        import threading
        if self._worker is None or not hasattr(self._worker, "spotify_controller"):
            QMessageBox.information(
                self,
                "Spotify",
                "Start the app (press Start on the home page) before re-authorizing Spotify.",
            )
            return
        controller = self._worker.spotify_controller
        self.spotify_reauth_button.setEnabled(False)
        self.spotify_reauth_status.setText("Opening browser — complete the Spotify authorization, then return here.")

        def _run() -> None:
            success = False
            message = ""
            try:
                success = bool(controller.authorize_full_scopes())
                message = getattr(controller, "message", "") or ""
            except Exception as exc:
                success = False
                message = f"error: {exc}"
            QTimer.singleShot(0, lambda: self._on_spotify_reauth_finished(success, message))

        threading.Thread(target=_run, daemon=True).start()

    def _on_spotify_reauth_finished(self, success: bool, message: str) -> None:
        self.spotify_reauth_button.setEnabled(True)
        if success:
            self.spotify_reauth_status.setText("Spotify authorized with full permissions. You can now use Add to Liked, Add to Playlist, and Create Playlist.")
        else:
            detail = message or "authorization failed"
            self.spotify_reauth_status.setText(f"Re-authorization did not complete: {detail}")

    def _build_gesture_guide_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Gesture Guide",
            "Open a section below to view each gesture and how to perform it.",
        )

        info_box = QFrame()
        info_box.setObjectName("innerCard")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(4)
        note = QLabel(
            "Static gestures are held poses. Dynamic gestures are moving gestures or motion-based controls."
        )
        note.setWordWrap(True)
        info_layout.addWidget(note)
        layout.addWidget(info_box, 0)

        scroll = QScrollArea()
        scroll.setObjectName("gestureGuideScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        container = QWidget()
        container.setObjectName("gestureGuideContainer")
        sections_layout = QVBoxLayout(container)
        sections_layout.setContentsMargins(0, 0, 0, 0)
        sections_layout.setSpacing(12)

        static_cards = [
            GestureGuideCard(
                title="Left Hand One",
                action="Start voice command listening",
                how_to=(
                    "Use your left hand with your palm facing your monitor. Keep only your index finger extended and keep your "
                    "thumb, middle, ring, and pinky closed. Hold the pose steadily for a moment so HGR can confirm it."
                ),
                gesture_key="voice_one",
                image_name="Left One.png",
            ),
            GestureGuideCard(
                title="Left Hand Two",
                action="Start or stop dictation",
                how_to=(
                    "Use your left hand with your palm facing your monitor. Extend your index and middle fingers while keeping the "
                    "thumb, ring, and pinky closed. Hold the pose steadily to toggle dictation mode on or off."
                ),
                gesture_key="two",
                image_name="Left Two.png",
            ),
            GestureGuideCard(
                title="Left Hand Three",
                action="Turn mouse mode on or off",
                how_to=(
                    "Use your left hand with your palm facing your monitor. Keep your index, middle, and ring fingers open and separated, "
                    "while the thumb and pinky stay closed. Hold the pose briefly to toggle mouse mode."
                ),
                gesture_key="left_three",
                image_name="Left Three.png",
            ),
            GestureGuideCard(
                title="Left Hand Four",
                action="Toggle drawing mode on or off",
                how_to=(
                    "Use your left hand with your palm facing your monitor. Extend your index, middle, ring, and pinky fingers while keeping your thumb folded in. "
                    "Hold the pose steady for about half a second. HGR will confirm by toggling drawing mode on or off."
                ),
                gesture_key="four",
                image_name="Left Hand Four.png",
            ),
            GestureGuideCard(
                title="Left Hand Fist",
                action="Cancel voice commands at any stage",
                how_to=(
                    "Use your left hand with your palm facing your monitor and close all five fingers into a compact fist. "
                    "Hold the pose steadily so HGR reads it as a true fist. This cancels the current voice command at any stage — "
                    "while listening, while confirming, or during dictation — and returns voice control to idle."
                ),
                gesture_key="fist",
                image_name="Left Fist.png",
            ),
            GestureGuideCard(
                title="Right Hand Two",
                action="Open or focus Spotify",
                how_to=(
                    "Use your right hand with your palm facing your monitor. Keep your index and middle fingers open and separated, and keep the "
                    "thumb, ring, and pinky closed. Hold the pose long enough for the app to confirm it."
                ),
                gesture_key="two",
                image_name="Two.png",
            ),
            GestureGuideCard(
                title="Fist",
                action="Pause or play media in Spotify",
                how_to=(
                    "Use your right hand and close all five fingers into a compact fist. Keep the hand stable and facing your monitor so the pose "
                    "is read as a true fist instead of a partially curled hand."
                ),
                gesture_key="fist",
                image_name="Fist.png",
            ),
            GestureGuideCard(
                title="Mute",
                action="Mute or unmute system volume",
                how_to=(
                    "Use your right hand with your palm facing your monitor. Open your thumb and pinky while keeping your index, middle, and ring fingers "
                    "folded. Hold the shape clearly so HGR sees the outer fingers open and the middle three closed."
                ),
                gesture_key="mute",
                image_name="Mute.png",
            ),
            GestureGuideCard(
                title="Gesture Wheel",
                action="Open the Spotify gesture wheel",
                how_to=(
                    "Use your right hand and make the gesture wheel pose. Keep the pose steady until the wheel opens. Once it appears, move toward a slice "
                    "and hold there to trigger that Spotify action."
                ),
                gesture_key="wheel_pose",
                image_name="Wheel Pose.png",
            ),
            GestureGuideCard(
                title="Screen Wheel",
                action="Open the screen utility wheel for screenshots, recordings, and clips",
                how_to=(
                    "Use your right hand with your palm facing your monitor. Extend your index finger and pinky while keeping your thumb, middle, and ring fingers folded — "
                    "like a rock or horns shape. Hold the pose steady for about one second until the screen utility wheel opens. "
                    "Then move your hand toward a slice and hold to trigger that action: full screenshot, custom area screenshot, full screen record, custom area record, save last 30 s clip, or save last 1 minute clip."
                ),
                gesture_key="mute",
                image_name="ScreenWheel.png",
            ),
        ]

        dynamic_cards = [
            GestureGuideCard(
                title="Swipe Left",
                action="Go to the previous song in Spotify or navigate back in Chrome",
                how_to=(
                    "Start with your right hand open and your palm facing your monitor. Move your hand smoothly to the left in one clean horizontal motion. "
                    "Try to avoid large up-and-down movement while swiping. A confident motion works better than a slow drift."
                ),
                gesture_key="open_hand",
                video_name="SwipeLeft.mp4",
            ),
            GestureGuideCard(
                title="Swipe Right",
                action="Go to the next song in Spotify or navigate forward in Chrome",
                how_to=(
                    "Start with your right hand open and your palm facing your monitor. Move your hand smoothly to the right in one clean horizontal motion. "
                    "A confident side-to-side swipe works better than a slow drift."
                ),
                gesture_key="open_hand",
                video_name="SwipeRight.mp4",
            ),
            GestureGuideCard(
                title="Volume Control",
                action="Adjust system volume or app volume up or down",
                how_to=(
                    "Use your right hand with your palm facing your monitor. Keep your index and middle fingers open and together while the thumb, ring, and pinky stay folded. "
                    "Once the volume overlay appears, move your hand up to raise volume or down to lower it. "
                    "If Spotify or Chrome audio is active, two bars appear — move your palm slightly left from your starting position to control app volume, "
                    "or slightly right to control system volume. Up and down adjusts whichever bar is highlighted."
                ),
                gesture_key="volume_pose",
                video_name="VolControl.mp4",
            ),
            GestureGuideCard(
                title="Refresh / Repeat",
                action="Refresh the current page in Chrome or toggle repeat mode in Spotify",
                how_to=(
                    "Use your right hand and trace a small smooth circle with the active gesture pose. The motion should feel like drawing a loop. "
                    "In Chrome this refreshes the current page. In Spotify this toggles repeat mode on or off."
                ),
                gesture_key="one",
                video_name="Repeat.mp4",
            ),
            GestureGuideCard(
                title="Mouse Controls",
                action="Move the cursor, left click, right click, and scroll while mouse mode is active",
                how_to=(
                    "First turn mouse mode on with the left-hand three gesture. Then use your right hand with all fingers open and spread to move the cursor. "
                    "Bend and straighten your index finger to left-click. Bend and straighten your middle finger to right-click. "
                    "Move your hand upward or downward while in the scrolling pose to scroll."
                ),
                gesture_key="open_hand",
                video_name="MouseControl.mp4",
            ),
            GestureGuideCard(
                title="Maximize Window",
                action="Maximize the active window using a two-hand spread gesture",
                how_to=(
                    "Hold both hands in frame with palms facing the monitor. Start with both hands close together or pinched. "
                    "Then slowly spread both hands apart and hold the spread position. The active window will expand to fill the screen. "
                    "This is a two-hand gesture — both hands must be visible."
                ),
                gesture_key="open_hand",
                video_name="Maximize.mp4",
            ),
            GestureGuideCard(
                title="Minimize Window",
                action="Minimize the active window using a two-hand pinch gesture",
                how_to=(
                    "Hold both hands in frame with palms facing the monitor. Start with both hands spread apart. "
                    "Then bring both hands together into a pinch or close position and hold. The active window will minimize to the taskbar. "
                    "This is a two-hand gesture — both hands must be visible."
                ),
                gesture_key="open_hand",
                video_name="Minimize.mp4",
            ),
            GestureGuideCard(
                title="Restore Window",
                action="Restore the active window to its floating size",
                how_to=(
                    "Hold both hands in frame with palms facing the monitor. From either a spread or pinched position, "
                    "move your hands to a mid-distance position and hold. The active window will return to its previous floating size. "
                    "This is a two-hand gesture — both hands must be visible."
                ),
                gesture_key="open_hand",
                video_name="restore.mp4",
            ),
            GestureGuideCard(
                title="Drawing",
                action="Draw freehand strokes on screen",
                how_to=(
                    "Drawing mode must be active first — hold the left-hand four static gesture to toggle drawing mode on. "
                    "Then use your right hand with your index finger extended and other fingers folded — like pointing. "
                    "Move your hand to draw on screen; the stroke follows your index fingertip. Lift or change pose to stop drawing."
                ),
                gesture_key="one",
                video_name="Drawing.mp4",
            ),
            GestureGuideCard(
                title="Erasing",
                action="Erase drawn strokes on screen",
                how_to=(
                    "Drawing mode must be active first — hold the left-hand four static gesture to toggle drawing mode on. "
                    "Switch to eraser mode from the drawing settings wheel, then move your hand over drawn strokes to erase them. "
                    "You can adjust eraser size in the drawing settings wheel."
                ),
                gesture_key="fist",
                video_name="Erasing.mp4",
            ),
            GestureGuideCard(
                title="Clear Canvas",
                action="Remove all strokes from the drawing canvas at once",
                how_to=(
                    "Drawing mode must be active first — hold the left-hand four static gesture to toggle drawing mode on. "
                    "Perform the clear canvas gesture to wipe the entire canvas. All drawn strokes are removed at once. "
                    "This cannot be undone with the undo gesture."
                ),
                gesture_key="fist",
                video_name="ClearCanvas.mp4",
            ),
            GestureGuideCard(
                title="Undo Drawing",
                action="Remove the last drawn stroke",
                how_to=(
                    "Drawing mode must be active first — hold the left-hand four static gesture to toggle drawing mode on. "
                    "Perform the undo gesture to remove the most recent stroke. You can repeat this to continue undoing "
                    "previous strokes one at a time."
                ),
                gesture_key="one",
                video_name="UndoDraw.mp4",
            ),
            GestureGuideCard(
                title="Drawing Settings Wheel",
                action="Open drawing options: pen color, size, brush type, and eraser",
                how_to=(
                    "Drawing mode must be active first — hold the left-hand four static gesture to toggle drawing mode on. "
                    "Then make the gesture wheel pose with your right hand and hold it steady. The drawing settings wheel opens, "
                    "letting you choose pen color, adjust brush size, switch brush type, or switch to eraser mode. "
                    "Move toward a slice and hold to select."
                ),
                gesture_key="wheel_pose",
                video_name="DrawingSettingsWheel.mp4",
            ),
        ]

        sections_layout.addWidget(GestureGuideSection("Static Gestures", static_cards))
        sections_layout.addWidget(GestureGuideSection("Dynamic Gestures", dynamic_cards))
        sections_layout.addStretch(1)

        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        return panel

    def _build_colors_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Colors",
            "Choose app colors and the HELLO USER! font size. Apply Changes saves them, and Revert to Original restores the original HGR theme.",
        )

        colors_box = QFrame()
        colors_box.setObjectName("innerCard")
        colors_layout = QVBoxLayout(colors_box)
        colors_layout.setContentsMargins(16, 16, 16, 16)
        colors_layout.setSpacing(12)

        self.primary_picker = self._create_color_row(colors_layout, "Primary color", self.config.primary_color, "primary_color")
        self.accent_picker = self._create_color_row(colors_layout, "Accent color", self.config.accent_color, "accent_color")
        self.surface_picker = self._create_color_row(colors_layout, "Surface color", self.config.surface_color, "surface_color")
        self.text_picker = self._create_color_row(colors_layout, "Text color", self.config.text_color, "text_color")

        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.setSpacing(12)
        size_label = QLabel("HELLO size")
        size_label.setMinimumWidth(110)
        self.font_slider = QSlider(Qt.Horizontal)
        self.font_slider.setMinimum(42)
        self.font_slider.setMaximum(140)
        self.font_slider.setValue(self.config.hello_font_size)
        self.font_slider.valueChanged.connect(self._font_size_changed)
        self.font_value = QLabel(str(self.config.hello_font_size))
        self.font_value.setMinimumWidth(40)
        size_layout.addWidget(size_label)
        size_layout.addWidget(self.font_slider, 1)
        size_layout.addWidget(self.font_value)
        colors_layout.addWidget(size_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        revert_button = QPushButton("Revert to Original")
        revert_button.clicked.connect(self.revert_to_original_colors)
        apply_button = QPushButton("Apply Changes")
        apply_button.clicked.connect(self.apply_current_settings)
        button_row.addWidget(revert_button)
        button_row.addWidget(apply_button)
        colors_layout.addLayout(button_row)

        layout.addWidget(colors_box)
        layout.addStretch(1)
        return panel

    def _build_camera_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Camera",
            "HGR App searches for cameras when it opens. You can leave it on Auto-select or save a specific camera from the devices found on this computer.",
        )

        box = QFrame()
        box.setObjectName("innerCard")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 16, 16, 16)
        box_layout.setSpacing(12)

        self.camera_page_status = QLabel("Detected cameras: scanning...")
        self.camera_page_status.setWordWrap(True)
        box_layout.addWidget(self.camera_page_status)

        self.camera_combo = QComboBox()
        self.camera_combo.setObjectName("settingsCameraCombo")
        box_layout.addWidget(self.camera_combo)

        note = QLabel(
            "Choosing Auto-select means HGR App will use the first available camera each time. "
            "Save a specific camera only if you always want the same device used by default."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        actions_row = QHBoxLayout()
        self.refresh_cameras_button = QPushButton("Search Devices")
        self.refresh_cameras_button.clicked.connect(lambda: self.refresh_camera_inventory(update_status=True, notify=True))
        self.save_camera_button = QPushButton("Save Camera Choice")
        self.save_camera_button.clicked.connect(self.save_camera_preference_from_settings)
        self.clear_camera_button = QPushButton("Use Auto-Select")
        self.clear_camera_button.clicked.connect(self.clear_camera_preference)
        actions_row.addWidget(self.refresh_cameras_button)
        actions_row.addWidget(self.save_camera_button)
        actions_row.addWidget(self.clear_camera_button)
        box_layout.addLayout(actions_row)

        layout.addWidget(box)
        layout.addStretch(1)
        return panel

    
    def _build_microphone_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Microphone",
            "HGR App can use the default Windows microphone automatically or a specific saved microphone if you have more than one input device.",
        )

        box = QFrame()
        box.setObjectName("innerCard")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 16, 16, 16)
        box_layout.setSpacing(12)

        self.microphone_page_status = QLabel("Detected microphones: scanning...")
        self.microphone_page_status.setWordWrap(True)
        box_layout.addWidget(self.microphone_page_status)

        self.microphone_combo = QComboBox()
        self.microphone_combo.setObjectName("settingsMicrophoneCombo")
        box_layout.addWidget(self.microphone_combo)

        note = QLabel(
            "Choosing Auto-select means HGR App will use the default Windows microphone. "
            "Save a specific microphone only if you always want the same input device used by default."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        actions_row = QHBoxLayout()
        self.refresh_microphones_button = QPushButton("Search Devices")
        self.refresh_microphones_button.clicked.connect(lambda: self.refresh_microphone_inventory(update_status=True, notify=True))
        self.save_microphone_button = QPushButton("Save Microphone Choice")
        self.save_microphone_button.clicked.connect(self.save_microphone_preference_from_settings)
        self.clear_microphone_button = QPushButton("Use Auto-Select")
        self.clear_microphone_button.clicked.connect(self.clear_microphone_preference)
        actions_row.addWidget(self.refresh_microphones_button)
        actions_row.addWidget(self.save_microphone_button)
        actions_row.addWidget(self.clear_microphone_button)
        box_layout.addLayout(actions_row)

        layout.addWidget(box)
        layout.addStretch(1)
        return panel

    def _build_save_locations_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Save Locations",
            "Choose the default folders used for drawings, screenshots, screen recordings, and clips. Each output type keeps its own saved location.",
        )

        box = QFrame()
        box.setObjectName("innerCard")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 16, 16, 16)
        box_layout.setSpacing(12)

        note = QLabel(
            "Type a folder path and press Save, or use Browse to choose a folder. "
            "If a folder does not exist yet, HGR will try to create it safely."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)

        for row, output_kind in enumerate(SAVE_LOCATION_OUTPUT_ORDER):
            label = QLabel(SAVE_LOCATION_LABELS.get(output_kind, output_kind.title()))
            path_edit = QLineEdit(str(self._save_output_directory(output_kind)))
            path_edit.setObjectName(f"{output_kind}SaveLocationEdit")
            path_edit.setClearButtonEnabled(True)
            path_edit.returnPressed.connect(lambda kind=output_kind, editor=path_edit: self._apply_save_location(kind, editor))
            browse_button = QPushButton("Browse")
            browse_button.clicked.connect(lambda _checked=False, kind=output_kind: self._browse_save_location(kind))
            save_button = QPushButton("Save")
            save_button.clicked.connect(lambda _checked=False, kind=output_kind, editor=path_edit: self._apply_save_location(kind, editor))
            self._save_location_inputs[output_kind] = path_edit

            grid.addWidget(label, row, 0)
            grid.addWidget(path_edit, row, 1)
            grid.addWidget(browse_button, row, 2)
            grid.addWidget(save_button, row, 3)

        box_layout.addLayout(grid)
        layout.addWidget(box)

        name_box = QFrame()
        name_box.setObjectName("innerCard")
        name_box_layout = QVBoxLayout(name_box)
        name_box_layout.setContentsMargins(16, 16, 16, 16)
        name_box_layout.setSpacing(12)
        name_note = QLabel(
            "Set the default file name prefix for each output type. "
            "The app auto-increments a counter (e.g. HGR_Drawing_1, HGR_Drawing_2) "
            "based on existing files in the save folder."
        )
        name_note.setObjectName("cameraNote")
        name_note.setWordWrap(True)
        name_box_layout.addWidget(name_note)

        name_grid = QGridLayout()
        name_grid.setHorizontalSpacing(10)
        name_grid.setVerticalSpacing(10)
        name_grid.setColumnStretch(1, 1)

        for row, output_kind in enumerate(SAVE_LOCATION_OUTPUT_ORDER):
            label = QLabel(SAVE_LOCATION_LABELS.get(output_kind, output_kind.title()))
            current_name = configured_save_name(self.config, output_kind)
            name_edit = QLineEdit(current_name)
            name_edit.setObjectName(f"{output_kind}SaveNameEdit")
            name_edit.setPlaceholderText(SAVE_NAME_DEFAULTS.get(output_kind, "HGR_File"))
            name_edit.returnPressed.connect(lambda kind=output_kind, editor=name_edit: self._apply_save_name(kind, editor))
            save_btn = QPushButton("Save")
            save_btn.clicked.connect(lambda _checked=False, kind=output_kind, editor=name_edit: self._apply_save_name(kind, editor))
            self._save_name_inputs[output_kind] = name_edit
            name_grid.addWidget(label, row, 0)
            name_grid.addWidget(name_edit, row, 1)
            name_grid.addWidget(save_btn, row, 2)

        name_box_layout.addLayout(name_grid)
        layout.addWidget(name_box)
        layout.addStretch(1)
        return panel

    def _apply_save_name(self, output_kind: str, editor: QLineEdit | None) -> None:
        field_name = save_name_config_field(output_kind)
        if not field_name:
            return
        raw_value = str(editor.text() if editor is not None else "").strip()
        if not raw_value:
            raw_value = SAVE_NAME_DEFAULTS.get(output_kind, "HGR_File")
            if editor is not None:
                editor.setText(raw_value)
        import re as _re
        safe_name = _re.sub(r"[^\w\-]", "_", raw_value).strip("_")
        if not safe_name:
            safe_name = SAVE_NAME_DEFAULTS.get(output_kind, "HGR_File")
            if editor is not None:
                editor.setText(safe_name)
        setattr(self.config, field_name, safe_name)
        save_config(self.config)
        self.last_action_label.setText(
            f"Last action: {SAVE_LOCATION_LABELS.get(output_kind, output_kind).lower()} save name updated"
        )

    def _build_tutorial_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Tutorial",
            "The tutorial walks through the six main control groups using the same live runtime as the app, so the gestures and voice actions you practice there behave like the real app behavior.",
        )
        tutorial_box = QFrame()
        tutorial_box.setObjectName("innerCard")
        tutorial_layout = QVBoxLayout(tutorial_box)
        tutorial_layout.setContentsMargins(16, 16, 16, 16)
        tutorial_layout.setSpacing(10)

        items = [
            "Part 1: practice three right swipes and three left swipes. After that, swipe right moves to the next tutorial step and swipe left moves to the previous step.",
            "Part 2: use the right-hand two gesture to actually open or focus Spotify.",
            "Part 3: use the right-hand fist gesture to actually pause and play Spotify so you can verify the app control is working.",
            "Part 4: use the wheel pose to open the real Spotify gesture wheel. There is also a separate Google Chrome gesture wheel in the full app.",
            "Part 5: turn mouse mode on, learn how the right hand controls the cursor, click the tutorial targets, then turn mouse mode off again.",
        ]
        for item in items:
            lbl = QLabel(f"• {item}")
            lbl.setWordWrap(True)
            tutorial_layout.addWidget(lbl)

        open_tutorial_button = QPushButton("Open Tutorial")
        open_tutorial_button.clicked.connect(lambda: self.open_tutorial(from_settings=True))
        tutorial_layout.addWidget(open_tutorial_button, 0, Qt.AlignLeft)

        layout.addWidget(tutorial_box)
        layout.addStretch(1)
        return panel

    def _create_color_row(self, parent_layout: QVBoxLayout, label_text: str, color: str, attribute_name: str) -> ColorPickerButton:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)
        label = QLabel(label_text)
        label.setMinimumWidth(110)
        button = ColorPickerButton(label_text.split()[0], color, lambda c, a=attribute_name: setattr(self.config, a, c))
        row_layout.addWidget(label)
        row_layout.addWidget(button, 1)
        parent_layout.addWidget(row)
        return button

    def _browse_save_location(self, output_kind: str) -> None:
        current_dir = self._save_output_directory(output_kind)
        chosen = QFileDialog.getExistingDirectory(
            self,
            f"Choose {SAVE_LOCATION_LABELS.get(output_kind, output_kind.title())} Folder",
            str(current_dir),
        )
        if not chosen:
            return
        editor = self._save_location_inputs.get(output_kind)
        if editor is not None:
            editor.setText(chosen)
            self._apply_save_location(output_kind, editor)

    def _apply_save_location(self, output_kind: str, editor: QLineEdit | None) -> None:
        field_name = save_location_config_field(output_kind)
        if not field_name:
            return
        raw_value = str(editor.text() if editor is not None else "").strip()
        target_dir = Path(raw_value).expanduser() if raw_value else self._save_output_directory(output_kind)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            fallback_dir = self._save_output_directory(output_kind)
            if editor is not None:
                editor.setText(str(fallback_dir))
            self.last_action_label.setText(
                f"Last action: could not update {SAVE_LOCATION_LABELS.get(output_kind, output_kind).lower()} folder"
            )
            return
        setattr(self.config, field_name, str(target_dir))
        save_config(self.config)
        if editor is not None:
            editor.setText(str(target_dir))
        self.last_action_label.setText(
            f"Last action: saved {SAVE_LOCATION_LABELS.get(output_kind, output_kind).lower()} folder {target_dir}"
        )

    def show_settings_page(self, section_index: int = SECTION_INSTRUCTIONS) -> None:
        self.page_stack.setCurrentWidget(self.settings_page)
        self.show_settings_section(section_index)

    def show_home_page(self) -> None:
        self.page_stack.setCurrentWidget(self.home_page)


    def open_tutorial(self, from_settings: bool = False) -> None:
        if self.tutorial_window is None:
            self.tutorial_window = TutorialWindow(self.config, self)
            self.tutorial_window.tutorial_closed.connect(self._on_tutorial_closed)
            self.tutorial_window.gesture_guide_requested.connect(self._on_tutorial_gesture_guide_requested)
        else:
            self.tutorial_window.apply_theme(self.config)

        self.tutorial_window.configure_session(
            camera_index=None,
            launched_from_settings=from_settings,
            auto_start_on_done=not from_settings,
        )
        self.tutorial_window.show()
        self.tutorial_window.raise_()
        self.tutorial_window.activateWindow()
        self.last_action_label.setText("Last action: opened tutorial")

    def _on_tutorial_closed(self, completed: bool, auto_start: bool, launched_from_settings: bool) -> None:
        if launched_from_settings:
            self.show_settings_page(SECTION_TUTORIAL)
            self.last_action_label.setText(
                "Last action: completed tutorial" if completed else "Last action: left tutorial"
            )
            return

        self.show_home_page()
        if completed and auto_start:
            self.last_action_label.setText("Last action: tutorial completed — starting app")
            QTimer.singleShot(150, lambda: self.start_engine(skip_tutorial_prompt=True))
        else:
            self.last_action_label.setText("Last action: left tutorial")

    def _on_tutorial_gesture_guide_requested(self, launched_from_settings: bool) -> None:
        self.show_settings_page(SECTION_GESTURES)
        self.last_action_label.setText("Last action: opened gesture guide from tutorial")


    def show_settings_section(self, index: int) -> None:
        self.settings_content_stack.setCurrentIndex(index)
        for i, button in enumerate(self._settings_nav_buttons):
            button.setChecked(i == index)

    def _font_size_changed(self, value: int) -> None:
        self.config.hello_font_size = value
        self.font_value.setText(str(value))

    def revert_to_original_colors(self) -> None:
        self.config.primary_color = ORIGINAL_PRIMARY_COLOR
        self.config.accent_color = ORIGINAL_ACCENT_COLOR
        self.config.surface_color = ORIGINAL_SURFACE_COLOR
        self.config.text_color = ORIGINAL_TEXT_COLOR
        self.config.hello_font_size = ORIGINAL_HELLO_FONT_SIZE
        self.primary_picker.set_color(self.config.primary_color)
        self.accent_picker.set_color(self.config.accent_color)
        self.surface_picker.set_color(self.config.surface_color)
        self.text_picker.set_color(self.config.text_color)
        self.font_slider.setValue(self.config.hello_font_size)
        self.apply_theme()
        self.last_action_label.setText("Last action: reverted to original colors")

    def apply_current_settings(self) -> None:
        self.config.preferred_camera_index = self.camera_combo.currentData()
        self.apply_new_config(self.config)
        self._refresh_camera_labels()

    def _update_home_status_card_width(self) -> None:
        if not hasattr(self, "home_status_card") or self.home_status_card is None:
            return
        if not hasattr(self, "home_page") or self.home_page is None:
            return

        available_width = max(320, self.home_page.width() - 52)
        target_width = min(1040, available_width)
        self.home_status_card.setFixedWidth(target_width)

    def apply_theme(self) -> None:
        self.overlay.set_font_size(self.config.hello_font_size)
        button_hover_color = _with_alpha(QColor(self.config.primary_color).lighter(118), 170).name(QColor.HexArgb)
        nav_hover_color = _with_alpha(QColor(self.config.primary_color).lighter(115), 115).name(QColor.HexArgb)
        stylesheet = f"""
        QMainWindow {{
            background-color: {self.config.surface_color};
            color: {self.config.text_color};
            font-size: 14px;
        }}
        #rootWindow, #pageStack, #homePage, #settingsPage {{
            background-color: {self.config.surface_color};
            color: {self.config.text_color};
        }}
        #titleBar {{
            background-color: {self.config.primary_color};
            border: none;
        }}
        #heroLabel {{
            font-size: 30px;
            font-weight: 900;
            color: {self.config.accent_color};
            background: transparent;
        }}
        #subtitleLabel, #settingsPanelSubtitle, #settingsTitle, #settingsPanelTitle {{
            background: transparent;
        }}
        #subtitleLabel {{
            color: {self.config.text_color};
        }}
        #card, #settingsSidebar, #settingsContentPanel, #innerCard {{
            background-color: rgba(255,255,255,0.04);
            border: 1px solid rgba(29, 233, 182, 0.22);
            border-radius: 18px;
        }}
        #cardTitle {{
            font-size: 18px;
            font-weight: 800;
            color: {self.config.accent_color};
            background: transparent;
        }}
        #settingsTitle {{
            font-size: 24px;
            font-weight: 900;
            color: {self.config.accent_color};
        }}
        #settingsPanelTitle {{
            font-size: 22px;
            font-weight: 900;
            color: {self.config.accent_color};
        }}
        #settingsPanelSubtitle {{
            color: {self.config.text_color};
        }}
        #gestureCardTitle {{
            font-size: 18px;
            font-weight: 800;
            color: {self.config.accent_color};
        }}
        #gestureCardSubtitle {{
            color: rgba(229,246,255,0.92);
            font-weight: 700;
        }}
        #gestureCardBody {{
            color: {self.config.text_color};
            background: transparent;
        }}
        QScrollArea#gestureGuideScroll, QScrollArea#gestureGuideScroll > QWidget, QScrollArea#gestureGuideScroll QWidget#qt_scrollarea_viewport {{
            background: transparent;
            border: none;
        }}
        QWidget#gestureGuideContainer {{
            background: transparent;
        }}
        QLineEdit#settingsSearch, QComboBox#settingsCameraCombo, QComboBox#settingsMicrophoneCombo {{
            background-color: rgba(255,255,255,0.06);
            color: {self.config.text_color};
            border: 1px solid rgba(29,233,182,0.35);
            border-radius: 12px;
            padding: 10px 12px;
        }}
        QComboBox#settingsCameraCombo QAbstractItemView, QComboBox#settingsMicrophoneCombo QAbstractItemView {{
            background-color: rgba(15,23,42,0.98);
            color: {self.config.text_color};
            border: 1px solid rgba(29,233,182,0.35);
            selection-background-color: {self.config.primary_color};
            selection-color: {self.config.text_color};
            outline: 0;
        }}
        QPushButton {{
            background-color: {self.config.primary_color};
            color: {self.config.text_color};
            border: 1px solid rgba(29,233,182,0.35);
            border-radius: 14px;
            padding: 12px 18px;
            font-weight: 800;
            min-width: 110px;
        }}
        QPushButton[hgrHover="true"] {{
            background-color: {button_hover_color};
            border: 1px solid {self.config.accent_color};
        }}
        QPushButton[hgrPressed="true"] {{
            background-color: {self.config.accent_color};
            color: #001B24;
            border: 1px solid {self.config.accent_color};
        }}
        QPushButton#settingsNavButton {{
            min-width: 0px;
            text-align: left;
            padding: 10px 12px;
            background-color: transparent;
            border: 1px solid rgba(255,255,255,0.08);
            color: {self.config.text_color};
            border-radius: 12px;
        }}
        QPushButton#settingsNavButton[hgrHover="true"] {{
            background-color: {nav_hover_color};
            border: 1px solid rgba(29,233,182,0.40);
        }}
        QPushButton#settingsNavButton[hgrPressed="true"] {{
            background-color: rgba(29,233,182,0.22);
            border: 1px solid rgba(29,233,182,0.70);
            color: {self.config.accent_color};
        }}
        QPushButton#settingsNavButton:checked {{
            background-color: rgba(29,233,182,0.16);
            border: 1px solid rgba(29,233,182,0.70);
            color: {self.config.accent_color};
        }}
        QPushButton#backButton {{
            min-width: 0px;
            padding-left: 18px;
            padding-right: 18px;
        }}
        QPushButton#debuggerButton {{
            min-width: 180px;
        }}
        QLabel {{
            color: {self.config.text_color};
            background: transparent;
        }}
        QLabel#cameraNote {{
            color: rgba(229,246,255,0.84);
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            border-radius: 3px;
            background: rgba(255,255,255,0.12);
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            margin: -6px 0;
            border-radius: 8px;
            background: {self.config.accent_color};
        }}
        """
        self.setStyleSheet(stylesheet)
        self.title_bar.refresh()
        if self.debugger_window is not None:
            self.debugger_window.apply_theme(self.config)

    def _target_screen_geometry(self):
        handle = self.windowHandle()
        screen = handle.screen() if handle is not None else None
        if screen is None:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return screen.geometry()

    def toggle_max_restore(self) -> None:
        target = self._target_screen_geometry()
        if self.is_custom_maximized:
            if self._restore_geometry is not None:
                self.setGeometry(self._restore_geometry)
            else:
                self.showNormal()
            self.is_custom_maximized = False
        else:
            if target is not None:
                self._restore_geometry = self.geometry()
                self.setGeometry(target)
                self.is_custom_maximized = True
            else:
                self.showMaximized()
                self.is_custom_maximized = True
        self.title_bar.refresh()

    def _maybe_prompt_for_tutorial(self) -> str:
        if not self.config.show_start_instructions_prompt:
            return "start"

        prompt = StartTutorialDialog(self.config, self)
        prompt.exec()

        if prompt.do_not_show_again:
            self.config.show_start_instructions_prompt = False
            save_config(self.config)

        if prompt.choice == "tutorial":
            return "tutorial"
        if prompt.choice == "start":
            return "start"
        return "cancel"

    def _ensure_mini_debugger(self) -> None:
        if self.debugger_window is None:
            self.debugger_window = StandaloneDebugWindow(self.config)
            self.debugger_window.destroyed.connect(self._clear_debugger_reference)
        else:
            self.debugger_window.apply_theme(self.config)

    def _show_mini_debugger_for_running_engine(self) -> None:
        self._show_mini_live_viewer()

    def _initial_camera_setup(self) -> None:
        self.refresh_camera_inventory(update_status=True, notify=False)
        if len(self._discovered_cameras) > 1 and self.config.preferred_camera_index is None:
            result = self._prompt_for_camera_choice(
                self._discovered_cameras,
                "Multiple cameras were detected. Choose which webcam you want HGR App to use, or cancel and decide later in Settings.",
            )
            if result is not None:
                selected_index, remember = result
                if remember:
                    self.config.preferred_camera_index = selected_index
                    save_config(self.config)
                self._refresh_camera_combo_selection(selected_index)
                self._refresh_camera_labels()
                self.last_action_label.setText(f"Last action: selected camera {selected_index}")

    def refresh_camera_inventory(self, update_status: bool = True, notify: bool = False) -> list[CameraInfo]:
        access_ok, access_message = request_camera_access_main_thread(self.config.camera_scan_limit)
        if not access_ok:
            self._discovered_cameras = []
            self._rebuild_camera_combo()
            if update_status:
                self.camera_label.setText("Camera: permission required")
                self.camera_page_status.setText(access_message)
            if notify:
                QMessageBox.warning(self, "HGR App", access_message)
            return []

        self._discovered_cameras = list_available_cameras(self.config.camera_scan_limit)
        self._rebuild_camera_combo()
        self._refresh_camera_labels()

        if notify:
            if self._discovered_cameras:
                QMessageBox.information(self, "HGR App", f"Found {len(self._discovered_cameras)} available camera(s).")
            else:
                QMessageBox.warning(self, "HGR App", "No available cameras were found.")
        return self._discovered_cameras

    def _rebuild_camera_combo(self) -> None:
        if not hasattr(self, "camera_combo"):
            return
        self._camera_combo_lookup = {}
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        self.camera_combo.addItem("Auto-select first available camera", None)
        for camera in self._discovered_cameras:
            self._camera_combo_lookup[camera.index] = self.camera_combo.count()
            self.camera_combo.addItem(camera.display_name, camera.index)
        self._refresh_camera_combo_selection(self.config.preferred_camera_index)
        self.camera_combo.blockSignals(False)

    def _refresh_camera_combo_selection(self, camera_index: Optional[int]) -> None:
        if not hasattr(self, "camera_combo"):
            return
        combo_index = 0 if camera_index is None else self._camera_combo_lookup.get(camera_index, 0)
        self.camera_combo.setCurrentIndex(combo_index)

    def _preferred_camera_info(self) -> Optional[CameraInfo]:
        if self.config.preferred_camera_index is None:
            return None
        for camera in self._discovered_cameras:
            if camera.index == self.config.preferred_camera_index:
                return camera
        return None

    def _refresh_camera_labels(self) -> None:
        preferred = self._preferred_camera_info()
        if preferred is not None:
            self.camera_label.setText(f"Camera: {preferred.display_name} (saved)")
        elif self._discovered_cameras:
            if len(self._discovered_cameras) == 1:
                self.camera_label.setText(f"Camera: {self._discovered_cameras[0].display_name}")
            else:
                self.camera_label.setText(f"Camera: {len(self._discovered_cameras)} available — choose in Settings")
        else:
            self.camera_label.setText("Camera: no camera found")

        if preferred is not None:
            self.camera_page_status.setText(f"Saved camera: {preferred.display_name}")
        elif self._discovered_cameras:
            names = ", ".join(camera.display_name for camera in self._discovered_cameras)
            self.camera_page_status.setText(f"Detected cameras: {names}")
        else:
            self.camera_page_status.setText("Detected cameras: none")

    def _prompt_for_camera_choice(self, cameras: list[CameraInfo], prompt_text: str) -> Optional[tuple[int, bool]]:
        dialog = CameraSelectionDialog(self.config, cameras, prompt_text, self)
        if dialog.exec() != QDialog.Accepted or dialog.selected_camera_index is None:
            return None
        return dialog.selected_camera_index, dialog.remember_choice

    def _resolve_camera_for_start(self, cameras: list[CameraInfo]) -> Optional[int]:
        valid_indices = {camera.index for camera in cameras}
        if self.config.preferred_camera_index in valid_indices:
            return self.config.preferred_camera_index

        if self.config.preferred_camera_index is not None and self.config.preferred_camera_index not in valid_indices:
            self.last_action_label.setText("Last action: saved camera was not available")

        if len(cameras) == 1:
            return cameras[0].index

        result = self._prompt_for_camera_choice(
            cameras,
            "Multiple cameras are available. Choose which one you want to use for this session.",
        )
        if result is None:
            return None

        selected_index, remember = result
        if remember:
            self.config.preferred_camera_index = selected_index
            save_config(self.config)
            self._refresh_camera_combo_selection(selected_index)
            self._refresh_camera_labels()
        return selected_index

    def save_camera_preference_from_settings(self) -> None:
        selected_index = self.camera_combo.currentData()
        self.config.preferred_camera_index = selected_index
        save_config(self.config)
        self._refresh_camera_labels()
        if selected_index is None:
            self.last_action_label.setText("Last action: camera set to auto-select")
        else:
            self.last_action_label.setText(f"Last action: saved camera {selected_index}")
        QMessageBox.information(self, "HGR App", "Camera preference saved.")

    def clear_camera_preference(self) -> None:
        self.config.preferred_camera_index = None
        save_config(self.config)
        self._refresh_camera_combo_selection(None)
        self._refresh_camera_labels()
        self.last_action_label.setText("Last action: cleared saved camera")

    def refresh_microphone_inventory(self, update_status: bool = True, notify: bool = False) -> list[str]:
        self._discovered_microphones = list_input_microphones()
        self._rebuild_microphone_combo()
        self._refresh_microphone_labels()

        if notify:
            if self._discovered_microphones:
                QMessageBox.information(self, "HGR App", f"Found {len(self._discovered_microphones)} available microphone(s).")
            else:
                QMessageBox.warning(self, "HGR App", "No available microphones were found.")
        return self._discovered_microphones

    def _rebuild_microphone_combo(self) -> None:
        if not hasattr(self, "microphone_combo"):
            return
        self._microphone_combo_lookup = {}
        self.microphone_combo.blockSignals(True)
        self.microphone_combo.clear()
        self.microphone_combo.addItem("Auto-select default microphone", None)
        for device_name in self._discovered_microphones:
            self._microphone_combo_lookup[device_name] = self.microphone_combo.count()
            self.microphone_combo.addItem(device_name, device_name)
        self._refresh_microphone_combo_selection(getattr(self.config, "preferred_microphone_name", None))
        self.microphone_combo.blockSignals(False)

    def _refresh_microphone_combo_selection(self, device_name: Optional[str]) -> None:
        if not hasattr(self, "microphone_combo"):
            return
        combo_index = 0 if device_name is None else self._microphone_combo_lookup.get(device_name, 0)
        self.microphone_combo.setCurrentIndex(combo_index)

    def _refresh_microphone_labels(self) -> None:
        preferred_name = getattr(self.config, "preferred_microphone_name", None)
        if preferred_name and preferred_name in self._discovered_microphones:
            self.microphone_page_status.setText(f"Saved microphone: {preferred_name}")
        elif self._discovered_microphones:
            self.microphone_page_status.setText(f"Detected microphones: {', '.join(self._discovered_microphones)}")
        else:
            self.microphone_page_status.setText("Detected microphones: none")

    def save_microphone_preference_from_settings(self) -> None:
        selected_name = self.microphone_combo.currentData()
        self.config.preferred_microphone_name = selected_name
        save_config(self.config)
        self._refresh_microphone_labels()
        if self._worker is not None:
            self._worker.voice_listener.set_input_device_name(selected_name)
        if self.tutorial_window is not None and hasattr(self.tutorial_window, "_voice_listener"):
            try:
                self.tutorial_window._voice_listener.set_input_device_name(selected_name)
            except Exception:
                pass
        if selected_name is None:
            self.last_action_label.setText("Last action: microphone set to auto-select")
        else:
            self.last_action_label.setText(f"Last action: saved microphone {selected_name}")
        QMessageBox.information(self, "HGR App", "Microphone preference saved.")

    def clear_microphone_preference(self) -> None:
        self.config.preferred_microphone_name = None
        save_config(self.config)
        self._refresh_microphone_combo_selection(None)
        self._refresh_microphone_labels()
        if self._worker is not None:
            self._worker.voice_listener.set_input_device_name(None)
        if self.tutorial_window is not None and hasattr(self.tutorial_window, "_voice_listener"):
            try:
                self.tutorial_window._voice_listener.set_input_device_name(None)
            except Exception:
                pass
        self.last_action_label.setText("Last action: cleared saved microphone")

    def start_engine(self, checked: bool = False, skip_tutorial_prompt: bool = False) -> None:
            prompt_result = "start" if skip_tutorial_prompt else self._maybe_prompt_for_tutorial()
            if prompt_result != "start":
                if prompt_result == "tutorial":
                    self.open_tutorial(from_settings=False)
                    return
                if prompt_result == "cancel":
                    self.last_action_label.setText("Last action: start cancelled")
                return
    
            cameras = self._discovered_cameras if self._discovered_cameras else self.refresh_camera_inventory(update_status=True, notify=False)
            if not cameras:
                QMessageBox.warning(self, "HGR App", "No available camera was found.")
                self.status_label.setText("Status: no camera found")
                return
    
            selected_camera_index = self._resolve_camera_for_start(cameras)
            if selected_camera_index is None:
                self.status_label.setText("Status: start cancelled")
                self.last_action_label.setText("Last action: camera selection cancelled")
                return

            if self._worker is not None:
                self._worker.stop()

            self._worker = GestureWorker(self.config, camera_index_override=selected_camera_index)
            self._worker.status_changed.connect(self._on_status_changed)
            self._worker.command_detected.connect(self._on_command_detected)
            self._worker.camera_selected.connect(self._on_camera_selected)
            self._worker.error_occurred.connect(self._on_error)
            self._worker.running_state_changed.connect(self._on_running_state_changed)
            self._worker.running_state_changed.connect(self._cleanup_thread_if_stopped)
            self._worker.debug_frame_ready.connect(self._on_worker_debug_frame)
            self._worker.save_prompt_completed.connect(self._on_save_prompt_completed)
            if self.live_view_window is not None:
                self.live_view_window.attach_to_worker(self._worker)
            if self.mini_live_viewer is not None:
                self.mini_live_viewer.attach_to_worker(self._worker)

            self.camera_label.setText(f"Camera: Camera {selected_camera_index}")
            self.status_label.setText("Status: starting...")
            self.last_action_label.setText("Last action: starting gesture and voice control")
            self.start_button.setEnabled(False)
            self.end_button.setEnabled(True)
            self._worker.start()
            self._start_clip_cache()
            if (
                (self.live_view_window is None or not self.live_view_window.isVisible())
                and (self.debugger_window is None or not self.debugger_window.isVisible())
            ):
                self._show_mini_live_viewer()

    def stop_engine(self) -> None:
            self._hide_mini_live_viewer()
            if self.live_view_window is not None:
                self.live_view_window.detach_from_worker()
            self._pending_post_action_save = None
            self._close_hand_selector_dialog()
            if getattr(self, "_capture_monitor_dialog", None) is not None and hasattr(self, "_clear_capture_monitor_dialog_state"):
                try:
                    self._clear_capture_monitor_dialog_state()
                except Exception:
                    pass
            self._set_drawing_mode(False)
            self.countdown_overlay.hide_countdown()
            self.recording_overlay.hide_indicator()
            self._set_worker_utility_capture_selection_active(False)
            if self.capture_region_overlay.isVisible():
                self.capture_region_overlay.hide()
            if self._screen_recording_active:
                self._stop_screen_recording()
            self._stop_clip_cache()
            if self._worker is not None:
                self._worker.stop()
            else:
                self.status_label.setText("Status: idle")
                self.last_action_label.setText("Last action: stopped")
                self.start_button.setEnabled(True)
                self.end_button.setEnabled(False)

    def open_debugger(self) -> None:
            self._hide_mini_live_viewer()
            if self._worker is not None and self._worker.is_running:
                self._ensure_live_view_window()
                if self.live_view_window is None:
                    return
                self.live_view_window.attach_to_worker(self._worker)
                self.live_view_window.set_gestures_enabled(bool(getattr(self._worker, "gestures_enabled", True)))
                self.live_view_window.show_window()
            else:
                self._ensure_mini_debugger()
                if self.debugger_window is None:
                    return
                self.debugger_window.restart_session()
                self.debugger_window.show()
                self.debugger_window.raise_()
                self.debugger_window.activateWindow()
            self.last_action_label.setText("Last action: opened live view")

    def _clear_debugger_reference(self, *args) -> None:
        self.debugger_window = None

    def _clear_mini_live_viewer_reference(self, *args) -> None:
        self.mini_live_viewer = None

    def _clear_live_view_window_reference(self, *args) -> None:
        self.live_view_window = None

    def _ensure_live_view_window(self) -> None:
        if self.live_view_window is None:
            self.live_view_window = LiveViewWindow(self.config, self._worker)
            self.live_view_window.destroyed.connect(self._clear_live_view_window_reference)
            self.live_view_window.minimize_requested.connect(self._handle_live_view_minimize_requested)
            self.live_view_window.toggle_gestures_requested.connect(self._handle_toggle_gestures_requested)
        else:
            self.live_view_window.apply_theme(self.config)
        if self._worker is not None and self._worker.is_running:
            self.live_view_window.attach_to_worker(self._worker)
        else:
            self.live_view_window.detach_from_worker()

    def _handle_live_view_minimize_requested(self) -> None:
        self._show_mini_live_viewer()

    def _ensure_mini_live_viewer(self) -> None:
        if self.mini_live_viewer is None:
            self.mini_live_viewer = MiniLiveViewer(self.config)
            self.mini_live_viewer.destroyed.connect(self._clear_mini_live_viewer_reference)
            self.mini_live_viewer.enlarge_requested.connect(self._handle_mini_live_viewer_enlarge)
            self.mini_live_viewer.toggle_gestures_requested.connect(self._handle_toggle_gestures_requested)
            self.mini_live_viewer.close_requested.connect(self._hide_mini_live_viewer)
        else:
            self.mini_live_viewer.apply_theme(self.config)
        if self._worker is not None and self._worker.is_running:
            self.mini_live_viewer.attach_to_worker(self._worker)
        else:
            self.mini_live_viewer.detach_from_worker()

    def _show_mini_live_viewer(self) -> None:
        self._ensure_mini_live_viewer()
        if self.mini_live_viewer is None:
            return
        self.mini_live_viewer.attach_to_worker(self._worker)
        self.mini_live_viewer.set_gestures_enabled(bool(getattr(self._worker, "gestures_enabled", True)))
        self.mini_live_viewer.show_overlay()

    def _hide_mini_live_viewer(self) -> None:
        if self.mini_live_viewer is not None:
            self.mini_live_viewer.detach_from_worker()
            self.mini_live_viewer.hide()

    def _handle_toggle_gestures_requested(self) -> None:
        if self._worker is None:
            return
        enabled = not bool(getattr(self._worker, "gestures_enabled", True))
        try:
            self._worker.set_gestures_enabled(enabled)
        except Exception:
            return
        if self.mini_live_viewer is not None:
            self.mini_live_viewer.set_gestures_enabled(enabled)
        if self.live_view_window is not None:
            self.live_view_window.set_gestures_enabled(enabled)
        state = "enabled" if enabled else "disabled"
        self.last_action_label.setText(f"Last action: gestures {state}")

    def _handle_mini_live_viewer_enlarge(self) -> None:
        self._hide_mini_live_viewer()
        self.open_debugger()

    def _handle_debugger_minimize_requested(self) -> None:
        if self._worker is not None and self._worker.is_running:
            self._show_mini_live_viewer()

    def _cleanup_thread_if_stopped(self, is_running: bool) -> None:
            if is_running:
                return
            if self.mini_live_viewer is not None:
                self.mini_live_viewer.detach_from_worker()
            if self.live_view_window is not None:
                self.live_view_window.detach_from_worker()
            self._set_drawing_mode(False)
            self._worker = None
            self._thread = None

    def _on_status_changed(self, text: str) -> None:
        self.status_label.setText(f"Status: {text}")

    def _on_camera_selected(self, text: str) -> None:
        self.camera_label.setText(f"Camera: {text}")

    def _on_running_state_changed(self, is_running: bool) -> None:
            self.start_button.setEnabled(not is_running)
            self.end_button.setEnabled(is_running)
            self.debugger_button.setEnabled(True)

    def _on_command_detected(self, command: str) -> None:
        action_text = str(command or "").strip() or "none"
        self.last_action_label.setText(f"Last action: {action_text}")

    def _hold_ready(self, key: str, active: bool, threshold: float, now: float, cooldown: float = 0.0) -> bool:
        if not active:
            self._hold_started.pop(key, None)
            return False
        start = self._hold_started.setdefault(key, now)
        if now - self._hold_last_fired.get(key, -1e9) < cooldown:
            return False
        if now - start >= threshold:
            self._hold_last_fired[key] = now
            self._hold_started[key] = now
            return True
        return False

    def _set_drawing_render_target(self, target: str) -> None:
        normalized = str(target or "screen").strip().lower()
        if normalized not in {"screen", "camera"}:
            normalized = "screen"
        if normalized == self._drawing_render_target:
            return
        self._drawing_render_target = normalized
        if self._drawing_mode_active and self._drawing_render_target == "screen":
            self.draw_overlay.show_overlay()
            self.draw_overlay.set_cursor(None, "hidden")
        else:
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(None, "hidden")
            self.draw_overlay.hide_overlay()
        if self._drawing_mode_active:
            self.last_action_label.setText(f"Last action: drawing target {self._drawing_render_target}")

    def _sync_drawing_brush_to_worker(self) -> None:
        if self._worker is not None and hasattr(self._worker, "set_drawing_brush"):
            try:
                self._worker.set_drawing_brush(self.draw_overlay.brush_color.name(), self.draw_overlay.brush_thickness)
            except Exception:
                pass
        if self._worker is not None and hasattr(self._worker, "set_drawing_eraser"):
            try:
                self._worker.set_drawing_eraser(self.draw_overlay.eraser_thickness, self.draw_overlay.eraser_mode)
            except Exception:
                pass

    def _disconnect_hand_selector_dialog(self, dialog: QWidget | None) -> None:
        if dialog is None or self._worker is None:
            return
        try:
            self._worker.debug_frame_ready.disconnect(dialog.handle_debug_frame)
        except Exception:
            pass

    def _close_hand_selector_dialog(self) -> None:
        dialog = self._hand_selector_dialog
        if dialog is None:
            self._drawing_settings_open = False
            self._set_worker_utility_capture_selection_active(False)
            return
        self._disconnect_hand_selector_dialog(dialog)
        self._hand_selector_dialog = None
        self._drawing_settings_open = False
        self._set_worker_utility_capture_selection_active(False)
        try:
            if dialog.isVisible():
                dialog.close()
        except Exception:
            pass

    def _present_hand_selector_dialog(
        self,
        dialog: _HandSelectorBase,
        *,
        on_accept,
        was_screen_active: bool,
    ) -> bool:
        if self._drawing_settings_open or self._hand_selector_dialog is not None:
            return False

        self._drawing_settings_open = True
        self._hand_selector_dialog = dialog
        if was_screen_active:
            self.draw_overlay.hide_overlay()
        self._set_worker_utility_capture_selection_active(True)
        if self._worker is not None:
            try:
                self._worker.debug_frame_ready.connect(dialog.handle_debug_frame)
            except Exception:
                pass

        cleaned_up = False

        def _cleanup() -> None:
            nonlocal cleaned_up
            if cleaned_up:
                return
            cleaned_up = True
            self._disconnect_hand_selector_dialog(dialog)
            if self._hand_selector_dialog is dialog:
                self._hand_selector_dialog = None
            self._drawing_settings_open = False
            self._set_worker_utility_capture_selection_active(False)
            if was_screen_active and self._drawing_mode_active and self._drawing_render_target == "screen":
                self.draw_overlay.show_overlay()

        def _accepted() -> None:
            try:
                on_accept()
            finally:
                _cleanup()

        def _canceled() -> None:
            _cleanup()

        dialog.accepted.connect(_accepted)
        dialog.canceled.connect(_canceled)
        dialog.show()
        dialog.raise_()
        dialog.update()
        return True

    def _open_drawing_color_picker_from_gesture(self) -> None:
        if self._drawing_settings_open:
            return
        self._drawing_settings_open = True
        was_screen_active = self._drawing_mode_active and self._drawing_render_target == "screen"
        if was_screen_active:
            self.draw_overlay.hide_overlay()
        picker = QColorDialog(self.draw_overlay.brush_color, self)
        picker.setWindowTitle("Choose Drawing Color")
        picker.setOption(QColorDialog.DontUseNativeDialog, False)
        if picker.exec() == QDialog.Accepted:
            chosen = picker.currentColor()
            if chosen.isValid():
                self.draw_overlay.set_brush(chosen, self.draw_overlay.brush_thickness)
                self._sync_drawing_brush_to_worker()
                self.last_action_label.setText(f"Last action: drawing color {chosen.name()}")
        if was_screen_active:
            self.draw_overlay.show_overlay()
        self._drawing_settings_open = False

    def _open_drawing_thickness_dialog_from_gesture(self) -> None:
        if self._drawing_settings_open:
            return
        self._drawing_settings_open = True
        was_screen_active = self._drawing_mode_active and self._drawing_render_target == "screen"
        if was_screen_active:
            self.draw_overlay.hide_overlay()
        dialog = QDialog(self)
        dialog.setWindowTitle("Drawing Thickness")
        dialog.setModal(True)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dialog.resize(420, 160)
        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)
        title = QLabel("Brush thickness")
        title.setStyleSheet("font-size: 18px; font-weight: 800;")
        root.addWidget(title)
        row = QHBoxLayout()
        slider = QSlider(Qt.Horizontal)
        slider.setRange(2, 48)
        slider.setValue(max(2, int(self.draw_overlay.brush_thickness)))
        value_label = QLabel(str(slider.value()))
        slider.valueChanged.connect(lambda v: value_label.setText(str(v)))
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        root.addLayout(row)
        buttons = QHBoxLayout(); buttons.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(dialog.reject)
        apply_btn = QPushButton("Apply"); apply_btn.clicked.connect(dialog.accept)
        buttons.addWidget(cancel); buttons.addWidget(apply_btn); root.addLayout(buttons)
        dialog.setStyleSheet(
            """
            QDialog { background: #0F172A; color: #E5F6FF; border: 1px solid rgba(29,233,182,0.35); }
            QLabel { color: #E5F6FF; }
            QPushButton {
                background-color: #0B3D91; color: #E5F6FF; border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px; padding: 9px 14px; font-weight: 700;
            }
            QPushButton:hover { border: 1px solid #1DE9B6; }
            QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: rgba(255,255,255,0.14); }
            QSlider::handle:horizontal { width: 16px; margin: -5px 0; border-radius: 8px; background: #1DE9B6; }
            """
        )
        if dialog.exec() == QDialog.Accepted:
            self.draw_overlay.set_brush(self.draw_overlay.brush_color, int(slider.value()))
            self._sync_drawing_brush_to_worker()
            self.last_action_label.setText(f"Last action: drawing thickness {int(slider.value())}")
        if was_screen_active:
            self.draw_overlay.show_overlay()
        self._drawing_settings_open = False

    def _open_pen_options_dialog_from_gesture(self) -> bool:
        if self._drawing_settings_open:
            return False
        was_screen_active = self._drawing_mode_active and self._drawing_render_target == "screen"
        dialog = PenOptionsDialog(self.config, self.draw_overlay.brush_color, self.draw_overlay.brush_thickness, self)
        dialog._parent_debug_signal = getattr(self._worker, "debug_frame_ready", None) if self._worker else None

        def _apply() -> None:
            self.draw_overlay.set_brush(dialog.selected_color, dialog.selected_thickness)
            self._sync_drawing_brush_to_worker()
            self.last_action_label.setText(
                f"Last action: pen color {dialog.selected_color.name()} thickness {dialog.selected_thickness}"
            )

        return self._present_hand_selector_dialog(
            dialog,
            on_accept=_apply,
            was_screen_active=was_screen_active,
        )

    def _open_eraser_options_dialog_from_gesture(self) -> bool:
        if self._drawing_settings_open:
            return False
        was_screen_active = self._drawing_mode_active and self._drawing_render_target == "screen"
        dialog = EraserOptionsDialog(
            self.config,
            int(getattr(self.draw_overlay, "eraser_thickness", 18)),
            str(getattr(self.draw_overlay, "eraser_mode", "normal")),
            self,
        )

        def _apply() -> None:
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_eraser_settings(dialog.selected_thickness, dialog.selected_mode)
            self._sync_drawing_brush_to_worker()
            self.last_action_label.setText(
                f"Last action: eraser {dialog.selected_mode} thickness {dialog.selected_thickness}"
            )

        return self._present_hand_selector_dialog(
            dialog,
            on_accept=_apply,
            was_screen_active=was_screen_active,
        )

    def _set_drawing_mode(self, active: bool) -> None:
        self._drawing_mode_active = active
        self._erase_mode_active = False
        self._hold_started.clear()
        if active:
            if self._drawing_render_target == "screen":
                self.draw_overlay.show_overlay()
                self.draw_overlay.set_cursor(None, "hidden")
            else:
                self.draw_overlay.end_stroke()
                self.draw_overlay.set_cursor(None, "hidden")
                self.draw_overlay.hide_overlay()
            self.last_action_label.setText(f"Last action: drawing mode enabled ({self._drawing_render_target})")
        else:
            self._close_hand_selector_dialog()
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(None, "hidden")
            self.draw_overlay.hide_overlay()
            self.last_action_label.setText("Last action: drawing mode disabled")

    def _toggle_erase_mode(self) -> None:
        self._erase_mode_active = not self._erase_mode_active
        self.draw_overlay.end_stroke()
        state = "enabled" if self._erase_mode_active else "disabled"
        self.last_action_label.setText(f"Last action: erase mode {state}")

    def _open_drawing_settings(self) -> None:
        if self._drawing_settings_open:
            return
        self._drawing_settings_open = True
        was_active = self._drawing_mode_active
        if was_active:
            self.draw_overlay.hide_overlay()
        dialog = DrawingSettingsDialog(self.draw_overlay.brush_color, self.draw_overlay.brush_thickness, self)
        if dialog.exec() == QDialog.Accepted:
            self.draw_overlay.set_brush(dialog.selected_color, dialog.selected_thickness)
            self._sync_drawing_brush_to_worker()
            self.last_action_label.setText(
                f"Last action: drawing color {dialog.selected_color.name()} thickness {dialog.selected_thickness}"
            )
        if was_active:
            self.draw_overlay.show_overlay()
        self._drawing_settings_open = False

    def _save_output_directory(self, output_kind: str) -> Path:
        return configured_save_directory(self.config, output_kind)

    def _next_output_path(self, output_kind: str, ext: str, *, extra_label: str = "") -> Path:
        import re as _re
        target_dir = self._save_output_directory(output_kind)
        prefix = configured_save_name(self.config, output_kind)
        label_part = f"_{extra_label}" if extra_label else ""
        pattern = _re.compile(rf"^{_re.escape(prefix)}{_re.escape(label_part)}_(\d+)\b", _re.IGNORECASE)
        max_count = 0
        try:
            for entry in target_dir.iterdir():
                m = pattern.match(entry.stem)
                if m:
                    try:
                        max_count = max(max_count, int(m.group(1)))
                    except ValueError:
                        pass
        except Exception:
            pass
        name = f"{prefix}{label_part}_{max_count + 1}{ext}"
        return self._unique_output_path(target_dir / name)

    def _unique_output_path(self, path: Path) -> Path:
        candidate = Path(path)
        counter = 2
        while candidate.exists():
            candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
            counter += 1
        return candidate

    def _queue_post_action_save_prompt(self, output_kind: str, path: Path | None) -> None:
        if path is None:
            return
        resolved_path = Path(path)
        if not resolved_path.exists():
            return
        self._pending_post_action_save = {
            "output_kind": str(output_kind or ""),
            "path": resolved_path,
        }
        if self._worker is None or not getattr(self._worker, "is_running", False):
            self._pending_post_action_save = None
            return
        try:
            started = bool(self._worker.start_save_location_prompt())
        except Exception:
            started = False
        if not started:
            self._pending_post_action_save = None

    def _move_saved_output(self, source_path: Path, destination_dir: Path) -> Path | None:
        source = Path(source_path)
        destination = Path(destination_dir)
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        if str(source.parent).lower() == str(destination).lower():
            return source
        target_path = self._unique_output_path(destination / source.name)
        try:
            shutil.move(str(source), str(target_path))
        except Exception:
            return None
        return target_path

    def _move_saved_output_as(self, source_path: Path, destination_dir: Path, new_name: str) -> Path | None:
        source = Path(source_path)
        destination = Path(destination_dir)
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        target_path = self._unique_output_path(destination / new_name)
        try:
            shutil.move(str(source), str(target_path))
        except Exception:
            return None
        return target_path

    def _discard_saved_output(self, path: Path) -> bool:
        try:
            Path(path).unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def _on_save_prompt_completed(self, payload) -> None:
        pending = self._pending_post_action_save
        self._pending_post_action_save = None
        if not pending:
            return
        source_path = Path(str(pending.get("path", "")))
        output_kind = str(pending.get("output_kind", "") or "")
        label = SAVE_LOCATION_LABELS.get(output_kind, "File").lower()
        decision = self._save_prompt_processor.parse(
            str((payload or {}).get("heard_text", "") or ""),
            success=bool((payload or {}).get("success")),
        )
        if decision.action == "discard":
            if self._discard_saved_output(source_path):
                self.last_action_label.setText(f"Last action: discarded {label}")
            else:
                self.last_action_label.setText(f"Last action: could not discard {label}")
            return
        if decision.action in {"move", "move_rename"} and decision.folder is not None and source_path.exists():
            custom_name = getattr(decision, "custom_name", None)
            if decision.action == "move_rename" and custom_name:
                new_name = f"{custom_name}{source_path.suffix}"
                moved_path = self._move_saved_output_as(source_path, decision.folder, new_name)
            else:
                moved_path = self._move_saved_output(source_path, decision.folder)
            if moved_path is not None:
                self.last_action_label.setText(f"Last action: saved {label} to {moved_path}")
                return
        if source_path.exists():
            self.last_action_label.setText(f"Last action: saved {label} to {source_path}")

    def _save_drawing_snapshot(self) -> None:
        target_path = self._next_output_path("drawings", ".png")
        path = self.draw_overlay.save_canvas_snapshot(target_path=target_path)
        if path is not None:
            self.last_action_label.setText(f"Last action: saved drawing to {path}")
            self._queue_post_action_save_prompt("drawings", path)
        else:
            self.last_action_label.setText("Last action: could not save drawing")

    def _screens_union_geometry(self) -> QRect:
        screens = [screen for screen in QGuiApplication.screens() if screen is not None]
        if not screens:
            return QRect()
        geometry = screens[0].geometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        return geometry

    def _capture_monitor_options(self) -> list[tuple[str, QRect]]:
        screens = [screen for screen in QGuiApplication.screens() if screen is not None]
        if not screens:
            return []
        if len(screens) == 1:
            return [("Main Monitor", QRect(screens[0].geometry()))]
        options: list[tuple[str, QRect]] = []
        for index, screen in enumerate(screens, start=1):
            label = f"Monitor {index}"
            try:
                if screen == QGuiApplication.primaryScreen():
                    label += " (Main)"
            except Exception:
                pass
            options.append((label, QRect(screen.geometry())))
        options.append(("All Monitors", QRect(self._screens_union_geometry())))
        return options

    def _choose_full_capture_region(self, action_label: str) -> QRect | None:
        options = self._capture_monitor_options()
        if not options:
            return None
        if len(options) == 1:
            return QRect(options[0][1])
        dialog = CaptureMonitorDialog(self.config, action_label, options, self)
        self.last_action_label.setText(f"Last action: choose monitor for {action_label} with your hand")
        self._capture_monitor_dialog = dialog
        self._set_worker_utility_capture_selection_active(True)
        try:
            result = dialog.exec()
        finally:
            self._set_worker_utility_capture_selection_active(False)
            self._capture_monitor_dialog = None
        if result != QDialog.Accepted or dialog.selected_region is None:
            self.last_action_label.setText(f"Last action: {action_label} canceled")
            return None
        return QRect(dialog.selected_region)

    def _grab_global_region_pixmap(self, region: QRect | None = None) -> QPixmap:
        screens = [screen for screen in QGuiApplication.screens() if screen is not None]
        if not screens:
            return QPixmap()
        union = self._screens_union_geometry()
        target = QRect(union if region is None or region.isNull() else region.normalized())
        if target.isNull() or target.width() <= 0 or target.height() <= 0:
            return QPixmap()
        pixmap = QPixmap(target.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        for screen in screens:
            geo = screen.geometry()
            if not geo.intersects(target):
                continue
            painter.drawPixmap(geo.x() - target.x(), geo.y() - target.y(), screen.grabWindow(0))
        painter.end()
        return pixmap


    def _locate_ffmpeg_executable(self, tool_name: str) -> str | None:
        candidates: list[Path] = []
        exe_name = tool_name + (".exe" if sys.platform.startswith("win") else "")
        try:
            candidates.append(Path(sys.executable).resolve().with_name(exe_name))
        except Exception:
            pass
        try:
            candidates.append(Path.cwd() / exe_name)
        except Exception:
            pass
        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate)
            except Exception:
                pass
        return shutil.which(tool_name) or shutil.which(exe_name)
    def _run_external_probe(self, *args: str, timeout: float = 5.0) -> str:
        if not args:
            return ""
        try:
            completed = subprocess.run(
                list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return ""
        return (completed.stdout or "") + "\n" + (completed.stderr or "")
    def _detect_ffmpeg_capabilities(self) -> dict:
        capabilities = {
            "available": False,
            "encoders": set(),
            "filters": set(),
            "devices": set(),
            "preferred_encoder": "libx264",
        }
        if not self._ffmpeg_path:
            return capabilities
        capabilities["available"] = True
        encoders_text = self._run_external_probe(self._ffmpeg_path, "-hide_banner", "-encoders")
        for encoder_name in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264"):
            if encoder_name in encoders_text:
                capabilities["encoders"].add(encoder_name)
        filters_text = self._run_external_probe(self._ffmpeg_path, "-hide_banner", "-filters")
        for filter_name in ("gfxcapture", "ddagrab"):
            if filter_name in filters_text:
                capabilities["filters"].add(filter_name)
        devices_text = self._run_external_probe(self._ffmpeg_path, "-hide_banner", "-devices")
        if "gdigrab" in devices_text:
            capabilities["devices"].add("gdigrab")
        for preferred in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264"):
            if preferred in capabilities["encoders"]:
                capabilities["preferred_encoder"] = preferred
                break
        return capabilities
    def _ffmpeg_encoder_args(self, *, purpose: str, fps: float, segment_seconds: float | None = None) -> list[str]:
        encoder = str(self._ffmpeg_capabilities.get("preferred_encoder", "libx264") or "libx264")
        gop = max(1, int(round(float(fps) * float(segment_seconds if segment_seconds is not None else 2.0))))
        if encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq:v", "24", "-g", str(gop), "-pix_fmt", "yuv420p"]
        if encoder == "h264_amf":
            return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24", "-g", str(gop), "-pix_fmt", "yuv420p"]
        if encoder == "h264_qsv":
            return ["-c:v", "h264_qsv", "-global_quality", "24", "-look_ahead", "0", "-g", str(gop), "-pix_fmt", "nv12"]
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", str(gop), "-pix_fmt", "yuv420p"]
    def _matching_monitor_index(self, region: QRect | None) -> int | None:
        if region is None or region.isNull():
            return None
        target = QRect(region.normalized())
        for index, screen in enumerate([screen for screen in QGuiApplication.screens() if screen is not None]):
            try:
                if QRect(screen.geometry()) == target:
                    return index
            except Exception:
                continue
        return None
    def _ffmpeg_capture_input_args(self, region: QRect, *, fps: float, prefer_low_overhead: bool) -> list[str]:
        target = QRect(region.normalized())
        monitor_index = self._matching_monitor_index(target)
        if prefer_low_overhead and monitor_index is not None and "gfxcapture" in self._ffmpeg_capabilities.get("filters", set()):
            capture = f"gfxcapture=monitor_idx={monitor_index}:capture_cursor=1:max_framerate={max(30, int(round(float(fps) * 2.0)))}"
            return ["-f", "lavfi", "-i", capture]
        if prefer_low_overhead and monitor_index is not None and "ddagrab" in self._ffmpeg_capabilities.get("filters", set()):
            capture = f"ddagrab=output_idx={monitor_index}:draw_mouse=1:framerate={float(fps):.3f}"
            return ["-f", "lavfi", "-i", capture]
        return [
            "-f", "gdigrab",
            "-framerate", f"{float(fps):.3f}",
            "-draw_mouse", "1",
            "-offset_x", str(int(target.x())),
            "-offset_y", str(int(target.y())),
            "-video_size", f"{int(target.width())}x{int(target.height())}",
            "-i", "desktop",
        ]
    def _start_ffmpeg_process(self, command: list[str]) -> subprocess.Popen | None:
        if not command:
            return None
        try:
            return subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return None
    def _stop_ffmpeg_process(self, process: subprocess.Popen | None, *, timeout: float = 8.0) -> None:
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                try:
                    process.stdin.write(b"q\n")
                    process.stdin.flush()
                except Exception:
                    pass
            process.wait(timeout=timeout)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                process.wait(timeout=3.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
    def _ffmpeg_ready(self) -> bool:
        return bool(sys.platform.startswith("win") and self._ffmpeg_path and self._ffmpeg_capabilities.get("available"))
    def _ffmpeg_clip_list_path(self) -> Path:
        return self._clip_cache_dir() / "segments.csv"
    def _ffmpeg_clip_segment_pattern(self) -> Path:
        return self._clip_cache_dir() / "segment_%03d.mkv"
    def _cleanup_ffmpeg_clip_cache_files(self) -> None:
        cache_dir = self._clip_cache_dir()
        for pattern in ("segment_*.mkv", "segments.csv", "concat_*.txt"):
            for path in cache_dir.glob(pattern):
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass
    def _parse_ffmpeg_clip_manifest(self) -> list[dict]:
        list_path = self._clip_cache_list_path
        if list_path is None or not list_path.exists():
            return []
        entries: list[dict] = []
        try:
            with list_path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                for row in reader:
                    if len(row) < 3:
                        continue
                    raw_path = (row[0] or "").strip()
                    try:
                        start_time = float(row[1])
                        end_time = float(row[2])
                    except Exception:
                        continue
                    path = Path(raw_path)
                    if not path.is_absolute():
                        path = self._clip_cache_dir() / path
                    if not path.exists() or path.stat().st_size <= 0:
                        continue
                    entries.append({
                        "path": path,
                        "start_time": start_time,
                        "end_time": end_time,
                        "region": QRect(self._clip_cache_region) if self._clip_cache_region is not None else QRect(self._screens_union_geometry()),
                    })
        except Exception:
            return []
        return entries
    def _build_clip_concat_file(self, segments: list[dict]) -> Path | None:
        if not segments:
            return None
        concat_path = self._clip_cache_dir() / f"concat_{time.time_ns()}.txt"
        try:
            with concat_path.open("w", encoding="utf-8") as handle:
                for entry in segments:
                    file_path = str(Path(entry["path"]).resolve()).replace("'", "'\''")
                    handle.write(f"file '{file_path}'\n")
            return concat_path
        except Exception:
            try:
                concat_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
    def _clip_crop_filter(self, capture_region: QRect, target_region: QRect) -> str:
        capture_region = QRect(capture_region.normalized())
        target_region = QRect(target_region.normalized())
        if capture_region == target_region:
            return ""
        relative = QRect(target_region)
        relative.translate(-capture_region.x(), -capture_region.y())
        x = max(0, int(relative.x()))
        y = max(0, int(relative.y()))
        w = max(2, int(target_region.width()))
        h = max(2, int(target_region.height()))
        return f"crop={w}:{h}:{x}:{y}"

        def _save_screenshot_pixmap(self, pixmap: QPixmap) -> Path | None:
            if pixmap.isNull():
                return None
            pictures_dir = Path.home() / "Pictures"
            target_dir = pictures_dir if pictures_dir.exists() else Path.home()
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"hgr_screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
            return path if pixmap.save(str(path), "PNG") else None

        def _save_full_screen_screenshot(self) -> None:
            path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(None))
            if path is not None:
                self.last_action_label.setText(f"Last action: saved screenshot to {path}")
            else:
                self.last_action_label.setText("Last action: could not save screenshot")

        def _save_custom_region_screenshot(self, region: QRect) -> None:
            path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(region))
            if path is not None:
                self.last_action_label.setText(f"Last action: saved custom screenshot to {path}")
            else:
                self.last_action_label.setText("Last action: could not save custom screenshot")

        def _set_worker_utility_recording_active(self, active: bool) -> None:
            if self._worker is not None and hasattr(self._worker, "set_utility_recording_active"):
                try:
                    self._worker.set_utility_recording_active(bool(active))
                except Exception:
                    pass

        def _set_worker_utility_capture_selection_active(self, active: bool) -> None:
            if self._worker is not None and hasattr(self._worker, "set_utility_capture_selection_active"):
                try:
                    self._worker.set_utility_capture_selection_active(bool(active))
                except Exception:
                    pass

        def _start_countdown_overlay(self, seconds: int, finish_callback, *, label_prefix: str) -> bool:
            if self._utility_countdown_active:
                return False
            self._utility_countdown_active = True
            self._utility_countdown_token += 1
            token = self._utility_countdown_token
            seconds = max(1, int(seconds))

            for offset in range(seconds):
                value = seconds - offset
                delay_ms = offset * 1000
                def _show(value=value, token=token):
                    if token != self._utility_countdown_token or not self._utility_countdown_active:
                        return
                    self.countdown_overlay.show_countdown(value)
                    self.last_action_label.setText(f"Last action: {label_prefix} in {value}...")
                QTimer.singleShot(delay_ms, _show)

            def _finish(token=token):
                if token != self._utility_countdown_token or not self._utility_countdown_active:
                    return
                self.countdown_overlay.hide_countdown()
                self._utility_countdown_active = False
                finish_callback()

            QTimer.singleShot(seconds * 1000, _finish)
            return True

        def _start_full_screen_screenshot_countdown(self) -> bool:
            if self._utility_screenshot_pending or self._capture_region_selection_mode is not None:
                return False
            region = self._choose_full_capture_region("screenshot")
            if region is None or region.isNull():
                return True
            self._utility_screenshot_pending = True
            def _finish(region=QRect(region)) -> None:
                try:
                    path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(region))
                    if path is not None:
                        self.last_action_label.setText(f"Last action: saved screenshot to {path}")
                    else:
                        self.last_action_label.setText("Last action: could not save screenshot")
                finally:
                    self._utility_screenshot_pending = False
            started = self._start_countdown_overlay(3, _finish, label_prefix="full screenshot")
            if not started:
                self._utility_screenshot_pending = False
            return started

        def _begin_capture_region_selection(self, mode: str) -> bool:
            if self._capture_region_selection_mode is not None or self._utility_countdown_active:
                return False
            if self._screen_recording_active and mode != "record_stop":
                return False
            self._capture_region_selection_mode = str(mode or "")
            self._pending_capture_region = None
            self.last_action_label.setText("Last action: choose a capture area with your hand")
            self._set_worker_utility_capture_selection_active(True)
            self.capture_region_overlay.begin_selection(hand_control=True)
            return True

        def _on_capture_region_selected(self, rect: QRect) -> None:
            mode = self._capture_region_selection_mode
            self._capture_region_selection_mode = None
            self._set_worker_utility_capture_selection_active(False)
            self._pending_capture_region = QRect(rect.normalized())
            if self._pending_capture_region.width() <= 0 or self._pending_capture_region.height() <= 0:
                self.last_action_label.setText("Last action: capture area canceled")
                return
            if mode == "screenshot_custom":
                self._utility_screenshot_pending = True
                def _finish() -> None:
                    try:
                        if self._pending_capture_region is not None:
                            self._save_custom_region_screenshot(self._pending_capture_region)
                    finally:
                        self._utility_screenshot_pending = False
                        self._pending_capture_region = None
                started = self._start_countdown_overlay(3, _finish, label_prefix="custom screenshot")
                if not started:
                    self._utility_screenshot_pending = False
            elif mode == "record_custom":
                region = QRect(self._pending_capture_region)
                self._pending_capture_region = None
                self._start_screen_record_countdown(region)
            else:
                self._pending_capture_region = None

        def _on_capture_region_canceled(self) -> None:
            self._capture_region_selection_mode = None
            self._pending_capture_region = None
            self._set_worker_utility_capture_selection_active(False)
            self.last_action_label.setText("Last action: capture area canceled")

        def _clip_cache_dir(self) -> Path:
            target_dir = Path(tempfile.gettempdir()) / "hgr_clip_cache"
            target_dir.mkdir(parents=True, exist_ok=True)
            return target_dir

        def _clip_cache_output_path(self) -> Path:
            return self._clip_cache_dir() / f"clip_cache_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}.avi"

        def _clip_output_specs(self, duration_seconds: int) -> list[tuple[Path, str]]:
            videos_dir = Path.home() / "Videos"
            target_dir = videos_dir if videos_dir.exists() else Path.home()
            target_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%d_%H%M%S')
            label = f"{int(duration_seconds)}s"
            return [
                (target_dir / f"hgr_clip_{label}_{stamp}.mp4", 'mp4v'),
                (target_dir / f"hgr_clip_{label}_{stamp}.avi", 'XVID'),
                (target_dir / f"hgr_clip_{label}_{stamp}_mjpg.avi", 'MJPG'),
            ]

        def _open_video_writer(self, path: Path, codec_name: str, width: int, height: int, fps: float):
            fourcc = cv2.VideoWriter_fourcc(*codec_name)
            writer = cv2.VideoWriter(str(path), fourcc, float(fps), (int(width), int(height)))
            if writer.isOpened():
                return writer
            try:
                writer.release()
            except Exception:
                pass
            return None
    def _save_screenshot_pixmap(self, pixmap: QPixmap) -> Path | None:
        if pixmap.isNull():
            return None
        path = self._next_output_path("screenshots", ".png")
        return path if pixmap.save(str(path), "PNG") else None
    def _save_full_screen_screenshot(self) -> None:
        path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(None))
        if path is not None:
            self.last_action_label.setText(f"Last action: saved screenshot to {path}")
            self._queue_post_action_save_prompt("screenshots", path)
        else:
            self.last_action_label.setText("Last action: could not save screenshot")
    def _save_custom_region_screenshot(self, region: QRect) -> None:
        path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(region))
        if path is not None:
            self.last_action_label.setText(f"Last action: saved custom screenshot to {path}")
            self._queue_post_action_save_prompt("screenshots", path)
        else:
            self.last_action_label.setText("Last action: could not save custom screenshot")
    def _set_worker_utility_recording_active(self, active: bool) -> None:
        if self._worker is not None and hasattr(self._worker, "set_utility_recording_active"):
            try:
                self._worker.set_utility_recording_active(bool(active))
            except Exception:
                pass
    def _set_worker_utility_capture_selection_active(self, active: bool) -> None:
        if self._worker is not None and hasattr(self._worker, "set_utility_capture_selection_active"):
            try:
                self._worker.set_utility_capture_selection_active(bool(active))
            except Exception:
                pass
    def _start_countdown_overlay(self, seconds: int, finish_callback, *, label_prefix: str) -> bool:
        if self._utility_countdown_active:
            return False
        self._utility_countdown_active = True
        self._utility_countdown_token += 1
        token = self._utility_countdown_token
        seconds = max(1, int(seconds))

        for offset in range(seconds):
            value = seconds - offset
            delay_ms = offset * 1000
            def _show(value=value, token=token):
                if token != self._utility_countdown_token or not self._utility_countdown_active:
                    return
                self.countdown_overlay.show_countdown(value)
                self.last_action_label.setText(f"Last action: {label_prefix} in {value}...")
            QTimer.singleShot(delay_ms, _show)

        def _finish(token=token):
            if token != self._utility_countdown_token or not self._utility_countdown_active:
                return
            self.countdown_overlay.hide_countdown()
            self._utility_countdown_active = False
            finish_callback()

        QTimer.singleShot(seconds * 1000, _finish)
        return True
    def _start_full_screen_screenshot_countdown(self) -> bool:
        if (
            self._utility_screenshot_pending
            or self._capture_region_selection_mode is not None
            or getattr(self, "_capture_monitor_dialog", None) is not None
        ):
            return False
        return self._begin_monitor_selection_async("screenshot_full")
    def _begin_capture_region_selection(self, mode: str) -> bool:
        if self._capture_region_selection_mode is not None or self._utility_countdown_active:
            return False
        if self._screen_recording_active and mode != "record_stop":
            return False
        self._capture_region_selection_mode = str(mode or "")
        self._pending_capture_region = None
        self.last_action_label.setText("Last action: choose a capture area with your hand")
        self._set_worker_utility_capture_selection_active(True)
        self.capture_region_overlay.begin_selection(hand_control=True)
        return True
    def _on_capture_region_selected(self, rect: QRect) -> None:
        mode = self._capture_region_selection_mode
        self._capture_region_selection_mode = None
        self._set_worker_utility_capture_selection_active(False)
        self._pending_capture_region = QRect(rect.normalized())
        if self._pending_capture_region.width() <= 0 or self._pending_capture_region.height() <= 0:
            self.last_action_label.setText("Last action: capture area canceled")
            return
        if mode == "screenshot_custom":
            self._utility_screenshot_pending = True
            def _finish() -> None:
                try:
                    if self._pending_capture_region is not None:
                        self._save_custom_region_screenshot(self._pending_capture_region)
                finally:
                    self._utility_screenshot_pending = False
                    self._pending_capture_region = None
            started = self._start_countdown_overlay(3, _finish, label_prefix="custom screenshot")
            if not started:
                self._utility_screenshot_pending = False
        elif mode == "record_custom":
            region = QRect(self._pending_capture_region)
            self._pending_capture_region = None
            self._start_screen_record_countdown(region)
        else:
            self._pending_capture_region = None
    def _on_capture_region_canceled(self) -> None:
        self._capture_region_selection_mode = None
        self._pending_capture_region = None
        self._set_worker_utility_capture_selection_active(False)
        self.last_action_label.setText("Last action: capture area canceled")
    def _clip_cache_dir(self) -> Path:
        target_dir = Path(tempfile.gettempdir()) / "hgr_clip_cache"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir
    def _clip_cache_output_path(self) -> Path:
        return self._clip_cache_dir() / f"clip_cache_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}.avi"
    def _clip_output_specs(self, duration_seconds: int) -> list[tuple[Path, str]]:
        label = f"{int(duration_seconds)}s"
        base = self._next_output_path("clips", ".mp4", extra_label=label)
        stem = base.stem
        target_dir = base.parent
        return [
            (base, 'mp4v'),
            (self._unique_output_path(target_dir / f"{stem}.avi"), 'XVID'),
            (self._unique_output_path(target_dir / f"{stem}_mjpg.avi"), 'MJPG'),
        ]
    def _open_video_writer(self, path: Path, codec_name: str, width: int, height: int, fps: float):
        fourcc = cv2.VideoWriter_fourcc(*codec_name)
        writer = cv2.VideoWriter(str(path), fourcc, float(fps), (int(width), int(height)))
        if writer.isOpened():
            return writer
        try:
            writer.release()
        except Exception:
            pass
        return None
    def _finalize_clip_cache_segment(self) -> None:
        writer = self._clip_cache_segment_writer
        path = self._clip_cache_segment_path
        region = QRect(self._clip_cache_region) if self._clip_cache_region is not None else None
        frame_count = int(self._clip_cache_segment_frame_count)
        started_at = float(self._clip_cache_segment_started_at or time.time())
        self._clip_cache_segment_writer = None
        self._clip_cache_segment_path = None
        self._clip_cache_segment_started_at = 0.0
        self._clip_cache_segment_frame_count = 0
        try:
            if writer is not None:
                writer.release()
        except Exception:
            pass
        if path is None:
            return
        if frame_count <= 0 or not path.exists() or path.stat().st_size <= 0:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            return
        self._clip_cache_segments.append({
            "path": path,
            "frame_count": frame_count,
            "start_time": started_at,
            "end_time": time.time(),
            "region": region,
        })
        self._prune_clip_cache_segments()
    def _start_new_clip_cache_segment(self) -> bool:
        region = self._normalized_record_region(self._screens_union_geometry())
        if region.isNull() or region.width() <= 1 or region.height() <= 1:
            return False
        path = self._clip_cache_output_path()
        writer = self._open_video_writer(path, 'MJPG', region.width(), region.height(), self._clip_cache_fps)
        if writer is None:
            return False
        self._clip_cache_region = QRect(region)
        self._clip_cache_segment_writer = writer
        self._clip_cache_segment_path = path
        self._clip_cache_segment_started_at = time.time()
        self._clip_cache_segment_frame_count = 0
        return True
    def _rotate_clip_cache_segment(self) -> bool:
        self._finalize_clip_cache_segment()
        return self._start_new_clip_cache_segment()
    def _prune_clip_cache_segments(self) -> None:
        cutoff = time.time() - float(self._clip_cache_max_seconds)
        kept = []
        for meta in self._clip_cache_segments:
            try:
                path = meta.get("path")
                end_time = float(meta.get("end_time", 0.0) or 0.0)
            except Exception:
                path = None
                end_time = 0.0
            if path is None:
                continue
            if end_time < cutoff:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            kept.append(meta)
        self._clip_cache_segments = kept
    def _capture_clip_cache_frame(self) -> None:
        if self._clip_cache_segment_writer is None or self._clip_cache_region is None:
            return
        frame = self._grab_global_region_bgr_frame(self._clip_cache_region)
        if frame is None:
            return
        expected_w = int(self._clip_cache_region.width())
        expected_h = int(self._clip_cache_region.height())
        if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
            frame = cv2.resize(frame, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
        try:
            self._clip_cache_segment_writer.write(frame)
            self._clip_cache_segment_frame_count += 1
        except Exception:
            return
        if (time.time() - self._clip_cache_segment_started_at) >= float(self._clip_cache_segment_seconds):
            self._rotate_clip_cache_segment()
    def _crop_cached_frame_to_region(self, frame: np.ndarray, capture_region: QRect, target_region: QRect) -> np.ndarray | None:
        if frame is None or frame.size == 0:
            return None
        capture_region = QRect(capture_region.normalized())
        target_region = QRect(target_region.normalized())
        if target_region == capture_region:
            return frame
        relative = QRect(target_region)
        relative.translate(-capture_region.x(), -capture_region.y())
        x = max(0, int(relative.x()))
        y = max(0, int(relative.y()))
        w = min(int(relative.width()), frame.shape[1] - x)
        h = min(int(relative.height()), frame.shape[0] - y)
        if w <= 0 or h <= 0:
            return None
        cropped = frame[y:y + h, x:x + w]
        expected_w = max(2, int(target_region.width()))
        expected_h = max(2, int(target_region.height()))
        if cropped.shape[1] != expected_w or cropped.shape[0] != expected_h:
            cropped = cv2.resize(cropped, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
        return cropped
    def _record_output_specs(self) -> list[tuple[Path, str]]:
        base = self._next_output_path("screen_recordings", ".mp4")
        stem = base.stem
        target_dir = base.parent
        return [
            (base, 'mp4v'),
            (self._unique_output_path(target_dir / f"{stem}.avi"), 'XVID'),
            (self._unique_output_path(target_dir / f"{stem}_mjpg.avi"), 'MJPG'),
        ]
    def _normalized_record_region(self, region: QRect | None) -> QRect:
        target = QRect(self._screens_union_geometry() if region is None or region.isNull() else region.normalized())
        if target.width() % 2 != 0:
            target.setWidth(max(2, target.width() - 1))
        if target.height() % 2 != 0:
            target.setHeight(max(2, target.height() - 1))
        return target
    def _grab_global_region_bgr_frame(self, region: QRect) -> np.ndarray | None:
        target = QRect(region.normalized())
        if target.isNull() or target.width() <= 0 or target.height() <= 0:
            return None
        if not sys.platform.startswith("win"):
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
        except Exception:
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))

        SRCCOPY = 0x00CC0020
        DIB_RGB_COLORS = 0
        BI_RGB = 0

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        width = int(target.width())
        height = int(target.height())
        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            user32.ReleaseDC(0, hdc_screen)
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        if not hbm:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        old_obj = gdi32.SelectObject(hdc_mem, hbm)
        try:
            if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, int(target.x()), int(target.y()), SRCCOPY):
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            buf = (ctypes.c_ubyte * (width * height * 4))()
            rows = gdi32.GetDIBits(hdc_mem, hbm, 0, height, ctypes.byref(buf), ctypes.byref(bmi), DIB_RGB_COLORS)
            if rows != height:
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            arr = np.ctypeslib.as_array(buf).reshape((height, width, 4)).copy()
            return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        finally:
            if old_obj:
                gdi32.SelectObject(hdc_mem, old_obj)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
    def _pixmap_to_bgr_frame(self, pixmap: QPixmap) -> np.ndarray | None:
        if pixmap.isNull():
            return None
        image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        width = image.width()
        height = image.height()
        if width <= 0 or height <= 0:
            return None
        ptr = image.bits()
        size = int(image.sizeInBytes())
        try:
            arr = np.frombuffer(ptr, dtype=np.uint8, count=size).copy().reshape((height, width, 4))
        except Exception:
            try:
                raw = ptr.tobytes()
            except Exception:
                try:
                    raw = bytes(ptr[:size])
                except Exception:
                    return None
            arr = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    def _start_screen_record_countdown(self, region: QRect | None = None) -> bool:
        if (
            self._screen_recording_active
            or self._capture_region_selection_mode is not None
            or self._utility_countdown_active
            or getattr(self, "_capture_monitor_dialog", None) is not None
        ):
            return False
        if region is not None and not region.isNull():
            target_region = self._normalized_record_region(QRect(region))
            if target_region.isNull() or target_region.width() <= 1 or target_region.height() <= 1:
                return False
            return self._start_countdown_overlay(
                3,
                lambda region=QRect(target_region): self._start_screen_recording(region),
                label_prefix="screen record",
            )
        return self._begin_monitor_selection_async("record_full")
    def _capture_screen_record_frame(self) -> None:
        if not self._screen_recording_active or self._screen_record_writer is None or self._screen_record_region is None:
            return
        frame = self._grab_global_region_bgr_frame(self._screen_record_region)
        if frame is None and self._screen_record_region is not None:
            overlay_was_visible = self.recording_overlay.isVisible()
            if overlay_was_visible:
                self.recording_overlay.hide_indicator()
            try:
                frame = self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(self._screen_record_region))
            finally:
                if overlay_was_visible and self._screen_recording_active:
                    self.recording_overlay.show_indicator()
        if frame is None:
            return
        if self._screen_record_frame_size is not None:
            expected_w, expected_h = self._screen_record_frame_size
            if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
                frame = cv2.resize(frame, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
        self._screen_record_writer.write(frame)
    def _start_clip_cache(self) -> bool:
        if self._ffmpeg_ready() and self._start_clip_cache_ffmpeg():
            return True
        if self._clip_cache_segment_writer is not None and self._clip_cache_timer.isActive():
            return True
        region = self._normalized_record_region(self._screens_union_geometry())
        if region.isNull() or region.width() <= 1 or region.height() <= 1:
            return False
        self._clip_cache_backend = "opencv"
        self._clip_cache_region = QRect(region)
        path = self._clip_cache_output_path()
        writer = self._open_video_writer(path, 'MJPG', region.width(), region.height(), self._clip_cache_fps)
        if writer is None:
            return False
        self._clip_cache_segment_writer = writer
        self._clip_cache_segment_path = path
        self._clip_cache_segment_started_at = time.time()
        self._clip_cache_segment_frame_count = 0
        self._clip_cache_timer.start()
        return True

        def _finalize_clip_cache_segment(self) -> None:
            writer = self._clip_cache_segment_writer
            path = self._clip_cache_segment_path
            region = QRect(self._clip_cache_region) if self._clip_cache_region is not None else None
            frame_count = int(self._clip_cache_segment_frame_count)
            started_at = float(self._clip_cache_segment_started_at or time.time())
            self._clip_cache_segment_writer = None
            self._clip_cache_segment_path = None
            self._clip_cache_segment_started_at = 0.0
            self._clip_cache_segment_frame_count = 0
            try:
                if writer is not None:
                    writer.release()
            except Exception:
                pass
            if path is None:
                return
            if frame_count <= 0 or not path.exists() or path.stat().st_size <= 0:
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
                return
            self._clip_cache_backend = "opencv"
            self._clip_cache_segments.append({
                "path": path,
                "frame_count": frame_count,
                "start_time": started_at,
                "end_time": time.time(),
                "region": region,
            })
            self._prune_clip_cache_segments()

        def _start_new_clip_cache_segment(self) -> bool:
            region = self._normalized_record_region(self._screens_union_geometry())
            if region.isNull() or region.width() <= 1 or region.height() <= 1:
                return False
            path = self._clip_cache_output_path()
            writer = self._open_video_writer(path, 'MJPG', region.width(), region.height(), self._clip_cache_fps)
            if writer is None:
                return False
            self._clip_cache_region = QRect(region)
            self._clip_cache_segment_writer = writer
            self._clip_cache_segment_path = path
            self._clip_cache_segment_started_at = time.time()
            self._clip_cache_segment_frame_count = 0
            return True

        def _rotate_clip_cache_segment(self) -> bool:
            self._finalize_clip_cache_segment()
            return self._start_new_clip_cache_segment()

        def _prune_clip_cache_segments(self) -> None:
            cutoff = time.time() - float(self._clip_cache_max_seconds)
            kept = []
            for meta in self._clip_cache_segments:
                try:
                    path = meta.get("path")
                    end_time = float(meta.get("end_time", 0.0) or 0.0)
                except Exception:
                    path = None
                    end_time = 0.0
                if path is None:
                    continue
                if end_time < cutoff:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue
                kept.append(meta)
            self._clip_cache_segments = kept
    def _stop_clip_cache(self) -> None:
        if self._clip_cache_backend == "ffmpeg":
            self._stop_clip_cache_ffmpeg(delete_files=True)
            return
        self._clip_cache_timer.stop()
        self._finalize_clip_cache_segment()
        for meta in self._clip_cache_segments:
            try:
                Path(meta.get("path")).unlink(missing_ok=True)
            except Exception:
                pass
        self._clip_cache_segments = []
        self._clip_cache_region = None
        self._clip_cache_backend = ""
    def _start_clip_cache_ffmpeg(self) -> bool:
        if self._clip_cache_process is not None and self._clip_cache_process.poll() is None:
            self._clip_cache_backend = "ffmpeg"
            return True
        region = self._normalized_record_region(self._screens_union_geometry())
        if region.isNull() or region.width() <= 1 or region.height() <= 1 or not self._ffmpeg_ready():
            return False
        self._cleanup_ffmpeg_clip_cache_files()
        self._clip_cache_region = QRect(region)
        self._clip_cache_list_path = self._ffmpeg_clip_list_path()
        self._clip_cache_segment_pattern = self._ffmpeg_clip_segment_pattern()
        command = [
            self._ffmpeg_path,
            "-hide_banner", "-loglevel", "error", "-y",
            *self._ffmpeg_capture_input_args(region, fps=self._clip_cache_fps, prefer_low_overhead=False),
            "-an",
            *self._ffmpeg_encoder_args(purpose="clip", fps=self._clip_cache_fps, segment_seconds=self._clip_cache_segment_seconds),
            "-force_key_frames", f"expr:gte(t,n_forced*{float(self._clip_cache_segment_seconds):.3f})",
            "-f", "segment",
            "-segment_time", f"{float(self._clip_cache_segment_seconds):.3f}",
            "-segment_wrap", str(int(self._clip_cache_wrap_count)),
            "-segment_list", str(self._clip_cache_list_path),
            "-segment_list_type", "csv",
            "-segment_list_size", str(int(self._clip_cache_wrap_count)),
            "-reset_timestamps", "1",
            str(self._clip_cache_segment_pattern),
        ]
        process = self._start_ffmpeg_process(command)
        if process is None:
            return False
        self._clip_cache_process = process
        self._clip_cache_backend = "ffmpeg"
        return True
    def _stop_clip_cache_ffmpeg(self, *, delete_files: bool) -> None:
        process = self._clip_cache_process
        self._clip_cache_process = None
        self._stop_ffmpeg_process(process)
        if delete_files:
            self._cleanup_ffmpeg_clip_cache_files()
            self._clip_cache_list_path = None
            self._clip_cache_segment_pattern = None
            self._clip_cache_region = None
        self._clip_cache_backend = ""

        def _capture_clip_cache_frame(self) -> None:
            if self._clip_cache_segment_writer is None or self._clip_cache_region is None:
                return
            frame = self._grab_global_region_bgr_frame(self._clip_cache_region)
            if frame is None:
                return
            expected_w = int(self._clip_cache_region.width())
            expected_h = int(self._clip_cache_region.height())
            if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
                frame = cv2.resize(frame, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
            try:
                self._clip_cache_segment_writer.write(frame)
                self._clip_cache_segment_frame_count += 1
            except Exception:
                return
            if (time.time() - self._clip_cache_segment_started_at) >= float(self._clip_cache_segment_seconds):
                self._rotate_clip_cache_segment()

        def _crop_cached_frame_to_region(self, frame: np.ndarray, capture_region: QRect, target_region: QRect) -> np.ndarray | None:
            if frame is None or frame.size == 0:
                return None
            capture_region = QRect(capture_region.normalized())
            target_region = QRect(target_region.normalized())
            if target_region == capture_region:
                return frame
            relative = QRect(target_region)
            relative.translate(-capture_region.x(), -capture_region.y())
            x = max(0, int(relative.x()))
            y = max(0, int(relative.y()))
            w = min(int(relative.width()), frame.shape[1] - x)
            h = min(int(relative.height()), frame.shape[0] - y)
            if w <= 0 or h <= 0:
                return None
            cropped = frame[y:y + h, x:x + w]
            expected_w = max(2, int(target_region.width()))
            expected_h = max(2, int(target_region.height()))
            if cropped.shape[1] != expected_w or cropped.shape[0] != expected_h:
                cropped = cv2.resize(cropped, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
            return cropped
    def _export_recent_clip_ffmpeg(self, duration_seconds: int, target_region: QRect) -> bool:
        was_active = self._clip_cache_backend == "ffmpeg" and self._clip_cache_process is not None
        if was_active:
            self._stop_clip_cache_ffmpeg(delete_files=False)
        try:
            entries = self._parse_ffmpeg_clip_manifest()
            if not entries:
                return False
            selected: list[dict] = []
            covered = 0.0
            for entry in reversed(entries):
                segment_seconds = max(1e-3, float(entry.get("end_time", 0.0)) - float(entry.get("start_time", 0.0)))
                selected.append(entry)
                covered += segment_seconds
                if covered >= float(duration_seconds):
                    break
            if not selected:
                return False
            selected.reverse()
            total_duration = sum(max(1e-3, float(entry.get("end_time", 0.0)) - float(entry.get("start_time", 0.0))) for entry in selected)
            start_trim = max(0.0, total_duration - float(duration_seconds))
            concat_path = self._build_clip_concat_file(selected)
            if concat_path is None:
                return False
            try:
                output_path = self._clip_output_specs(duration_seconds)[0][0]
                capture_region = QRect(self._clip_cache_region) if self._clip_cache_region is not None else QRect(self._screens_union_geometry())
                filters = []
                crop_filter = self._clip_crop_filter(capture_region, target_region)
                if crop_filter:
                    filters.append(crop_filter)
                filters.append(f"trim=start={start_trim:.3f}:duration={float(duration_seconds):.3f}")
                filters.append("setpts=PTS-STARTPTS")
                command = [
                    self._ffmpeg_path,
                    "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat_path),
                    "-an",
                    "-vf", ",".join(filters),
                    *self._ffmpeg_encoder_args(purpose="clip_export", fps=self._clip_cache_fps),
                    str(output_path),
                ]
                completed = subprocess.run(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
                    actual_seconds = min(float(duration_seconds), max(0.0, total_duration))
                    self.last_action_label.setText(f"Last action: saved {actual_seconds:.1f}s clip to {output_path}")
                    self._queue_post_action_save_prompt("clips", output_path)
                    return True
                return False
            finally:
                try:
                    concat_path.unlink(missing_ok=True)
                except Exception:
                    pass
        finally:
            self._cleanup_ffmpeg_clip_cache_files()
            if was_active and self._worker is not None and getattr(self._worker, "is_running", False):
                self._start_clip_cache_ffmpeg()
    def _export_recent_clip_opencv(self, duration_seconds: int, target_region: QRect) -> bool:
        if self._clip_cache_segment_writer is not None:
            self._rotate_clip_cache_segment()
        segments = [meta for meta in self._clip_cache_segments if Path(meta.get("path")).exists() and int(meta.get("frame_count", 0) or 0) > 0]
        if not segments:
            return False
        selected_segments: list[tuple[dict, int]] = []
        covered_seconds = 0.0
        for meta in reversed(segments):
            frame_count = int(meta.get("frame_count", 0) or 0)
            if frame_count <= 0:
                continue
            start_time = float(meta.get("start_time", 0.0) or 0.0)
            end_time = float(meta.get("end_time", start_time) or start_time)
            segment_seconds = max(1e-3, end_time - start_time)
            if covered_seconds + segment_seconds <= float(duration_seconds):
                selected_segments.append((meta, 0))
                covered_seconds += segment_seconds
                continue
            needed_seconds = max(0.0, float(duration_seconds) - covered_seconds)
            keep_ratio = min(1.0, max(0.0, needed_seconds / segment_seconds))
            keep_frames = max(1, int(round(frame_count * keep_ratio)))
            skip_frames = max(0, frame_count - keep_frames)
            selected_segments.append((meta, skip_frames))
            covered_seconds += min(segment_seconds, needed_seconds)
            break
        if not selected_segments:
            return False
        selected_segments.reverse()
        estimated_frames = sum(max(0, int(meta.get("frame_count", 0) or 0) - int(skip)) for meta, skip in selected_segments)
        output_fps = max(1.0, min(30.0, float(estimated_frames) / max(1e-3, covered_seconds)))
        output_writer = None
        output_path = None
        for candidate_path, codec_name in self._clip_output_specs(duration_seconds):
            candidate_writer = self._open_video_writer(candidate_path, codec_name, target_region.width(), target_region.height(), output_fps)
            if candidate_writer is not None:
                output_writer = candidate_writer
                output_path = candidate_path
                break
        if output_writer is None or output_path is None:
            return False
        written = 0
        try:
            for meta, skip_frames in selected_segments:
                path = Path(meta.get("path"))
                capture_region = meta.get("region") or self._clip_cache_region or self._screens_union_geometry()
                capture_region = QRect(capture_region)
                cap = cv2.VideoCapture(str(path))
                local_index = 0
                try:
                    while True:
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            break
                        if local_index >= int(skip_frames):
                            cropped = self._crop_cached_frame_to_region(frame, capture_region, target_region)
                            if cropped is not None:
                                output_writer.write(cropped)
                                written += 1
                        local_index += 1
                finally:
                    cap.release()
        finally:
            try:
                output_writer.release()
            except Exception:
                pass
        if written > 0 and output_path.exists() and output_path.stat().st_size > 1024:
            actual_seconds = written / float(output_fps) if output_fps > 0 else 0.0
            self.last_action_label.setText(f"Last action: saved {actual_seconds:.1f}s clip to {output_path}")
            self._queue_post_action_save_prompt("clips", output_path)
            return True
        return False
    def _export_recent_clip(self, duration_seconds: int) -> bool:
        duration_seconds = int(max(1, duration_seconds))
        if self._utility_countdown_active or self._capture_region_selection_mode is not None:
            return False
        if getattr(self, "_capture_monitor_dialog", None) is not None:
            return False
        options = self._capture_monitor_options()
        if not options:
            return False

        def _do_export(region: QRect) -> None:
            target = self._normalized_record_region(region)
            if target.isNull() or target.width() <= 1 or target.height() <= 1:
                self.last_action_label.setText("Last action: clip canceled")
                return
            if self._ffmpeg_ready() and self._clip_cache_backend == "ffmpeg":
                success = self._export_recent_clip_ffmpeg(duration_seconds, target)
            else:
                success = self._export_recent_clip_opencv(duration_seconds, target)
            if not success:
                self.last_action_label.setText("Last action: no recent clip available yet")

        if len(options) == 1:
            _do_export(QRect(options[0][1]))
            return True

        dialog = CaptureMonitorDialog(self.config, f"clip {duration_seconds} sec", options, self)
        self._capture_monitor_dialog = dialog
        self._capture_monitor_selection_mode = f"clip_{duration_seconds}s"
        self.last_action_label.setText("Last action: choose monitor for clip with your hand")
        self._set_worker_utility_capture_selection_active(True)

        def _on_selected(region: QRect) -> None:
            self._clear_capture_monitor_dialog_state()
            _do_export(QRect(region.normalized()))

        def _on_canceled() -> None:
            self._clear_capture_monitor_dialog_state()
            self.last_action_label.setText("Last action: clip canceled")

        dialog.selection_made.connect(_on_selected)
        dialog.canceled.connect(_on_canceled)
        if self._worker is not None:
            try:
                self._worker.debug_frame_ready.connect(dialog.handle_debug_frame)
            except Exception:
                pass
        dialog.show()
        dialog.raise_()
        dialog.update()
        return True
    def _start_screen_recording_ffmpeg(self, region: QRect) -> bool:
        if not self._ffmpeg_ready():
            return False
        region = self._normalized_record_region(region)
        if region.isNull() or region.width() <= 1 or region.height() <= 1:
            return False
        output_path = self._record_output_specs()[0][0]
        command = [
            self._ffmpeg_path,
            "-hide_banner", "-loglevel", "error", "-y",
            *self._ffmpeg_capture_input_args(region, fps=self._screen_record_fps, prefer_low_overhead=True),
            "-an",
            *self._ffmpeg_encoder_args(purpose="record", fps=self._screen_record_fps),
            str(output_path),
        ]
        process = self._start_ffmpeg_process(command)
        if process is None:
            return False
        self._screen_record_process = process
        self._screen_record_backend = "ffmpeg"
        self._screen_record_region = QRect(region)
        self._screen_record_path = output_path
        self._screen_record_frame_size = (region.width(), region.height())
        self._screen_recording_active = True
        self._set_worker_utility_recording_active(True)
        self.recording_overlay.show_indicator()
        self.last_action_label.setText(f"Last action: screen recording started {output_path}")
        return True

        def _record_output_specs(self) -> list[tuple[Path, str]]:
            videos_dir = Path.home() / "Videos"
            target_dir = videos_dir if videos_dir.exists() else Path.home()
            target_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%d_%H%M%S')
            return [
                (target_dir / f"hgr_record_{stamp}.mp4", 'mp4v'),
                (target_dir / f"hgr_record_{stamp}.avi", 'XVID'),
                (target_dir / f"hgr_record_{stamp}_mjpg.avi", 'MJPG'),
            ]

        def _normalized_record_region(self, region: QRect | None) -> QRect:
            target = QRect(self._screens_union_geometry() if region is None or region.isNull() else region.normalized())
            if target.width() % 2 != 0:
                target.setWidth(max(2, target.width() - 1))
            if target.height() % 2 != 0:
                target.setHeight(max(2, target.height() - 1))
            return target

        def _grab_global_region_bgr_frame(self, region: QRect) -> np.ndarray | None:
            target = QRect(region.normalized())
            if target.isNull() or target.width() <= 0 or target.height() <= 0:
                return None
            if not sys.platform.startswith("win"):
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            try:
                user32 = ctypes.windll.user32
                gdi32 = ctypes.windll.gdi32
            except Exception:
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))

            SRCCOPY = 0x00CC0020
            DIB_RGB_COLORS = 0
            BI_RGB = 0

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD),
                    ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD),
                ]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

            width = int(target.width())
            height = int(target.height())
            hdc_screen = user32.GetDC(0)
            if not hdc_screen:
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            if not hdc_mem:
                user32.ReleaseDC(0, hdc_screen)
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not hbm:
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(0, hdc_screen)
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            old_obj = gdi32.SelectObject(hdc_mem, hbm)
            try:
                if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, int(target.x()), int(target.y()), SRCCOPY):
                    return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
                bmi = BITMAPINFO()
                bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.bmiHeader.biWidth = width
                bmi.bmiHeader.biHeight = -height
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = BI_RGB
                buf = (ctypes.c_ubyte * (width * height * 4))()
                rows = gdi32.GetDIBits(hdc_mem, hbm, 0, height, ctypes.byref(buf), ctypes.byref(bmi), DIB_RGB_COLORS)
                if rows != height:
                    return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
                arr = np.ctypeslib.as_array(buf).reshape((height, width, 4)).copy()
                return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
            finally:
                if old_obj:
                    gdi32.SelectObject(hdc_mem, old_obj)
                gdi32.DeleteObject(hbm)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(0, hdc_screen)

        def _pixmap_to_bgr_frame(self, pixmap: QPixmap) -> np.ndarray | None:
            if pixmap.isNull():
                return None
            image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
            width = image.width()
            height = image.height()
            if width <= 0 or height <= 0:
                return None
            ptr = image.bits()
            size = int(image.sizeInBytes())
            try:
                arr = np.frombuffer(ptr, dtype=np.uint8, count=size).copy().reshape((height, width, 4))
            except Exception:
                try:
                    raw = ptr.tobytes()
                except Exception:
                    try:
                        raw = bytes(ptr[:size])
                    except Exception:
                        return None
                arr = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

        def _start_screen_record_countdown(self, region: QRect | None = None) -> bool:
            if self._screen_recording_active or self._capture_region_selection_mode is not None or self._utility_countdown_active:
                return False
            chosen_region = QRect(region) if region is not None and not region.isNull() else self._choose_full_capture_region("record")
            if chosen_region is None or chosen_region.isNull():
                return True
            target_region = self._normalized_record_region(chosen_region)
            if target_region.isNull() or target_region.width() <= 1 or target_region.height() <= 1:
                return False
            return self._start_countdown_overlay(3, lambda region=QRect(target_region): self._start_screen_recording(region), label_prefix="screen record")
    def _start_screen_recording(self, region: QRect) -> None:
        region = self._normalized_record_region(region)
        if self._start_screen_recording_ffmpeg(region):
            return
        writer = None
        path = None
        for candidate_path, codec_name in self._record_output_specs():
            fourcc = cv2.VideoWriter_fourcc(*codec_name)
            candidate_writer = cv2.VideoWriter(str(candidate_path), fourcc, float(self._screen_record_fps), (region.width(), region.height()))
            if candidate_writer.isOpened():
                writer = candidate_writer
                path = candidate_path
                break
            try:
                candidate_writer.release()
            except Exception:
                pass
        if writer is None or path is None:
            self.last_action_label.setText("Last action: could not start screen recording")
            self._set_worker_utility_recording_active(False)
            return
        self._screen_record_backend = "opencv"
        self._screen_record_writer = writer
        self._screen_record_process = None
        self._screen_record_region = QRect(region)
        self._screen_record_path = path
        self._screen_record_frame_size = (region.width(), region.height())
        self._screen_recording_active = True
        self._set_worker_utility_recording_active(True)
        self.recording_overlay.show_indicator()
        self._capture_screen_record_frame()
        self._screen_record_timer.start()
        self.last_action_label.setText(f"Last action: screen recording started {path}")

        def _capture_screen_record_frame(self) -> None:
            if self._screen_record_backend == "ffmpeg":
                return
            if not self._screen_recording_active or self._screen_record_writer is None or self._screen_record_region is None:
                return
            frame = self._grab_global_region_bgr_frame(self._screen_record_region)
            if frame is None and self._screen_record_region is not None:
                overlay_was_visible = self.recording_overlay.isVisible()
                if overlay_was_visible:
                    self.recording_overlay.hide_indicator()
                try:
                    frame = self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(self._screen_record_region))
                finally:
                    if overlay_was_visible and self._screen_recording_active:
                        self.recording_overlay.show_indicator()
            if frame is None:
                return
            if self._screen_record_frame_size is not None:
                expected_w, expected_h = self._screen_record_frame_size
                if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
                    frame = cv2.resize(frame, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
            self._screen_record_writer.write(frame)
    def _stop_screen_recording(self) -> bool:
        if not self._screen_recording_active:
            return False
        self._screen_record_timer.stop()
        self.recording_overlay.hide_indicator()
        writer = self._screen_record_writer
        process = self._screen_record_process
        path = self._screen_record_path
        backend = self._screen_record_backend
        self._screen_record_writer = None
        self._screen_record_process = None
        self._screen_record_region = None
        self._screen_record_frame_size = None
        self._screen_record_path = None
        self._screen_record_backend = ""
        self._screen_recording_active = False
        self._set_worker_utility_recording_active(False)
        try:
            if backend == "ffmpeg" and process is not None:
                self._stop_ffmpeg_process(process)
            elif writer is not None:
                writer.release()
        finally:
            if path is not None and path.exists() and path.stat().st_size > 1024:
                self.last_action_label.setText(f"Last action: saved screen recording to {path}")
                self._queue_post_action_save_prompt("screen_recordings", path)
            elif path is not None:
                self.last_action_label.setText("Last action: screen recording failed to save")
            else:
                self.last_action_label.setText("Last action: screen recording stopped")
        return True

        def _on_worker_debug_frame(self, frame, info) -> None:
            if not isinstance(info, dict):
                return
            drawing_target = str(info.get("drawing_render_target", self._drawing_render_target) or self._drawing_render_target)
            self._set_drawing_render_target(drawing_target)
            request_token = int(info.get("drawing_request_token", 0) or 0)
            request_action = str(info.get("drawing_request_action", "") or "")
            if request_token > self._last_drawing_request_token:
                handled = False
                if request_action == "pen_options":
                    handled = self._open_pen_options_dialog_from_gesture()
                elif request_action == "eraser_options":
                    handled = self._open_eraser_options_dialog_from_gesture()
                elif request_action == "save":
                    self._save_drawing_snapshot()
                    handled = True
                elif request_action == "undo":
                    handled = True
                    if self.draw_overlay.undo_last_action():
                        self.last_action_label.setText("Last action: drawing undo")
                elif request_action == "clear":
                    self.draw_overlay.push_undo_state()
                    self.draw_overlay.clear_canvas()
                    self.last_action_label.setText("Last action: drawing cleared")
                    handled = True
                if handled:
                    self._last_drawing_request_token = request_token
                    if self._worker is not None and hasattr(self._worker, "acknowledge_drawing_request"):
                        try:
                            self._worker.acknowledge_drawing_request(request_token)
                        except Exception:
                            pass
            utility_request_token = int(info.get("utility_request_token", 0) or 0)
            utility_request_action = str(info.get("utility_request_action", "") or "")
            if utility_request_token > self._last_utility_request_token:
                utility_handled = False
                if utility_request_action == "screenshot_full":
                    utility_handled = self._start_full_screen_screenshot_countdown()
                elif utility_request_action == "screenshot_custom":
                    utility_handled = self._begin_capture_region_selection("screenshot_custom")
                elif utility_request_action == "record_full":
                    utility_handled = self._start_screen_record_countdown(None)
                elif utility_request_action == "record_custom":
                    utility_handled = self._begin_capture_region_selection("record_custom")
                elif utility_request_action == "stop_recording":
                    utility_handled = self._stop_screen_recording()
                elif utility_request_action == "clip_30s":
                    utility_handled = self._export_recent_clip(30)
                elif utility_request_action == "clip_1m":
                    utility_handled = self._export_recent_clip(60)
                if utility_handled:
                    self._last_utility_request_token = utility_request_token
                    if self._worker is not None and hasattr(self._worker, "acknowledge_utility_request"):
                        try:
                            self._worker.acknowledge_utility_request(utility_request_token)
                        except Exception:
                            pass
            capture_active = bool(info.get("utility_capture_selection_active", False))
            if capture_active and self.capture_region_overlay.isVisible():
                capture_cursor = info.get("utility_capture_cursor_norm")
                capture_point = None
                if isinstance(capture_cursor, (tuple, list)) and len(capture_cursor) >= 2:
                    union_geo = self._screens_union_geometry()
                    try:
                        cx = max(0.0, min(1.0, float(capture_cursor[0])))
                        cy = max(0.0, min(1.0, float(capture_cursor[1])))
                        capture_point = QPoint(
                            int(round(union_geo.left() + cx * max(union_geo.width() - 1, 1))),
                            int(round(union_geo.top() + cy * max(union_geo.height() - 1, 1))),
                        )
                    except Exception:
                        capture_point = None
                self.capture_region_overlay.update_hand_control(
                    capture_point,
                    left_down=bool(info.get("utility_capture_left_down", False)),
                    right_down=bool(info.get("utility_capture_right_down", False)),
                )

            drawing_enabled = bool(info.get("drawing_mode_enabled", False))
            if drawing_enabled != self._drawing_mode_active:
                self._set_drawing_mode(drawing_enabled)
                self._worker_drawing_tool = "hidden"
            if not self._drawing_mode_active or self._drawing_render_target != "screen":
                self.draw_overlay.end_stroke()
                self.draw_overlay.set_cursor(None, "hidden")
                self._worker_drawing_tool = "hidden"
                return

            cursor_norm = info.get("drawing_cursor_norm")
            tool = str(info.get("drawing_tool", "hidden") or "hidden")
            pos = None
            if isinstance(cursor_norm, (tuple, list)) and len(cursor_norm) >= 2:
                try:
                    pos = self.draw_overlay.map_normalized_to_screen(float(cursor_norm[0]), float(cursor_norm[1]))
                except Exception:
                    pos = None

            if pos is None or tool == "hidden":
                self.draw_overlay.end_stroke()
                self.draw_overlay.set_cursor(None, "hidden")
                self._worker_drawing_tool = "hidden"
                return

            qpos = QPointF(pos)
            if tool == "draw":
                if self._worker_drawing_tool != "draw":
                    self.draw_overlay.push_undo_state()
                    self.draw_overlay.begin_draw(qpos)
                else:
                    self.draw_overlay.draw_to(qpos)
            elif tool == "erase":
                if self._worker_drawing_tool != "erase":
                    self.draw_overlay.push_undo_state()
                self.draw_overlay.end_stroke()
                self.draw_overlay.erase_at(qpos)
            else:
                self.draw_overlay.end_stroke()
                self.draw_overlay.set_cursor(qpos, "hover")
            self._worker_drawing_tool = tool

        def _on_error(self, message: str) -> None:
            self.last_action_label.setText(f"Last action: {message}")
            QMessageBox.critical(self, "HGR App", message)

        def apply_new_config(self, config: AppConfig) -> None:
            self.config = AppConfig(**config.__dict__)
            self.overlay.set_font_size(self.config.hello_font_size)
            self.draw_overlay.set_brush(self.config.accent_color, self.draw_overlay.brush_thickness)
            self._sync_drawing_brush_to_worker()
            self.actions.open_settings_callback = self.show_settings_page
            self.apply_theme()
            save_config(self.config)
            self.last_action_label.setText("Last action: settings applied")
            if self._worker is not None:
                if hasattr(self._worker, "apply_config"):
                    self._worker.apply_config(self.config)
                else:
                    self._worker.config = self.config
            if self.mini_live_viewer is not None:
                self.mini_live_viewer.apply_theme(self.config)
            if self.live_view_window is not None:
                self.live_view_window.apply_theme(self.config)
            self._refresh_camera_combo_selection(self.config.preferred_camera_index)

        def _install_button_hover_refresh(self) -> None:
            for button in self.findChildren(QPushButton):
                button.setAttribute(Qt.WA_Hover, True)
                button.setMouseTracking(True)
                button.setProperty("hgrHover", False)
                button.setProperty("hgrPressed", False)
                button.installEventFilter(self)

        def _refresh_button_hover_visual(self, button: QPushButton) -> None:
            if button is None:
                return
            style = button.style()
            style.unpolish(button)
            style.polish(button)
            button.update()

        def _sync_button_visual_state(self, button: QPushButton) -> None:
            if button is None:
                return
            hovered = button.isVisible() and button.rect().contains(button.mapFromGlobal(QCursor.pos()))
            pressed = bool(button.isDown())
            if button.property("hgrHover") != hovered:
                button.setProperty("hgrHover", hovered)
            if button.property("hgrPressed") != pressed:
                button.setProperty("hgrPressed", pressed)
            self._refresh_button_hover_visual(button)

        def eventFilter(self, obj, event):  # noqa: N802
            if isinstance(obj, QPushButton) and not isinstance(obj, WindowControlButton):
                if event.type() in (
                    QEvent.Enter,
                    QEvent.Leave,
                    QEvent.HoverEnter,
                    QEvent.HoverLeave,
                    QEvent.MouseMove,
                    QEvent.HoverMove,
                    QEvent.MouseButtonPress,
                    QEvent.MouseButtonRelease,
                    QEvent.Show,
                ):
                    QTimer.singleShot(0, lambda b=obj: self._sync_button_visual_state(b))
            return super().eventFilter(obj, event)

        def nativeEvent(self, event_type, message):  # noqa: N802
            if not sys.platform.startswith("win") or self.is_custom_maximized:
                return super().nativeEvent(event_type, message)
            try:
                msg = _NativeMessage.from_address(message.__int__())
            except Exception:
                return super().nativeEvent(event_type, message)
            if msg.message != WM_NCHITTEST:
                return super().nativeEvent(event_type, message)

            cursor_x = ctypes.c_short(msg.lParam & 0xFFFF).value
            cursor_y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            local_pos = self.mapFromGlobal(QPoint(cursor_x, cursor_y))
            if not self.rect().contains(local_pos):
                return super().nativeEvent(event_type, message)

            border = 8
            on_left = local_pos.x() <= border
            on_right = local_pos.x() >= self.width() - border
            on_top = local_pos.y() <= border
            on_bottom = local_pos.y() >= self.height() - border

            if on_top and on_left:
                return True, HTTOPLEFT
            if on_top and on_right:
                return True, HTTOPRIGHT
            if on_bottom and on_left:
                return True, HTBOTTOMLEFT
            if on_bottom and on_right:
                return True, HTBOTTOMRIGHT
            if on_left:
                return True, HTLEFT
            if on_right:
                return True, HTRIGHT
            if on_top:
                return True, HTTOP
            if on_bottom:
                return True, HTBOTTOM
            return super().nativeEvent(event_type, message)

        def resizeEvent(self, event) -> None:  # noqa: N802
            super().resizeEvent(event)
            self._update_home_status_card_width()

        def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
            self.stop_engine()
            self._hide_mini_live_viewer()
            self.draw_overlay.hide_overlay()
            if self.mini_live_viewer is not None:
                self.mini_live_viewer.close()
            if self.live_view_window is not None:
                self.live_view_window.close()
            if self.tutorial_window is not None:
                self.tutorial_window.close()
            self.overlay.hide_message()
            super().closeEvent(event)
    def _on_worker_debug_frame(self, frame, info) -> None:
        if not isinstance(info, dict):
            return
        drawing_target = str(info.get("drawing_render_target", self._drawing_render_target) or self._drawing_render_target)
        self._set_drawing_render_target(drawing_target)
        request_token = int(info.get("drawing_request_token", 0) or 0)
        request_action = str(info.get("drawing_request_action", "") or "")
        if request_token > self._last_drawing_request_token:
            handled = False
            if request_action == "pen_options":
                handled = self._open_pen_options_dialog_from_gesture()
            elif request_action == "eraser_options":
                handled = self._open_eraser_options_dialog_from_gesture()
            elif request_action == "save":
                self._save_drawing_snapshot()
                handled = True
            elif request_action == "undo":
                handled = True
                if self.draw_overlay.undo_last_action():
                    self.last_action_label.setText("Last action: drawing undo")
            elif request_action == "clear":
                self.draw_overlay.push_undo_state()
                self.draw_overlay.clear_canvas()
                self.last_action_label.setText("Last action: drawing cleared")
                handled = True
            if handled:
                self._last_drawing_request_token = request_token
                if self._worker is not None and hasattr(self._worker, "acknowledge_drawing_request"):
                    try:
                        self._worker.acknowledge_drawing_request(request_token)
                    except Exception:
                        pass
        utility_request_token = int(info.get("utility_request_token", 0) or 0)
        utility_request_action = str(info.get("utility_request_action", "") or "")
        if utility_request_token > self._last_utility_request_token:
            utility_handled = False
            if utility_request_action == "screenshot_full":
                utility_handled = self._start_full_screen_screenshot_countdown()
            elif utility_request_action == "screenshot_custom":
                utility_handled = self._begin_capture_region_selection("screenshot_custom")
            elif utility_request_action == "record_full":
                utility_handled = self._start_screen_record_countdown(None)
            elif utility_request_action == "record_custom":
                utility_handled = self._begin_capture_region_selection("record_custom")
            elif utility_request_action == "stop_recording":
                utility_handled = self._stop_screen_recording()
            elif utility_request_action == "clip_30s":
                utility_handled = self._export_recent_clip(30)
            elif utility_request_action == "clip_1m":
                utility_handled = self._export_recent_clip(60)
            if utility_handled:
                self._last_utility_request_token = utility_request_token
                if self._worker is not None and hasattr(self._worker, "acknowledge_utility_request"):
                    try:
                        self._worker.acknowledge_utility_request(utility_request_token)
                    except Exception:
                        pass
        capture_active = bool(info.get("utility_capture_selection_active", False))
        if capture_active and self.capture_region_overlay.isVisible():
            capture_cursor = info.get("utility_capture_cursor_norm")
            capture_point = None
            if isinstance(capture_cursor, (tuple, list)) and len(capture_cursor) >= 2:
                union_geo = self._screens_union_geometry()
                try:
                    cx = max(0.0, min(1.0, float(capture_cursor[0])))
                    cy = max(0.0, min(1.0, float(capture_cursor[1])))
                    capture_point = QPoint(
                        int(round(union_geo.left() + cx * max(union_geo.width() - 1, 1))),
                        int(round(union_geo.top() + cy * max(union_geo.height() - 1, 1))),
                    )
                except Exception:
                    capture_point = None
            self.capture_region_overlay.update_hand_control(
                capture_point,
                left_down=bool(info.get("utility_capture_left_down", False)),
                right_down=bool(info.get("utility_capture_right_down", False)),
            )

        drawing_enabled = bool(info.get("drawing_mode_enabled", False))
        if drawing_enabled != self._drawing_mode_active:
            self._set_drawing_mode(drawing_enabled)
            self._worker_drawing_tool = "hidden"
        if not self._drawing_mode_active or self._drawing_render_target != "screen":
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(None, "hidden")
            self._worker_drawing_tool = "hidden"
            return

        cursor_norm = info.get("drawing_cursor_norm")
        tool = str(info.get("drawing_tool", "hidden") or "hidden")
        pos = None
        if isinstance(cursor_norm, (tuple, list)) and len(cursor_norm) >= 2:
            try:
                pos = self.draw_overlay.map_normalized_to_screen(float(cursor_norm[0]), float(cursor_norm[1]))
            except Exception:
                pos = None

        if pos is None or tool == "hidden":
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(None, "hidden")
            self._worker_drawing_tool = "hidden"
            return

        qpos = QPointF(pos)
        if tool == "draw":
            if self._worker_drawing_tool != "draw":
                self.draw_overlay.push_undo_state()
                self.draw_overlay.begin_draw(qpos)
            else:
                self.draw_overlay.draw_to(qpos)
        elif tool == "erase":
            if self._worker_drawing_tool != "erase":
                self.draw_overlay.push_undo_state()
            self.draw_overlay.end_stroke()
            self.draw_overlay.erase_at(qpos)
        else:
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(qpos, "hover")
        self._worker_drawing_tool = tool

    def _on_error(self, message: str) -> None:
        self.last_action_label.setText(f"Last action: {message}")
        QMessageBox.critical(self, "HGR App", message)

    def apply_new_config(self, config: AppConfig) -> None:
        self.config = AppConfig(**config.__dict__)
        self.overlay.set_font_size(self.config.hello_font_size)
        self.draw_overlay.set_brush(self.config.accent_color, self.draw_overlay.brush_thickness)
        self._sync_drawing_brush_to_worker()
        self.actions.open_settings_callback = self.show_settings_page
        self.apply_theme()
        save_config(self.config)
        self.last_action_label.setText("Last action: settings applied")
        if self._worker is not None:
            if hasattr(self._worker, "apply_config"):
                self._worker.apply_config(self.config)
            else:
                self._worker.config = self.config
        if self.mini_live_viewer is not None:
            self.mini_live_viewer.apply_theme(self.config)
        if self.live_view_window is not None:
            self.live_view_window.apply_theme(self.config)
        self._refresh_camera_combo_selection(self.config.preferred_camera_index)

    def _install_button_hover_refresh(self) -> None:
        for button in self.findChildren(QPushButton):
            button.setAttribute(Qt.WA_Hover, True)
            button.setMouseTracking(True)
            button.setProperty("hgrHover", False)
            button.setProperty("hgrPressed", False)
            button.installEventFilter(self)

    def _refresh_button_hover_visual(self, button: QPushButton) -> None:
        if button is None:
            return
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _sync_button_visual_state(self, button: QPushButton) -> None:
        if button is None:
            return
        hovered = button.isVisible() and button.rect().contains(button.mapFromGlobal(QCursor.pos()))
        pressed = bool(button.isDown())
        if button.property("hgrHover") != hovered:
            button.setProperty("hgrHover", hovered)
        if button.property("hgrPressed") != pressed:
            button.setProperty("hgrPressed", pressed)
        self._refresh_button_hover_visual(button)

    def eventFilter(self, obj, event):  # noqa: N802
        if isinstance(obj, QPushButton) and not isinstance(obj, WindowControlButton):
            if event.type() in (
                QEvent.Enter,
                QEvent.Leave,
                QEvent.HoverEnter,
                QEvent.HoverLeave,
                QEvent.MouseMove,
                QEvent.HoverMove,
                QEvent.MouseButtonPress,
                QEvent.MouseButtonRelease,
                QEvent.Show,
            ):
                QTimer.singleShot(0, lambda b=obj: self._sync_button_visual_state(b))
        return super().eventFilter(obj, event)

    def nativeEvent(self, event_type, message):  # noqa: N802
        if not sys.platform.startswith("win") or self.is_custom_maximized:
            return super().nativeEvent(event_type, message)
        try:
            msg = _NativeMessage.from_address(message.__int__())
        except Exception:
            return super().nativeEvent(event_type, message)
        if msg.message != WM_NCHITTEST:
            return super().nativeEvent(event_type, message)

        cursor_x = ctypes.c_short(msg.lParam & 0xFFFF).value
        cursor_y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        local_pos = self.mapFromGlobal(QPoint(cursor_x, cursor_y))
        if not self.rect().contains(local_pos):
            return super().nativeEvent(event_type, message)

        border = 8
        on_left = local_pos.x() <= border
        on_right = local_pos.x() >= self.width() - border
        on_top = local_pos.y() <= border
        on_bottom = local_pos.y() >= self.height() - border

        if on_top and on_left:
            return True, HTTOPLEFT
        if on_top and on_right:
            return True, HTTOPRIGHT
        if on_bottom and on_left:
            return True, HTBOTTOMLEFT
        if on_bottom and on_right:
            return True, HTBOTTOMRIGHT
        if on_left:
            return True, HTLEFT
        if on_right:
            return True, HTRIGHT
        if on_top:
            return True, HTTOP
        if on_bottom:
            return True, HTBOTTOM
        return super().nativeEvent(event_type, message)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_home_status_card_width()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.stop_engine()
        self._close_hand_selector_dialog()
        self._hide_mini_live_viewer()
        self.draw_overlay.hide_overlay()
        if self.mini_live_viewer is not None:
            self.mini_live_viewer.close()
        if self.live_view_window is not None:
            self.live_view_window.close()
        if self.tutorial_window is not None:
            self.tutorial_window.close()
        self.overlay.hide_message()
        super().closeEvent(event)


def _locate_ffmpeg_executable(self, tool_name: str) -> str | None:
    candidates: list[Path] = []
    exe_name = tool_name + (".exe" if sys.platform.startswith("win") else "")
    try:
        candidates.append(Path(sys.executable).resolve().with_name(exe_name))
    except Exception:
        pass
    try:
        candidates.append(Path.cwd() / exe_name)
    except Exception:
        pass
    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except Exception:
            pass
    return shutil.which(tool_name) or shutil.which(exe_name)

def _run_external_probe(self, *args: str, timeout: float = 5.0) -> str:
    if not args:
        return ""
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return ""
    return (completed.stdout or "") + "\n" + (completed.stderr or "")

def _detect_ffmpeg_capabilities(self) -> dict:
    capabilities = {
        "available": False,
        "encoders": set(),
        "filters": set(),
        "devices": set(),
        "preferred_encoder": "libx264",
    }
    if not self._ffmpeg_path:
        return capabilities
    capabilities["available"] = True
    encoders_text = self._run_external_probe(self._ffmpeg_path, "-hide_banner", "-encoders")
    for encoder_name in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264"):
        if encoder_name in encoders_text:
            capabilities["encoders"].add(encoder_name)
    filters_text = self._run_external_probe(self._ffmpeg_path, "-hide_banner", "-filters")
    for filter_name in ("gfxcapture", "ddagrab"):
        if filter_name in filters_text:
            capabilities["filters"].add(filter_name)
    devices_text = self._run_external_probe(self._ffmpeg_path, "-hide_banner", "-devices")
    if "gdigrab" in devices_text:
        capabilities["devices"].add("gdigrab")
    for preferred in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264"):
        if preferred in capabilities["encoders"]:
            capabilities["preferred_encoder"] = preferred
            break
    return capabilities

def _ffmpeg_encoder_args(self, *, purpose: str, fps: float, segment_seconds: float | None = None) -> list[str]:
    encoder = str(self._ffmpeg_capabilities.get("preferred_encoder", "libx264") or "libx264")
    gop = max(1, int(round(float(fps) * float(segment_seconds if segment_seconds is not None else 2.0))))
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq:v", "24", "-g", str(gop), "-pix_fmt", "yuv420p"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24", "-g", str(gop), "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", "24", "-look_ahead", "0", "-g", str(gop), "-pix_fmt", "nv12"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", str(gop), "-pix_fmt", "yuv420p"]

def _matching_monitor_index(self, region: QRect | None) -> int | None:
    if region is None or region.isNull():
        return None
    target = QRect(region.normalized())
    for index, screen in enumerate([screen for screen in QGuiApplication.screens() if screen is not None]):
        try:
            if QRect(screen.geometry()) == target:
                return index
        except Exception:
            continue
    return None

def _ffmpeg_capture_input_args(self, region: QRect, *, fps: float, prefer_low_overhead: bool) -> list[str]:
    target = QRect(region.normalized())
    monitor_index = self._matching_monitor_index(target)
    if prefer_low_overhead and monitor_index is not None and "gfxcapture" in self._ffmpeg_capabilities.get("filters", set()):
        capture = f"gfxcapture=monitor_idx={monitor_index}:capture_cursor=1:max_framerate={max(30, int(round(float(fps) * 2.0)))}"
        return ["-f", "lavfi", "-i", capture]
    if prefer_low_overhead and monitor_index is not None and "ddagrab" in self._ffmpeg_capabilities.get("filters", set()):
        capture = f"ddagrab=output_idx={monitor_index}:draw_mouse=1:framerate={float(fps):.3f}"
        return ["-f", "lavfi", "-i", capture]
    return [
        "-f", "gdigrab",
        "-framerate", f"{float(fps):.3f}",
        "-draw_mouse", "1",
        "-offset_x", str(int(target.x())),
        "-offset_y", str(int(target.y())),
        "-video_size", f"{int(target.width())}x{int(target.height())}",
        "-i", "desktop",
    ]

def _start_ffmpeg_process(self, command: list[str]) -> subprocess.Popen | None:
    if not command:
        return None
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None

def _stop_ffmpeg_process(self, process: subprocess.Popen | None, *, timeout: float = 8.0) -> None:
    if process is None:
        return
    try:
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write(b"q\n")
                process.stdin.flush()
            except Exception:
                pass
        process.wait(timeout=timeout)
    except Exception:
        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=3.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass

def _ffmpeg_ready(self) -> bool:
    return bool(sys.platform.startswith("win") and self._ffmpeg_path and self._ffmpeg_capabilities.get("available"))

def _ffmpeg_clip_list_path(self) -> Path:
    return self._clip_cache_dir() / "segments.csv"

def _ffmpeg_clip_segment_pattern(self) -> Path:
    return self._clip_cache_dir() / "segment_%03d.mkv"

def _cleanup_ffmpeg_clip_cache_files(self) -> None:
    cache_dir = self._clip_cache_dir()
    for pattern in ("segment_*.mkv", "segments.csv", "concat_*.txt"):
        for path in cache_dir.glob(pattern):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

def _parse_ffmpeg_clip_manifest(self) -> list[dict]:
    list_path = self._clip_cache_list_path
    if list_path is None or not list_path.exists():
        return []
    entries: list[dict] = []
    try:
        with list_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if len(row) < 3:
                    continue
                raw_path = (row[0] or "").strip()
                try:
                    start_time = float(row[1])
                    end_time = float(row[2])
                except Exception:
                    continue
                path = Path(raw_path)
                if not path.is_absolute():
                    path = self._clip_cache_dir() / path
                if not path.exists() or path.stat().st_size <= 0:
                    continue
                entries.append({
                    "path": path,
                    "start_time": start_time,
                    "end_time": end_time,
                    "region": QRect(self._clip_cache_region) if self._clip_cache_region is not None else QRect(self._screens_union_geometry()),
                })
    except Exception:
        return []
    return entries

def _build_clip_concat_file(self, segments: list[dict]) -> Path | None:
    if not segments:
        return None
    concat_path = self._clip_cache_dir() / f"concat_{time.time_ns()}.txt"
    try:
        with concat_path.open("w", encoding="utf-8") as handle:
            for entry in segments:
                file_path = str(Path(entry["path"]).resolve()).replace("'", "'\''")
                handle.write(f"file '{file_path}'\n")
        return concat_path
    except Exception:
        try:
            concat_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None

def _clip_crop_filter(self, capture_region: QRect, target_region: QRect) -> str:
    capture_region = QRect(capture_region.normalized())
    target_region = QRect(target_region.normalized())
    if capture_region == target_region:
        return ""
    relative = QRect(target_region)
    relative.translate(-capture_region.x(), -capture_region.y())
    x = max(0, int(relative.x()))
    y = max(0, int(relative.y()))
    w = max(2, int(target_region.width()))
    h = max(2, int(target_region.height()))
    return f"crop={w}:{h}:{x}:{y}"

    def _save_screenshot_pixmap(self, pixmap: QPixmap) -> Path | None:
        if pixmap.isNull():
            return None
        pictures_dir = Path.home() / "Pictures"
        target_dir = pictures_dir if pictures_dir.exists() else Path.home()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"hgr_screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
        return path if pixmap.save(str(path), "PNG") else None

    def _save_full_screen_screenshot(self) -> None:
        path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(None))
        if path is not None:
            self.last_action_label.setText(f"Last action: saved screenshot to {path}")
        else:
            self.last_action_label.setText("Last action: could not save screenshot")

    def _save_custom_region_screenshot(self, region: QRect) -> None:
        path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(region))
        if path is not None:
            self.last_action_label.setText(f"Last action: saved custom screenshot to {path}")
        else:
            self.last_action_label.setText("Last action: could not save custom screenshot")

    def _set_worker_utility_recording_active(self, active: bool) -> None:
        if self._worker is not None and hasattr(self._worker, "set_utility_recording_active"):
            try:
                self._worker.set_utility_recording_active(bool(active))
            except Exception:
                pass

    def _set_worker_utility_capture_selection_active(self, active: bool) -> None:
        if self._worker is not None and hasattr(self._worker, "set_utility_capture_selection_active"):
            try:
                self._worker.set_utility_capture_selection_active(bool(active))
            except Exception:
                pass

    def _start_countdown_overlay(self, seconds: int, finish_callback, *, label_prefix: str) -> bool:
        if self._utility_countdown_active:
            return False
        self._utility_countdown_active = True
        self._utility_countdown_token += 1
        token = self._utility_countdown_token
        seconds = max(1, int(seconds))

        for offset in range(seconds):
            value = seconds - offset
            delay_ms = offset * 1000
            def _show(value=value, token=token):
                if token != self._utility_countdown_token or not self._utility_countdown_active:
                    return
                self.countdown_overlay.show_countdown(value)
                self.last_action_label.setText(f"Last action: {label_prefix} in {value}...")
            QTimer.singleShot(delay_ms, _show)

        def _finish(token=token):
            if token != self._utility_countdown_token or not self._utility_countdown_active:
                return
            self.countdown_overlay.hide_countdown()
            self._utility_countdown_active = False
            finish_callback()

        QTimer.singleShot(seconds * 1000, _finish)
        return True

    def _start_full_screen_screenshot_countdown(self) -> bool:
        if (
            self._utility_screenshot_pending
            or self._capture_region_selection_mode is not None
            or getattr(self, "_capture_monitor_dialog", None) is not None
        ):
            return False
        return self._begin_monitor_selection_async("screenshot_full")

    def _begin_capture_region_selection(self, mode: str) -> bool:
        if self._capture_region_selection_mode is not None or self._utility_countdown_active:
            return False
        if self._screen_recording_active and mode != "record_stop":
            return False
        self._capture_region_selection_mode = str(mode or "")
        self._pending_capture_region = None
        self.last_action_label.setText("Last action: choose a capture area with your hand")
        self._set_worker_utility_capture_selection_active(True)
        self.capture_region_overlay.begin_selection(hand_control=True)
        return True

    def _on_capture_region_selected(self, rect: QRect) -> None:
        mode = self._capture_region_selection_mode
        self._capture_region_selection_mode = None
        self._set_worker_utility_capture_selection_active(False)
        self._pending_capture_region = QRect(rect.normalized())
        if self._pending_capture_region.width() <= 0 or self._pending_capture_region.height() <= 0:
            self.last_action_label.setText("Last action: capture area canceled")
            return
        if mode == "screenshot_custom":
            self._utility_screenshot_pending = True
            def _finish() -> None:
                try:
                    if self._pending_capture_region is not None:
                        self._save_custom_region_screenshot(self._pending_capture_region)
                finally:
                    self._utility_screenshot_pending = False
                    self._pending_capture_region = None
            started = self._start_countdown_overlay(3, _finish, label_prefix="custom screenshot")
            if not started:
                self._utility_screenshot_pending = False
        elif mode == "record_custom":
            region = QRect(self._pending_capture_region)
            self._pending_capture_region = None
            self._start_screen_record_countdown(region)
        else:
            self._pending_capture_region = None

    def _on_capture_region_canceled(self) -> None:
        self._capture_region_selection_mode = None
        self._pending_capture_region = None
        self._set_worker_utility_capture_selection_active(False)
        self.last_action_label.setText("Last action: capture area canceled")

    def _clip_cache_dir(self) -> Path:
        target_dir = Path(tempfile.gettempdir()) / "hgr_clip_cache"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def _clip_cache_output_path(self) -> Path:
        return self._clip_cache_dir() / f"clip_cache_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns()}.avi"

    def _clip_output_specs(self, duration_seconds: int) -> list[tuple[Path, str]]:
        videos_dir = Path.home() / "Videos"
        target_dir = videos_dir if videos_dir.exists() else Path.home()
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        label = f"{int(duration_seconds)}s"
        return [
            (target_dir / f"hgr_clip_{label}_{stamp}.mp4", 'mp4v'),
            (target_dir / f"hgr_clip_{label}_{stamp}.avi", 'XVID'),
            (target_dir / f"hgr_clip_{label}_{stamp}_mjpg.avi", 'MJPG'),
        ]

    def _open_video_writer(self, path: Path, codec_name: str, width: int, height: int, fps: float):
        fourcc = cv2.VideoWriter_fourcc(*codec_name)
        writer = cv2.VideoWriter(str(path), fourcc, float(fps), (int(width), int(height)))
        if writer.isOpened():
            return writer
        try:
            writer.release()
        except Exception:
            pass
        return None


def _start_clip_cache(self) -> bool:
    if self._ffmpeg_ready() and self._start_clip_cache_ffmpeg():
        return True
    if self._clip_cache_segment_writer is not None and self._clip_cache_timer.isActive():
        return True
    region = self._normalized_record_region(self._screens_union_geometry())
    if region.isNull() or region.width() <= 1 or region.height() <= 1:
        return False
    self._clip_cache_backend = "opencv"
    self._clip_cache_region = QRect(region)
    path = self._clip_cache_output_path()
    writer = self._open_video_writer(path, 'MJPG', region.width(), region.height(), self._clip_cache_fps)
    if writer is None:
        return False
    self._clip_cache_segment_writer = writer
    self._clip_cache_segment_path = path
    self._clip_cache_segment_started_at = time.time()
    self._clip_cache_segment_frame_count = 0
    self._clip_cache_timer.start()
    return True

    def _finalize_clip_cache_segment(self) -> None:
        writer = self._clip_cache_segment_writer
        path = self._clip_cache_segment_path
        region = QRect(self._clip_cache_region) if self._clip_cache_region is not None else None
        frame_count = int(self._clip_cache_segment_frame_count)
        started_at = float(self._clip_cache_segment_started_at or time.time())
        self._clip_cache_segment_writer = None
        self._clip_cache_segment_path = None
        self._clip_cache_segment_started_at = 0.0
        self._clip_cache_segment_frame_count = 0
        try:
            if writer is not None:
                writer.release()
        except Exception:
            pass
        if path is None:
            return
        if frame_count <= 0 or not path.exists() or path.stat().st_size <= 0:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            return
        self._clip_cache_backend = "opencv"
        self._clip_cache_segments.append({
            "path": path,
            "frame_count": frame_count,
            "start_time": started_at,
            "end_time": time.time(),
            "region": region,
        })
        self._prune_clip_cache_segments()

    def _start_new_clip_cache_segment(self) -> bool:
        region = self._normalized_record_region(self._screens_union_geometry())
        if region.isNull() or region.width() <= 1 or region.height() <= 1:
            return False
        path = self._clip_cache_output_path()
        writer = self._open_video_writer(path, 'MJPG', region.width(), region.height(), self._clip_cache_fps)
        if writer is None:
            return False
        self._clip_cache_region = QRect(region)
        self._clip_cache_segment_writer = writer
        self._clip_cache_segment_path = path
        self._clip_cache_segment_started_at = time.time()
        self._clip_cache_segment_frame_count = 0
        return True

    def _rotate_clip_cache_segment(self) -> bool:
        self._finalize_clip_cache_segment()
        return self._start_new_clip_cache_segment()

    def _prune_clip_cache_segments(self) -> None:
        cutoff = time.time() - float(self._clip_cache_max_seconds)
        kept = []
        for meta in self._clip_cache_segments:
            try:
                path = meta.get("path")
                end_time = float(meta.get("end_time", 0.0) or 0.0)
            except Exception:
                path = None
                end_time = 0.0
            if path is None:
                continue
            if end_time < cutoff:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            kept.append(meta)
        self._clip_cache_segments = kept


def _stop_clip_cache(self) -> None:
    if self._clip_cache_backend == "ffmpeg":
        self._stop_clip_cache_ffmpeg(delete_files=True)
        return
    self._clip_cache_timer.stop()
    self._finalize_clip_cache_segment()
    for meta in self._clip_cache_segments:
        try:
            Path(meta.get("path")).unlink(missing_ok=True)
        except Exception:
            pass
    self._clip_cache_segments = []
    self._clip_cache_region = None
    self._clip_cache_backend = ""


def _start_clip_cache_ffmpeg(self) -> bool:
    if self._clip_cache_process is not None and self._clip_cache_process.poll() is None:
        self._clip_cache_backend = "ffmpeg"
        return True
    region = self._normalized_record_region(self._screens_union_geometry())
    if region.isNull() or region.width() <= 1 or region.height() <= 1 or not self._ffmpeg_ready():
        return False
    self._cleanup_ffmpeg_clip_cache_files()
    self._clip_cache_region = QRect(region)
    self._clip_cache_list_path = self._ffmpeg_clip_list_path()
    self._clip_cache_segment_pattern = self._ffmpeg_clip_segment_pattern()
    command = [
        self._ffmpeg_path,
        "-hide_banner", "-loglevel", "error", "-y",
        *self._ffmpeg_capture_input_args(region, fps=self._clip_cache_fps, prefer_low_overhead=False),
        "-an",
        *self._ffmpeg_encoder_args(purpose="clip", fps=self._clip_cache_fps, segment_seconds=self._clip_cache_segment_seconds),
        "-force_key_frames", f"expr:gte(t,n_forced*{float(self._clip_cache_segment_seconds):.3f})",
        "-f", "segment",
        "-segment_time", f"{float(self._clip_cache_segment_seconds):.3f}",
        "-segment_wrap", str(int(self._clip_cache_wrap_count)),
        "-segment_list", str(self._clip_cache_list_path),
        "-segment_list_type", "csv",
        "-segment_list_size", str(int(self._clip_cache_wrap_count)),
        "-reset_timestamps", "1",
        str(self._clip_cache_segment_pattern),
    ]
    process = self._start_ffmpeg_process(command)
    if process is None:
        return False
    self._clip_cache_process = process
    self._clip_cache_backend = "ffmpeg"
    return True

def _stop_clip_cache_ffmpeg(self, *, delete_files: bool) -> None:
    process = self._clip_cache_process
    self._clip_cache_process = None
    self._stop_ffmpeg_process(process)
    if delete_files:
        self._cleanup_ffmpeg_clip_cache_files()
        self._clip_cache_list_path = None
        self._clip_cache_segment_pattern = None
        self._clip_cache_region = None
    self._clip_cache_backend = ""

    def _capture_clip_cache_frame(self) -> None:
        if self._clip_cache_segment_writer is None or self._clip_cache_region is None:
            return
        frame = self._grab_global_region_bgr_frame(self._clip_cache_region)
        if frame is None:
            return
        expected_w = int(self._clip_cache_region.width())
        expected_h = int(self._clip_cache_region.height())
        if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
            frame = cv2.resize(frame, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
        try:
            self._clip_cache_segment_writer.write(frame)
            self._clip_cache_segment_frame_count += 1
        except Exception:
            return
        if (time.time() - self._clip_cache_segment_started_at) >= float(self._clip_cache_segment_seconds):
            self._rotate_clip_cache_segment()

    def _crop_cached_frame_to_region(self, frame: np.ndarray, capture_region: QRect, target_region: QRect) -> np.ndarray | None:
        if frame is None or frame.size == 0:
            return None
        capture_region = QRect(capture_region.normalized())
        target_region = QRect(target_region.normalized())
        if target_region == capture_region:
            return frame
        relative = QRect(target_region)
        relative.translate(-capture_region.x(), -capture_region.y())
        x = max(0, int(relative.x()))
        y = max(0, int(relative.y()))
        w = min(int(relative.width()), frame.shape[1] - x)
        h = min(int(relative.height()), frame.shape[0] - y)
        if w <= 0 or h <= 0:
            return None
        cropped = frame[y:y + h, x:x + w]
        expected_w = max(2, int(target_region.width()))
        expected_h = max(2, int(target_region.height()))
        if cropped.shape[1] != expected_w or cropped.shape[0] != expected_h:
            cropped = cv2.resize(cropped, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
        return cropped


def _export_recent_clip_ffmpeg(self, duration_seconds: int, target_region: QRect) -> bool:
    was_active = self._clip_cache_backend == "ffmpeg" and self._clip_cache_process is not None
    if was_active:
        self._stop_clip_cache_ffmpeg(delete_files=False)
    try:
        entries = self._parse_ffmpeg_clip_manifest()
        if not entries:
            return False
        selected: list[dict] = []
        covered = 0.0
        for entry in reversed(entries):
            segment_seconds = max(1e-3, float(entry.get("end_time", 0.0)) - float(entry.get("start_time", 0.0)))
            selected.append(entry)
            covered += segment_seconds
            if covered >= float(duration_seconds):
                break
        if not selected:
            return False
        selected.reverse()
        total_duration = sum(max(1e-3, float(entry.get("end_time", 0.0)) - float(entry.get("start_time", 0.0))) for entry in selected)
        start_trim = max(0.0, total_duration - float(duration_seconds))
        concat_path = self._build_clip_concat_file(selected)
        if concat_path is None:
            return False
        try:
            output_path = self._clip_output_specs(duration_seconds)[0][0]
            capture_region = QRect(self._clip_cache_region) if self._clip_cache_region is not None else QRect(self._screens_union_geometry())
            filters = []
            crop_filter = self._clip_crop_filter(capture_region, target_region)
            if crop_filter:
                filters.append(crop_filter)
            filters.append(f"trim=start={start_trim:.3f}:duration={float(duration_seconds):.3f}")
            filters.append("setpts=PTS-STARTPTS")
            command = [
                self._ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_path),
                "-an",
                "-vf", ",".join(filters),
                *self._ffmpeg_encoder_args(purpose="clip_export", fps=self._clip_cache_fps),
                str(output_path),
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1024:
                actual_seconds = min(float(duration_seconds), max(0.0, total_duration))
                self.last_action_label.setText(f"Last action: saved {actual_seconds:.1f}s clip to {output_path}")
                self._queue_post_action_save_prompt("clips", output_path)
                return True
            return False
        finally:
            try:
                concat_path.unlink(missing_ok=True)
            except Exception:
                pass
    finally:
        self._cleanup_ffmpeg_clip_cache_files()
        if was_active and self._worker is not None and getattr(self._worker, "is_running", False):
            self._start_clip_cache_ffmpeg()

def _export_recent_clip_opencv(self, duration_seconds: int, target_region: QRect) -> bool:
    if self._clip_cache_segment_writer is not None:
        self._rotate_clip_cache_segment()
    segments = [meta for meta in self._clip_cache_segments if Path(meta.get("path")).exists() and int(meta.get("frame_count", 0) or 0) > 0]
    if not segments:
        return False
    selected_segments: list[tuple[dict, int]] = []
    covered_seconds = 0.0
    for meta in reversed(segments):
        frame_count = int(meta.get("frame_count", 0) or 0)
        if frame_count <= 0:
            continue
        start_time = float(meta.get("start_time", 0.0) or 0.0)
        end_time = float(meta.get("end_time", start_time) or start_time)
        segment_seconds = max(1e-3, end_time - start_time)
        if covered_seconds + segment_seconds <= float(duration_seconds):
            selected_segments.append((meta, 0))
            covered_seconds += segment_seconds
            continue
        needed_seconds = max(0.0, float(duration_seconds) - covered_seconds)
        keep_ratio = min(1.0, max(0.0, needed_seconds / segment_seconds))
        keep_frames = max(1, int(round(frame_count * keep_ratio)))
        skip_frames = max(0, frame_count - keep_frames)
        selected_segments.append((meta, skip_frames))
        covered_seconds += min(segment_seconds, needed_seconds)
        break
    if not selected_segments:
        return False
    selected_segments.reverse()
    estimated_frames = sum(max(0, int(meta.get("frame_count", 0) or 0) - int(skip)) for meta, skip in selected_segments)
    output_fps = max(1.0, min(30.0, float(estimated_frames) / max(1e-3, covered_seconds)))
    output_writer = None
    output_path = None
    for candidate_path, codec_name in self._clip_output_specs(duration_seconds):
        candidate_writer = self._open_video_writer(candidate_path, codec_name, target_region.width(), target_region.height(), output_fps)
        if candidate_writer is not None:
            output_writer = candidate_writer
            output_path = candidate_path
            break
    if output_writer is None or output_path is None:
        return False
    written = 0
    try:
        for meta, skip_frames in selected_segments:
            path = Path(meta.get("path"))
            capture_region = meta.get("region") or self._clip_cache_region or self._screens_union_geometry()
            capture_region = QRect(capture_region)
            cap = cv2.VideoCapture(str(path))
            local_index = 0
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    if local_index >= int(skip_frames):
                        cropped = self._crop_cached_frame_to_region(frame, capture_region, target_region)
                        if cropped is not None:
                            output_writer.write(cropped)
                            written += 1
                    local_index += 1
            finally:
                cap.release()
    finally:
        try:
            output_writer.release()
        except Exception:
            pass
    if written > 0 and output_path.exists() and output_path.stat().st_size > 1024:
        actual_seconds = written / float(output_fps) if output_fps > 0 else 0.0
        self.last_action_label.setText(f"Last action: saved {actual_seconds:.1f}s clip to {output_path}")
        self._queue_post_action_save_prompt("clips", output_path)
        return True
    return False


def _export_recent_clip(self, duration_seconds: int) -> bool:
    duration_seconds = int(max(1, duration_seconds))
    if self._utility_countdown_active or self._capture_region_selection_mode is not None:
        return False
    region = self._choose_full_capture_region(f"clip {duration_seconds} sec")
    if region is None or region.isNull():
        return True
    target_region = self._normalized_record_region(region)
    if self._ffmpeg_ready() and self._clip_cache_backend == "ffmpeg":
        success = self._export_recent_clip_ffmpeg(duration_seconds, target_region)
    else:
        success = self._export_recent_clip_opencv(duration_seconds, target_region)
    if not success:
        self.last_action_label.setText("Last action: no recent clip available yet")
    return True


def _start_screen_recording_ffmpeg(self, region: QRect) -> bool:
    if not self._ffmpeg_ready():
        return False
    region = self._normalized_record_region(region)
    if region.isNull() or region.width() <= 1 or region.height() <= 1:
        return False
    output_path = self._record_output_specs()[0][0]
    command = [
        self._ffmpeg_path,
        "-hide_banner", "-loglevel", "error", "-y",
        *self._ffmpeg_capture_input_args(region, fps=self._screen_record_fps, prefer_low_overhead=True),
        "-an",
        *self._ffmpeg_encoder_args(purpose="record", fps=self._screen_record_fps),
        str(output_path),
    ]
    process = self._start_ffmpeg_process(command)
    if process is None:
        return False
    self._screen_record_process = process
    self._screen_record_backend = "ffmpeg"
    self._screen_record_region = QRect(region)
    self._screen_record_path = output_path
    self._screen_record_frame_size = (region.width(), region.height())
    self._screen_recording_active = True
    self._set_worker_utility_recording_active(True)
    self.recording_overlay.show_indicator()
    self.last_action_label.setText(f"Last action: screen recording started {output_path}")
    return True

    def _record_output_specs(self) -> list[tuple[Path, str]]:
        videos_dir = Path.home() / "Videos"
        target_dir = videos_dir if videos_dir.exists() else Path.home()
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        return [
            (target_dir / f"hgr_record_{stamp}.mp4", 'mp4v'),
            (target_dir / f"hgr_record_{stamp}.avi", 'XVID'),
            (target_dir / f"hgr_record_{stamp}_mjpg.avi", 'MJPG'),
        ]

    def _normalized_record_region(self, region: QRect | None) -> QRect:
        target = QRect(self._screens_union_geometry() if region is None or region.isNull() else region.normalized())
        if target.width() % 2 != 0:
            target.setWidth(max(2, target.width() - 1))
        if target.height() % 2 != 0:
            target.setHeight(max(2, target.height() - 1))
        return target

    def _grab_global_region_bgr_frame(self, region: QRect) -> np.ndarray | None:
        target = QRect(region.normalized())
        if target.isNull() or target.width() <= 0 or target.height() <= 0:
            return None
        if not sys.platform.startswith("win"):
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        try:
            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32
        except Exception:
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))

        SRCCOPY = 0x00CC0020
        DIB_RGB_COLORS = 0
        BI_RGB = 0

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        width = int(target.width())
        height = int(target.height())
        hdc_screen = user32.GetDC(0)
        if not hdc_screen:
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            user32.ReleaseDC(0, hdc_screen)
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        if not hbm:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)
            return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
        old_obj = gdi32.SelectObject(hdc_mem, hbm)
        try:
            if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, int(target.x()), int(target.y()), SRCCOPY):
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = width
            bmi.bmiHeader.biHeight = -height
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            buf = (ctypes.c_ubyte * (width * height * 4))()
            rows = gdi32.GetDIBits(hdc_mem, hbm, 0, height, ctypes.byref(buf), ctypes.byref(bmi), DIB_RGB_COLORS)
            if rows != height:
                return self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(target))
            arr = np.ctypeslib.as_array(buf).reshape((height, width, 4)).copy()
            return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        finally:
            if old_obj:
                gdi32.SelectObject(hdc_mem, old_obj)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)

    def _pixmap_to_bgr_frame(self, pixmap: QPixmap) -> np.ndarray | None:
        if pixmap.isNull():
            return None
        image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        width = image.width()
        height = image.height()
        if width <= 0 or height <= 0:
            return None
        ptr = image.bits()
        size = int(image.sizeInBytes())
        try:
            arr = np.frombuffer(ptr, dtype=np.uint8, count=size).copy().reshape((height, width, 4))
        except Exception:
            try:
                raw = ptr.tobytes()
            except Exception:
                try:
                    raw = bytes(ptr[:size])
                except Exception:
                    return None
            arr = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 4))
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)

    def _start_screen_record_countdown(self, region: QRect | None = None) -> bool:
        if (
            self._screen_recording_active
            or self._capture_region_selection_mode is not None
            or self._utility_countdown_active
            or getattr(self, "_capture_monitor_dialog", None) is not None
        ):
            return False
        if region is not None and not region.isNull():
            target_region = self._normalized_record_region(QRect(region))
            if target_region.isNull() or target_region.width() <= 1 or target_region.height() <= 1:
                return False
            return self._start_countdown_overlay(
                3,
                lambda region=QRect(target_region): self._start_screen_recording(region),
                label_prefix="screen record",
            )
        return self._begin_monitor_selection_async("record_full")


def _start_screen_recording(self, region: QRect) -> None:
    region = self._normalized_record_region(region)
    if self._start_screen_recording_ffmpeg(region):
        return
    writer = None
    path = None
    for candidate_path, codec_name in self._record_output_specs():
        fourcc = cv2.VideoWriter_fourcc(*codec_name)
        candidate_writer = cv2.VideoWriter(str(candidate_path), fourcc, float(self._screen_record_fps), (region.width(), region.height()))
        if candidate_writer.isOpened():
            writer = candidate_writer
            path = candidate_path
            break
        try:
            candidate_writer.release()
        except Exception:
            pass
    if writer is None or path is None:
        self.last_action_label.setText("Last action: could not start screen recording")
        self._set_worker_utility_recording_active(False)
        return
    self._screen_record_backend = "opencv"
    self._screen_record_writer = writer
    self._screen_record_process = None
    self._screen_record_region = QRect(region)
    self._screen_record_path = path
    self._screen_record_frame_size = (region.width(), region.height())
    self._screen_recording_active = True
    self._set_worker_utility_recording_active(True)
    self.recording_overlay.show_indicator()
    self._capture_screen_record_frame()
    self._screen_record_timer.start()
    self.last_action_label.setText(f"Last action: screen recording started {path}")

    def _capture_screen_record_frame(self) -> None:
        if self._screen_record_backend == "ffmpeg":
            return
        if not self._screen_recording_active or self._screen_record_writer is None or self._screen_record_region is None:
            return
        frame = self._grab_global_region_bgr_frame(self._screen_record_region)
        if frame is None and self._screen_record_region is not None:
            overlay_was_visible = self.recording_overlay.isVisible()
            if overlay_was_visible:
                self.recording_overlay.hide_indicator()
            try:
                frame = self._pixmap_to_bgr_frame(self._grab_global_region_pixmap(self._screen_record_region))
            finally:
                if overlay_was_visible and self._screen_recording_active:
                    self.recording_overlay.show_indicator()
        if frame is None:
            return
        if self._screen_record_frame_size is not None:
            expected_w, expected_h = self._screen_record_frame_size
            if frame.shape[1] != expected_w or frame.shape[0] != expected_h:
                frame = cv2.resize(frame, (expected_w, expected_h), interpolation=cv2.INTER_AREA)
        self._screen_record_writer.write(frame)


def _stop_screen_recording(self) -> bool:
    if not self._screen_recording_active:
        return False
    self._screen_record_timer.stop()
    self.recording_overlay.hide_indicator()
    writer = self._screen_record_writer
    process = self._screen_record_process
    path = self._screen_record_path
    backend = self._screen_record_backend
    self._screen_record_writer = None
    self._screen_record_process = None
    self._screen_record_region = None
    self._screen_record_frame_size = None
    self._screen_record_path = None
    self._screen_record_backend = ""
    self._screen_recording_active = False
    self._set_worker_utility_recording_active(False)
    try:
        if backend == "ffmpeg" and process is not None:
            self._stop_ffmpeg_process(process)
        elif writer is not None:
            writer.release()
    finally:
        if path is not None and path.exists() and path.stat().st_size > 1024:
            self.last_action_label.setText(f"Last action: saved screen recording to {path}")
            self._queue_post_action_save_prompt("screen_recordings", path)
        elif path is not None:
            self.last_action_label.setText("Last action: screen recording failed to save")
        else:
            self.last_action_label.setText("Last action: screen recording stopped")
    return True

    def _on_worker_debug_frame(self, frame, info) -> None:
        if not isinstance(info, dict):
            return
        drawing_target = str(info.get("drawing_render_target", self._drawing_render_target) or self._drawing_render_target)
        self._set_drawing_render_target(drawing_target)
        request_token = int(info.get("drawing_request_token", 0) or 0)
        request_action = str(info.get("drawing_request_action", "") or "")
        if request_token > self._last_drawing_request_token:
            handled = False
            if request_action == "pen_options":
                handled = self._open_pen_options_dialog_from_gesture()
            elif request_action == "eraser_options":
                handled = self._open_eraser_options_dialog_from_gesture()
            elif request_action == "save":
                self._save_drawing_snapshot()
                handled = True
            elif request_action == "undo":
                handled = True
                if self.draw_overlay.undo_last_action():
                    self.last_action_label.setText("Last action: drawing undo")
            elif request_action == "clear":
                self.draw_overlay.push_undo_state()
                self.draw_overlay.clear_canvas()
                self.last_action_label.setText("Last action: drawing cleared")
                handled = True
            if handled:
                self._last_drawing_request_token = request_token
                if self._worker is not None and hasattr(self._worker, "acknowledge_drawing_request"):
                    try:
                        self._worker.acknowledge_drawing_request(request_token)
                    except Exception:
                        pass
        utility_request_token = int(info.get("utility_request_token", 0) or 0)
        utility_request_action = str(info.get("utility_request_action", "") or "")
        if utility_request_token > self._last_utility_request_token:
            utility_handled = False
            if utility_request_action == "screenshot_full":
                utility_handled = self._start_full_screen_screenshot_countdown()
            elif utility_request_action == "screenshot_custom":
                utility_handled = self._begin_capture_region_selection("screenshot_custom")
            elif utility_request_action == "record_full":
                utility_handled = self._start_screen_record_countdown(None)
            elif utility_request_action == "record_custom":
                utility_handled = self._begin_capture_region_selection("record_custom")
            elif utility_request_action == "stop_recording":
                utility_handled = self._stop_screen_recording()
            elif utility_request_action == "clip_30s":
                utility_handled = self._export_recent_clip(30)
            elif utility_request_action == "clip_1m":
                utility_handled = self._export_recent_clip(60)
            if utility_handled:
                self._last_utility_request_token = utility_request_token
                if self._worker is not None and hasattr(self._worker, "acknowledge_utility_request"):
                    try:
                        self._worker.acknowledge_utility_request(utility_request_token)
                    except Exception:
                        pass
        capture_active = bool(info.get("utility_capture_selection_active", False))
        capture_cursor = info.get("utility_capture_cursor_norm")
        capture_point = None
        if capture_active and isinstance(capture_cursor, (tuple, list)) and len(capture_cursor) >= 2:
            union_geo = self._screens_union_geometry()
            try:
                cx = max(0.0, min(1.0, float(capture_cursor[0])))
                cy = max(0.0, min(1.0, float(capture_cursor[1])))
                capture_point = QPoint(
                    int(round(union_geo.left() + cx * max(union_geo.width() - 1, 1))),
                    int(round(union_geo.top() + cy * max(union_geo.height() - 1, 1))),
                )
            except Exception:
                capture_point = None
        if capture_active and self.capture_region_overlay.isVisible():
            self.capture_region_overlay.update_hand_control(
                capture_point,
                left_down=bool(info.get("utility_capture_left_down", False)),
                right_down=bool(info.get("utility_capture_right_down", False)),
            )

        drawing_enabled = bool(info.get("drawing_mode_enabled", False))
        if drawing_enabled != self._drawing_mode_active:
            self._set_drawing_mode(drawing_enabled)
            self._worker_drawing_tool = "hidden"
        if not self._drawing_mode_active or self._drawing_render_target != "screen":
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(None, "hidden")
            self._worker_drawing_tool = "hidden"
            return

        cursor_norm = info.get("drawing_cursor_norm")
        tool = str(info.get("drawing_tool", "hidden") or "hidden")
        pos = None
        if isinstance(cursor_norm, (tuple, list)) and len(cursor_norm) >= 2:
            try:
                pos = self.draw_overlay.map_normalized_to_screen(float(cursor_norm[0]), float(cursor_norm[1]))
            except Exception:
                pos = None

        if pos is None or tool == "hidden":
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(None, "hidden")
            self._worker_drawing_tool = "hidden"
            return

        qpos = QPointF(pos)
        if tool == "draw":
            if self._worker_drawing_tool != "draw":
                self.draw_overlay.push_undo_state()
                self.draw_overlay.begin_draw(qpos)
            else:
                self.draw_overlay.draw_to(qpos)
        elif tool == "erase":
            if self._worker_drawing_tool != "erase":
                self.draw_overlay.push_undo_state()
            self.draw_overlay.end_stroke()
            self.draw_overlay.erase_at(qpos)
        else:
            self.draw_overlay.end_stroke()
            self.draw_overlay.set_cursor(qpos, "hover")
        self._worker_drawing_tool = tool

    def _on_error(self, message: str) -> None:
        self.last_action_label.setText(f"Last action: {message}")
        QMessageBox.critical(self, "HGR App", message)

    def apply_new_config(self, config: AppConfig) -> None:
        self.config = AppConfig(**config.__dict__)
        self.overlay.set_font_size(self.config.hello_font_size)
        self.draw_overlay.set_brush(self.config.accent_color, self.draw_overlay.brush_thickness)
        self._sync_drawing_brush_to_worker()
        self.actions.open_settings_callback = self.show_settings_page
        self.apply_theme()
        save_config(self.config)
        self.last_action_label.setText("Last action: settings applied")
        if self._worker is not None:
            if hasattr(self._worker, "apply_config"):
                self._worker.apply_config(self.config)
            else:
                self._worker.config = self.config
        if self.mini_live_viewer is not None:
            self.mini_live_viewer.apply_theme(self.config)
        if self.live_view_window is not None:
            self.live_view_window.apply_theme(self.config)
        self._refresh_camera_combo_selection(self.config.preferred_camera_index)

    def _install_button_hover_refresh(self) -> None:
        for button in self.findChildren(QPushButton):
            button.setAttribute(Qt.WA_Hover, True)
            button.setMouseTracking(True)
            button.setProperty("hgrHover", False)
            button.setProperty("hgrPressed", False)
            button.installEventFilter(self)

    def _refresh_button_hover_visual(self, button: QPushButton) -> None:
        if button is None:
            return
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _sync_button_visual_state(self, button: QPushButton) -> None:
        if button is None:
            return
        hovered = button.isVisible() and button.rect().contains(button.mapFromGlobal(QCursor.pos()))
        pressed = bool(button.isDown())
        if button.property("hgrHover") != hovered:
            button.setProperty("hgrHover", hovered)
        if button.property("hgrPressed") != pressed:
            button.setProperty("hgrPressed", pressed)
        self._refresh_button_hover_visual(button)

    def eventFilter(self, obj, event):  # noqa: N802
        if isinstance(obj, QPushButton) and not isinstance(obj, WindowControlButton):
            if event.type() in (
                QEvent.Enter,
                QEvent.Leave,
                QEvent.HoverEnter,
                QEvent.HoverLeave,
                QEvent.MouseMove,
                QEvent.HoverMove,
                QEvent.MouseButtonPress,
                QEvent.MouseButtonRelease,
                QEvent.Show,
            ):
                QTimer.singleShot(0, lambda b=obj: self._sync_button_visual_state(b))
        return super().eventFilter(obj, event)

    def nativeEvent(self, event_type, message):  # noqa: N802
        if not sys.platform.startswith("win") or self.is_custom_maximized:
            return super().nativeEvent(event_type, message)
        try:
            msg = _NativeMessage.from_address(message.__int__())
        except Exception:
            return super().nativeEvent(event_type, message)
        if msg.message != WM_NCHITTEST:
            return super().nativeEvent(event_type, message)

        cursor_x = ctypes.c_short(msg.lParam & 0xFFFF).value
        cursor_y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        local_pos = self.mapFromGlobal(QPoint(cursor_x, cursor_y))
        if not self.rect().contains(local_pos):
            return super().nativeEvent(event_type, message)

        border = 8
        on_left = local_pos.x() <= border
        on_right = local_pos.x() >= self.width() - border
        on_top = local_pos.y() <= border
        on_bottom = local_pos.y() >= self.height() - border

        if on_top and on_left:
            return True, HTTOPLEFT
        if on_top and on_right:
            return True, HTTOPRIGHT
        if on_bottom and on_left:
            return True, HTBOTTOMLEFT
        if on_bottom and on_right:
            return True, HTBOTTOMRIGHT
        if on_left:
            return True, HTLEFT
        if on_right:
            return True, HTRIGHT
        if on_top:
            return True, HTTOP
        if on_bottom:
            return True, HTBOTTOM
        return super().nativeEvent(event_type, message)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_home_status_card_width()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.stop_engine()
        self._hide_mini_live_viewer()
        self.draw_overlay.hide_overlay()
        if self.mini_live_viewer is not None:
            self.mini_live_viewer.close()
        if self.live_view_window is not None:
            self.live_view_window.close()
        if self.tutorial_window is not None:
            self.tutorial_window.close()
        self.overlay.hide_message()
        super().closeEvent(event)

    def _monitor_dialog_cursor_from_info(self, info) -> tuple[float, float] | None:
        if not isinstance(info, dict):
            return None
        result = info.get("result")
        hand_reading = getattr(result, "hand_reading", None) if result is not None else None
        tracked_hand = getattr(result, "tracked_hand", None) if result is not None else None
        handedness = str(getattr(tracked_hand, "handedness", "") or "")
        if hand_reading is None or handedness != "Right":
            return None
        try:
            palm_center = getattr(hand_reading.palm, "center", None)
            if palm_center is None or len(palm_center) < 2:
                return None
            return (
                max(0.0, min(1.0, float(palm_center[0]))),
                max(0.0, min(1.0, float(palm_center[1]))),
            )
        except Exception:
            return None

    def _monitor_dialog_clicks_from_info(self, info) -> tuple[bool, bool]:
        if not isinstance(info, dict):
            return False, False
        result = info.get("result")
        hand_reading = getattr(result, "hand_reading", None) if result is not None else None
        tracked_hand = getattr(result, "tracked_hand", None) if result is not None else None
        handedness = str(getattr(tracked_hand, "handedness", "") or "")
        if hand_reading is None or handedness != "Right":
            return False, False

        def _finger_down(finger) -> bool:
            if finger is None:
                return False
            openness = float(getattr(finger, "openness", 0.0) or 0.0)
            curl = float(getattr(finger, "curl", 0.0) or 0.0)
            state = str(getattr(finger, "state", "") or "")
            return state in {"closed", "mostly_curled"} or openness <= 0.42 or curl >= 0.52

        try:
            fingers = hand_reading.fingers
            index_down = _finger_down(fingers.get("index"))
            middle_down = _finger_down(fingers.get("middle"))
            return index_down and not middle_down, middle_down and not index_down
        except Exception:
            return False, False

    def _clear_capture_monitor_dialog_state(self) -> None:
        self._set_worker_utility_capture_selection_active(False)
        self._capture_monitor_dialog = None
        self._capture_monitor_selection_mode = None

    def _begin_monitor_selection_async(self, mode: str) -> bool:
        if getattr(self, '_capture_monitor_dialog', None) is not None:
            return False
        options = self._capture_monitor_options()
        if not options:
            return False
        if len(options) == 1:
            region = QRect(options[0][1])
            if mode == 'screenshot_full':
                return self._start_full_screen_screenshot_countdown_for_region(region)
            if mode == 'record_full':
                return self._start_screen_record_countdown_for_region(region)
            return False
        action_label = 'screenshot' if mode == 'screenshot_full' else 'record'
        dialog = CaptureMonitorDialog(self.config, action_label, options, self)
        self._capture_monitor_dialog = dialog
        self._capture_monitor_selection_mode = str(mode or '')
        self.last_action_label.setText(f"Last action: choose monitor for {action_label} with your hand")
        self._set_worker_utility_capture_selection_active(True)
        dialog.selection_made.connect(self._on_capture_monitor_dialog_selected)
        dialog.canceled.connect(self._on_capture_monitor_dialog_rejected)
        dialog.show()
        dialog.raise_()
        dialog.update()
        return True

    def _on_capture_monitor_dialog_selected(self, region: QRect) -> None:
        mode = str(getattr(self, '_capture_monitor_selection_mode', '') or '')
        chosen = QRect(region.normalized()) if region is not None else None
        self._clear_capture_monitor_dialog_state()
        if chosen is None or chosen.isNull():
            action_label = 'screenshot' if mode == 'screenshot_full' else 'record'
            self.last_action_label.setText(f"Last action: {action_label} canceled")
            return
        if mode == 'screenshot_full':
            self._start_full_screen_screenshot_countdown_for_region(chosen)
        elif mode == 'record_full':
            self._start_screen_record_countdown_for_region(chosen)

    def _on_capture_monitor_dialog_rejected(self) -> None:
        mode = str(getattr(self, '_capture_monitor_selection_mode', '') or '')
        self._clear_capture_monitor_dialog_state()
        action_label = 'screenshot' if mode == 'screenshot_full' else 'record'
        self.last_action_label.setText(f"Last action: {action_label} canceled")

    def _start_full_screen_screenshot_countdown_for_region(self, region: QRect) -> bool:
        chosen = QRect(region.normalized())
        if chosen.isNull() or chosen.width() <= 0 or chosen.height() <= 0:
            return False
        self._utility_screenshot_pending = True
        def _finish(region=QRect(chosen)) -> None:
            try:
                path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(region))
                if path is not None:
                    self.last_action_label.setText(f"Last action: saved screenshot to {path}")
                else:
                    self.last_action_label.setText('Last action: could not save screenshot')
            finally:
                self._utility_screenshot_pending = False
        started = self._start_countdown_overlay(3, _finish, label_prefix='full screenshot')
        if not started:
            self._utility_screenshot_pending = False
        return started

    def _start_full_screen_screenshot_countdown(self) -> bool:
        if self._utility_screenshot_pending or self._capture_region_selection_mode is not None or getattr(self, '_capture_monitor_dialog', None) is not None:
            return False
        return self._begin_monitor_selection_async('screenshot_full')

    def _start_screen_record_countdown_for_region(self, region: QRect) -> bool:
        target_region = self._normalized_record_region(QRect(region))
        if target_region.isNull() or target_region.width() <= 1 or target_region.height() <= 1:
            return False
        return self._start_countdown_overlay(3, lambda region=QRect(target_region): self._start_screen_recording(region), label_prefix='screen record')

    def _start_screen_record_countdown(self, region: QRect | None = None) -> bool:
        if self._screen_recording_active or self._capture_region_selection_mode is not None or self._utility_countdown_active or getattr(self, '_capture_monitor_dialog', None) is not None:
            return False
        if region is not None and not region.isNull():
            return self._start_screen_record_countdown_for_region(QRect(region))
        return self._begin_monitor_selection_async('record_full')


# --- Runtime patch: async monitor chooser helpers belong on MainWindow ---
def _mw_clear_capture_monitor_dialog_state(self) -> None:
    dialog = getattr(self, '_capture_monitor_dialog', None)
    worker = getattr(self, '_worker', None)
    if dialog is not None and worker is not None:
        try:
            worker.debug_frame_ready.disconnect(dialog.handle_debug_frame)
        except Exception:
            pass
    self._set_worker_utility_capture_selection_active(False)
    self._capture_monitor_dialog = None
    self._capture_monitor_selection_mode = None


def _mw_begin_monitor_selection_async(self, mode: str) -> bool:
    if getattr(self, '_capture_monitor_dialog', None) is not None:
        return False
    options = self._capture_monitor_options()
    if not options:
        return False
    if len(options) == 1:
        region = QRect(options[0][1])
        if mode == 'screenshot_full':
            return self._start_full_screen_screenshot_countdown_for_region(region)
        if mode == 'record_full':
            return self._start_screen_record_countdown_for_region(region)
        return False
    action_label = 'screenshot' if mode == 'screenshot_full' else 'record'
    dialog = CaptureMonitorDialog(self.config, action_label, options, self)
    self._capture_monitor_dialog = dialog
    self._capture_monitor_selection_mode = str(mode or '')
    self.last_action_label.setText(f"Last action: choose monitor for {action_label} with your hand")
    self._set_worker_utility_capture_selection_active(True)
    dialog.selection_made.connect(self._on_capture_monitor_dialog_selected)
    dialog.canceled.connect(self._on_capture_monitor_dialog_rejected)
    if self._worker is not None:
        try:
            self._worker.debug_frame_ready.connect(dialog.handle_debug_frame)
        except Exception:
            pass
    dialog.show()
    dialog.raise_()
    dialog.update()
    return True


def _mw_on_capture_monitor_dialog_selected(self, region: QRect) -> None:
    mode = str(getattr(self, '_capture_monitor_selection_mode', '') or '')
    chosen = QRect(region.normalized()) if region is not None else None
    self._clear_capture_monitor_dialog_state()
    if chosen is None or chosen.isNull():
        action_label = 'screenshot' if mode == 'screenshot_full' else 'record'
        self.last_action_label.setText(f"Last action: {action_label} canceled")
        return
    if mode == 'screenshot_full':
        self._start_full_screen_screenshot_countdown_for_region(chosen)
    elif mode == 'record_full':
        self._start_screen_record_countdown_for_region(chosen)


def _mw_on_capture_monitor_dialog_rejected(self) -> None:
    mode = str(getattr(self, '_capture_monitor_selection_mode', '') or '')
    self._clear_capture_monitor_dialog_state()
    action_label = 'screenshot' if mode == 'screenshot_full' else 'record'
    self.last_action_label.setText(f"Last action: {action_label} canceled")


def _mw_start_full_screen_screenshot_countdown_for_region(self, region: QRect) -> bool:
    chosen = QRect(region.normalized())
    if chosen.isNull() or chosen.width() <= 0 or chosen.height() <= 0:
        return False
    self._utility_screenshot_pending = True
    def _finish(region=QRect(chosen)) -> None:
        try:
            path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(region))
            if path is not None:
                self.last_action_label.setText(f"Last action: saved screenshot to {path}")
                self._queue_post_action_save_prompt("screenshots", path)
            else:
                self.last_action_label.setText('Last action: could not save screenshot')
        finally:
            self._utility_screenshot_pending = False
    started = self._start_countdown_overlay(3, _finish, label_prefix='full screenshot')
    if not started:
        self._utility_screenshot_pending = False
    return started


def _mw_start_screen_record_countdown_for_region(self, region: QRect) -> bool:
    target_region = self._normalized_record_region(QRect(region))
    if target_region.isNull() or target_region.width() <= 1 or target_region.height() <= 1:
        return False
    return self._start_countdown_overlay(3, lambda region=QRect(target_region): self._start_screen_recording(region), label_prefix='screen record')


# Bind patched methods onto MainWindow so async chooser works even if these defs landed outside the class.
MainWindow._clear_capture_monitor_dialog_state = _mw_clear_capture_monitor_dialog_state
MainWindow._begin_monitor_selection_async = _mw_begin_monitor_selection_async
MainWindow._on_capture_monitor_dialog_selected = _mw_on_capture_monitor_dialog_selected
MainWindow._on_capture_monitor_dialog_rejected = _mw_on_capture_monitor_dialog_rejected
MainWindow._start_full_screen_screenshot_countdown_for_region = _mw_start_full_screen_screenshot_countdown_for_region
MainWindow._start_screen_record_countdown_for_region = _mw_start_screen_record_countdown_for_region


def _capture_monitor_dialog_handle_debug_frame_v2(self, frame, info) -> None:
    if not self.isVisible() or getattr(self, '_completed', False) or not isinstance(info, dict):
        return

    union_geo = self.geometry()
    global_point = None
    utility_left_down = bool(info.get("utility_capture_left_down", False))
    utility_right_down = bool(info.get("utility_capture_right_down", False))

    capture_cursor = info.get("utility_capture_cursor_norm")
    if isinstance(capture_cursor, (tuple, list)) and len(capture_cursor) >= 2:
        try:
            cx = max(0.0, min(1.0, float(capture_cursor[0])))
            cy = max(0.0, min(1.0, float(capture_cursor[1])))
            global_point = QPoint(
                int(round(union_geo.left() + cx * max(union_geo.width() - 1, 1))),
                int(round(union_geo.top() + cy * max(union_geo.height() - 1, 1))),
            )
        except Exception:
            global_point = None

    raw_index_down = False
    raw_middle_down = False
    result = info.get("result")
    hand_reading = getattr(result, "hand_reading", None) if result is not None else None
    tracked_hand = getattr(result, "tracked_hand", None) if result is not None else None
    handedness = str(getattr(tracked_hand, "handedness", "") or "")
    if hand_reading is not None and handedness == "Right":
        try:
            palm_center = getattr(hand_reading.palm, "center", None)
            if global_point is None and palm_center is not None and len(palm_center) >= 2:
                px = max(0.0, min(1.0, float(palm_center[0])))
                py = max(0.0, min(1.0, float(palm_center[1])))
                global_point = QPoint(
                    int(round(union_geo.left() + px * max(union_geo.width() - 1, 1))),
                    int(round(union_geo.top() + py * max(union_geo.height() - 1, 1))),
                )

            fingers = getattr(hand_reading, "fingers", {})
            def _finger_down(finger) -> bool:
                if finger is None:
                    return False
                openness = float(getattr(finger, "openness", 0.0) or 0.0)
                curl = float(getattr(finger, "curl", 0.0) or 0.0)
                state = str(getattr(finger, "state", "") or "")
                return state in {"closed", "mostly_curled"} or openness <= 0.42 or curl >= 0.52

            raw_index_down = _finger_down(fingers.get("index"))
            raw_middle_down = _finger_down(fingers.get("middle"))
        except Exception:
            pass

    if not hasattr(self, "_raw_clicks_armed"):
        self._raw_clicks_armed = False
    if not self._raw_clicks_armed:
        if not raw_index_down and not raw_middle_down:
            self._raw_clicks_armed = True
        left_down = False
        right_down = False
    else:
        # Prefer worker-provided gated click state. Fall back to raw fingers only after re-arm.
        if utility_left_down or utility_right_down:
            left_down = utility_left_down
            right_down = utility_right_down
        else:
            left_down = raw_index_down and not raw_middle_down
            right_down = raw_middle_down and not raw_index_down

    self.update_hand_control(global_point, left_down=left_down, right_down=right_down)

CaptureMonitorDialog.handle_debug_frame = _capture_monitor_dialog_handle_debug_frame_v2

# --- Runtime patch: keep monitor chooser in a tutorial-style hand-controlled panel ---
_capture_monitor_dialog_original_show_event_v22 = CaptureMonitorDialog.showEvent

def _capture_monitor_dialog_show_event_v22(self, event) -> None:
    _capture_monitor_dialog_original_show_event_v22(self, event)
    self._hand_clicks_armed = False
    self._last_left_down = False
    self._last_right_down = False
    try:
        panel_rect = self._panel.geometry()
        center_local = panel_rect.center()
        self._cursor_global = self.mapToGlobal(center_local)
    except Exception:
        pass
    self.update()


def _capture_monitor_dialog_panel_mapped_global_v22(self, source_global: QPoint) -> QPoint | None:
    if source_global is None:
        return None
    geo = self.geometry()
    if geo.width() <= 1 or geo.height() <= 1:
        return None
    panel_rect = self._panel.geometry()
    if panel_rect.width() <= 1 or panel_rect.height() <= 1:
        return None
    nx = (float(source_global.x()) - float(geo.left())) / float(max(geo.width() - 1, 1))
    ny = (float(source_global.y()) - float(geo.top())) / float(max(geo.height() - 1, 1))
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    _sensitivity = 1.5
    nx = max(0.0, min(1.0, 0.5 + (nx - 0.5) * _sensitivity))
    ny = max(0.0, min(1.0, 0.5 + (ny - 0.5) * _sensitivity))

    pad_x = 18
    pad_y = 18
    usable_left = panel_rect.left() + pad_x
    usable_top = panel_rect.top() + pad_y
    usable_width = max(1, panel_rect.width() - pad_x * 2)
    usable_height = max(1, panel_rect.height() - pad_y * 2)
    target_local = QPoint(
        int(round(usable_left + nx * max(usable_width - 1, 1))),
        int(round(usable_top + ny * max(usable_height - 1, 1))),
    )
    return self.mapToGlobal(target_local)


def _capture_monitor_dialog_update_hand_control_v22(self, global_point, *, left_down: bool, right_down: bool) -> None:
    if not self.isVisible():
        return
    if isinstance(global_point, QPoint):
        mapped_global = _capture_monitor_dialog_panel_mapped_global_v22(self, global_point)
        if mapped_global is not None:
            self._update_cursor_from_global(mapped_global)
    elif self._cursor_global is None:
        try:
            self._cursor_global = self.mapToGlobal(self._panel.geometry().center())
        except Exception:
            pass

    if not hasattr(self, '_hand_clicks_armed'):
        self._hand_clicks_armed = False
    if not self._hand_clicks_armed:
        if not left_down and not right_down:
            self._hand_clicks_armed = True
        self._last_left_down = bool(left_down)
        self._last_right_down = bool(right_down)
        self.update()
        return

    self._process_hand_clicks(left_down=bool(left_down), right_down=bool(right_down))


def _capture_monitor_dialog_process_hand_clicks_v22(self, *, left_down: bool, right_down: bool) -> None:
    if right_down and not self._last_right_down:
        self._last_right_down = True
        self._last_left_down = bool(left_down)
        self._cancel()
        return
    self._last_right_down = bool(right_down)

    if left_down and not self._last_left_down and self._cursor_global is not None:
        local = self.mapFromGlobal(self._cursor_global)
        widget = self.childAt(local)
        while widget is not None and not isinstance(widget, QAbstractButton):
            widget = widget.parentWidget()
        if isinstance(widget, QAbstractButton) and widget.isEnabled():
            widget.click()
            self._last_left_down = True
            self.update()
            return
    self._last_left_down = bool(left_down)
    self.update()


CaptureMonitorDialog.showEvent = _capture_monitor_dialog_show_event_v22
CaptureMonitorDialog.update_hand_control = _capture_monitor_dialog_update_hand_control_v22
CaptureMonitorDialog._process_hand_clicks = _capture_monitor_dialog_process_hand_clicks_v22
