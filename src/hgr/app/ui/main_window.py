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

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QThread, QTimer, QEvent, QUrl, Signal
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
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

try:
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    _HAS_QT_MEDIA = True
except Exception:
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
SECTION_CUSTOM_GESTURE = 2
SECTION_CAMERA = 3
SECTION_MICROPHONE = 4
SECTION_SAVE_LOCATIONS = 5
SECTION_COLORS = 6
SECTION_TUTORIAL = 7
SECTION_UPDATES = 8



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

        # Small version tag in the title bar's left edge — gives
        # users an at-a-glance view of which Touchless they're on
        # without digging into Settings. Updated dynamically by
        # MainWindow if the version ever changes mid-session
        # (which it doesn't today, but the hook is here).
        from ... import __version__ as _APP_VERSION
        self.version_label = QLabel(f"v{_APP_VERSION}", self)
        self.version_label.setObjectName("titleBarVersion")
        self.version_label.setStyleSheet(
            "QLabel#titleBarVersion {"
            "  color: rgba(255,255,255,0.55);"
            "  font-size: 11px;"
            "  padding: 0 8px;"
            "  background: transparent;"
            "}"
        )
        self.version_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.version_label, 0, Qt.AlignLeft | Qt.AlignVCenter)
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
        self.setWindowTitle("Touchless")
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

        title = QLabel("Choose the camera Touchless should use")
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
            # Deliberately do NOT attach a QAudioOutput. Each QAudioOutput
            # registers a Windows audio session with the endpoint (even when
            # muted), and the GestureMediaWidget is instantiated ~20 times
            # across the tutorial UI. That many ghost sessions changes how the
            # Razer / Windows shared-mode mixer negotiates format and applies
            # DSP, which caused a loud-spike regression when a second real
            # audio source (e.g. YouTube) joined on top of a game. With no
            # audio output attached, QMediaPlayer skips audio decoding entirely
            # — no session is ever registered. Video-only was the intent here
            # anyway (the previous code just set the QAudioOutput to muted).
            self._audio = None
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


def _build_gesture_guide_static_cards() -> list[GestureGuideCard]:
    return [
        GestureGuideCard(
            title="Left Hand One",
            action="Start voice command listening",
            how_to=(
                "How To: Face your left palm toward the monitor, extend only the index finger, and keep your thumb, middle, "
                "ring, and pinky fingers closed. Hold the pose steady for roughly half a second until Touchless beeps and the "
                "voice overlay appears.\n\n"
                "Requirements: A working microphone and the whisper.cpp model files bundled with the app. After Touchless confirms "
                "the pose, speak a command such as 'open YouTube on Google Chrome'. Use the left-hand fist gesture at any "
                "time to cancel listening."
            ),
            gesture_key="voice_one",
            image_name="Left One.png",
        ),
        GestureGuideCard(
            title="Left Hand Two",
            action="Start or stop dictation",
            how_to=(
                "How To: Face your left palm toward the monitor, extend the index and middle fingers in a V shape, and "
                "keep the thumb, ring, and pinky closed. Hold the pose steady for about half a second to toggle dictation.\n\n"
                "Requirements: A working microphone and a text field, chat box, or document that currently has keyboard "
                "focus — dictation types into whichever window was active when you started. Dictation runs continuously "
                "until you perform left-hand two a second time to stop, or perform the left-hand fist to cancel."
            ),
            gesture_key="two",
            image_name="Left Two.png",
        ),
        GestureGuideCard(
            title="Left Hand Three",
            action="Turn mouse mode on or off",
            how_to=(
                "How To: Face your left palm toward the monitor. Extend the index, middle, and ring fingers and keep them "
                "separated; fold the thumb and pinky. Hold the pose for about half a second to toggle mouse mode.\n\n"
                "Requirements: None — mouse mode can be toggled at any time. When mouse mode is on, use your right hand "
                "open-palm pose to move the cursor and bend the index or middle finger to click (see Mouse Controls)."
            ),
            gesture_key="left_three",
            image_name="Left Three.png",
        ),
        GestureGuideCard(
            title="Left Hand Four",
            action="Toggle drawing mode on or off",
            how_to=(
                "How To: Face your left palm toward the monitor. Extend the index, middle, ring, and pinky fingers and "
                "fold the thumb across the palm. Hold the pose steady for about half a second.\n\n"
                "Requirements: None to toggle — but while drawing mode is on, you unlock the Drawing, Erasing, Clear "
                "Canvas, Undo Drawing, and Drawing Settings Wheel gestures. Toggle drawing mode off with the same gesture "
                "when you're done; otherwise drawing gestures will keep intercepting your right hand."
            ),
            gesture_key="four",
            image_name="Left Hand Four.png",
        ),
        GestureGuideCard(
            title="Left Hand Fist",
            action="Cancel any voice command or dictation in progress",
            how_to=(
                "How To: Face your left palm toward the monitor and close all five fingers into a tight, compact fist. "
                "Hold the pose clearly so Touchless reads it as a fist rather than a partially curled hand.\n\n"
                "Requirements: Only useful while a voice process is active. It cancels listening, cancels the "
                "recognize/process stage, and stops dictation immediately. If nothing voice-related is running, the "
                "gesture does nothing."
            ),
            gesture_key="fist",
            image_name="LeftFist.png",
        ),
        GestureGuideCard(
            title="Right Hand Two",
            action="Open or focus Spotify",
            how_to=(
                "How To: Face your right palm toward the monitor, extend the index and middle fingers in a V shape, and "
                "keep the thumb, ring, and pinky closed. Hold the pose steady for about one second.\n\n"
                "Requirements: Spotify must be installed on the system. If Spotify is already running, Touchless brings it to "
                "the front; otherwise it launches it."
            ),
            gesture_key="two",
            image_name="Two.png",
        ),
        GestureGuideCard(
            title="Right Hand Fist",
            action="Play or pause the media currently playing (Spotify, YouTube, or any media)",
            how_to=(
                "How To: Face your right palm toward the monitor and close all five fingers into a tight, compact fist. "
                "Hold the pose steady for about half a second to trigger a play/pause toggle.\n\n"
                "Requirements: Something must be playing or paused. The gesture sends a global media-key event, so it "
                "controls whichever app currently owns media focus — Spotify, a Chrome tab playing YouTube, or any other "
                "media player."
            ),
            gesture_key="fist",
            image_name="Fist.png",
        ),
        GestureGuideCard(
            title="Mute",
            action="Mute or unmute the system's default audio output",
            how_to=(
                "How To: Face your right palm toward the monitor. Extend the thumb and pinky outward (like a 'call me' "
                "shape) while keeping the index, middle, and ring fingers folded. Hold the pose clearly for about half a "
                "second.\n\n"
                "Requirements: None. The gesture toggles the master system volume mute state, not individual apps. You'll "
                "see the system volume overlay flash to confirm."
            ),
            gesture_key="mute",
            image_name="Mute.png",
        ),
        GestureGuideCard(
            title="Gesture Wheel",
            action="Open the Spotify gesture wheel, or the Chrome wheel when Chrome is focused",
            how_to=(
                "How To: Face your right palm toward the monitor and make the wheel pose (thumb, index, and pinky "
                "extended; middle and ring folded). Hold for about one second until the wheel opens. Move your hand "
                "toward a slice and keep it there for one second to confirm that action.\n\n"
                "Requirements: The active window determines which wheel appears — Chrome/YouTube opens the Chrome wheel "
                "(refresh, back/forward, close tab, mute tab, share video), anything else opens the Spotify wheel (add to "
                "playlist, queue, like, shuffle). To close the wheel without selecting, lower your hand or move it away."
            ),
            gesture_key="wheel_pose",
            image_name="Wheel Pose.png",
        ),
        GestureGuideCard(
            title="Screen Wheel",
            action="Open the capture wheel for screenshots, screen recordings, and saved clips",
            how_to=(
                "How To: Face your right palm toward the monitor. Extend the index finger and pinky while keeping the "
                "thumb, middle, and ring fingers folded — a 'rock on' or horns shape. Hold the pose steady for about one "
                "second until the screen utility wheel opens. Move your hand toward a slice and hold for one second to "
                "confirm.\n\n"
                "Requirements: None to open. Slices are: full screenshot, custom area screenshot, full screen recording, "
                "custom area recording, save the last 30 seconds as a clip, and save the last 1 minute as a clip. Output "
                "paths can be configured in Settings → Save Locations."
            ),
            gesture_key="mute",
            image_name="ScreenWheel.png",
        ),
    ]


def _build_gesture_guide_dynamic_cards() -> list[GestureGuideCard]:
    return [
        GestureGuideCard(
            title="Swipe Left",
            action="Previous song in Spotify, or navigate back one page in Chrome",
            how_to=(
                "How To: Start with your right hand open and palm facing the monitor, positioned toward the right side "
                "of your camera frame. Move your hand smoothly and confidently to the left in one clean horizontal "
                "motion. Keep the palm open throughout the swipe and avoid bobbing up or down.\n\n"
                "Requirements: Either Spotify or Chrome must be the focused app. In Spotify it goes to the previous track; "
                "in Chrome it navigates to the previous page in history."
            ),
            gesture_key="open_hand",
            video_name="SwipeLeft.mp4",
        ),
        GestureGuideCard(
            title="Swipe Right",
            action="Next song in Spotify, or navigate forward one page in Chrome",
            how_to=(
                "How To: Start with your right hand open and palm facing the monitor, positioned toward the left side "
                "of your camera frame. Move your hand smoothly and confidently to the right in one clean horizontal "
                "motion. Keep the palm open throughout and avoid up-and-down drift.\n\n"
                "Requirements: Either Spotify or Chrome must be the focused app. In Spotify it skips to the next track; "
                "in Chrome it navigates forward in history (if a forward page exists)."
            ),
            gesture_key="open_hand",
            video_name="SwipeRight.mp4",
        ),
        GestureGuideCard(
            title="Volume Control",
            action="Adjust system volume, or the focused app's volume, up or down",
            how_to=(
                "How To: Face your right palm toward the monitor. Extend the index and middle fingers together (touching) "
                "and fold the thumb, ring, and pinky. Hold the pose until the volume overlay appears. Then move your hand "
                "up to raise volume or down to lower it, all while keeping the pose.\n\n"
                "Requirements: If Spotify or Chrome is playing audio, the overlay shows two bars — move your palm slightly "
                "left of your start position to select the app bar, or slightly right to select the system bar. Up/down "
                "adjusts whichever bar is highlighted. Drop the pose to close the overlay."
            ),
            gesture_key="volume_pose",
            video_name="VolControl.mp4",
        ),
        GestureGuideCard(
            title="Refresh / Repeat",
            action="Refresh the Chrome tab, or toggle repeat in Spotify",
            how_to=(
                "How To: With your right hand, extend only the index finger (other fingers folded, like pointing). Trace "
                "a small smooth circle in the air with your fingertip — roughly the size of a coaster. The motion must "
                "close into a loop, not just a partial arc.\n\n"
                "Requirements: Chrome or Spotify must be the focused app. In Chrome this reloads the current tab. In "
                "Spotify it cycles through repeat modes (off → repeat all → repeat one → off)."
            ),
            gesture_key="one",
            video_name="Repeat.mp4",
        ),
        GestureGuideCard(
            title="Mouse Controls",
            action="Move the cursor, left click, right click, and scroll",
            how_to=(
                "How To:\n"
                "\u2022 Turn mouse mode ON with your LEFT hand: face your palm to the monitor, extend only index, middle, "
                "and ring fingers (thumb and pinky folded), and hold the pose briefly.\n"
                "\u2022 Move the cursor with your RIGHT hand: hold it open-palm facing the monitor and move it inside the "
                "camera frame. The cursor tracks your palm.\n"
                "\u2022 Left-click: bend your right INDEX finger down and straighten it back up in one motion.\n"
                "\u2022 Right-click: bend your right MIDDLE finger down and straighten it back up in one motion.\n"
                "\u2022 Scroll: keep your right index and middle fingers extended together (ring and pinky folded) and "
                "move your hand UP to scroll up or DOWN to scroll down.\n"
                "\u2022 Turn mouse mode OFF: make the same left-hand three-finger pose again.\n\n"
                "Requirements: Mouse mode must be turned on first — the right hand only controls the cursor while mouse "
                "mode is active. Your right hand must be inside the calibrated control box (adjustable in Settings \u2192 "
                "Mouse Control). Turn mouse mode off when you're done so your right hand is free for other gestures."
            ),
            gesture_key="open_hand",
            video_name="MouseControl.mp4",
        ),
        GestureGuideCard(
            title="Maximize Window",
            action="Maximize the active window to fill the screen",
            how_to=(
                "How To: Hold both hands in the camera frame with palms facing the monitor. Start with your hands close "
                "together in front of your chest. Then spread both hands outward and apart in one smooth motion and hold "
                "the spread position briefly.\n\n"
                "Requirements: Both hands must be visible to the camera for the full motion. A window must be active "
                "(focused) — the gesture maximizes whichever window currently has focus."
            ),
            gesture_key="open_hand",
            video_name="Maximize.mp4",
        ),
        GestureGuideCard(
            title="Minimize Window",
            action="Minimize the active window to the taskbar",
            how_to=(
                "How To: Hold both hands in the camera frame with palms facing the monitor, spread apart. Bring both "
                "hands together in one smooth pinching motion until they are close together, and hold briefly.\n\n"
                "Requirements: Both hands must remain visible for the full motion. A window must be active — the gesture "
                "minimizes whichever window currently has focus."
            ),
            gesture_key="open_hand",
            video_name="Minimize.mp4",
        ),
        GestureGuideCard(
            title="Restore Window",
            action="Restore the active window to its floating (non-maximized) size",
            how_to=(
                "How To: Hold both hands visible with palms facing the monitor. From either a spread-apart or "
                "pinched-together position, move your hands to a medium distance apart and hold the pose briefly.\n\n"
                "Requirements: Both hands must remain visible. The active window must be maximized or minimized — "
                "restoring a normal-sized window has no visible effect."
            ),
            gesture_key="open_hand",
            video_name="restore.mp4",
        ),
        GestureGuideCard(
            title="Drawing",
            action="Draw freehand strokes on top of whatever is on screen",
            how_to=(
                "How To: With your right hand, extend only the index finger (other fingers folded, like pointing). Move "
                "your fingertip through the air — the stroke follows your index fingertip in real time. To stop a stroke, "
                "lift the hand out of the frame or switch to a different pose.\n\n"
                "Requirements: Drawing mode must be on — toggle it with the left-hand four static gesture first. Stroke "
                "color, thickness, and brush type can be changed via the Drawing Settings Wheel. The canvas floats above "
                "all other windows until you clear it or turn off drawing mode."
            ),
            gesture_key="one",
            video_name="Drawing.mp4",
        ),
        GestureGuideCard(
            title="Erasing",
            action="Erase drawn strokes from the canvas",
            how_to=(
                "How To: While drawing mode is active, open the Drawing Settings Wheel (right-hand wheel pose), move to "
                "the eraser slice, and hold to confirm. Then use the pointer pose — index finger extended — and move "
                "your hand over any strokes you want to erase.\n\n"
                "Requirements: Drawing mode must be on (left-hand four) and eraser mode must be selected from the "
                "Drawing Settings Wheel. Eraser size can be adjusted in the same wheel. Switch back to the brush from "
                "that wheel when you're done erasing."
            ),
            gesture_key="fist",
            video_name="Erasing.mp4",
        ),
        GestureGuideCard(
            title="Clear Canvas",
            action="Instantly remove every stroke from the drawing canvas",
            how_to=(
                "How To: While drawing mode is active, perform the clear canvas gesture (see the video) and hold it "
                "until the canvas clears. All strokes disappear at once.\n\n"
                "Requirements: Drawing mode must be on (left-hand four). This is destructive — Undo Drawing cannot bring "
                "cleared strokes back, so use it only when you want a fresh canvas."
            ),
            gesture_key="fist",
            video_name="ClearCanvas.mp4",
        ),
        GestureGuideCard(
            title="Undo Drawing",
            action="Remove the most recently drawn stroke",
            how_to=(
                "How To: While drawing mode is active, perform the undo gesture (see the video). Each confirmation "
                "removes the most recent stroke; repeat to undo multiple strokes in order.\n\n"
                "Requirements: Drawing mode must be on (left-hand four) and at least one stroke must exist on the canvas. "
                "Undo cannot restore strokes cleared by Clear Canvas."
            ),
            gesture_key="one",
            video_name="UndoDraw.mp4",
        ),
        GestureGuideCard(
            title="Drawing Settings Wheel",
            action="Open drawing options: pen color, brush size, brush type, and eraser toggle",
            how_to=(
                "How To: While drawing mode is on, make the wheel pose with your right hand (thumb, index, pinky "
                "extended; middle and ring folded) and hold it steady. The drawing settings wheel opens. Move toward a "
                "slice and hold for about one second to confirm the selection.\n\n"
                "Requirements: Drawing mode must be on (left-hand four) — otherwise the wheel pose opens the Spotify or "
                "Chrome wheel instead. Slices include color picker, brush size, brush type, and eraser toggle."
            ),
            gesture_key="wheel_pose",
            video_name="DrawingSettingsWheel.mp4",
        ),
    ]


def build_gesture_guide_content_widget(parent=None) -> QWidget:
    container = QWidget(parent)
    container.setObjectName("gestureGuideContainer")
    sections_layout = QVBoxLayout(container)
    sections_layout.setContentsMargins(0, 0, 0, 0)
    sections_layout.setSpacing(12)

    sections_layout.addWidget(GestureGuideSection("Static Gestures", _build_gesture_guide_static_cards()))
    sections_layout.addWidget(GestureGuideSection("Dynamic Gestures", _build_gesture_guide_dynamic_cards()))
    sections_layout.addStretch(1)
    return container


def build_gesture_guide_scroll_area(parent=None) -> QScrollArea:
    scroll = QScrollArea(parent)
    scroll.setObjectName("gestureGuideScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(build_gesture_guide_content_widget())
    return scroll


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

        if left_down and self._cursor_global is not None:
            local = self.mapFromGlobal(self._cursor_global)
            widget = self.childAt(local)
            pickable = widget
            while pickable is not None and not hasattr(pickable, "pick_from_local"):
                pickable = pickable.parentWidget()
            if pickable is not None and hasattr(pickable, "pick_from_local"):
                try:
                    local_to_pick = pickable.mapFromGlobal(self._cursor_global)
                    pickable.pick_from_local(local_to_pick)
                except Exception:
                    pass
                self._last_left_down = True
                self.update()
                return
            if not self._last_left_down:
                button_widget = widget
                while button_widget is not None and not isinstance(button_widget, QAbstractButton):
                    button_widget = button_widget.parentWidget()
                if isinstance(button_widget, QAbstractButton) and button_widget.isEnabled():
                    button_widget.click()
                    self._last_left_down = True
                    self.update()
                    return
        self._last_left_down = bool(left_down)
        self.update()

    def handle_debug_frame(self, frame, info) -> None:
        if not self.isVisible() or self._completed or not isinstance(info, dict):
            return
        if getattr(self, "_suspended_for_child", False):
            self._hand_clicks_armed = False
            self._raw_clicks_armed = False
            self._last_left_down = False
            self._last_right_down = False
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
        self._suspended_for_child = True
        self._hand_clicks_armed = False
        self._raw_clicks_armed = False
        self._last_left_down = False
        self._last_right_down = False
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
            self._suspended_for_child = False
            self._hand_clicks_armed = False
            self._raw_clicks_armed = False
            self._last_left_down = False
            self._last_right_down = False
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


class _HueSatChart(QWidget):
    picked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoMousePropagation, True)
        self._hue = 0.5
        self._sat = 1.0
        self._val = 1.0
        self._cache_image: QImage | None = None
        self._cache_key: tuple[int, int, float] | None = None

    def set_value(self, value: float) -> None:
        self._val = max(0.0, min(1.0, float(value)))
        self._cache_image = None
        self.update()

    def set_hue_sat(self, hue: float, sat: float) -> None:
        self._hue = max(0.0, min(1.0, float(hue)))
        self._sat = max(0.0, min(1.0, float(sat)))
        self.update()

    def _ensure_cache(self, w: int, h: int) -> None:
        key = (int(w), int(h), round(float(self._val), 3))
        if self._cache_image is not None and self._cache_key == key:
            return
        img = QImage(int(w), int(h), QImage.Format_RGB32)
        for y in range(int(h)):
            sat = 1.0 - (y / max(1.0, float(h - 1)))
            for x in range(int(w)):
                hue = x / max(1.0, float(w - 1))
                color = QColor.fromHsvF(max(0.0, min(0.9999, hue)), max(0.0, min(1.0, sat)), self._val)
                img.setPixel(x, y, color.rgb())
        self._cache_image = img
        self._cache_key = key

    def paintEvent(self, event) -> None:  # noqa: N802
        rect = self.rect().adjusted(2, 2, -2, -2)
        w = max(1, rect.width())
        h = max(1, rect.height())
        self._ensure_cache(w, h)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.drawImage(rect.topLeft(), self._cache_image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        cx = rect.x() + int(round(self._hue * (w - 1)))
        cy = rect.y() + int(round((1.0 - self._sat) * (h - 1)))
        painter.setPen(QPen(QColor(0, 0, 0, 220), 2))
        painter.drawEllipse(QPoint(cx, cy), 7, 7)
        painter.setPen(QPen(QColor(255, 255, 255, 240), 2))
        painter.drawEllipse(QPoint(cx, cy), 5, 5)
        painter.end()

    def _pick_from_pos(self, pos) -> None:
        rect = self.rect().adjusted(2, 2, -2, -2)
        x = max(rect.x(), min(rect.right(), pos.x())) - rect.x()
        y = max(rect.y(), min(rect.bottom(), pos.y())) - rect.y()
        w = max(1, rect.width() - 1)
        h = max(1, rect.height() - 1)
        hue = x / float(w)
        sat = 1.0 - (y / float(h))
        self._hue = max(0.0, min(1.0, hue))
        self._sat = max(0.0, min(1.0, sat))
        self.update()
        self.picked.emit(self._hue, self._sat)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pick_from_pos(event.position().toPoint() if hasattr(event, "position") else event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._pick_from_pos(event.position().toPoint() if hasattr(event, "position") else event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def pick_from_local(self, local_point) -> None:
        self._pick_from_pos(local_point)


class _BrightnessBar(QWidget):
    picked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(32)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoMousePropagation, True)
        self._hue = 0.5
        self._sat = 1.0
        self._val = 1.0

    def set_hue_sat(self, hue: float, sat: float) -> None:
        self._hue = max(0.0, min(1.0, float(hue)))
        self._sat = max(0.0, min(1.0, float(sat)))
        self.update()

    def set_value(self, value: float) -> None:
        self._val = max(0.0, min(1.0, float(value)))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        rect = self.rect().adjusted(2, 6, -2, -6)
        w = max(1, rect.width())
        h = max(1, rect.height())
        img = QImage(int(w), 1, QImage.Format_RGB32)
        for x in range(int(w)):
            val = x / max(1.0, float(w - 1))
            color = QColor.fromHsvF(self._hue, self._sat, val)
            img.setPixel(x, 0, color.rgb())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.drawImage(rect, img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        cx = rect.x() + int(round(self._val * (w - 1)))
        cy = rect.y() + rect.height() // 2
        painter.setPen(QPen(QColor(0, 0, 0, 220), 2))
        painter.drawEllipse(QPoint(cx, cy), 8, 8)
        painter.setPen(QPen(QColor(255, 255, 255, 240), 2))
        painter.drawEllipse(QPoint(cx, cy), 6, 6)
        painter.end()

    def _pick_from_pos(self, pos) -> None:
        rect = self.rect().adjusted(2, 6, -2, -6)
        x = max(rect.x(), min(rect.right(), pos.x())) - rect.x()
        w = max(1, rect.width() - 1)
        val = x / float(w)
        self._val = max(0.0, min(1.0, val))
        self.update()
        self.picked.emit(self._val)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._pick_from_pos(event.position().toPoint() if hasattr(event, "position") else event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._pick_from_pos(event.position().toPoint() if hasattr(event, "position") else event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def pick_from_local(self, local_point) -> None:
        self._pick_from_pos(local_point)


class HandColorPickerDialog(_HandSelectorBase):
    """Hand-controllable color picker: continuous HS chart + horizontal brightness bar."""

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

        chart_box = QFrame(self)
        chart_box.setObjectName("innerCard")
        chart_layout = QVBoxLayout(chart_box)
        chart_layout.setContentsMargins(12, 12, 12, 12)
        chart_layout.setSpacing(10)

        self._hs_chart = _HueSatChart(self)
        self._hs_chart.set_hue_sat(self._hue, self._sat)
        self._hs_chart.set_value(self._val)
        self._hs_chart.picked.connect(self._on_hs_picked)
        chart_layout.addWidget(self._hs_chart, 1)

        brightness_label = QLabel("Brightness")
        chart_layout.addWidget(brightness_label)

        self._brightness_bar = _BrightnessBar(self)
        self._brightness_bar.set_hue_sat(self._hue, self._sat)
        self._brightness_bar.set_value(self._val)
        self._brightness_bar.picked.connect(self._on_val_picked)
        chart_layout.addWidget(self._brightness_bar)

        preview_row = QHBoxLayout()
        self._val_label = QLabel(f"{int(self._val * 100)}%")
        self._val_label.setMinimumWidth(44)
        self._val_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        preview_row.addWidget(self._val_label)
        preview_row.addStretch(1)
        self._preview_swatch = QPushButton()
        self._preview_swatch.setFixedSize(72, 28)
        self._preview_swatch.setEnabled(False)
        preview_row.addWidget(self._preview_swatch)
        chart_layout.addLayout(preview_row)

        self.content_layout.addWidget(chart_box, 1)

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

    def _on_hs_picked(self, hue: float, sat: float) -> None:
        self._hue = float(hue)
        self._sat = float(sat)
        self._brightness_bar.set_hue_sat(self._hue, self._sat)
        self._refresh_preview()

    def _on_val_picked(self, value: float) -> None:
        self._val = float(value)
        self._hs_chart.set_value(self._val)
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


class _RefreshingCameraCombo(QComboBox):
    """QComboBox that emits `popup_about_to_show` right before its
    dropdown opens, so the camera list can refresh lazily without a
    separate "Search Devices" button."""

    popup_about_to_show = Signal()

    def showPopup(self) -> None:  # noqa: N802 (Qt API)
        try:
            self.popup_about_to_show.emit()
        except Exception:
            pass
        super().showPopup()


class _CameraInventoryThread(QThread):
    """Enumerates available cameras off the GUI thread.

    OpenCV's cv2.VideoCapture(idx) probe takes ~150-500ms per slot
    on Windows (longer when virtual webcam drivers are installed),
    so a synchronous scan of 8 slots can lock up the UI for several
    seconds. This worker runs the scan in the background and emits
    one signal with the resulting list — caller can use the cached
    list to populate the dropdown immediately and replace it when
    the fresh list arrives.
    """

    finished_with_inventory = Signal(object)   # list[CameraInfo]

    def __init__(self, scan_limit: int, parent=None) -> None:
        super().__init__(parent)
        self._scan_limit = int(scan_limit)

    def run(self) -> None:
        try:
            from ..camera.camera_utils import list_available_cameras
        except Exception:
            self.finished_with_inventory.emit([])
            return
        try:
            cams = list_available_cameras(self._scan_limit)
        except Exception:
            cams = []
        self.finished_with_inventory.emit(list(cams))


class _PhoneCameraTestThread(QThread):
    """Background probe for the "Use phone camera" Test button.

    Runs `try_open_camera_url` on a worker thread so the Settings UI stays
    responsive while OpenCV negotiates the stream (first-frame latency on a
    live MJPEG URL can be 2-5 seconds). Emits one signal with the outcome.
    """

    finished_with_result = Signal(bool, str)

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self._url = str(url or "").strip()

    def run(self) -> None:
        from ..camera.camera_utils import try_open_camera_url
        url = self._url
        if not url:
            self.finished_with_result.emit(False, "No URL provided.")
            return
        try:
            cap = try_open_camera_url(url)
        except Exception as exc:
            self.finished_with_result.emit(False, f"{type(exc).__name__}: {exc}")
            return
        if cap is None:
            self.finished_with_result.emit(
                False,
                "Could not connect. Check that the phone app is streaming, the URL is correct, and your phone and PC are on the same WiFi network.",
            )
            return
        try:
            ok, frame = cap.read()
        except Exception as exc:
            ok, frame = False, None
        finally:
            try:
                cap.release()
            except Exception:
                pass
        if not ok or frame is None:
            self.finished_with_result.emit(False, "Opened the stream but received no frames.")
            return
        height = int(getattr(frame, "shape", (0, 0, 0))[0]) if hasattr(frame, "shape") else 0
        width = int(getattr(frame, "shape", (0, 0, 0))[1]) if hasattr(frame, "shape") else 0
        self.finished_with_result.emit(True, f"connected — frame {width}x{height}.")


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self._phone_camera_qr_server = None
        # If a phone was previously paired, auto-start the embedded
        # server on launch so the user's already-open phone Safari tab
        # can just tap Start to reconnect — no QR rescan, no cert
        # reinstall (as long as the LAN IP didn't change, which it
        # usually doesn't across a single session / overnight on the
        # same WiFi).
        # Defensive state-consistency: a prior run might have saved
        # phone_camera_qr_use_mic=True with no accompanying pairing. If
        # we leave it True when not paired, the UI surfaces a "checked
        # but disabled" checkbox with no way to uncheck, which is
        # exactly the dead-end users hit on first install.
        if (
            bool(getattr(self.config, "phone_camera_qr_use_mic", False))
            and not bool(getattr(self.config, "phone_camera_qr_paired", False))
        ):
            self.config.phone_camera_qr_use_mic = False
            try:
                save_config(self.config)
            except Exception:
                pass
        if bool(getattr(self.config, "phone_camera_qr_paired", False)):
            try:
                from ..debug.phone_camera import PhoneCameraServer
                server = PhoneCameraServer(port=8765)
                server.start()
                self._phone_camera_qr_server = server
            except Exception as exc:
                print(f"[phone-camera] auto-start failed: {type(exc).__name__}: {exc}")
                # Stale pairing (port conflict, LAN changed, etc.) —
                # clear so next launch starts clean.
                self.config.phone_camera_qr_paired = False
                self.config.phone_camera_qr_active = False
                try:
                    save_config(self.config)
                except Exception:
                    pass
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
        self.setWindowTitle("Touchless")
        self.setMinimumSize(700, 540)
        self.resize(1020, 740)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self._build_ui()
        self._install_button_hover_refresh()
        self.apply_theme()
        QTimer.singleShot(0, self._initial_camera_setup)
        QTimer.singleShot(0, lambda: self.refresh_microphone_inventory(update_status=True, notify=False))
        # Auto-update check: defer 3s after launch so the app feels
        # snappy on cold start and the user has the UI in front of
        # them before any modal dialog appears. Failures (offline,
        # GitHub rate-limited) are silently swallowed — no nagging.
        self._update_checker = None
        self._update_dialog = None
        self._updater = None
        QTimer.singleShot(3000, self._kick_off_update_check)

    def _kick_off_update_check(self) -> None:
        try:
            from ..updater import ReleaseChecker
        except Exception:
            return
        self._update_checker = ReleaseChecker(parent=self)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    def _on_update_available(self, info) -> None:
        from ..updater.update_dialog import UpdateDialog
        from ..updater import Updater
        self._update_dialog = UpdateDialog(info, parent=self)
        self._updater = Updater(parent=self)
        self._update_dialog.download_requested.connect(self._updater.start_download)
        self._updater.progress.connect(
            lambda pct, msg: self._update_dialog.set_progress(pct, msg)
            if self._update_dialog is not None else None
        )
        self._updater.failed.connect(
            lambda reason: self._update_dialog.set_failure(reason)
            if self._update_dialog is not None else None
        )
        self._updater.ready_to_launch.connect(self._on_installer_ready)
        self._update_dialog.show()
        self._update_dialog.raise_()
        self._update_dialog.activateWindow()

    def _on_installer_ready(self, path: str) -> None:
        # apply_update_and_exit dispatches based on the update kind
        # (full installer vs app-only zip) the ReleaseChecker tagged
        # on the ReleaseInfo. Both paths exit the app on success.
        ok = self._updater.apply_update_and_exit(path) if self._updater else False
        if not ok and self._update_dialog is not None:
            self._update_dialog.set_failure(
                "Couldn't apply the update. Try running the installer manually "
                "from your Downloads or temp folder."
            )

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

        hero = QLabel("Touchless")
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
        self.last_action_label.setVisible(False)
        for label in (self.camera_label, self.status_label):
            label.setWordWrap(True)
            info_layout.addWidget(label)

        history_header_row = QHBoxLayout()
        history_header_row.setContentsMargins(0, 4, 0, 0)
        history_header_row.setSpacing(8)
        history_header = QLabel("Recent Actions")
        history_header.setObjectName("cardSubtitle")
        history_header_row.addWidget(history_header)
        history_header_row.addStretch(1)
        self.undo_action_button = QPushButton("Undo Last")
        self.undo_action_button.setObjectName("undoActionButton")
        self.undo_action_button.setEnabled(False)
        self.undo_action_button.clicked.connect(self._on_undo_last_action)
        history_header_row.addWidget(self.undo_action_button)
        info_layout.addLayout(history_header_row)
        self.action_history_list = QListWidget()
        self.action_history_list.setObjectName("actionHistoryList")
        self.action_history_list.setMaximumHeight(140)
        self.action_history_list.setSelectionMode(QListWidget.NoSelection)
        self.action_history_list.setFocusPolicy(Qt.NoFocus)
        info_layout.addWidget(self.action_history_list)

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

        settings_title = QLabel("Settings")
        settings_title.setObjectName("settingsTitle")
        left_layout.addWidget(settings_title)

        self._settings_search_input = QLineEdit()
        self._settings_search_input.setObjectName("settingsSearch")
        self._settings_search_input.setPlaceholderText("Search settings...")
        self._settings_search_input.setClearButtonEnabled(True)
        self._settings_search_input.textChanged.connect(self._on_settings_search_changed)
        left_layout.addWidget(self._settings_search_input)

        instructions_button = SettingsNavButton("Instructions", SECTION_INSTRUCTIONS, self)
        gestures_button = SettingsNavButton("Gesture Guide", SECTION_GESTURES, self)
        custom_gesture_button = SettingsNavButton("Custom Gesture", SECTION_CUSTOM_GESTURE, self)
        camera_button = SettingsNavButton("Camera", SECTION_CAMERA, self)
        microphone_button = SettingsNavButton("Microphone", SECTION_MICROPHONE, self)
        save_locations_button = SettingsNavButton("Save Locations", SECTION_SAVE_LOCATIONS, self)
        colors_button = SettingsNavButton("Colors", SECTION_COLORS, self)
        tutorial_button = SettingsNavButton("Tutorial", SECTION_TUTORIAL, self)
        updates_button = SettingsNavButton("Updates", SECTION_UPDATES, self)
        self._settings_nav_buttons = [
            instructions_button,
            gestures_button,
            custom_gesture_button,
            camera_button,
            microphone_button,
            save_locations_button,
            colors_button,
            tutorial_button,
            updates_button,
        ]
        self._settings_nav_search_keywords = {
            instructions_button: "instructions quick start help guide overview",
            gestures_button: "gesture guide swipe wheel mouse volume drawing spotify chrome youtube",
            custom_gesture_button: "custom gesture create record new",
            camera_button: "camera webcam device fps resolution auto-select low-fps",
            microphone_button: "microphone mic input gain audio voice whisper sapi",
            save_locations_button: "save locations folder path drawings screenshots recordings clips",
            colors_button: "colors theme accent overlay background",
            tutorial_button: "tutorial walkthrough practice guided onboarding",
            updates_button: "updates version release changelog about check",
        }
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
        self.settings_content_stack.addWidget(self._build_custom_gesture_panel())
        self.settings_content_stack.addWidget(self._build_camera_panel())
        self.settings_content_stack.addWidget(self._build_microphone_panel())
        self.settings_content_stack.addWidget(self._build_save_locations_panel())
        self.settings_content_stack.addWidget(self._build_colors_panel())
        self.settings_content_stack.addWidget(self._build_tutorial_panel())
        self.settings_content_stack.addWidget(self._build_updates_panel())

        layout.addWidget(left_panel)
        layout.addWidget(self.settings_content_stack, 1)

        self.show_settings_section(SECTION_INSTRUCTIONS)
        return page

    def _on_settings_search_changed(self, text: str) -> None:
        query = str(text or "").strip().lower()
        if not query:
            for button in self._settings_nav_buttons:
                button.setVisible(True)
            return
        tokens = [tok for tok in query.split() if tok]
        for button in self._settings_nav_buttons:
            haystack = f"{button.text().lower()} {self._settings_nav_search_keywords.get(button, '')}"
            button.setVisible(all(tok in haystack for tok in tokens))

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
            "Touchless lets you control Spotify, Chrome, mouse input, volume, and voice features from a live camera feed. Use this page as the quick start, Gesture Guide for the full control map, and Tutorial for the guided walkthrough.",
        )
        info_box = QFrame()
        info_box.setObjectName("innerCard")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(16, 16, 16, 16)
        info_layout.setSpacing(10)
        items = [
            "1. Press Start to begin live tracking with the selected camera. Touchless then reads your hand pose in real time and routes gestures to the active control context.",
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
        layout.addStretch(1)
        return panel

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

        scroll = build_gesture_guide_scroll_area()
        layout.addWidget(scroll, 1)
        return panel

    def _build_custom_gesture_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Custom Gesture",
            "Custom gesture setup is reserved for a future update.",
        )
        box = QFrame()
        box.setObjectName("innerCard")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 16, 16, 16)
        box_layout.setSpacing(10)
        coming_soon = QLabel("Coming soon.")
        coming_soon.setWordWrap(True)
        box_layout.addWidget(coming_soon)
        layout.addWidget(box)
        layout.addStretch(1)
        return panel

    def _build_colors_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Colors",
            "Choose app colors. Apply Changes saves them, and Revert to Original restores the original Touchless theme.",
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
            "Touchless searches for cameras when it opens. You can leave it on Auto-select or save a specific camera from the devices found on this computer.",
        )

        # The camera section has outgrown a single viewport. Wrap it in a
        # scroll area so long content (phone-camera URL + QR block, etc.)
        # scrolls vertically instead of squishing buttons horizontally.
        scroll = QScrollArea()
        scroll.setObjectName("cameraScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            f"""
            QScrollArea#cameraScroll, QScrollArea#cameraScroll > QWidget,
            QScrollArea#cameraScroll QWidget#qt_scrollarea_viewport {{
                background: transparent;
                border: none;
            }}
            QScrollArea#cameraScroll QScrollBar:vertical {{
                background: rgba(255,255,255,0.04);
                width: 10px;
                margin: 6px 3px 6px 3px;
                border-radius: 5px;
            }}
            QScrollArea#cameraScroll QScrollBar::handle:vertical {{
                background: {self.config.accent_color};
                border-radius: 5px;
                min-height: 32px;
            }}
            QScrollArea#cameraScroll QScrollBar::handle:vertical:hover {{
                background: {self.config.accent_color};
                /* slight brighten on hover via a subtle outer ring */
                border: 1px solid rgba(255,255,255,0.25);
            }}
            QScrollArea#cameraScroll QScrollBar::add-line:vertical,
            QScrollArea#cameraScroll QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
            }}
            QScrollArea#cameraScroll QScrollBar::add-page:vertical,
            QScrollArea#cameraScroll QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            """
        )

        box = QFrame()
        box.setObjectName("innerCard")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 16, 16, 16)
        box_layout.setSpacing(12)

        # Small checkbox stylesheet shared across this panel.
        checkbox_style_tpl = (
            "QCheckBox#{name} {{"
            "  color: {text};"
            "  spacing: 10px;"
            "  font-size: 13px;"
            "}}"
            "QCheckBox#{name}:disabled {{"
            "  color: rgba(255,255,255,0.35);"
            "}}"
            "QCheckBox#{name}::indicator {{"
            "  width: 16px;"
            "  height: 16px;"
            "  border-radius: 4px;"
            "  border: 1px solid rgba(255,255,255,0.35);"
            "  background: rgba(255,255,255,0.05);"
            "}}"
            "QCheckBox#{name}::indicator:checked {{"
            "  background: {accent};"
            "  border: 1px solid {accent};"
            "}}"
        )
        section_style = (
            f"QLabel#cameraSectionHeader {{"
            f"  color: {self.config.accent_color};"
            f"  font-size: 13px;"
            f"  font-weight: 600;"
            f"  letter-spacing: 1px;"
            f"  text-transform: uppercase;"
            f"  margin-top: 4px;"
            f"}}"
        )

        def _section_header(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("cameraSectionHeader")
            lbl.setStyleSheet(section_style)
            return lbl

        # ============================================================
        # 1. CONNECTED DEVICES (local camera selection)
        # ============================================================
        box_layout.addWidget(_section_header("Connected Devices"))

        self.camera_page_status = QLabel("Detected cameras: scanning...")
        self.camera_page_status.setWordWrap(True)
        box_layout.addWidget(self.camera_page_status)

        self.camera_combo = _RefreshingCameraCombo()
        self.camera_combo.setObjectName("settingsCameraCombo")
        # Refresh the device list right before the user sees the list —
        # plugging in a new webcam between app-launch and opening Settings
        # shouldn't require a separate "Search Devices" click. Runs on a
        # background thread (cv2.VideoCapture probes are 150-500ms each
        # and would freeze the UI for ~2-4s with the default scan limit
        # of 8). The dropdown shows the existing cached list immediately;
        # when the fresh scan finishes, the combo gets repopulated in
        # place. If a scan is already in flight, additional popups
        # don't kick off duplicate scans.
        self._camera_inventory_thread: _CameraInventoryThread | None = None
        self.camera_combo.popup_about_to_show.connect(self._kick_off_async_camera_refresh)
        box_layout.addWidget(self.camera_combo)

        note = QLabel(
            "Pick a local camera from the list above — the dropdown auto-refreshes each time you open it. "
            "To actually use the selected device, uncheck 'Use phone camera (QR) as source' below, then click "
            "Save Camera Selection at the bottom."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        self.camera_already_mirrored_checkbox = QCheckBox("This camera source is already mirrored (skip Touchless's flip)")
        self.camera_already_mirrored_checkbox.setObjectName("cameraMirroredCheckbox")
        self.camera_already_mirrored_checkbox.setStyleSheet(
            checkbox_style_tpl.format(name="cameraMirroredCheckbox", text=self.config.text_color, accent=self.config.accent_color)
        )
        self.camera_already_mirrored_checkbox.setChecked(bool(getattr(self.config, "camera_source_is_mirrored", False)))
        self.camera_already_mirrored_checkbox.toggled.connect(self._on_camera_already_mirrored_toggled)
        box_layout.addWidget(self.camera_already_mirrored_checkbox)

        # ============================================================
        # 2. PHONE CAMERA VIA HTTP URL
        # ============================================================
        box_layout.addWidget(_section_header("Phone Camera — Via HTTP URL"))

        phone_note = QLabel(
            "Use your phone's camera over WiFi by pasting an IP-camera URL. "
            "Install a free phone app (IP Webcam on Android, Iriun / EpocCam on iOS), start the stream, and paste "
            "the URL shown (like http://192.168.1.50:8080/video). Both devices must be on the same WiFi network."
        )
        phone_note.setObjectName("cameraNote")
        phone_note.setWordWrap(True)
        box_layout.addWidget(phone_note)

        self.phone_camera_checkbox = QCheckBox("Use phone camera (URL) as source")
        self.phone_camera_checkbox.setObjectName("phoneCameraCheckbox")
        self.phone_camera_checkbox.setStyleSheet(
            checkbox_style_tpl.format(name="phoneCameraCheckbox", text=self.config.text_color, accent=self.config.accent_color)
        )
        self.phone_camera_checkbox.setChecked(bool(getattr(self.config, "phone_camera_enabled", False)))
        self.phone_camera_checkbox.toggled.connect(self._on_phone_camera_toggled)
        box_layout.addWidget(self.phone_camera_checkbox)

        phone_row = QHBoxLayout()
        self.phone_camera_url_input = QLineEdit()
        self.phone_camera_url_input.setPlaceholderText("http://192.168.1.50:8080/video")
        self.phone_camera_url_input.setText(str(getattr(self.config, "phone_camera_url", "") or ""))
        self.phone_camera_url_input.editingFinished.connect(self._on_phone_camera_url_changed)
        phone_row.addWidget(self.phone_camera_url_input, 1)

        self.phone_camera_test_button = QPushButton("Test")
        self.phone_camera_test_button.setMinimumWidth(72)
        self.phone_camera_test_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.phone_camera_test_button.clicked.connect(self._on_phone_camera_test_clicked)
        phone_row.addWidget(self.phone_camera_test_button, 0)
        box_layout.addLayout(phone_row)

        self.phone_camera_status_label = QLabel("")
        self.phone_camera_status_label.setObjectName("cameraNote")
        self.phone_camera_status_label.setWordWrap(True)
        box_layout.addWidget(self.phone_camera_status_label)

        # ============================================================
        # 3. PHONE CAMERA VIA QR CODE
        # ============================================================
        box_layout.addWidget(_section_header("Phone Camera — Via QR Code"))

        qr_note = QLabel(
            "No phone app needed. Click Connect Phone (QR), scan the code with your phone's camera, "
            "then follow the prompts on the phone page. Touchless serves a small web page that streams your phone's "
            "camera directly to this PC. Works on iOS and Android."
        )
        qr_note.setObjectName("cameraNote")
        qr_note.setWordWrap(True)
        box_layout.addWidget(qr_note)

        qr_row = QHBoxLayout()
        qr_row.setSpacing(8)
        already_paired = bool(getattr(self.config, "phone_camera_qr_paired", False))
        self.phone_camera_qr_button = QPushButton("Show QR Code" if already_paired else "Connect Phone (QR)")
        self.phone_camera_qr_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.phone_camera_qr_button.clicked.connect(self._on_phone_camera_qr_clicked)
        qr_row.addWidget(self.phone_camera_qr_button)

        self.phone_camera_qr_disconnect_button = QPushButton("Disconnect Phone")
        self.phone_camera_qr_disconnect_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.phone_camera_qr_disconnect_button.clicked.connect(self._on_phone_camera_qr_disconnect_clicked)
        self.phone_camera_qr_disconnect_button.setVisible(already_paired)
        qr_row.addWidget(self.phone_camera_qr_disconnect_button)

        qr_row.addStretch(1)
        box_layout.addLayout(qr_row)

        self.use_phone_camera_qr_checkbox = QCheckBox("Use phone camera (QR) as source")
        self.use_phone_camera_qr_checkbox.setObjectName("usePhoneQrCheckbox")
        self.use_phone_camera_qr_checkbox.setStyleSheet(
            checkbox_style_tpl.format(name="usePhoneQrCheckbox", text=self.config.text_color, accent=self.config.accent_color)
        )
        self.use_phone_camera_qr_checkbox.setChecked(bool(getattr(self.config, "phone_camera_qr_active", False)))
        self.use_phone_camera_qr_checkbox.setEnabled(already_paired)
        self.use_phone_camera_qr_checkbox.toggled.connect(self._on_use_phone_camera_qr_toggled)
        box_layout.addWidget(self.use_phone_camera_qr_checkbox)

        self.phone_camera_qr_status_label = QLabel(
            "Phone paired — tap Start on your phone's browser to connect." if already_paired else ""
        )
        self.phone_camera_qr_status_label.setObjectName("cameraNote")
        self.phone_camera_qr_status_label.setWordWrap(True)
        box_layout.addWidget(self.phone_camera_qr_status_label)

        # ============================================================
        # 4. LOW FPS MODE
        # ============================================================
        box_layout.addWidget(_section_header("Low FPS Mode"))

        low_fps_note = QLabel(
            "Low FPS Mode loosens tracking thresholds so gestures still register when the camera runs slow (around 10-17 FPS). "
            "Touchless also offers to turn this on automatically if your measured FPS stays low for too long."
        )
        low_fps_note.setObjectName("cameraNote")
        low_fps_note.setWordWrap(True)
        box_layout.addWidget(low_fps_note)

        low_fps_row = QHBoxLayout()
        self.low_fps_button = QPushButton()
        self.low_fps_button.setCheckable(True)
        self.low_fps_button.setChecked(bool(self.config.low_fps_mode))
        self.low_fps_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.low_fps_button.clicked.connect(self._on_low_fps_button_toggled)
        low_fps_row.addWidget(self.low_fps_button)
        low_fps_row.addStretch(1)
        box_layout.addLayout(low_fps_row)
        self._refresh_low_fps_button_label()

        # ============================================================
        # 5. SAVE CAMERA SELECTION (at the bottom)
        # ============================================================
        box_layout.addWidget(_section_header("Save Camera Selection"))

        save_hint = QLabel(
            "When 'Use phone camera (QR) as source' or 'Use phone camera (URL) as source' is checked, Touchless uses that "
            "phone feed as its camera — saving below confirms that choice. To switch back to a device in 'Connected Devices', "
            "uncheck both phone options, select your device from the dropdown, then click Save Camera Selection."
        )
        save_hint.setObjectName("cameraNote")
        save_hint.setWordWrap(True)
        box_layout.addWidget(save_hint)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        self.save_camera_button = QPushButton("Save Camera Selection")
        self.save_camera_button.clicked.connect(self.save_camera_preference_from_settings)
        self.clear_camera_button = QPushButton("Use Auto-Select")
        self.clear_camera_button.clicked.connect(self.clear_camera_preference)
        for btn in (self.save_camera_button, self.clear_camera_button):
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        actions_row.addWidget(self.save_camera_button)
        actions_row.addWidget(self.clear_camera_button)
        actions_row.addStretch(1)
        box_layout.addLayout(actions_row)

        self._refresh_phone_camera_controls()

        scroll.setWidget(box)
        layout.addWidget(scroll, 1)
        return panel

    def _refresh_phone_camera_controls(self) -> None:
        if not hasattr(self, "phone_camera_checkbox"):
            return
        enabled = bool(self.phone_camera_checkbox.isChecked())
        # Enable the URL field whether or not the checkbox is ticked so the
        # user can paste a URL and hit Test before flipping the switch.
        self.phone_camera_url_input.setEnabled(True)
        self.phone_camera_test_button.setEnabled(True)
        # Grey out the local-camera picker when the phone source is active so
        # it's clear which setting is winning.
        if hasattr(self, "camera_combo"):
            self.camera_combo.setEnabled(not enabled)
        for attr in ("save_camera_button", "clear_camera_button"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(not enabled)

    def _on_camera_already_mirrored_toggled(self, checked: bool) -> None:
        self.config.camera_source_is_mirrored = bool(checked)
        save_config(self.config)
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText(
                "Last action: source-already-mirrored ON" if checked else "Last action: source-already-mirrored OFF"
            )
        # Takes effect on the very next frame the engine reads — no camera
        # restart needed, since the flip decision is re-evaluated every tick.

    def _on_phone_camera_toggled(self, checked: bool) -> None:
        self.config.phone_camera_enabled = bool(checked)
        save_config(self.config)
        self._refresh_phone_camera_controls()
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText(
                "Last action: phone camera ON" if checked else "Last action: phone camera OFF"
            )
        # If the checkbox is on but no URL has been saved yet, warn the user
        # instead of silently falling back to the local webcam.
        if checked and not str(getattr(self.config, "phone_camera_url", "") or "").strip():
            self.phone_camera_status_label.setText(
                "Enter a stream URL and click Test. Until then, the local camera will be used."
            )
            return
        # A live camera switch needs the engine to re-open, because the URL
        # / local-device selection is evaluated inside _open_camera().
        self._restart_camera_for_phone_toggle()

    def _on_phone_camera_url_changed(self) -> None:
        if not hasattr(self, "phone_camera_url_input"):
            return
        url = self.phone_camera_url_input.text().strip()
        if str(getattr(self.config, "phone_camera_url", "") or "") == url:
            return
        self.config.phone_camera_url = url
        save_config(self.config)
        if bool(getattr(self.config, "phone_camera_enabled", False)):
            # URL changed while phone-source is active: restart the capture
            # so the new URL takes effect immediately.
            self._restart_camera_for_phone_toggle()

    def _on_phone_camera_test_clicked(self) -> None:
        if not hasattr(self, "phone_camera_url_input"):
            return
        url = self.phone_camera_url_input.text().strip()
        if not url:
            self.phone_camera_status_label.setText("Enter a URL first.")
            return
        # Persist whatever the user typed before testing, so a successful
        # test is immediately usable by the engine when they flip the toggle.
        if str(getattr(self.config, "phone_camera_url", "") or "") != url:
            self.config.phone_camera_url = url
            save_config(self.config)
        self.phone_camera_test_button.setEnabled(False)
        self.phone_camera_status_label.setText("Testing connection...")
        self._phone_camera_test_thread = _PhoneCameraTestThread(url, self)
        self._phone_camera_test_thread.finished_with_result.connect(self._on_phone_camera_test_result)
        self._phone_camera_test_thread.finished.connect(self._phone_camera_test_thread.deleteLater)
        self._phone_camera_test_thread.start()

    def _on_phone_camera_test_result(self, ok: bool, message: str) -> None:
        self.phone_camera_test_button.setEnabled(True)
        prefix = "OK — " if ok else "Failed — "
        self.phone_camera_status_label.setText(prefix + message)

    def _on_phone_camera_qr_clicked(self) -> None:
        from .phone_camera_connect_dialog import PhoneCameraConnectDialog
        # Reuse the already-running server if one exists (auto-started at
        # launch or started by a previous dialog). This makes re-opening
        # the dialog a free "show me the QR again" action rather than a
        # teardown + fresh server on every click.
        existing = self._phone_camera_qr_server
        dialog = PhoneCameraConnectDialog(self.config, parent=self, existing_server=existing)
        dialog.camera_accepted.connect(self._adopt_phone_camera_server)
        dialog.exec()

    def _adopt_phone_camera_server(self, server) -> None:
        # User clicked "Use This Camera" in the QR dialog — the server is
        # running with frames flowing, we just need to point the engine at
        # its capture. Treat it like any other "source switch": flip the
        # URL-based phone-camera flag OFF (mutually exclusive), persist
        # the paired + active state so the server auto-starts next
        # launch, and restart the engine on the phone source.
        prev = getattr(self, "_phone_camera_qr_server", None)
        if prev is not None and prev is not server:
            try:
                prev.stop()
            except Exception:
                pass
        self._phone_camera_qr_server = server
        self.config.phone_camera_enabled = False
        self.config.phone_camera_qr_paired = True
        self.config.phone_camera_qr_active = True
        save_config(self.config)
        if hasattr(self, "phone_camera_checkbox"):
            self.phone_camera_checkbox.blockSignals(True)
            self.phone_camera_checkbox.setChecked(False)
            self.phone_camera_checkbox.blockSignals(False)
        if hasattr(self, "use_phone_camera_qr_checkbox"):
            self.use_phone_camera_qr_checkbox.blockSignals(True)
            self.use_phone_camera_qr_checkbox.setChecked(True)
            self.use_phone_camera_qr_checkbox.setEnabled(True)
            self.use_phone_camera_qr_checkbox.blockSignals(False)
        if hasattr(self, "use_phone_mic_checkbox"):
            # Pairing the camera does NOT imply the user wants to route
            # phone audio as well — keep the mic checkbox in whatever
            # state the user left it, but guarantee it isn't auto-
            # checked by this flow. The checkbox is an explicit opt-in.
            self.use_phone_mic_checkbox.setEnabled(True)
        if hasattr(self, "use_phone_mic_hint"):
            self.use_phone_mic_hint.setText(
                "Also make sure the phone page's Mic dropdown is set to 'send to PC' — otherwise no audio "
                "reaches Touchless and voice commands fall back to the local mic."
            )
        if hasattr(self, "phone_camera_qr_button_mic"):
            self.phone_camera_qr_button_mic.setText("Show QR Code")
        self.phone_camera_qr_status_label.setText(f"Paired — {server.info.url}")
        self.phone_camera_qr_disconnect_button.setVisible(True)
        self.phone_camera_qr_button.setText("Show QR Code")
        self._restart_camera_for_phone_toggle()
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText("Last action: phone camera paired via QR")

    def _on_phone_camera_qr_disconnect_clicked(self) -> None:
        server = getattr(self, "_phone_camera_qr_server", None)
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
        self._phone_camera_qr_server = None
        self.config.phone_camera_qr_paired = False
        self.config.phone_camera_qr_active = False
        save_config(self.config)
        self.phone_camera_qr_status_label.setText("Phone unpaired. The server is stopped.")
        self.phone_camera_qr_disconnect_button.setVisible(False)
        self.phone_camera_qr_button.setText("Connect Phone (QR)")
        if hasattr(self, "phone_camera_qr_button_mic"):
            self.phone_camera_qr_button_mic.setText("Connect Phone (QR)")
        if hasattr(self, "use_phone_camera_qr_checkbox"):
            self.use_phone_camera_qr_checkbox.blockSignals(True)
            self.use_phone_camera_qr_checkbox.setChecked(False)
            self.use_phone_camera_qr_checkbox.setEnabled(False)
            self.use_phone_camera_qr_checkbox.blockSignals(False)
        if hasattr(self, "use_phone_mic_checkbox"):
            self.use_phone_mic_checkbox.blockSignals(True)
            self.use_phone_mic_checkbox.setChecked(False)
            self.use_phone_mic_checkbox.setEnabled(True)
            self.use_phone_mic_checkbox.blockSignals(False)
        if hasattr(self, "use_phone_mic_hint"):
            self.use_phone_mic_hint.setText(
                "Click 'Connect Phone (QR)' above to pair your phone, then tick the box to use its mic."
            )
        self._refresh_phone_mic_dependent_ui()
        # Ensure the voice pipeline drops the now-stopped audio source
        # BEFORE the engine restart so the next command goes to the
        # local mic as expected.
        self.config.phone_camera_qr_use_mic = False
        save_config(self.config)
        self._apply_phone_mic_preference()
        self._restart_camera_for_phone_toggle()
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText("Last action: phone camera (QR) unpaired")

    def _apply_phone_mic_preference(self) -> None:
        """Point the voice pipeline at the phone mic or the local mic.

        Called every time the phone-camera-QR server state changes or
        the user toggles the "Use phone microphone" checkbox. Idempotent —
        clearing the external source re-enables sounddevice on the
        very next voice command.
        """
        worker = getattr(self, "_worker", None)
        listener = getattr(worker, "voice_listener", None) if worker is not None else None
        if listener is None or not hasattr(listener, "set_external_audio_source"):
            return
        qr_server = self._current_phone_camera_qr_server()
        use_phone_mic = (
            qr_server is not None
            and bool(getattr(self.config, "phone_camera_qr_paired", False))
            and bool(getattr(self.config, "phone_camera_qr_use_mic", False))
        )
        try:
            listener.set_external_audio_source(qr_server.audio_source if use_phone_mic else None)
        except Exception:
            pass

    def _on_use_phone_mic_toggled(self, checked: bool) -> None:
        # Block "on" without a paired phone: there's nowhere for phone
        # audio to come from, so flipping this on would silently still
        # use the local mic. Force back to off and update the hint so
        # the user knows what to do.
        if checked and not bool(getattr(self.config, "phone_camera_qr_paired", False)):
            if hasattr(self, "use_phone_mic_checkbox"):
                self.use_phone_mic_checkbox.blockSignals(True)
                self.use_phone_mic_checkbox.setChecked(False)
                self.use_phone_mic_checkbox.blockSignals(False)
            if hasattr(self, "use_phone_mic_hint"):
                self.use_phone_mic_hint.setText(
                    "No phone is paired yet. Go to Settings → Camera → Connect Phone (QR) first, "
                    "then come back here and turn this on."
                )
            self.config.phone_camera_qr_use_mic = False
            save_config(self.config)
            self._apply_phone_mic_preference()
            if hasattr(self, "last_action_label"):
                self.last_action_label.setText("Last action: phone not paired — pair first")
            self._refresh_phone_mic_dependent_ui()
            return
        self.config.phone_camera_qr_use_mic = bool(checked)
        save_config(self.config)
        self._apply_phone_mic_preference()
        self._refresh_phone_mic_dependent_ui()
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText(
                "Last action: using phone microphone" if checked else "Last action: using local microphone"
            )

    def _refresh_phone_mic_dependent_ui(self) -> None:
        """Visually de-emphasize the local-mic dropdown when phone mic
        is the chosen source, but KEEP the save/clear buttons clickable
        so the user can still save their fallback preference and see a
        confirmation popup. Matches the Camera panel's priority-hint
        pattern — we don't block the UI, we label it."""
        phone_active = bool(getattr(self.config, "phone_camera_qr_use_mic", False))
        if hasattr(self, "microphone_combo"):
            self.microphone_combo.setEnabled(not phone_active)

    def _on_use_phone_camera_qr_toggled(self, checked: bool) -> None:
        """Switch which camera source the engine reads from.

        Does NOT touch the server — it stays up in the background so
        the phone's browser tab can keep its WebSocket alive even while
        Touchless temporarily uses the laptop webcam. Perfect for
        "switch away, switch back" without rescanning the QR.
        """
        self.config.phone_camera_qr_active = bool(checked)
        save_config(self.config)
        self._restart_camera_for_phone_toggle()
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText(
                "Last action: using phone camera" if checked else "Last action: using local camera"
            )

    def _current_phone_camera_qr_server(self):
        return getattr(self, "_phone_camera_qr_server", None)

    def _restart_camera_for_phone_toggle(self) -> None:
        """Stop and re-start the engine so the new camera source is picked up.

        The engine evaluates phone_camera_enabled / phone_camera_url inside
        `_open_camera()`, which only runs during `start()`. Calling start()
        while already running is a no-op, so we must stop first.
        """
        worker = getattr(self, "_worker", None)
        if worker is None:
            return
        stop_fn = getattr(worker, "stop", None)
        start_fn = getattr(worker, "start", None)
        set_phone = getattr(worker, "set_phone_camera_capture", None)
        if callable(set_phone):
            # Always inform the engine of the current phone-camera-QR
            # capture (or None), BEFORE start() so _open_camera() sees it.
            qr_server = self._current_phone_camera_qr_server()
            qr_capture = qr_server.capture if qr_server is not None else None
            if not bool(getattr(self.config, "phone_camera_qr_active", False)):
                qr_capture = None
            try:
                set_phone(qr_capture)
            except Exception:
                pass
        # Similarly sync the phone-microphone source for the voice pipeline.
        self._apply_phone_mic_preference()
        if callable(stop_fn):
            try:
                stop_fn()
            except Exception:
                pass
        if callable(start_fn):
            try:
                start_fn()
            except Exception:
                pass

    def _refresh_low_fps_button_label(self) -> None:
        if not hasattr(self, "low_fps_button"):
            return
        on = bool(self.config.low_fps_mode)
        self.low_fps_button.setText("Low FPS Mode: ON" if on else "Low FPS Mode")
        self.low_fps_button.setChecked(on)

    def _on_low_fps_button_toggled(self, checked: bool) -> None:
        self.config.low_fps_mode = bool(checked)
        save_config(self.config)
        self._refresh_low_fps_button_label()
        worker = getattr(self, "_worker", None)
        if worker is not None and hasattr(worker, "set_low_fps_mode"):
            worker.set_low_fps_mode(self.config.low_fps_mode)
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText(
                "Last action: Low FPS Mode on" if self.config.low_fps_mode else "Last action: Low FPS Mode off"
            )


    def _build_microphone_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Microphone",
            "Touchless can use the default Windows microphone automatically or a specific saved microphone if you have more than one input device.",
        )

        # Wrap the panel body in a scroll area so the mic selector +
        # phone-mic toggle + test/gain controls don't get squeezed when
        # the Settings column is narrow. Matches the Camera panel's
        # pattern — accent-colored handle on a faint track.
        scroll = QScrollArea()
        scroll.setObjectName("micScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            f"""
            QScrollArea#micScroll, QScrollArea#micScroll > QWidget,
            QScrollArea#micScroll QWidget#qt_scrollarea_viewport {{
                background: transparent;
                border: none;
            }}
            QScrollArea#micScroll QScrollBar:vertical {{
                background: rgba(255,255,255,0.04);
                width: 10px;
                margin: 6px 3px 6px 3px;
                border-radius: 5px;
            }}
            QScrollArea#micScroll QScrollBar::handle:vertical {{
                background: {self.config.accent_color};
                border-radius: 5px;
                min-height: 32px;
            }}
            QScrollArea#micScroll QScrollBar::handle:vertical:hover {{
                background: {self.config.accent_color};
                border: 1px solid rgba(255,255,255,0.25);
            }}
            QScrollArea#micScroll QScrollBar::add-line:vertical,
            QScrollArea#micScroll QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
            }}
            QScrollArea#micScroll QScrollBar::add-page:vertical,
            QScrollArea#micScroll QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            """
        )

        scroll_container = QWidget()
        # Qt gives a naked QWidget a white system background unless we
        # explicitly opt out — without this the Microphone panel
        # viewport paints white behind our innerCard frames and
        # everything becomes unreadable against the pale text.
        scroll_container.setAutoFillBackground(False)
        scroll_container.setAttribute(Qt.WA_StyledBackground, False)
        scroll_container.setStyleSheet("background: transparent;")
        scroll_vbox = QVBoxLayout(scroll_container)
        scroll_vbox.setContentsMargins(0, 0, 0, 0)
        scroll_vbox.setSpacing(14)

        section_style = (
            f"QLabel#micSectionHeader {{"
            f"  color: {self.config.accent_color};"
            f"  font-size: 13px;"
            f"  font-weight: 600;"
            f"  letter-spacing: 1px;"
            f"  text-transform: uppercase;"
            f"  margin-top: 4px;"
            f"}}"
        )

        def _section_header(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("micSectionHeader")
            lbl.setStyleSheet(section_style)
            return lbl

        box = QFrame()
        box.setObjectName("innerCard")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(16, 16, 16, 16)
        box_layout.setSpacing(12)

        # ============================================================
        # LOCAL MICROPHONE
        # ============================================================
        box_layout.addWidget(_section_header("Local Microphone"))

        self.microphone_combo = QComboBox()
        self.microphone_combo.setObjectName("settingsMicrophoneCombo")
        box_layout.addWidget(self.microphone_combo)

        note = QLabel(
            "Choosing Auto-select means Touchless will use the default Windows microphone. "
            "Save a specific microphone only if you always want the same input device used by default."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        # ============================================================
        # PHONE MICROPHONE (QR)
        # ============================================================
        box_layout.addWidget(_section_header("Phone Microphone (QR)"))

        phone_mic_note = QLabel(
            "Pair your phone via the QR button below (or from Settings → Camera) and then tick the box "
            "to route its microphone into Touchless. Phone mics are usually cleaner than laptop webcam mics."
        )
        phone_mic_note.setObjectName("cameraNote")
        phone_mic_note.setWordWrap(True)
        box_layout.addWidget(phone_mic_note)

        already_paired = bool(getattr(self.config, "phone_camera_qr_paired", False))

        # QR pair / show button — same handler as the one in the Camera
        # panel so users don't have to cross tabs to pair.
        mic_qr_row = QHBoxLayout()
        mic_qr_row.setContentsMargins(0, 0, 0, 0)
        self.phone_camera_qr_button_mic = QPushButton(
            "Show QR Code" if already_paired else "Connect Phone (QR)"
        )
        self.phone_camera_qr_button_mic.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.phone_camera_qr_button_mic.clicked.connect(self._on_phone_camera_qr_clicked)
        mic_qr_row.addWidget(self.phone_camera_qr_button_mic)
        mic_qr_row.addStretch(1)
        box_layout.addLayout(mic_qr_row)

        self.use_phone_mic_checkbox = QCheckBox("Use phone microphone (QR) as source")
        self.use_phone_mic_checkbox.setObjectName("usePhoneMicCheckbox")
        self.use_phone_mic_checkbox.setStyleSheet(
            f"""
            QCheckBox#usePhoneMicCheckbox {{
                color: {self.config.text_color};
                spacing: 10px;
                font-size: 13px;
            }}
            QCheckBox#usePhoneMicCheckbox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid rgba(255,255,255,0.35);
                background: rgba(255,255,255,0.05);
            }}
            QCheckBox#usePhoneMicCheckbox::indicator:checked {{
                background: {self.config.accent_color};
                border: 1px solid {self.config.accent_color};
            }}
            """
        )
        self.use_phone_mic_checkbox.setChecked(bool(getattr(self.config, "phone_camera_qr_use_mic", False)))
        # Intentionally always enabled so the user can clear a stale
        # "checked" state even when the phone isn't currently paired.
        # The _on_use_phone_mic_toggled handler validates state and
        # refuses to turn on without a paired phone (and shows a hint).
        self.use_phone_mic_checkbox.setEnabled(True)
        self.use_phone_mic_checkbox.toggled.connect(self._on_use_phone_mic_toggled)
        box_layout.addWidget(self.use_phone_mic_checkbox)

        self.use_phone_mic_hint = QLabel(
            "Also make sure the phone page's Mic dropdown is set to 'send to PC' — otherwise no audio "
            "reaches Touchless and voice commands fall back to the local mic."
            if already_paired
            else "Click 'Connect Phone (QR)' above to pair your phone, then tick the box to use its mic."
        )
        self.use_phone_mic_hint.setObjectName("cameraNote")
        self.use_phone_mic_hint.setWordWrap(True)
        box_layout.addWidget(self.use_phone_mic_hint)

        # ============================================================
        # SAVE MICROPHONE SELECTION (at the bottom)
        # ============================================================
        box_layout.addWidget(_section_header("Save Microphone Selection"))

        save_hint = QLabel(
            "When 'Use phone microphone (QR) as source' is checked, Touchless uses the phone's mic regardless of "
            "the device selected above — saving confirms the phone as the source. To go back to a local device, "
            "uncheck the phone option, pick a device from Local Microphone, then click Save Microphone Choice."
        )
        save_hint.setObjectName("cameraNote")
        save_hint.setWordWrap(True)
        box_layout.addWidget(save_hint)

        actions_row = QHBoxLayout()
        self.save_microphone_button = QPushButton("Save Microphone Choice")
        self.save_microphone_button.clicked.connect(self.save_microphone_preference_from_settings)
        self.clear_microphone_button = QPushButton("Use Auto-Select")
        self.clear_microphone_button.clicked.connect(self.clear_microphone_preference)
        for btn in (self.save_microphone_button, self.clear_microphone_button):
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        actions_row.addWidget(self.save_microphone_button)
        actions_row.addWidget(self.clear_microphone_button)
        actions_row.addStretch(1)
        box_layout.addLayout(actions_row)

        scroll_vbox.addWidget(box)

        test_box = QFrame()
        test_box.setObjectName("innerCard")
        test_layout = QVBoxLayout(test_box)
        test_layout.setContentsMargins(16, 16, 16, 16)
        test_layout.setSpacing(10)

        test_title = QLabel("Test Microphone")
        test_title.setObjectName("sectionSubtitle")
        test_layout.addWidget(test_title)

        test_note = QLabel(
            "Start the test to see incoming audio as a 0-100 volume bar. "
            "Click Stop Mic Test to finish, then press Playback to replay what was captured.\n\n"
            "The gain slider applies software gain to your captured microphone audio. "
            "It boosts the level bar, the test playback, AND what voice commands hear \u2014 the slider value is saved. "
            "Dictation uses Windows' microphone level directly, so raise Windows Sound Settings \u2192 your microphone "
            "\u2192 Input volume / boost if you also want dictation to hear you louder."
        )
        test_note.setObjectName("cameraNote")
        test_note.setWordWrap(True)
        test_layout.addWidget(test_note)

        self.mic_test_level_bar = QProgressBar()
        self.mic_test_level_bar.setRange(0, 100)
        self.mic_test_level_bar.setValue(0)
        self.mic_test_level_bar.setTextVisible(True)
        self.mic_test_level_bar.setFormat("%p%")
        self.mic_test_level_bar.setObjectName("micTestLevelBar")
        self.mic_test_level_bar.setMinimumHeight(28)
        self.mic_test_level_bar.setStyleSheet(
            "QProgressBar#micTestLevelBar {"
            " background-color: rgba(255,255,255,0.08);"
            " border: 1px solid rgba(255,255,255,0.25);"
            " border-radius: 6px;"
            " color: #F4FAFF;"
            " font-weight: 600;"
            " text-align: center;"
            " min-height: 28px;"
            "}"
            "QProgressBar#micTestLevelBar::chunk {"
            " background-color: rgba(29,233,182,0.85);"
            " border-radius: 5px;"
            "}"
        )
        test_layout.addWidget(self.mic_test_level_bar)

        gain_row = QHBoxLayout()
        gain_label = QLabel("Microphone Gain (applies to voice commands + test)")
        gain_row.addWidget(gain_label)
        self.mic_test_gain_slider = QSlider(Qt.Horizontal)
        self.mic_test_gain_slider.setRange(10, 1000)
        saved_gain = float(getattr(self.config, "mic_input_gain", 1.0) or 1.0)
        saved_gain = max(0.1, min(10.0, saved_gain))
        self.mic_test_gain_slider.setValue(int(round(saved_gain * 100)))
        self.mic_test_gain_slider.valueChanged.connect(self._on_mic_test_gain_changed)
        gain_row.addWidget(self.mic_test_gain_slider, 1)
        self.mic_test_gain_value_label = QLabel(f"{saved_gain:.1f}x")
        self.mic_test_gain_value_label.setMinimumWidth(48)
        gain_row.addWidget(self.mic_test_gain_value_label)
        test_layout.addLayout(gain_row)

        test_buttons_row = QHBoxLayout()
        self.mic_test_toggle_button = QPushButton("Start Mic Test")
        self.mic_test_toggle_button.setCheckable(True)
        self.mic_test_toggle_button.toggled.connect(self._on_mic_test_toggled)
        self.mic_test_playback_button = QPushButton("Playback")
        self.mic_test_playback_button.clicked.connect(self._on_mic_test_playback_clicked)
        test_buttons_row.addWidget(self.mic_test_toggle_button)
        test_buttons_row.addWidget(self.mic_test_playback_button)
        test_layout.addLayout(test_buttons_row)

        self.mic_test_status_label = QLabel("")
        self.mic_test_status_label.setObjectName("cameraNote")
        self.mic_test_status_label.setWordWrap(True)
        test_layout.addWidget(self.mic_test_status_label)

        self._mic_test_input_stream = None
        self._mic_test_level_value = 0.0
        self._mic_test_gain = saved_gain
        self._mic_test_sample_rate = 48000
        self._mic_test_recorded_chunks: list[np.ndarray] = []
        self._mic_test_is_recording = False
        self._mic_test_playback_thread = None
        self._mic_test_playback_stop = False
        self._mic_test_level_timer = QTimer(self)
        self._mic_test_level_timer.setInterval(60)
        self._mic_test_level_timer.timeout.connect(self._refresh_mic_test_level_display)
        self._update_mic_test_playback_button_state()

        scroll_vbox.addWidget(test_box)
        scroll_vbox.addStretch(1)

        scroll.setWidget(scroll_container)
        layout.addWidget(scroll, 1)
        # Reflect current phone-mic preference on the local dropdown +
        # save/clear buttons (greyed when phone mic is active).
        self._refresh_phone_mic_dependent_ui()
        return panel

    def _on_mic_test_gain_changed(self, value: int) -> None:
        gain = max(0.1, min(10.0, float(value) / 100.0))
        self._mic_test_gain = gain
        if hasattr(self, "mic_test_gain_value_label"):
            self.mic_test_gain_value_label.setText(f"{gain:.1f}x")
        self.config.mic_input_gain = gain
        save_config(self.config)
        if self._worker is not None:
            try:
                self._worker.voice_listener.set_input_gain(gain)
            except Exception:
                pass
        if self.tutorial_window is not None and hasattr(self.tutorial_window, "_voice_listener"):
            try:
                self.tutorial_window._voice_listener.set_input_gain(gain)
            except Exception:
                pass

    def _selected_mic_test_device(self):
        combo = getattr(self, "microphone_combo", None)
        if combo is None:
            return None
        data = combo.currentData()
        if data is None:
            return None
        name = str(data).strip()
        if not name:
            return None
        try:
            import sounddevice as sd
            devices = sd.query_devices()
        except Exception:
            return None
        for idx, device in enumerate(devices):
            try:
                if int(device.get("max_input_channels", 0) or 0) <= 0:
                    continue
            except Exception:
                continue
            if str(device.get("name", "") or "").strip() == name:
                return idx
        return None

    def _on_mic_test_toggled(self, checked: bool) -> None:
        if checked:
            self._start_mic_test()
        else:
            self._stop_mic_test()

    def _start_mic_test(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            self.mic_test_status_label.setText(f"sounddevice unavailable: {exc}")
            self.mic_test_toggle_button.blockSignals(True)
            self.mic_test_toggle_button.setChecked(False)
            self.mic_test_toggle_button.blockSignals(False)
            return
        self._stop_mic_test_streams()
        self._stop_mic_test_playback()
        device = self._selected_mic_test_device()
        sample_rate = self._mic_test_sample_rate
        channels = 1
        self._mic_test_recorded_chunks = []
        self._mic_test_is_recording = True
        try:
            def _callback(indata, frames, time_info, status):
                if indata is None or len(indata) == 0:
                    return
                try:
                    mono = indata[:, 0] if indata.ndim > 1 else indata
                    mono = np.asarray(mono, dtype=np.float32) * float(self._mic_test_gain)
                    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
                    self._mic_test_level_value = min(1.0, peak)
                    if self._mic_test_is_recording:
                        self._mic_test_recorded_chunks.append(np.clip(mono, -1.0, 1.0).copy())
                except Exception:
                    pass
            self._mic_test_input_stream = sd.InputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="float32",
                device=device,
                blocksize=0,
                callback=_callback,
            )
            self._mic_test_input_stream.start()
        except Exception as exc:
            self.mic_test_status_label.setText(f"Could not open microphone: {exc}")
            self._mic_test_input_stream = None
            self._mic_test_is_recording = False
            self.mic_test_toggle_button.blockSignals(True)
            self.mic_test_toggle_button.setChecked(False)
            self.mic_test_toggle_button.blockSignals(False)
            return
        self.mic_test_toggle_button.setText("Stop Mic Test")
        self.mic_test_status_label.setText("")
        self._update_mic_test_playback_button_state()
        self._mic_test_level_timer.start()

    def _stop_mic_test_streams(self) -> None:
        stream = self._mic_test_input_stream
        self._mic_test_input_stream = None
        self._mic_test_is_recording = False
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def _stop_mic_test(self) -> None:
        self._stop_mic_test_streams()
        self._mic_test_level_timer.stop()
        self._mic_test_level_value = 0.0
        if hasattr(self, "mic_test_level_bar"):
            self.mic_test_level_bar.setValue(0)
        if hasattr(self, "mic_test_toggle_button"):
            self.mic_test_toggle_button.setText("Start Mic Test")
        if hasattr(self, "mic_test_status_label"):
            self.mic_test_status_label.setText("")
        self._update_mic_test_playback_button_state()

    def _update_mic_test_playback_button_state(self) -> None:
        btn = getattr(self, "mic_test_playback_button", None)
        if btn is None:
            return
        btn.setText("Playback")
        if self._mic_test_is_recording:
            btn.setEnabled(False)
            return
        if self._mic_test_playback_thread is not None and self._mic_test_playback_thread.is_alive():
            btn.setEnabled(True)
            return
        btn.setEnabled(bool(self._mic_test_recorded_chunks))

    def _on_mic_test_playback_clicked(self) -> None:
        if self._mic_test_is_recording:
            return
        if self._mic_test_playback_thread is not None and self._mic_test_playback_thread.is_alive():
            self._stop_mic_test_playback()
            self._update_mic_test_playback_button_state()
            return
        if not self._mic_test_recorded_chunks:
            self.mic_test_status_label.setText("Nothing recorded yet.")
            return
        try:
            import sounddevice as sd
        except Exception as exc:
            self.mic_test_status_label.setText(f"Playback unavailable: {exc}")
            return
        try:
            audio = np.concatenate(self._mic_test_recorded_chunks).astype(np.float32)
        except Exception as exc:
            self.mic_test_status_label.setText(f"Playback error: {exc}")
            return
        if audio.size == 0:
            self.mic_test_status_label.setText("Nothing recorded yet.")
            return
        self._mic_test_playback_stop = False
        sample_rate = self._mic_test_sample_rate

        import threading
        def _play():
            try:
                sd.play(audio, samplerate=sample_rate)
                while sd.get_stream().active:
                    if self._mic_test_playback_stop:
                        sd.stop()
                        break
                    time.sleep(0.05)
            except Exception:
                pass
            finally:
                QTimer.singleShot(0, self._update_mic_test_playback_button_state)

        self._mic_test_playback_thread = threading.Thread(target=_play, daemon=True)
        self._mic_test_playback_thread.start()
        self.mic_test_status_label.setText("")
        self._update_mic_test_playback_button_state()

    def _stop_mic_test_playback(self) -> None:
        self._mic_test_playback_stop = True
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def _refresh_mic_test_level_display(self) -> None:
        if not hasattr(self, "mic_test_level_bar"):
            return
        level = max(0.0, min(1.0, float(self._mic_test_level_value)))
        self.mic_test_level_bar.setValue(int(round(level * 100)))

    def _build_save_locations_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Save Locations",
            "Choose the default folders used for drawings, screenshots, screen recordings, and clips. Each output type keeps its own saved location.",
        )

        scroll = QScrollArea()
        scroll.setObjectName("saveLocationsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFocusPolicy(Qt.StrongFocus)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        scroll_content = QWidget()
        scroll_content.setObjectName("saveLocationsScrollContent")
        scroll_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(2, 2, 2, 2)
        scroll_layout.setSpacing(16)

        box = QFrame()
        box.setObjectName("innerCard")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(18, 18, 18, 18)
        box_layout.setSpacing(14)

        note = QLabel(
            "Type a folder path and press Save, or use Browse to choose a folder. "
            "If a folder does not exist yet, Touchless will try to create it safely."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        for output_kind in SAVE_LOCATION_OUTPUT_ORDER:
            row_frame = QFrame()
            row_frame.setObjectName("saveLocationRow")
            row_layout = QVBoxLayout(row_frame)
            row_layout.setContentsMargins(0, 4, 0, 4)
            row_layout.setSpacing(8)

            label = QLabel(SAVE_LOCATION_LABELS.get(output_kind, output_kind.title()))
            label.setObjectName("saveLocationLabel")
            row_layout.addWidget(label)

            path_edit = QLineEdit(str(self._save_output_directory(output_kind)))
            path_edit.setObjectName(f"{output_kind}SaveLocationEdit")
            path_edit.setProperty("saveLocationPath", True)
            path_edit.setClearButtonEnabled(True)
            path_edit.setMinimumWidth(280)
            path_edit.setMinimumHeight(40)
            path_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            path_edit.returnPressed.connect(lambda kind=output_kind, editor=path_edit: self._apply_save_location(kind, editor))
            self._save_location_inputs[output_kind] = path_edit
            row_layout.addWidget(path_edit)

            button_row = QHBoxLayout()
            button_row.setContentsMargins(0, 0, 0, 0)
            button_row.setSpacing(10)
            browse_button = QPushButton("Browse")
            browse_button.setMinimumHeight(38)
            browse_button.clicked.connect(lambda _checked=False, kind=output_kind: self._browse_save_location(kind))
            save_button = QPushButton("Save")
            save_button.setMinimumHeight(38)
            save_button.clicked.connect(lambda _checked=False, kind=output_kind, editor=path_edit: self._apply_save_location(kind, editor))
            button_row.addWidget(browse_button)
            button_row.addWidget(save_button)
            button_row.addStretch(1)
            row_layout.addLayout(button_row)

            box_layout.addWidget(row_frame)

        scroll_layout.addWidget(box)

        name_box = QFrame()
        name_box.setObjectName("innerCard")
        name_box_layout = QVBoxLayout(name_box)
        name_box_layout.setContentsMargins(18, 18, 18, 18)
        name_box_layout.setSpacing(14)
        name_note = QLabel(
            "Set the default file name prefix for each output type. "
            "The app auto-increments a counter (e.g. Touchless_Drawing_1, Touchless_Drawing_2) "
            "based on existing files in the save folder."
        )
        name_note.setObjectName("cameraNote")
        name_note.setWordWrap(True)
        name_box_layout.addWidget(name_note)

        for output_kind in SAVE_LOCATION_OUTPUT_ORDER:
            name_row_frame = QFrame()
            name_row_layout = QVBoxLayout(name_row_frame)
            name_row_layout.setContentsMargins(0, 4, 0, 4)
            name_row_layout.setSpacing(8)

            name_label = QLabel(SAVE_LOCATION_LABELS.get(output_kind, output_kind.title()))
            name_label.setObjectName("saveLocationLabel")
            name_row_layout.addWidget(name_label)

            current_name = configured_save_name(self.config, output_kind)
            name_edit = QLineEdit(current_name)
            name_edit.setObjectName(f"{output_kind}SaveNameEdit")
            name_edit.setProperty("saveLocationPath", True)
            name_edit.setPlaceholderText(SAVE_NAME_DEFAULTS.get(output_kind, "Touchless_File"))
            name_edit.setMinimumWidth(280)
            name_edit.setMinimumHeight(40)
            name_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            name_edit.returnPressed.connect(lambda kind=output_kind, editor=name_edit: self._apply_save_name(kind, editor))
            self._save_name_inputs[output_kind] = name_edit
            name_row_layout.addWidget(name_edit)

            name_button_row = QHBoxLayout()
            name_button_row.setContentsMargins(0, 0, 0, 0)
            name_button_row.setSpacing(10)
            name_save_btn = QPushButton("Save")
            name_save_btn.setMinimumHeight(38)
            name_save_btn.clicked.connect(lambda _checked=False, kind=output_kind, editor=name_edit: self._apply_save_name(kind, editor))
            name_button_row.addWidget(name_save_btn)
            name_button_row.addStretch(1)
            name_row_layout.addLayout(name_button_row)

            name_box_layout.addWidget(name_row_frame)

        scroll_layout.addWidget(name_box)
        scroll_layout.addStretch(1)

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)
        return panel

    def _apply_save_name(self, output_kind: str, editor: QLineEdit | None) -> None:
        field_name = save_name_config_field(output_kind)
        if not field_name:
            return
        raw_value = str(editor.text() if editor is not None else "").strip()
        if not raw_value:
            raw_value = SAVE_NAME_DEFAULTS.get(output_kind, "Touchless_File")
            if editor is not None:
                editor.setText(raw_value)
        import re as _re
        safe_name = _re.sub(r"[^\w\-]", "_", raw_value).strip("_")
        if not safe_name:
            safe_name = SAVE_NAME_DEFAULTS.get(output_kind, "Touchless_File")
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

    def _build_updates_panel(self) -> QWidget:
        from ... import __version__ as APP_VERSION  # local import keeps top-of-module clean
        panel, layout = self._make_content_panel(
            "Updates",
            "See what version of Touchless you're running, manually trigger an update check, and review what's changed in past releases.",
        )

        # ---- Current version + Check button ----
        current_box = QFrame()
        current_box.setObjectName("innerCard")
        current_layout = QVBoxLayout(current_box)
        current_layout.setContentsMargins(16, 16, 16, 16)
        current_layout.setSpacing(10)

        version_row = QHBoxLayout()
        version_label = QLabel(f"<b>Current version:</b>  v{APP_VERSION}")
        version_label.setStyleSheet("font-size: 14px;")
        version_row.addWidget(version_label)
        version_row.addStretch(1)

        self._updates_check_button = QPushButton("Check for Updates")
        self._updates_check_button.clicked.connect(self._on_updates_panel_check_clicked)
        version_row.addWidget(self._updates_check_button)
        current_layout.addLayout(version_row)

        self._updates_status_label = QLabel("Click 'Check for Updates' to look for a newer version.")
        self._updates_status_label.setWordWrap(True)
        self._updates_status_label.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 12px;")
        current_layout.addWidget(self._updates_status_label)

        layout.addWidget(current_box)

        # ---- Release history ----
        history_header = QLabel("Release History")
        history_header.setStyleSheet("font-size: 13px; font-weight: 600; padding: 8px 0 4px 4px;")
        layout.addWidget(history_header)

        # The history is loaded lazily on first panel view to avoid
        # an unconditional GitHub API call at startup.
        from PySide6.QtWidgets import QScrollArea
        self._updates_history_scroll = QScrollArea()
        self._updates_history_scroll.setWidgetResizable(True)
        self._updates_history_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        history_container = QWidget()
        history_container.setStyleSheet("background: transparent;")
        self._updates_history_layout = QVBoxLayout(history_container)
        self._updates_history_layout.setContentsMargins(4, 4, 4, 4)
        self._updates_history_layout.setSpacing(8)
        self._updates_history_layout.addStretch(1)
        self._updates_history_scroll.setWidget(history_container)
        layout.addWidget(self._updates_history_scroll, 1)

        self._updates_history_loaded = False
        self._updates_history_fetcher = None

        return panel

    def _on_updates_panel_check_clicked(self) -> None:
        """Manual update check from the Updates settings panel."""
        from ..updater import ReleaseChecker
        if hasattr(self, "_updates_check_button"):
            self._updates_check_button.setEnabled(False)
            self._updates_check_button.setText("Checking...")
        self._updates_status_label.setText("Checking GitHub for the latest release...")
        # Reuse the dialog flow from the auto-check path, so a found
        # update presents the same Download/Later UI the user already
        # knows from the startup notification.
        checker = ReleaseChecker(parent=self)
        checker.update_available.connect(self._on_update_available)
        checker.update_available.connect(self._on_manual_update_found)
        checker.no_update.connect(self._on_manual_no_update)
        checker.check_failed.connect(self._on_manual_check_failed)
        checker.start()
        self._update_checker = checker  # keep alive

    def _on_manual_update_found(self, info) -> None:
        self._updates_check_button.setEnabled(True)
        self._updates_check_button.setText("Check for Updates")
        self._updates_status_label.setText(
            f"Update available: Touchless {info.version}. The download dialog has opened."
        )

    def _on_manual_no_update(self) -> None:
        self._updates_check_button.setEnabled(True)
        self._updates_check_button.setText("Check for Updates")
        self._updates_status_label.setText("You're already on the latest version. Nothing to update.")

    def _on_manual_check_failed(self, reason: str) -> None:
        self._updates_check_button.setEnabled(True)
        self._updates_check_button.setText("Check for Updates")
        self._updates_status_label.setText(
            f"Couldn't reach GitHub: {reason}. Try again in a moment, or visit the Releases page manually."
        )

    def _ensure_updates_history_loaded(self) -> None:
        if self._updates_history_loaded or self._updates_history_fetcher is not None:
            return
        from ..updater import ReleaseHistoryFetcher
        self._updates_history_fetcher = ReleaseHistoryFetcher(parent=self)
        self._updates_history_fetcher.history_loaded.connect(self._on_updates_history_loaded)
        self._updates_history_fetcher.history_failed.connect(self._on_updates_history_failed)
        # Show a placeholder while loading.
        loading = QLabel("Loading release history from GitHub...")
        loading.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 12px; padding: 8px;")
        loading.setObjectName("updatesHistoryPlaceholder")
        self._updates_history_layout.insertWidget(0, loading)
        self._updates_history_fetcher.start()

    def _on_updates_history_loaded(self, entries: list) -> None:
        self._updates_history_loaded = True
        self._clear_updates_history_widgets()
        if not entries:
            empty = QLabel("No releases published yet.")
            empty.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 12px; padding: 8px;")
            self._updates_history_layout.insertWidget(0, empty)
            return
        for entry in entries:
            self._updates_history_layout.insertWidget(
                self._updates_history_layout.count() - 1,
                self._build_release_entry_widget(entry),
            )

    def _on_updates_history_failed(self, reason: str) -> None:
        self._updates_history_loaded = False
        self._clear_updates_history_widgets()
        err = QLabel(
            f"Couldn't load release history: {reason}.\n"
            f"Check your internet connection or open the Releases page on GitHub directly."
        )
        err.setWordWrap(True)
        err.setStyleSheet("color: rgba(255,140,140,0.85); font-size: 12px; padding: 8px;")
        self._updates_history_layout.insertWidget(0, err)
        # Clear the fetcher reference so a re-open of the panel
        # triggers a fresh fetch (e.g. user reconnected to wifi).
        self._updates_history_fetcher = None

    def _clear_updates_history_widgets(self) -> None:
        # Remove every widget except the trailing stretch spacer.
        layout = self._updates_history_layout
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_release_entry_widget(self, entry) -> QWidget:
        # Inline import to avoid pushing more names into the
        # module's already-large import block.
        from PySide6.QtWidgets import QToolButton, QTextBrowser
        box = QFrame()
        box.setObjectName("innerCard")
        box.setStyleSheet(
            "QFrame#innerCard { background: rgba(255,255,255,0.04); "
            "border-radius: 8px; padding: 8px; }"
        )
        v = QVBoxLayout(box)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        title_text = f"<b>v{entry.version}</b>"
        if entry.is_current:
            title_text += "  <span style='color:#1de9b6;'>(installed)</span>"
        title = QLabel(title_text)
        title.setStyleSheet("font-size: 13px;")
        head_row.addWidget(title)

        # Date — strip the time portion for readability.
        date_str = (entry.published_at or "").split("T", 1)[0]
        if date_str:
            date_label = QLabel(date_str)
            date_label.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 11px;")
            head_row.addWidget(date_label)
        head_row.addStretch(1)
        v.addLayout(head_row)

        body_text = entry.body.strip() or "_No release notes provided._"
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setStyleSheet(
            "QTextBrowser { background: transparent; border: none; "
            "color: rgba(255,255,255,0.85); font-size: 12px; }"
        )
        try:
            notes.setMarkdown(body_text)
        except Exception:
            notes.setPlainText(body_text)
        # Cap height so very long bodies don't blow out the panel.
        notes.setMaximumHeight(180)
        v.addWidget(notes)

        return box

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
        # Clear focus before switching so stale focus on a now-hidden line edit
        # doesn't leave the incoming panel unable to receive clicks/wheel events.
        current = self.settings_content_stack.currentWidget()
        if current is not None:
            focused = current.focusWidget()
            if focused is not None:
                focused.clearFocus()
        self.settings_content_stack.setCurrentIndex(index)
        for i, button in enumerate(self._settings_nav_buttons):
            button.setChecked(i == index)
        # Lazily fetch the release history the first time the user
        # opens the Updates section, so we don't hit GitHub on every
        # settings page entry.
        if index == SECTION_UPDATES:
            self._ensure_updates_history_loaded()
        # Force the newly-shown panel to recompute geometry. QStackedWidget
        # occasionally skips this for scroll-area children, leaving the viewport
        # at a stale size where wheel/click hit-testing silently misses.
        new_widget = self.settings_content_stack.currentWidget()
        if new_widget is not None:
            layout = new_widget.layout()
            if layout is not None:
                layout.activate()
            new_widget.updateGeometry()
            QTimer.singleShot(0, new_widget.update)

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
            font-size: 68px;
            font-weight: 900;
            letter-spacing: -0.02em;
            color: {self.config.accent_color};
            background: transparent;
            padding: 6px 0 2px 0;
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
        #cardSubtitle {{
            font-size: 13px;
            font-weight: 700;
            color: rgba(176,219,252,0.95);
            background: transparent;
        }}
        QListWidget#actionHistoryList {{
            background-color: rgba(130, 187, 255, 0.12);
            border: 1px solid rgba(130, 187, 255, 0.40);
            border-radius: 10px;
            color: #DEEBFF;
            padding: 6px;
        }}
        QListWidget#actionHistoryList::item {{
            padding: 3px 6px;
            color: #DEEBFF;
            background: transparent;
        }}
        QPushButton#undoActionButton {{
            background-color: rgba(130, 187, 255, 0.18);
            border: 1px solid rgba(130, 187, 255, 0.55);
            border-radius: 8px;
            color: #DEEBFF;
            padding: 4px 10px;
            font-weight: 700;
        }}
        QPushButton#undoActionButton:hover {{
            background-color: rgba(130, 187, 255, 0.28);
        }}
        QPushButton#undoActionButton:disabled {{
            color: rgba(222,235,255,0.45);
            background-color: rgba(130, 187, 255, 0.06);
            border-color: rgba(130, 187, 255, 0.20);
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
        QLineEdit[saveLocationPath="true"] {{
            background-color: #E3F2FD;
            color: #0B2A45;
            selection-background-color: {self.config.accent_color};
            selection-color: #001B24;
            border: 1px solid rgba(29,233,182,0.45);
            border-radius: 10px;
            padding: 10px 12px;
            font-weight: 600;
        }}
        QLineEdit[saveLocationPath="true"]:focus {{
            border: 1px solid {self.config.accent_color};
            background-color: #F1F8FE;
        }}
        QLabel#saveLocationLabel {{
            font-weight: 700;
            color: {self.config.accent_color};
        }}
        QScrollArea#gestureGuideScroll QScrollBar:vertical {{
            background: rgba(255,255,255,0.06);
            width: 14px;
            border-radius: 7px;
            margin: 2px 0;
        }}
        QScrollArea#gestureGuideScroll QScrollBar::handle:vertical {{
            background: {self.config.accent_color};
            min-height: 40px;
            border-radius: 7px;
        }}
        QScrollArea#gestureGuideScroll QScrollBar::handle:vertical:hover {{
            background: {self.config.accent_color};
        }}
        QScrollArea#gestureGuideScroll QScrollBar::add-line:vertical,
        QScrollArea#gestureGuideScroll QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollArea#gestureGuideScroll QScrollBar::add-page:vertical,
        QScrollArea#gestureGuideScroll QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollArea#saveLocationsScroll, QScrollArea#saveLocationsScroll > QWidget,
        QScrollArea#saveLocationsScroll QWidget#qt_scrollarea_viewport,
        QWidget#saveLocationsScrollContent {{
            background: transparent;
            border: none;
        }}
        QScrollArea#saveLocationsScroll QScrollBar:vertical {{
            background: rgba(255,255,255,0.06);
            width: 14px;
            border-radius: 7px;
            margin: 2px 0;
        }}
        QScrollArea#saveLocationsScroll QScrollBar::handle:vertical {{
            background: {self.config.accent_color};
            min-height: 40px;
            border-radius: 7px;
        }}
        QScrollArea#saveLocationsScroll QScrollBar::add-line:vertical,
        QScrollArea#saveLocationsScroll QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QScrollArea#saveLocationsScroll QScrollBar::add-page:vertical,
        QScrollArea#saveLocationsScroll QScrollBar::sub-page:vertical {{
            background: transparent;
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
        /* Native QMessageBox has a light-gray background and relies on a
           dark label color for its message text. Our window-wide
           QLabel rule sets color to our light theme color, which makes
           the message invisible on the light popup. Scope back to sane
           colors inside QMessageBox so its text actually shows up. */
        QMessageBox {{
            background-color: {self.config.surface_color};
        }}
        QMessageBox QLabel {{
            color: {self.config.text_color};
            background: transparent;
            min-width: 240px;
        }}
        QMessageBox QPushButton {{
            min-width: 80px;
            padding: 6px 14px;
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
        # Just populate the inventory silently on startup. Previously we
        # popped a modal chooser when 2+ cameras were detected and no
        # preference was saved, but that interrupts the launch any time the
        # user has a virtual webcam installed (Iriun, OBS Virtual Camera,
        # DroidCam, etc.). Users can pick a specific camera anytime from
        # Settings -> Camera; the engine falls back to the first available
        # camera when no preference is saved, which is the expected default.
        self.refresh_camera_inventory(update_status=True, notify=False)

    def refresh_camera_inventory(self, update_status: bool = True, notify: bool = False) -> list[CameraInfo]:
        access_ok, access_message = request_camera_access_main_thread(self.config.camera_scan_limit)
        if not access_ok:
            self._discovered_cameras = []
            self._rebuild_camera_combo()
            if update_status:
                self.camera_label.setText("Camera: permission required")
                self.camera_page_status.setText(access_message)
            if notify:
                QMessageBox.warning(self, "Touchless", access_message)
            return []

        self._discovered_cameras = list_available_cameras(self.config.camera_scan_limit)
        self._rebuild_camera_combo()
        self._refresh_camera_labels()

        if notify:
            if self._discovered_cameras:
                QMessageBox.information(self, "Touchless", f"Found {len(self._discovered_cameras)} available camera(s).")
            else:
                QMessageBox.warning(self, "Touchless", "No available cameras were found.")
        return self._discovered_cameras

    def _kick_off_async_camera_refresh(self) -> None:
        """Start a background camera enumeration. Called when the user
        opens the camera dropdown — keeps the UI responsive while
        OpenCV probes each device slot (which can take 1-3 seconds in
        total when virtual webcam drivers are installed).

        If a scan is already running, this is a no-op so a fast user
        opening the dropdown twice doesn't queue duplicate threads."""
        if self._camera_inventory_thread is not None and self._camera_inventory_thread.isRunning():
            return
        scan_limit = int(getattr(self.config, "camera_scan_limit", 8))
        thread = _CameraInventoryThread(scan_limit, parent=self)
        thread.finished_with_inventory.connect(self._on_async_camera_refresh_done)
        thread.finished.connect(thread.deleteLater)
        self._camera_inventory_thread = thread
        thread.start()

    def _on_async_camera_refresh_done(self, cameras_obj: object) -> None:
        try:
            cameras = list(cameras_obj or [])
        except TypeError:
            cameras = []
        self._discovered_cameras = cameras
        self._rebuild_camera_combo()
        self._refresh_camera_labels()
        self._camera_inventory_thread = None

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
        # Capture the friendly name from the combo BEFORE we lose
        # easy access to it, so the confirmation popup tells the user
        # exactly which camera is now their saved choice.
        selected_name = ""
        try:
            selected_name = str(self.camera_combo.currentText() or "").strip()
        except Exception:
            selected_name = ""
        # Phone-camera state — overrides the local dropdown when on.
        phone_qr_active = bool(getattr(self.config, "phone_camera_qr_active", False)) and self._current_phone_camera_qr_server() is not None
        phone_url_active = bool(getattr(self.config, "phone_camera_enabled", False)) and bool(str(getattr(self.config, "phone_camera_url", "") or "").strip())

        self.config.preferred_camera_index = selected_index
        save_config(self.config)
        self._refresh_camera_labels()

        # Hot-swap: if the engine is currently running, restart the
        # worker against the new camera so the user doesn't have to
        # End then Start manually. The 1-3 second blip during the
        # restart is acceptable per the requested behavior.
        engine_was_running = self._worker is not None
        if engine_was_running:
            try:
                self.start_engine(skip_tutorial_prompt=True)
            except Exception:
                # If restart fails for any reason, fall back to the
                # old behavior (user can manually restart).
                pass

        if phone_qr_active:
            self.last_action_label.setText("Last action: saved phone camera (QR) as source")
            confirmation = (
                "Camera preference saved.\n\nTouchless is currently using your phone's camera (QR) "
                "as the source. The local device above is saved as a fallback for when the phone "
                "camera is turned off."
            )
        elif phone_url_active:
            url = str(getattr(self.config, "phone_camera_url", "") or "").strip()
            self.last_action_label.setText("Last action: saved phone camera (URL) as source")
            confirmation = (
                f"Camera preference saved.\n\nTouchless is currently using your phone's camera over "
                f"the URL stream ({url}). The local device above is saved as a fallback."
            )
        elif selected_index is None:
            self.last_action_label.setText("Last action: camera set to auto-select")
            confirmation = (
                "Camera preference saved. Touchless will pick the best available camera at startup."
            )
        else:
            label = selected_name if selected_name else f"index {selected_index}"
            self.last_action_label.setText(f"Last action: saved camera {label}")
            confirmation = (
                f"Camera preference saved. Touchless will now use:\n\n{label}"
            )
        if engine_was_running:
            confirmation += "\n\nThe camera is being switched live — gestures may pause for 1-3 seconds while the new camera initializes."
        QMessageBox.information(self, "Camera Saved", confirmation)

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
                QMessageBox.information(self, "Touchless", f"Found {len(self._discovered_microphones)} available microphone(s).")
            else:
                QMessageBox.warning(self, "Touchless", "No available microphones were found.")
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
        return

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
        # If phone mic is the active source, the local dropdown choice
        # is effectively bypassed — surface that in the confirmation
        # text so the user doesn't think we ignored the phone setting.
        using_phone_mic = bool(getattr(self.config, "phone_camera_qr_use_mic", False))
        if using_phone_mic:
            self.last_action_label.setText("Last action: saved phone microphone as source")
            confirmation = (
                "Microphone preference saved.\n\nTouchless is currently using your phone's microphone (QR) "
                "as the source. Any local device selected above is saved as a fallback for when the phone "
                "mic is turned off."
            )
        elif selected_name is None:
            self.last_action_label.setText("Last action: microphone set to auto-select")
            confirmation = "Microphone preference saved. Voice commands will now use the default Windows microphone."
        else:
            self.last_action_label.setText(f"Last action: saved microphone {selected_name}")
            confirmation = (
                f"Microphone preference saved. Voice commands and dictation will now use:\n\n{selected_name}"
            )
        QMessageBox.information(self, "Microphone Saved", confirmation)

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
                QMessageBox.warning(self, "Touchless", "No available camera was found.")
                self.status_label.setText("Status: no camera found")
                return
    
            selected_camera_index = self._resolve_camera_for_start(cameras)
            if selected_camera_index is None:
                self.status_label.setText("Status: start cancelled")
                self.last_action_label.setText("Last action: camera selection cancelled")
                return

            if self._worker is not None:
                # Disconnect every signal from the old worker BEFORE
                # stopping it. Without this, when the old worker's
                # thread finally exits and emits `running_state_changed
                # (False)`, our `_cleanup_thread_if_stopped` handler
                # fires — but by then we've already swapped in a new
                # worker, so the cleanup detaches the mini viewer from
                # the NEW (running) worker. User-visible bug: after a
                # camera hot-swap, the mini viewer keeps showing the
                # last frame from the old camera until they enlarge
                # to the debugger and back. Disconnecting first avoids
                # the lingering-emit race entirely.
                old_worker = self._worker
                try:
                    old_worker.status_changed.disconnect()
                except Exception:
                    pass
                try:
                    old_worker.command_detected.disconnect()
                except Exception:
                    pass
                try:
                    old_worker.camera_selected.disconnect()
                except Exception:
                    pass
                try:
                    old_worker.error_occurred.disconnect()
                except Exception:
                    pass
                try:
                    old_worker.running_state_changed.disconnect()
                except Exception:
                    pass
                try:
                    old_worker.debug_frame_ready.disconnect()
                except Exception:
                    pass
                try:
                    old_worker.save_prompt_completed.disconnect()
                except Exception:
                    pass
                try:
                    old_worker.action_history_changed.disconnect()
                except Exception:
                    pass
                if self.mini_live_viewer is not None:
                    self.mini_live_viewer.detach_from_worker()
                if self.live_view_window is not None:
                    self.live_view_window.detach_from_worker()
                old_worker.stop()

            # A phone camera source must override the dropdown-selected
            # local device. GestureWorker treats a non-None
            # camera_index_override as an unconditional choice, so we
            # must pass None when a phone source is active to let
            # _open_camera() see and select the phone path.
            phone_qr_active = bool(getattr(self.config, "phone_camera_qr_active", False)) and self._current_phone_camera_qr_server() is not None
            phone_url_active = bool(getattr(self.config, "phone_camera_enabled", False)) and bool(str(getattr(self.config, "phone_camera_url", "") or "").strip())
            worker_override = None if (phone_qr_active or phone_url_active) else selected_camera_index
            self._worker = GestureWorker(self.config, camera_index_override=worker_override)
            self._worker.status_changed.connect(self._on_status_changed)
            self._worker.command_detected.connect(self._on_command_detected)
            self._worker.camera_selected.connect(self._on_camera_selected)
            self._worker.error_occurred.connect(self._on_error)
            self._worker.running_state_changed.connect(self._on_running_state_changed)
            self._worker.running_state_changed.connect(self._cleanup_thread_if_stopped)
            self._worker.debug_frame_ready.connect(self._on_worker_debug_frame)
            self._worker.save_prompt_completed.connect(self._on_save_prompt_completed)
            self._worker.action_history_changed.connect(self._on_action_history_changed)
            if self.live_view_window is not None:
                self.live_view_window.attach_to_worker(self._worker)
            if self.mini_live_viewer is not None:
                self.mini_live_viewer.attach_to_worker(self._worker)

            # Attach any currently-paired phone-camera-QR capture BEFORE
            # the worker starts so _open_camera() picks the phone source
            # on the first tick when phone_camera_qr_active is on.
            qr_server = self._current_phone_camera_qr_server()
            if qr_server is not None and bool(getattr(self.config, "phone_camera_qr_active", False)):
                try:
                    self._worker.set_phone_camera_capture(qr_server.capture)
                except Exception:
                    pass
            # Sync the phone-mic preference onto the freshly-constructed
            # voice listener — without this, a new engine start picks up
            # a new VoiceCommandListener that never had its external
            # audio source installed, so voice commands silently fall
            # back to the local sounddevice mic even while `/audio`
            # POSTs are flowing from the phone.
            self._apply_phone_mic_preference()

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
            # Prefer the LiveViewWindow whenever a worker exists at all.
            # Previously this required `worker.is_running` to be True,
            # but during a hot-swap there's a 1-3s window after start()
            # while the camera opens where the flag is still False —
            # clicking enlarge in that gap fell into the standalone-
            # debugger fallback (which has its own camera + Restart
            # Camera button), surprising users who'd just saved a
            # camera change and expected the normal live view.
            if self._worker is not None:
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
        # Push the same human-readable text to the phone via SSE so
        # users get a live toast confirming the PC saw their gesture
        # or voice command. Skipped silently if no phone is paired,
        # the QR server isn't running, or no SSE clients are
        # subscribed — publish_event is a no-op in any of those.
        self._publish_phone_event_for_action(action_text)

    def _publish_phone_event_for_action(self, action_text: str) -> None:
        if not action_text or action_text == "none":
            return
        # Suppress noise: drawing intermediate states fire command_detected
        # multiple times per stroke for UI feedback. The user already
        # sees the drawing on screen — toasting every state change just
        # spams the phone.
        suppressed_prefixes = (
            "drawing ",         # "drawing pen", "drawing eraser", etc. fire continuously
        )
        lower = action_text.lower()
        if any(lower.startswith(p) for p in suppressed_prefixes):
            return
        server = self._current_phone_camera_qr_server()
        if server is None:
            return
        try:
            # When voice listening is active and we just got a result,
            # tag as "voice" so the toast picks the warm-yellow border.
            # Otherwise tag as "gesture" with the green-accent border.
            voice_active = False
            if self._worker is not None:
                voice_active = bool(getattr(self._worker, "_voice_listening", False))
            if voice_active:
                server.publish_event("voice", text=action_text)
            else:
                server.publish_event("gesture", label=action_text, action_text=action_text)
        except Exception:
            pass


    def _on_action_history_changed(self, events: object) -> None:
        if not hasattr(self, "action_history_list"):
            return
        try:
            event_list = list(events or [])
        except TypeError:
            event_list = []
        self.action_history_list.clear()
        any_undoable = False
        for event in reversed(event_list[-10:]):
            display = getattr(event, "display_text", "") or getattr(event, "label", "") or "?"
            if getattr(event, "is_undo", False):
                prefix = "↶ "
            elif getattr(event, "undoable", False):
                prefix = "• "
                any_undoable = True
            else:
                prefix = "· "
            item = QListWidgetItem(f"{prefix}{display}")
            self.action_history_list.addItem(item)
        if hasattr(self, "undo_action_button"):
            self.undo_action_button.setEnabled(any_undoable)

    def _on_undo_last_action(self) -> None:
        worker = getattr(self, "_worker", None)
        if worker is None:
            return
        try:
            worker.undo_last_action()
        except Exception:
            pass

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
        path = None
        if self._drawing_render_target == "camera":
            worker = self._worker
            if worker is not None and hasattr(worker, "save_camera_draw_snapshot"):
                try:
                    saved = bool(worker.save_camera_draw_snapshot(target_path))
                except Exception:
                    saved = False
                path = target_path if saved else None
        else:
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
                elif request_action in {"shape_on", "shape_off"}:
                    self.draw_overlay.set_shape_mode(request_action == "shape_on")
                    self.last_action_label.setText(
                        "Last action: shape mode on" if request_action == "shape_on" else "Last action: shape mode off"
                    )
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
            QMessageBox.critical(self, "Touchless", message)

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
            elif request_action in {"shape_on", "shape_off"}:
                self.draw_overlay.set_shape_mode(request_action == "shape_on")
                self.last_action_label.setText(
                    "Last action: shape mode on" if request_action == "shape_on" else "Last action: shape mode off"
                )
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
        QMessageBox.critical(self, "Touchless", message)

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
        try:
            self._stop_mic_test()
        except Exception:
            pass
        self.stop_engine()
        # Tear down the phone-camera server so its daemon thread doesn't
        # keep a port bound while the app window is closing.
        qr_server = getattr(self, "_phone_camera_qr_server", None)
        if qr_server is not None:
            try:
                qr_server.stop()
            except Exception:
                pass
            self._phone_camera_qr_server = None
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
            elif request_action in {"shape_on", "shape_off"}:
                self.draw_overlay.set_shape_mode(request_action == "shape_on")
                self.last_action_label.setText(
                    "Last action: shape mode on" if request_action == "shape_on" else "Last action: shape mode off"
                )
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
        QMessageBox.critical(self, "Touchless", message)

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
