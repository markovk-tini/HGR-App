from __future__ import annotations

import ctypes
import csv
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, Qt, QThread, QTimer, QEvent, QUrl, Signal
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
from ...utils.runtime_paths import app_base_path
from ...voice.save_prompt import SavePromptProcessor
from ..integration.noop_engine import GestureWorker
from ..overlays.overlay import HelloOverlay, ScreenDrawOverlay, DrawingSettingsDialog, CountdownOverlay, CaptureRegionOverlay, ProcessingOverlay, RecordingIndicatorOverlay, SavedLocationOverlay
from .mini_live_viewer import MiniLiveViewer
from .live_view_window import LiveViewWindow
from .tutorial_window import TutorialWindow


SECTION_INSTRUCTIONS = 0
SECTION_GESTURES = 1
SECTION_CUSTOM_GESTURE = 2
SECTION_GESTURE_BINDS = 3
SECTION_CAMERA = 4
SECTION_MICROPHONE = 5
SECTION_SAVE_LOCATIONS = 6
SECTION_COLORS = 7
SECTION_TUTORIAL = 8
SECTION_UPDATES = 9



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
        # Show the Touchless logo next to the title in the OS title
        # bar / Alt-Tab list. Qt would normally inherit the
        # QApplication-level icon, but a top-level QDialog created
        # without a parent in some launch paths (e.g., the splash
        # transition before MainWindow is shown) doesn't pick it up
        # — set it explicitly here.
        from PySide6.QtWidgets import QApplication
        app_icon = QApplication.windowIcon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.setMinimumWidth(380)
        self.setObjectName("startTutorialDialog")
        # Hug the content vertically — no extra slack below the buttons.
        self.setSizeGripEnabled(False)
        self._build_ui()
        self._apply_theme()

    @property
    def do_not_show_again(self) -> bool:
        return self.do_not_show_checkbox.isChecked()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(8)

        title = QLabel("Quick 2-min gesture tutorial?")
        title.setObjectName("startDialogTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        subtitle = QLabel(
            "Yes — walk through the basics. No — start the app now."
        )
        subtitle.setObjectName("startDialogSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self.do_not_show_checkbox = QCheckBox("Please don't show this message again")
        self.do_not_show_checkbox.setObjectName("startDialogCheckbox")
        root.addWidget(self.do_not_show_checkbox)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 4, 0, 0)
        button_row.addStretch(1)

        # Yes is the primary action: positioned first (right side
        # of the row, since the leading addStretch pushes everything
        # right) and styled with the accent palette so it's
        # visibly the emphasized choice. No keeps the neutral
        # secondary styling.
        self.yes_button = QPushButton("Yes")
        self.yes_button.setObjectName("startDialogPrimaryButton")
        self.yes_button.clicked.connect(self._choose_tutorial)
        self.yes_button.setDefault(True)

        self.no_button = QPushButton("No")
        self.no_button.setObjectName("startDialogButton")
        self.no_button.clicked.connect(self._choose_start)

        button_row.addWidget(self.yes_button)
        button_row.addWidget(self.no_button)
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
            QPushButton#startDialogPrimaryButton {{
                background-color: {self.config.accent_color};
                color: #001B24;
                border: 1px solid {self.config.accent_color};
                border-radius: 12px;
                padding: 10px 18px;
                min-width: 86px;
                font-weight: 900;
            }}
            QPushButton#startDialogPrimaryButton:hover {{
                background-color: rgba(29,233,182,0.92);
                border: 1px solid #6BFFE0;
            }}
            QPushButton#startDialogPrimaryButton:pressed {{
                background-color: rgba(29,233,182,0.78);
                color: #001B24;
            }}
            """
        )

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API name)
        super().showEvent(event)
        # Color the OS title bar to match the Touchless app theme so
        # the popup looks like a native Touchless window. Same DWM
        # path TouchlessNotice uses — silently no-ops on Windows 10
        # (the API only exists on Windows 11 build 22000+) which is
        # acceptable since the dialog body is themed regardless.
        try:
            self._apply_dwm_caption_color()
        except Exception:
            pass

    def _apply_dwm_caption_color(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return
        hwnd = int(self.winId())
        if not hwnd:
            return
        # DWMWA_CAPTION_COLOR = 35, DWMWA_TEXT_COLOR = 36
        # COLORREF is 0x00BBGGRR. Touchless primary blue is #0B3D91
        # → R=0x0B, G=0x3D, B=0x91 → COLORREF=0x00913D0B.
        # Caption text #E5F6FF → 0x00FFF6E5.
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36
        caption = ctypes.c_uint32(0x00913D0B)
        text = ctypes.c_uint32(0x00FFF6E5)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint32(DWMWA_CAPTION_COLOR),
                ctypes.byref(caption),
                ctypes.sizeof(caption),
            )
        except Exception:
            pass
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint32(DWMWA_TEXT_COLOR),
                ctypes.byref(text),
                ctypes.sizeof(text),
            )
        except Exception:
            pass

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
        candidate_name = self._image_name or self._video_name
        if not candidate_name:
            return None
        # In source mode (python run_app.py) the GestureGuide folder
        # sits at the project root, four parents above this file.
        # In a PyInstaller --onedir bundle the spec copies it to
        # `<bundle>/_internal/GestureGuide/`, which is what
        # app_base_path() points at when sys.frozen is True. Try both
        # so the gesture cards show real images / videos in either
        # mode — previously the bundled app fell through to the
        # auto-generated GestureSketchWidget because parents[4] was
        # nowhere near the bundled GestureGuide directory.
        candidate_roots: list[Path] = []
        try:
            candidate_roots.append(app_base_path() / "GestureGuide")
        except Exception:
            pass
        try:
            candidate_roots.append(Path(__file__).resolve().parents[4] / "GestureGuide")
        except Exception:
            pass
        for root in candidate_roots:
            candidate = root / candidate_name
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                continue
        return None

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


class VoiceCommandCard(QFrame):
    """Text-only card describing a voice command pattern. Sits in the
    Control Guide alongside GestureGuideCard but renders without
    media — voice commands don't have a visual demo, so we show the
    phrase pattern, what it does, and a list of recognized
    variations the user can say."""

    def __init__(
        self,
        *,
        title: str,
        action: str,
        examples: list[str],
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("innerCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("gestureCardTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        action_label = QLabel(f"Action: {action}")
        action_label.setObjectName("gestureCardSubtitle")
        action_label.setWordWrap(True)
        layout.addWidget(action_label)

        examples_header = QLabel("Examples (any of these phrases work)")
        examples_header.setObjectName("gestureCardSubtitle")
        layout.addWidget(examples_header)

        for ex in examples:
            bullet = QLabel(f"• “{ex}”")
            bullet.setObjectName("gestureCardBody")
            bullet.setWordWrap(True)
            layout.addWidget(bullet)


def _build_voice_command_cards() -> list[VoiceCommandCard]:
    """Catalog of recognized voice-command patterns shown in the
    Control Guide. Each card lists alternative phrasings the
    voice processor accepts so users discover variations they can
    say without trial-and-error.

    Keep this list aligned with the actual recognition rules in
    `voice/command_processor.py` — if we add a new verb or app
    alias there, also update the example list here so the guide
    stays accurate."""
    return [
        VoiceCommandCard(
            title="Open / focus an app",
            action="Launches the named app, or brings it to focus if already running.",
            examples=[
                "open spotify",
                "launch chrome",
                "fire up discord",
                "boot up steam",
                "show me settings",
                "bring up file explorer",
                "switch to outlook",
                "go to chatgpt",
            ],
        ),
        VoiceCommandCard(
            title="Play music on Spotify",
            action="Plays a song / artist / playlist on Spotify (opens it first if needed).",
            examples=[
                "play master of puppets",
                "play master of puppets by metallica",
                "play feel-good playlist on spotify",
                "put on some lo-fi",
                "queue up daft punk",
                "play random",
            ],
        ),
        VoiceCommandCard(
            title="Add / remove from a named Spotify playlist",
            action=(
                "Manage the currently-playing track against a playlist by NAME — "
                "something gestures can't do because they can't pick a specific "
                "playlist. Skip / previous / shuffle / pause are all available "
                "as right-hand gestures and aren't included here on purpose."
            ),
            examples=[
                "add this to my workout playlist",
                "remove this song from my chill mix",
                "save this track to liked songs",
            ],
        ),
        VoiceCommandCard(
            title="Search / open content",
            action="Searches the web or opens the named site.",
            examples=[
                "search for python tutorials",
                "look up best pizza nearby",
                "go to github",
                "navigate to gmail",
            ],
        ),
        VoiceCommandCard(
            title="Dictation mode",
            action="Triggered by holding LEFT-hand two; not a voice command per se. Speak naturally; pauses become spaces. Spoken punctuation: 'comma', 'period', 'question mark', 'new line', 'new paragraph'.",
            examples=[
                "Hey there comma how's it going question mark",
                "Final report period new paragraph First section colon",
                "Stopped by left-hand fist or another left-hand two.",
            ],
        ),
        VoiceCommandCard(
            title="Triggering a voice command",
            action="Hold LEFT-hand one for ~0.5s. Touchless will say 'Listening', record up to 12 seconds, transcribe, and execute. Cancel any time with LEFT-hand fist.",
            examples=[
                "(no phrase — this card describes how to start the listener)",
            ],
        ),
    ]


class GestureGuideSection(QFrame):
    """Collapsible section in the Control Guide. Originally just for
    GestureGuideCard rows, now also accepts VoiceCommandCard rows
    via the same `cards` list (any QWidget works since we just
    stack them in a QVBoxLayout)."""

    def __init__(self, title: str, cards: list, parent=None):
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
    sections_layout.addWidget(GestureGuideSection("Voice Commands", _build_voice_command_cards()))
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


class CameraPreviewDialog(QDialog):
    """Touchless-themed live preview of a single camera.

    Built so users can sanity-check a camera selection before saving
    it. Opens the requested device via cv2.VideoCapture on a poll
    timer (no engine, no inference — just frames), shows them in a
    rounded card matching the rest of the app's chrome, and exits
    cleanly on the Exit button or window close.

    Threading note: cv2.VideoCapture.read() runs on the GUI thread
    here. That's intentional — preview frames are pulled at ~30 FPS
    on a 30 ms timer and a webcam read is normally <5 ms, so the
    main loop never noticeably stalls. If we ever want a slow remote
    source previewable here, move the read off-thread."""

    def __init__(self, config: AppConfig, camera_index: int, camera_label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self._camera_index = int(camera_index)
        self._camera_label = str(camera_label or f"Camera {camera_index}")
        self._cap = None
        self.setWindowTitle("Camera Preview")
        from PySide6.QtWidgets import QApplication
        app_icon = QApplication.windowIcon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.setObjectName("cameraPreviewDialog")
        self.setModal(False)
        self.resize(720, 560)
        self._build_ui()
        self._apply_theme()
        # Poll timer driving the read+render loop. 33 ms ≈ 30 FPS — fast
        # enough to look smooth without hammering the device.
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        QTimer.singleShot(0, self._open_camera)

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QFrame
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel(self._camera_label)
        title.setObjectName("cameraPreviewTitle")
        title.setWordWrap(True)
        root.addWidget(title)

        self.video_label = QLabel("Opening camera…")
        self.video_label.setObjectName("cameraPreviewVideo")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(640, 360)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.video_label, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("cameraPreviewStatus")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.exit_button = QPushButton("Exit Preview")
        self.exit_button.setObjectName("cameraPreviewButton")
        self.exit_button.clicked.connect(self.accept)
        button_row.addWidget(self.exit_button)
        root.addLayout(button_row)

    def _apply_theme(self) -> None:
        accent = self.config.accent_color or "#1DE9B6"
        text = self.config.text_color or "#E5F6FF"
        surface = self.config.surface_color or "#0F172A"
        self.setStyleSheet(
            f"""
            QDialog#cameraPreviewDialog {{
                background-color: {surface};
                color: {text};
                border: 1px solid rgba(29, 233, 182, 0.30);
            }}
            QLabel#cameraPreviewTitle {{
                color: {accent};
                font-size: 18px;
                font-weight: 800;
                background: transparent;
            }}
            QLabel#cameraPreviewVideo {{
                background-color: rgba(0, 0, 0, 0.35);
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.08);
                color: {text};
                padding: 6px;
            }}
            QLabel#cameraPreviewStatus {{
                color: {text};
                font-size: 12px;
                opacity: 0.8;
                background: transparent;
            }}
            QPushButton#cameraPreviewButton {{
                background-color: rgba(255, 255, 255, 0.08);
                color: {text};
                border: 1px solid rgba(29, 233, 182, 0.45);
                border-radius: 10px;
                padding: 9px 20px;
                font-weight: 700;
                min-width: 120px;
            }}
            QPushButton#cameraPreviewButton:hover {{
                background-color: rgba(29, 233, 182, 0.18);
                border: 1px solid {accent};
            }}
            """
        )

    def _open_camera(self) -> None:
        # camera_utils.open_camera_by_index returns (CameraInfo, cap)
        # — earlier code stashed the whole tuple on self._cap, which
        # made every read() raise and the preview never advanced past
        # "Opening camera…". Unpack cleanly here.
        try:
            from ..camera.camera_utils import open_camera_by_index
            info, cap = open_camera_by_index(self._camera_index)
        except Exception as exc:
            self._cap = None
            self.status_label.setText(f"Couldn't open camera: {type(exc).__name__}: {exc}")
            return
        if cap is None:
            self._cap = None
            self.status_label.setText(
                "Couldn't open this camera. It may be in use by another app, or the index changed."
            )
            return
        self._cap = cap
        if info is not None and getattr(info, "display_name", ""):
            # Refresh the dialog header with the resolved display name
            # when Preview was launched from Auto-Select.
            try:
                self.findChild(QLabel, "cameraPreviewTitle").setText(info.display_name)
            except Exception:
                pass
        self.status_label.setText("Live preview")
        self._timer.start()

    def _tick(self) -> None:
        if self._cap is None:
            return
        try:
            ok, frame = self._cap.read()
        except Exception:
            ok, frame = False, None
        if not ok or frame is None:
            return
        # Always mirror — matches the unified selfie convention used
        # everywhere else in the app (engine, tutorial, recorder).
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        bytes_per_line = w * 3
        from PySide6.QtGui import QImage as _QImage
        image = _QImage(rgb.data, w, h, bytes_per_line, _QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(image).scaled(
            self.video_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.video_label.setPixmap(pix)

    def closeEvent(self, event):  # noqa: N802
        self._teardown()
        super().closeEvent(event)

    def reject(self):
        self._teardown()
        super().reject()

    def accept(self):
        self._teardown()
        super().accept()

    def _teardown(self) -> None:
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None


class _ScrollWheelForwarder(QObject):
    """Event filter that forwards wheel events from focus-trapping
    child widgets (QComboBox, QSpinBox, QSlider, ...) to a parent
    QScrollArea when the child isn't focused. Lets the user scroll
    a settings panel by spinning the wheel anywhere in it without
    accidentally changing values in a combobox / spinbox under the
    cursor.

    A single instance can be shared across multiple scroll areas;
    .attach(scroll) registers a target, and on each wheel event the
    forwarder picks the closest ancestor scroll area for the
    widget that received the event."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scroll_areas: list[QScrollArea] = []

    def attach(self, scroll_area: QScrollArea) -> None:
        if scroll_area is not None and scroll_area not in self._scroll_areas:
            self._scroll_areas.append(scroll_area)

    def _find_target_scroll(self, widget) -> Optional[QScrollArea]:
        # Walk up the parent chain from the widget that received the
        # wheel event and return the first attached scroll area that
        # contains it. Multiple panels can share one forwarder; we
        # only want to scroll the panel the user is actually in.
        try:
            current = widget
            while current is not None:
                for scroll in self._scroll_areas:
                    if scroll is current:
                        return scroll
                current = current.parent()
        except Exception:
            return None
        return None

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() != QEvent.Wheel:
            return False
        try:
            if obj.hasFocus():
                # User has clicked into the widget — let it consume
                # the wheel as normal (e.g. scrolling through combo
                # box options).
                return False
        except Exception:
            return False
        target = self._find_target_scroll(obj)
        if target is None:
            return False
        try:
            bar = target.verticalScrollBar()
            if bar is None:
                return False
            delta_y = event.angleDelta().y() if hasattr(event, "angleDelta") else 0
            if delta_y == 0:
                return False
            # 120 wheel-units == one notch. Translate to pixel scroll
            # roughly matching the platform default (~3 lines per
            # notch, ~16 px per line).
            bar.setValue(bar.value() - int(delta_y * 0.40))
            return True
        except Exception:
            return False


class TouchlessNotice(QDialog):
    """Touchless-themed information popup. Replacement for
    QMessageBox.information / .warning that uses the OS native
    look — that pulled focus away with a separate taskbar window
    and clashed with the rest of the dark-blue Touchless theme.

    Looks like a small modal Touchless panel: dark blue surface,
    light text, single primary OK button. Frameless + no taskbar
    entry (Qt.Tool flag) so it stays attached to its parent
    window in the taskbar. Word-wraps long messages.
    """

    def __init__(self, parent, title: str, message: str, *, kind: str = "info") -> None:
        super().__init__(parent)
        self._kind = kind
        self.setWindowTitle(title)
        # Tool window: still has a close button, won't show its own
        # entry in the taskbar, stays on top of the parent.
        self.setWindowFlag(Qt.Tool, True)
        self.setMinimumWidth(360)
        self.setSizeGripEnabled(False)
        # Match the app's body surface (#0F172A — the dark navy
        # behind the START/END/SETTINGS row), not the brighter
        # primary blue (#0B3D91) which is reserved for the title
        # bar accent. The OS caption is colored to #0B3D91 in
        # showEvent below so the popup looks like a small
        # detached Touchless window: blue title bar + navy body.
        self.setStyleSheet(
            "QDialog {"
            "  background: #0F172A;"
            "  color: #E5F6FF;"
            "}"
            "QLabel {"
            "  color: #E5F6FF;"
            "}"
            "QPushButton#touchlessNoticeOk {"
            "  background: #1DE9B6;"
            "  color: #003d2a;"
            "  border: none;"
            "  border-radius: 8px;"
            "  padding: 8px 22px;"
            "  font-weight: 600;"
            "  min-width: 90px;"
            "}"
            "QPushButton#touchlessNoticeOk:hover {"
            "  background: #29f0c1;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title_label)

        body_label = QLabel(message)
        body_label.setWordWrap(True)
        body_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        body_label.setStyleSheet("font-size: 13px; line-height: 1.4;")
        layout.addWidget(body_label, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        ok_button = QPushButton("OK")
        ok_button.setObjectName("touchlessNoticeOk")
        ok_button.setDefault(True)
        ok_button.clicked.connect(self.accept)
        button_row.addWidget(ok_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API name)
        super().showEvent(event)
        # Color the OS title bar to match the Touchless app theme so
        # the popup looks like a native Touchless window. Uses
        # DwmSetWindowAttribute with DWMWA_CAPTION_COLOR (added in
        # Windows 11 build 22000) — silently no-ops on Windows 10
        # which is acceptable since the dialog body is themed
        # regardless and the system title bar is just a small accent.
        try:
            self._apply_dwm_caption_color()
        except Exception:
            pass

    def _apply_dwm_caption_color(self) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
        except Exception:
            return
        hwnd = int(self.winId())
        if not hwnd:
            return
        # DWMWA_CAPTION_COLOR = 35, DWMWA_TEXT_COLOR = 36
        # Color is COLORREF (0x00BBGGRR). Touchless primary blue is
        # #0B3D91 → R=0x0B, G=0x3D, B=0x91 → COLORREF=0x00913D0B.
        # Text color = #E5F6FF → 0x00FFF6E5.
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36
        caption = ctypes.c_uint32(0x00913D0B)
        text = ctypes.c_uint32(0x00FFF6E5)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint32(DWMWA_CAPTION_COLOR),
                ctypes.byref(caption),
                ctypes.sizeof(caption),
            )
        except Exception:
            pass
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint32(DWMWA_TEXT_COLOR),
                ctypes.byref(text),
                ctypes.sizeof(text),
            )
        except Exception:
            pass

    @staticmethod
    def show_info(parent, title: str, message: str) -> None:
        dlg = TouchlessNotice(parent, title, message, kind="info")
        dlg.exec()

    @staticmethod
    def show_warn(parent, title: str, message: str) -> None:
        dlg = TouchlessNotice(parent, title, message, kind="warn")
        dlg.exec()


# ---------------------------------------------------------------------------
# Gesture Binds tab — registries of (1) bindable actions and (2) gesture poses
# the user can pick from. Source of truth lives in
# `hgr.config.gesture_bindings` so the live engine can read the same data
# without dragging in PySide6/UI deps via this module.
# ---------------------------------------------------------------------------
from ...config.gesture_bindings import (
    gesture_bind_actions as _gesture_bind_actions,
    gesture_bind_poses as _gesture_bind_poses,
    resolve_gesture_binding,
)

_GESTURE_BIND_ACTIONS = _gesture_bind_actions()
_GESTURE_BIND_POSES = _gesture_bind_poses()


def _gesture_bind_pose_lookup() -> dict[str, tuple[str, str, str, str]]:
    """Helper: pose_id -> (pose_id, label, image, description)."""
    return {p[0]: p for p in _GESTURE_BIND_POSES}


class MainWindow(QMainWindow):
    # Cross-thread bridge for the off-thread clip export. The
    # worker thread emits this signal after stashing its result on
    # self._clip_export_result; Qt's auto-connection delivers the
    # slot call onto the main thread (where MainWindow lives), so
    # the GUI-side completion handler runs safely.
    #
    # We can't use QTimer.singleShot from the worker thread because
    # singleShot(0, callable) schedules the timer on the CALLING
    # thread, and our worker thread has no Qt event loop, so the
    # callback would never fire — leaving the processing overlay
    # stuck on screen forever.
    _clip_export_finished_signal = Signal()
    # Cross-thread bridge for the phone-camera server's status callback.
    # The server runs on its own daemon thread; emitting this signal
    # marshals the (event, data) payload onto the GUI thread before we
    # touch any widgets (specifically: phone_camera_qr_status_label).
    _phone_server_status_signal = Signal(str, dict)

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
        # Latest connected-phone label (e.g., "iPhone — Safari (iOS 17.5)").
        # Stays empty until the phone actually loads the page or sends
        # a frame; updated via the phone-server status callback.
        self._phone_connected_label: str = ""
        # Connect cross-thread signal up front so it's wired before the
        # server fires its first callback — prevents a race where the
        # phone announces itself between server.start() and the connect
        # call below.
        self._phone_server_status_signal.connect(self._on_phone_server_status_event)
        if bool(getattr(self.config, "phone_camera_qr_paired", False)):
            try:
                from ..debug.phone_camera import PhoneCameraServer
                server = PhoneCameraServer(
                    port=8765,
                    on_status=self._forward_phone_server_status,
                )
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
        # "Processing ..." indicator shown during heavier save
        # operations (clip export, screenshot save, screen
        # recording finalize, drawing save). One overlay instance
        # is reused across all of them since they're never
        # concurrent.
        self.processing_overlay = ProcessingOverlay()
        # Bottom-center pill with the full save path that fades
        # out after 3 s — fires for every successful save in
        # _on_save_prompt_completed.
        self.saved_location_overlay = SavedLocationOverlay()
        # Active clip-export worker thread, if any. Held so we can
        # query state and so Python doesn't garbage-collect it
        # while it's still running.
        self._clip_export_thread: threading.Thread | None = None
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
        # Skip-this-version: if the user clicked Later on this same
        # release (or one strictly newer that they later dismissed),
        # don't pester them on every launch. Re-prompt only when
        # GitHub ships a newer version than the dismissed one.
        try:
            from ..updater.release_checker import _parse_version_tuple
            dismissed = str(getattr(self.config, "last_dismissed_update_version", "") or "").strip()
            if dismissed:
                if _parse_version_tuple(info.version) <= _parse_version_tuple(dismissed):
                    return
        except Exception:
            pass

        from ..updater.update_dialog import UpdateDialog
        from ..updater import Updater
        self._update_dialog = UpdateDialog(info, parent=self)
        self._updater = Updater(parent=self)
        self._update_dialog.download_requested.connect(self._updater.start_download)
        self._update_dialog.dismissed.connect(
            lambda v=info.version: self._on_update_dismissed(v)
        )
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

    def _on_update_dismissed(self, version: str) -> None:
        """User clicked Later. Persist the dismissed version so the
        next launch doesn't re-prompt for the same release. A newer
        release will still trigger the dialog."""
        try:
            self.config.last_dismissed_update_version = str(version or "")
            save_config(self.config)
        except Exception:
            pass

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
        # Initial state: app launches with the engine stopped, so END
        # is the inactive button. start_engine / stop_engine swap the
        # pair below; the QPushButton:disabled rule in the global
        # stylesheet makes the inactive one visibly greyed out.
        self.end_button.setEnabled(False)
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
        self.microphone_label = QLabel("Microphone: scanning...")
        # status_label kept as a hidden compatibility shim — older code
        # paths still call setText on it; we route the meaningful bits
        # (errors, missing-camera notices) into camera_label instead.
        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        self.last_action_label = QLabel("Last action: none")
        self.last_action_label.setVisible(False)
        for label in (self.camera_label, self.microphone_label):
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
        # Expand toggle in the top-right of the Recent Actions box —
        # taps between the default ~140px height and a roomier ~300px
        # so users with many recent actions can scroll a longer log
        # without giving up the rest of the home screen.
        self.action_history_expand_button = QPushButton("⤢")
        self.action_history_expand_button.setObjectName("actionHistoryExpand")
        self.action_history_expand_button.setToolTip("Expand / collapse Recent Actions")
        self.action_history_expand_button.setFixedWidth(34)
        self.action_history_expand_button.setCheckable(True)
        self.action_history_expand_button.toggled.connect(self._on_action_history_expand_toggled)
        history_header_row.addWidget(self.action_history_expand_button)
        info_layout.addLayout(history_header_row)
        self.action_history_list = QListWidget()
        self.action_history_list.setObjectName("actionHistoryList")
        self.action_history_list.setMaximumHeight(140)
        self.action_history_list.setSelectionMode(QListWidget.NoSelection)
        self.action_history_list.setFocusPolicy(Qt.NoFocus)
        # Ensure the list actually scrolls when the content exceeds the
        # visible height (especially after expand). The default policy
        # leaves the scrollbar off until Qt re-evaluates layout, which
        # can leave entries hidden with no way to reach them.
        self.action_history_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.action_history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Grow vertically when given more room (used by the expand
        # toggle to occupy the whole Runtime Status box).
        self.action_history_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        info_layout.addWidget(self.action_history_list)

        # Stash references to widgets that hide when the user expands
        # Recent Actions to fill the box. The runtime title / camera /
        # microphone lines disappear so the action log gets the full
        # height; collapsing restores them.
        self._action_history_collapsible = [
            info_title,
            self.camera_label,
            self.microphone_label,
        ]

        body_layout.addWidget(info_card, 0, Qt.AlignHCenter)

        # Stash the body layout so the expand toggle can swap stretch
        # factors at runtime — that's what lets the info_card grow to
        # fill available height when expanded WITHOUT overflowing on
        # smaller windows. A static minimum-height on the list would
        # have pushed the legend off-screen at default window sizes.
        self._home_body_layout = body_layout
        self._home_body_bottom_stretch_index = None  # filled in below

        # Color legend for the category dots, shown beneath the
        # Runtime Status box. Tells users at a glance what each
        # colored dot in Recent Actions means without having to
        # hover or guess. Added AFTER stashing _home_body_layout so
        # we can address widgets by index in the expand toggle.
        self.action_history_legend = self._build_action_history_legend()
        body_layout.addWidget(self.action_history_legend, 0, Qt.AlignHCenter)

        QTimer.singleShot(0, self._update_home_status_card_width)

        self.debugger_button = QPushButton("LIVE VIEW")
        self.debugger_button.setObjectName("debuggerButton")
        self.debugger_button.clicked.connect(self.open_debugger)
        debug_row = QHBoxLayout()
        debug_row.addStretch(1)
        debug_row.addWidget(self.debugger_button)
        debug_row.addStretch(1)
        body_layout.addLayout(debug_row)

        # Local Agent UI removed (paused). The underlying live_api/
        # package code is intact — re-enable by restoring the home-page
        # card + handlers from git history when ready.

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
        self._settings_search_input.setPlaceholderText("Search settings, gestures, voice commands...")
        # Use a custom accent-green X clear action instead of the
        # built-in clearButton (whose pixmap can't be recolored
        # reliably across Qt versions). Action only shows when the
        # input has text — same UX as setClearButtonEnabled.
        self._settings_search_clear_action = self._settings_search_input.addAction(
            self._build_search_clear_icon(self.config.accent_color),
            QLineEdit.TrailingPosition,
        )
        self._settings_search_clear_action.setVisible(False)
        self._settings_search_clear_action.triggered.connect(self._settings_search_input.clear)
        self._settings_search_input.textChanged.connect(self._on_settings_search_changed)
        self._settings_search_input.returnPressed.connect(self._on_settings_search_activate_first)
        left_layout.addWidget(self._settings_search_input)

        # Dropdown of specific matching items below the search box.
        # When the user types, e.g., 'dictation', this lists every
        # gesture / voice command / panel subsection whose title or
        # keywords match. Clicking an entry navigates: switch to its
        # settings tab, expand its collapsible section if any, and
        # scroll the target widget into view.
        from PySide6.QtWidgets import QListWidget
        self._settings_search_results = QListWidget()
        self._settings_search_results.setObjectName("settingsSearchResults")
        self._settings_search_results.setVisible(False)
        self._settings_search_results.itemActivated.connect(self._on_settings_search_result_clicked)
        self._settings_search_results.itemClicked.connect(self._on_settings_search_result_clicked)
        self._settings_search_results.setStyleSheet(
            "QListWidget#settingsSearchResults {"
            "  background-color: rgba(15,23,42,0.96);"
            "  color: #E5F6FF;"
            "  border: 1px solid rgba(29,233,182,0.30);"
            "  border-radius: 8px;"
            "  padding: 4px;"
            "}"
            "QListWidget#settingsSearchResults::item { padding: 6px 8px; border-radius: 4px; }"
            "QListWidget#settingsSearchResults::item:hover { background-color: rgba(29,233,182,0.18); }"
            "QListWidget#settingsSearchResults::item:selected { background-color: rgba(29,233,182,0.28); }"
        )
        self._settings_search_results.setMaximumHeight(220)
        left_layout.addWidget(self._settings_search_results)
        # Populated lazily after settings_content_stack is built.
        self._settings_search_index: list = []

        instructions_button = SettingsNavButton("Instructions", SECTION_INSTRUCTIONS, self)
        gestures_button = SettingsNavButton("Control Guide", SECTION_GESTURES, self)
        custom_gesture_button = SettingsNavButton("Custom Gesture", SECTION_CUSTOM_GESTURE, self)
        gesture_binds_button = SettingsNavButton("Gesture Binds", SECTION_GESTURE_BINDS, self)
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
            gesture_binds_button,
            camera_button,
            microphone_button,
            save_locations_button,
            colors_button,
            tutorial_button,
            updates_button,
        ]
        self._settings_nav_search_keywords = {
            instructions_button: (
                "instructions quick start help guide overview getting started "
                "intro readme"
            ),
            gestures_button: (
                "control guide gesture voice command swipe wheel mouse volume "
                "drawing spotify chrome youtube dictation open launch play next "
                "previous static dynamic motion pose hand"
            ),
            custom_gesture_button: (
                "custom gesture create record new beta sandbox edit user "
                "personal recorded mine my own"
            ),
            gesture_binds_button: (
                "gesture binds bindings keybinds keybinding rebind reassign "
                "action assign remap mapping shortcut shortcuts hotkey trigger "
                "swap"
            ),
            camera_button: (
                "camera webcam device fps resolution auto-select low-fps "
                "low fps mode lite mode gpu mode cpu mode performance "
                "phone camera qr code pair pairing connect phone iphone "
                "android save camera selection mirror flip"
            ),
            microphone_button: (
                "microphone mic input gain audio voice whisper sapi save "
                "microphone choice phone microphone phone mic qr pair "
                "iphone dictation listening"
            ),
            save_locations_button: (
                "save locations folder path directory drawings screenshots "
                "screen recordings clips save name prefix file name save "
                "drawings save screenshots save recordings save clips"
            ),
            colors_button: (
                "colors color theme accent primary surface text overlay "
                "background revert customize palette"
            ),
            tutorial_button: (
                "tutorial walkthrough practice guided onboarding lesson "
                "demo learn how to part 1 part 2 part 3 part 4 part 5 part 6"
            ),
            updates_button: (
                "updates update version release changelog about check "
                "history changes news"
            ),
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
        self.settings_content_stack.addWidget(self._build_gesture_binds_panel())
        self.settings_content_stack.addWidget(self._build_camera_panel())
        self.settings_content_stack.addWidget(self._build_microphone_panel())
        self.settings_content_stack.addWidget(self._build_save_locations_panel())
        self.settings_content_stack.addWidget(self._build_colors_panel())
        self.settings_content_stack.addWidget(self._build_tutorial_panel())
        self.settings_content_stack.addWidget(self._build_updates_panel())

        layout.addWidget(left_panel)
        layout.addWidget(self.settings_content_stack, 1)

        # Build the search index now that every panel has been
        # constructed and added. Walks each panel for searchable
        # widgets (gesture cards, voice command cards, panel section
        # labels) and records (label, target_section, target_widget)
        # so the dropdown can navigate to the precise spot.
        self._build_settings_search_index()

        self.show_settings_section(SECTION_INSTRUCTIONS)
        return page

    @staticmethod
    def _build_search_clear_icon(color_hex: str) -> "QIcon":
        """Programmatically draw an accent-color "X" icon for the
        settings-search clear action. The built-in QLineEdit clear
        button uses a system pixmap that can't be recolored across
        Qt versions; rendering our own keeps the icon on-theme."""
        from PySide6.QtGui import QIcon
        size = 18
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            color = QColor(color_hex)
            if not color.isValid():
                color = QColor("#1DE9B6")
            pen = QPen(color, 2.4)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            inset = 5
            painter.drawLine(inset, inset, size - inset, size - inset)
            painter.drawLine(size - inset, inset, inset, size - inset)
        finally:
            painter.end()
        return QIcon(pm)

    def _build_settings_search_index(self) -> None:
        """Construct the searchable index used by the settings search
        dropdown. Each entry is a dict with:
          - label: display text shown in the dropdown
          - haystack: lowercased text used for matching
          - section_id: which settings tab to open (SECTION_*)
          - section_widget: collapsible to expand (or None)
          - target_widget: widget to scroll into view (or None)
        """
        from PySide6.QtWidgets import QLabel
        index: list[dict] = []
        # Map section button -> SECTION_* constant by index in the
        # nav button list (same order as settings_content_stack).
        section_for_index = (
            SECTION_INSTRUCTIONS, SECTION_GESTURES, SECTION_CUSTOM_GESTURE,
            SECTION_GESTURE_BINDS, SECTION_CAMERA, SECTION_MICROPHONE,
            SECTION_SAVE_LOCATIONS, SECTION_COLORS, SECTION_TUTORIAL,
            SECTION_UPDATES,
        )
        # 1. Gesture / voice cards inside the Control Guide. For each
        # GestureGuideCard / VoiceCommandCard in any panel, walk up
        # to find its parent GestureGuideSection so the dropdown can
        # auto-expand it on selection.
        gesture_panel = self.settings_content_stack.widget(SECTION_GESTURES)
        if gesture_panel is not None:
            for card in gesture_panel.findChildren(GestureGuideCard):
                title_label = card.findChild(QLabel, "gestureCardTitle")
                title = title_label.text() if title_label is not None else ""
                action_label = card.findChild(QLabel, "gestureCardSubtitle")
                action_text = action_label.text() if action_label is not None else ""
                section_widget = card.parent()
                while section_widget is not None and not isinstance(section_widget, GestureGuideSection):
                    section_widget = section_widget.parent()
                if not title:
                    continue
                index.append({
                    "label": title + (f"  —  {action_text}" if action_text else ""),
                    "haystack": f"{title} {action_text}".lower(),
                    "section_id": SECTION_GESTURES,
                    "section_widget": section_widget,
                    "target_widget": card,
                })
            for card in gesture_panel.findChildren(VoiceCommandCard):
                # VoiceCommandCard reuses the gestureCardTitle
                # objectName for its title QLabel.
                title_label = card.findChild(QLabel, "gestureCardTitle")
                title = title_label.text() if title_label is not None else ""
                section_widget = card.parent()
                while section_widget is not None and not isinstance(section_widget, GestureGuideSection):
                    section_widget = section_widget.parent()
                if not title:
                    continue
                index.append({
                    "label": f"Voice: {title}",
                    "haystack": f"voice {title}".lower(),
                    "section_id": SECTION_GESTURES,
                    "section_widget": section_widget,
                    "target_widget": card,
                })
        # 2. Section header buttons themselves so users can search
        # by tab name even when the gesture-card index doesn't
        # match.
        for nav_idx, button in enumerate(self._settings_nav_buttons):
            section_id = section_for_index[nav_idx] if nav_idx < len(section_for_index) else None
            if section_id is None:
                continue
            keywords = self._settings_nav_search_keywords.get(button, "")
            index.append({
                "label": button.text(),
                "haystack": f"{button.text()} {keywords}".lower(),
                "section_id": section_id,
                "section_widget": None,
                "target_widget": None,
            })
        # 3. Camera and microphone subsections — index any QFrame
        # with objectName == "innerCard" plus its first QLabel
        # child as a "subsection" entry. Lets users type 'phone'
        # and jump to the phone-camera card.
        for nav_idx, section_id in enumerate(
            (SECTION_CAMERA, SECTION_MICROPHONE, SECTION_SAVE_LOCATIONS, SECTION_COLORS, SECTION_TUTORIAL, SECTION_UPDATES, SECTION_INSTRUCTIONS)
        ):
            panel = self.settings_content_stack.widget(section_id)
            if panel is None:
                continue
            for card in panel.findChildren(QFrame):
                if card.objectName() != "innerCard":
                    continue
                first_label = None
                for lbl in card.findChildren(QLabel):
                    text = (lbl.text() or "").strip()
                    if text and len(text) <= 64:
                        first_label = lbl
                        break
                if first_label is None:
                    continue
                title = first_label.text().strip()
                if not title:
                    continue
                # Skip if the card title duplicates the panel name
                # (already covered by the tab-button entry).
                tab_name = self._settings_nav_buttons[section_id].text() if section_id < len(self._settings_nav_buttons) else ""
                if title.lower() == tab_name.lower():
                    continue
                index.append({
                    "label": f"{tab_name}: {title}",
                    "haystack": f"{tab_name} {title}".lower(),
                    "section_id": section_id,
                    "section_widget": None,
                    "target_widget": card,
                })

        # 4. Explicit per-feature entries for toggles, buttons, and
        # cards that aren't reachable through the generic inner-card
        # walk above (e.g., toggles tucked inside a row, or features
        # whose discoverable label doesn't share words with the user's
        # mental model — "Lite Mode" vs "low resolution model"). Each
        # entry navigates to the right tab and (when a target widget
        # is registered) scrolls it into view.
        feature_entries: list[tuple[str, str, int, object]] = [
            # (label, extra_haystack_keywords, section_id, target_widget_or_None)
            (
                "Camera: GPU Mode",
                "gpu mode hardware acceleration directml onnx fast",
                SECTION_CAMERA,
                getattr(self, "gpu_mode_button", None),
            ),
            (
                "Camera: Lite Mode",
                "lite mode model complexity speed performance light",
                SECTION_CAMERA,
                getattr(self, "lite_mode_button", None),
            ),
            (
                "Camera: Low FPS Mode",
                "low fps mode slow framerate degrade auto fallback",
                SECTION_CAMERA,
                getattr(self, "low_fps_button", None),
            ),
            (
                "Camera: CPU Mode",
                "cpu mode mediapipe default no gpu fallback software",
                SECTION_CAMERA,
                getattr(self, "gpu_mode_button", None),
            ),
            (
                "Camera: Save Camera Selection",
                "save camera selection preferred device choose remember",
                SECTION_CAMERA,
                getattr(self, "save_camera_button", None),
            ),
            (
                "Camera: Use Auto-Select",
                "auto select clear preferred camera default",
                SECTION_CAMERA,
                getattr(self, "clear_camera_button", None),
            ),
            (
                "Camera: Phone Camera (QR)",
                "phone camera qr code pair pairing iphone android connect mobile",
                SECTION_CAMERA,
                getattr(self, "phone_camera_qr_button", None),
            ),
            (
                "Camera: Disconnect Phone",
                "disconnect phone qr unpair stop",
                SECTION_CAMERA,
                getattr(self, "phone_camera_qr_disconnect_button", None),
            ),
            (
                "Camera: Use Phone Camera as Source",
                "use phone camera source qr active enable",
                SECTION_CAMERA,
                getattr(self, "use_phone_camera_qr_checkbox", None),
            ),
            (
                "Microphone: Save Microphone Choice",
                "save microphone choice mic preferred remember",
                SECTION_MICROPHONE,
                getattr(self, "save_microphone_button", None),
            ),
            (
                "Microphone: Use Phone Microphone (QR)",
                "use phone microphone phone mic qr iphone android",
                SECTION_MICROPHONE,
                getattr(self, "use_phone_mic_checkbox", None),
            ),
            (
                "Microphone: Phone Microphone QR",
                "phone microphone qr pair connect iphone",
                SECTION_MICROPHONE,
                getattr(self, "phone_camera_qr_button_mic", None),
            ),
        ]
        # Save Locations: one entry per output kind (drawings, screenshots,
        # screen recordings, clips). The save-location panel iterates
        # SAVE_LOCATION_OUTPUT_ORDER on build, so the QLineEdits are
        # discoverable via objectName once the panel exists.
        save_panel = self.settings_content_stack.widget(SECTION_SAVE_LOCATIONS)
        for output_kind in SAVE_LOCATION_OUTPUT_ORDER:
            label = SAVE_LOCATION_LABELS.get(output_kind, output_kind.title())
            target = None
            if save_panel is not None:
                from PySide6.QtWidgets import QLineEdit
                target = save_panel.findChild(QLineEdit, f"{output_kind}SaveLocationEdit")
            feature_entries.append((
                f"Save Locations: {label}",
                f"save {output_kind.replace('_', ' ')} {label.lower()} folder path directory",
                SECTION_SAVE_LOCATIONS,
                target,
            ))

        for label_text, keywords, section_id, target_widget in feature_entries:
            tab_name = (
                self._settings_nav_buttons[section_id].text()
                if 0 <= section_id < len(self._settings_nav_buttons)
                else ""
            )
            index.append({
                "label": label_text,
                "haystack": f"{tab_name} {label_text} {keywords}".lower(),
                "section_id": section_id,
                "section_widget": None,
                "target_widget": target_widget,
            })

        self._settings_search_index = index

    def _on_settings_search_changed(self, text: str) -> None:
        query = str(text or "").strip().lower()
        # The custom accent-X clear action only shows when there's
        # text to clear — matches the built-in clearButton UX but
        # uses our themed icon.
        try:
            self._settings_search_clear_action.setVisible(bool(query))
        except Exception:
            pass
        # Keep the original behavior of hiding non-matching tab
        # buttons so the user still has a quick visual filter on the
        # left rail.
        if not query:
            for button in self._settings_nav_buttons:
                button.setVisible(True)
            self._settings_search_results.clear()
            self._settings_search_results.setVisible(False)
            return
        tokens = [tok for tok in query.split() if tok]
        for button in self._settings_nav_buttons:
            haystack = f"{button.text().lower()} {self._settings_nav_search_keywords.get(button, '')}"
            button.setVisible(all(tok in haystack for tok in tokens))
        # Populate the results dropdown with entries whose haystack
        # contains every token. Limit to ~12 to avoid a giant list.
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt as _Qt
        self._settings_search_results.clear()
        matches: list[dict] = []
        for entry in self._settings_search_index:
            haystack = entry["haystack"]
            if all(tok in haystack for tok in tokens):
                matches.append(entry)
        matches = matches[:12]
        for entry in matches:
            item = QListWidgetItem(entry["label"])
            item.setData(_Qt.UserRole, entry)
            self._settings_search_results.addItem(item)
        self._settings_search_results.setVisible(bool(matches))
        if matches:
            self._settings_search_results.setCurrentRow(0)

    def _on_settings_search_activate_first(self) -> None:
        # Enter on the search box activates the first match.
        if self._settings_search_results.count() == 0:
            return
        item = self._settings_search_results.item(0)
        if item is None:
            return
        self._on_settings_search_result_clicked(item)

    def _on_settings_search_result_clicked(self, item) -> None:
        from PySide6.QtCore import Qt as _Qt
        if item is None:
            return
        entry = item.data(_Qt.UserRole)
        if not isinstance(entry, dict):
            return
        section_id = entry.get("section_id")
        if section_id is not None:
            self.show_settings_section(section_id)
        section_widget = entry.get("section_widget")
        # Auto-expand the matching collapsible section. The
        # GestureGuideSection toggles by clicking its header_button;
        # only expand if currently collapsed so we don't toggle off
        # an already-open section.
        if isinstance(section_widget, GestureGuideSection):
            try:
                if not section_widget.header_button.isChecked():
                    section_widget.header_button.setChecked(True)
                    section_widget._toggle_expanded(True)
            except Exception:
                pass
        target_widget = entry.get("target_widget")
        if target_widget is not None:
            # Walk up until we find the ancestor QScrollArea, then
            # scroll the target into view via ensureWidgetVisible.
            from PySide6.QtWidgets import QScrollArea
            ancestor = target_widget.parent()
            scroll: Optional[QScrollArea] = None
            while ancestor is not None:
                if isinstance(ancestor, QScrollArea):
                    scroll = ancestor
                    break
                ancestor = ancestor.parent()
            if scroll is not None:
                # Defer one tick so the panel switch + section
                # expansion has actually painted.
                QTimer.singleShot(50, lambda s=scroll, w=target_widget: s.ensureWidgetVisible(w, 30, 60))
        # Hide the dropdown after navigation; let the user re-type
        # to search again.
        self._settings_search_results.setVisible(False)

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
            "Touchless turns a live camera feed into hands-free control of Spotify, Chrome, the mouse, system volume, voice dictation, and an on-screen drawing canvas. Use this page as the quick start, Control Guide for the full gesture + voice command map, and Tutorial for the guided walkthrough.",
        )

        # Wrap the body in a scroll area so the long step list stays
        # readable on shorter windows instead of squishing.
        accent = self.config.accent_color or "#1DE9B6"
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setStyleSheet(
            f"""
            QScrollArea, QScrollArea > QWidget {{
                background: transparent; border: none;
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
            QScrollArea QScrollBar::add-line:vertical,
            QScrollArea QScrollBar::sub-line:vertical {{
                height: 0px; background: transparent;
            }}
            """
        )

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        scroll.setWidget(inner)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 8, 0)
        inner_layout.setSpacing(12)

        info_box = QFrame()
        info_box.setObjectName("innerCard")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(16, 16, 16, 16)
        info_layout.setSpacing(10)
        items = [
            "1. Press Start to begin live tracking with the selected camera. Touchless reads both hands in real time and routes gestures to whichever control context is active (Spotify, Chrome, mouse, volume, voice, drawing).",
            "2. Live View shows the camera feed with the hand skeleton, a per-hand gesture label (red outline by default, green when a gesture is recognized), the voice state, the volume state, and the current app routing. Open it whenever you want to verify what Touchless is seeing.",
            "3. Right-hand gestures drive Spotify, Chrome, the gesture wheels, drawing, and volume. Left-hand gestures drive voice and dictation, the mouse-mode toggle, the YouTube wheel toggle (left 'four'), and Spotify-side controls during a save prompt.",
            "4. Spotify actions only work while Spotify is running on this device. Chrome wheel actions only work while Chrome is already open and active. The right-hand 'two' gesture will open or focus Spotify if it isn't running.",
            "5. Mouse mode is a separate control mode. Turn it on with the left hand, control the pointer with the right hand, and turn it off again when you're finished. While mouse mode is on, drawing and the gesture wheels are disabled.",
            "6. Drawing mode (right-hand gesture) lets you sketch directly over the camera feed. Opening the thumb for about 0.2 seconds lifts the pen; a left-hand pinch + stretch resizes the captured stroke; swiping right clears the canvas, swiping left undoes one stroke. Color, thickness, and eraser type persist across sessions; shape mode always resets to off when you re-enter drawing mode.",
            "7. Use your phone as the camera (and optionally the microphone) by opening Settings -> Camera, scanning the QR code with your phone, and tapping 'Use phone camera'. The phone streams video over your local Wi-Fi and bypasses any built-in webcam. The phone microphone is a separate toggle in Settings -> Microphone.",
            "8. Three camera-performance modes live in Settings -> Camera. Low FPS auto-engages on slower hardware (drops to 0.34 / 0.22 detection thresholds + the lite landmark model). Lite Mode is a manual switch to MediaPipe model_complexity=0 for roughly 2.5x faster CPU inference. GPU Mode routes the hand-detection model through ONNX Runtime + DirectML on any DX12 GPU (NVIDIA / AMD / Intel) and falls back to CPU MediaPipe if the GPU path isn't reachable. Use whichever combination keeps your machine near 30 fps.",
            "9. Custom Gestures (Beta) lets you record your own static hand pose and bind it to a key, hotkey, text snippet, URL, or shell command. Open Settings -> Custom Gesture to create one and to test it in the Sandbox.",
            "10. Tutorial is the easiest place to learn the main motions. It uses the same live gesture and voice runtime as the rest of the app, so the actions you practice there are the real actions the app will run.",
        ]
        for item in items:
            lbl = QLabel(item)
            lbl.setWordWrap(True)
            info_layout.addWidget(lbl)
        inner_layout.addWidget(info_box)
        inner_layout.addStretch(1)

        layout.addWidget(scroll, 1)
        return panel

    def _build_gesture_guide_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Control Guide",
            "Open a section below to view each control and how to use it. "
            "Static gestures are held poses; dynamic gestures are motion-based; "
            "voice commands are spoken phrases recognized after the listener trigger.",
        )

        info_box = QFrame()
        info_box.setObjectName("innerCard")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(4)
        note = QLabel(
            "Static gestures = held hand poses. "
            "Dynamic gestures = motion-based (swipes, circles, slides). "
            "Voice commands = phrases spoken after holding LEFT-hand 'one' to start the listener."
        )
        note.setWordWrap(True)
        info_layout.addWidget(note)
        layout.addWidget(info_box, 0)

        scroll = build_gesture_guide_scroll_area()
        layout.addWidget(scroll, 1)
        return panel

    def _build_custom_gesture_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Custom Gestures Beta",
            "Record your own hand pose and bind it to a key, hotkey, "
            "text snippet, URL, or shell command. Static poses only — "
            "see the How it works card below for limitations.",
        )
        from .custom_gestures_panel import CustomGesturesPanel

        self._custom_gestures_panel = CustomGesturesPanel(
            config=self.config,
            accent_color=self.config.accent_color or "#1DE9B6",
            worker_provider=lambda: getattr(self, "_worker", None),
            parent=panel,
        )
        self._custom_gestures_panel.open_create_requested.connect(
            self._open_custom_gesture_creator
        )
        self._custom_gestures_panel.open_sandbox_requested.connect(
            self._open_custom_gesture_sandbox
        )
        self._custom_gestures_panel.open_edit_requested.connect(
            self._open_custom_gesture_editor
        )
        layout.addWidget(self._custom_gestures_panel)
        return panel

    # -------- Gesture Binds tab ------------------------------------------
    def _resolve_gesture_pose_image(self, image_filename: str) -> "Path | None":
        """Look up an image path for the Gesture Binds preview.

        Two flavours of image_filename are handled:
        - Bare filename (e.g., "Mute.png"): search the bundled
          GestureGuide/ folder. Mirrors GestureMediaWidget's resolution
          so both source-mode and PyInstaller bundle runs work.
        - Absolute path (e.g., a user-picked custom gesture thumbnail
          stored in <registry_dir>/gesture_thumbnails/): treat as
          already-resolved and just verify the file still exists.
        """
        if not image_filename:
            return None
        # Absolute path passthrough — used for custom-gesture thumbnails
        # whose location is registry-dir-relative, not GestureGuide/.
        try:
            direct = Path(image_filename)
            if direct.is_absolute() and direct.exists():
                return direct
        except Exception:
            pass
        candidate_roots: list[Path] = []
        try:
            candidate_roots.append(app_base_path() / "GestureGuide")
        except Exception:
            pass
        try:
            candidate_roots.append(Path(__file__).resolve().parents[4] / "GestureGuide")
        except Exception:
            pass
        for root in candidate_roots:
            candidate = root / image_filename
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                continue
        return None

    def _all_pose_entries(self) -> list[tuple[str, str, str, str]]:
        """Static poses + the user's recorded custom gestures, in display order.
        Custom gestures use pose_id `custom:<name>`; the third tuple slot
        carries either an empty string (no thumbnail picked) or the
        absolute path to the user-picked thumbnail PNG so the resolver
        below can use it directly."""
        entries: list[tuple[str, str, str, str]] = list(_GESTURE_BIND_POSES)
        try:
            from hgr.custom_gestures.registry import GestureRegistry
            registry = GestureRegistry()
            registry.load()
            for g in registry.list():
                desc = (g.description or "").strip() or "User-recorded custom gesture."
                thumb = registry.thumbnail_path(g)
                image_token = str(thumb) if thumb is not None else ""
                entries.append((f"custom:{g.name}", g.name, image_token, desc))
        except Exception:
            pass
        return entries

    def _describe_custom_action(self, gesture) -> str:
        """Render a one-line, human-readable description of a custom gesture's
        bound action for the Action column."""
        action = getattr(gesture, "action", None)
        if action is None:
            return f"Custom: {gesture.name}"
        kind = getattr(action, "kind", "noop") or "noop"
        payload = getattr(action, "payload", None) or {}
        if kind == "keystroke":
            key = str(payload.get("key", "")).strip()
            return f"Press {key}" if key else f"Custom keystroke ({gesture.name})"
        if kind == "hotkey":
            keys = payload.get("keys") or []
            combo = "+".join(str(k) for k in keys) if keys else ""
            return f"Press {combo}" if combo else f"Custom hotkey ({gesture.name})"
        if kind == "text":
            text = str(payload.get("text", "")).strip()
            preview = (text[:32] + "…") if len(text) > 32 else text
            return f"Type '{preview}'" if preview else f"Type text ({gesture.name})"
        if kind == "open_url":
            url = str(payload.get("url", "")).strip()
            return f"Open {url}" if url else f"Open URL ({gesture.name})"
        if kind == "run_command":
            cmd = str(payload.get("command", "")).strip()
            preview = (cmd[:40] + "…") if len(cmd) > 40 else cmd
            return f"Run: {preview}" if preview else f"Run command ({gesture.name})"
        return f"Custom: {gesture.name}"

    def _collect_gesture_bind_actions(self) -> list[tuple[str, str, str]]:
        """Static action rows + one row per custom gesture, in display order.
        Custom gesture rows use action_id `custom_action:<name>` with default
        pose `custom:<name>` (the gesture itself triggers itself)."""
        rows: list[tuple[str, str, str]] = list(_GESTURE_BIND_ACTIONS)
        try:
            from hgr.custom_gestures.registry import GestureRegistry
            registry = GestureRegistry()
            registry.load()
            for g in registry.list():
                rows.append((
                    f"custom_action:{g.name}",
                    self._describe_custom_action(g),
                    f"custom:{g.name}",
                ))
        except Exception:
            pass
        return rows

    def _pose_label_for_id(self, pose_id: str) -> str:
        if not pose_id:
            return "(unbound)"
        if pose_id.startswith("custom:"):
            return pose_id.split(":", 1)[1] or "(custom)"
        for pid, label, _img, _desc in _GESTURE_BIND_POSES:
            if pid == pose_id:
                return label
        return pose_id

    def _build_gesture_binds_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Gesture Binds",
            "Reassign which hand pose triggers each action. Click an Active Gesture to "
            "start a rebind, then pick the new pose from All Gesture Poses on the right. "
            "Press Esc to cancel. Click Save Changes when you're done.",
        )
        accent = self.config.accent_color or "#1DE9B6"

        # Rebind state — pending changes are held in _pending_changes until
        # the user clicks Save. _pending_action is the action being rebound
        # right now (None when no rebind is in progress).
        self._gesture_binds_pending_action: str | None = None
        self._gesture_binds_pending_changes: dict[str, str] = {}
        self._gesture_binds_active_buttons: dict[str, QPushButton] = {}
        self._gesture_binds_hover_timer: QTimer | None = None
        self._gesture_binds_hover_popup: QFrame | None = None
        self._gesture_binds_hover_pose_id: str | None = None

        scroll = QScrollArea()
        scroll.setObjectName("gestureBindsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            f"""
            QScrollArea#gestureBindsScroll,
            QScrollArea#gestureBindsScroll > QWidget,
            QScrollArea#gestureBindsScroll QWidget#qt_scrollarea_viewport {{
                background: transparent;
                border: none;
            }}
            QScrollArea#gestureBindsScroll QScrollBar:vertical {{
                background: rgba(255,255,255,0.04);
                width: 10px;
                margin: 6px 3px 6px 3px;
                border-radius: 5px;
            }}
            QScrollArea#gestureBindsScroll QScrollBar::handle:vertical {{
                background: {accent};
                border-radius: 5px;
                min-height: 32px;
            }}
            QScrollArea#gestureBindsScroll QScrollBar::handle:vertical:hover {{
                background: {accent};
                border: 1px solid rgba(255,255,255,0.25);
            }}
            QScrollArea#gestureBindsScroll QScrollBar::add-line:vertical,
            QScrollArea#gestureBindsScroll QScrollBar::sub-line:vertical {{
                height: 0px;
                background: transparent;
            }}
            QScrollArea#gestureBindsScroll QScrollBar::add-page:vertical,
            QScrollArea#gestureBindsScroll QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            """
        )

        body = QWidget()
        body.setObjectName("gestureBindsBody")
        body.setAttribute(Qt.WA_StyledBackground, True)
        body.setStyleSheet("QWidget#gestureBindsBody { background: transparent; }")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(14)

        # Pill — appears under the subtitle while a rebind is pending.
        pill = QLabel(
            "To change this action's activation gesture click on a gesture pose from the "
            "All Gesture Poses list. Press Esc to cancel."
        )
        pill.setObjectName("gestureBindsPill")
        pill.setWordWrap(True)
        pill.setVisible(False)
        pill.setStyleSheet(
            f"""
            QLabel#gestureBindsPill {{
                background: rgba(29, 233, 182, 0.16);
                border: 1px solid {accent};
                border-radius: 10px;
                padding: 10px 14px;
                color: {self.config.text_color or "#E5F6FF"};
                font-size: 13px;
            }}
            """
        )
        self._gesture_binds_pill = pill
        body_layout.addWidget(pill)

        columns = QHBoxLayout()
        columns.setSpacing(18)

        # ---- Left column: bindings table ------------------------------------
        table_box = QFrame()
        table_box.setObjectName("innerCard")
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(14, 14, 14, 14)
        table_layout.setSpacing(8)

        table_header = QLabel("Bindings")
        table_header.setObjectName("settingsPanelTitle")
        table_layout.addWidget(table_header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._gesture_binds_grid = grid

        col_a = QLabel("Action")
        col_a.setObjectName("gestureCardSubtitle")
        col_b = QLabel("Active Gesture")
        col_b.setObjectName("gestureCardSubtitle")
        grid.addWidget(col_a, 0, 0)
        grid.addWidget(col_b, 0, 1)

        self._populate_gesture_binds_table()

        table_layout.addLayout(grid)
        table_layout.addStretch(1)
        columns.addWidget(table_box, 3)

        # ---- Right column: All Gesture Poses --------------------------------
        poses_box = QFrame()
        poses_box.setObjectName("innerCard")
        poses_layout = QVBoxLayout(poses_box)
        poses_layout.setContentsMargins(14, 14, 14, 14)
        poses_layout.setSpacing(8)

        poses_header = QLabel("All Gesture Poses")
        poses_header.setObjectName("settingsPanelTitle")
        poses_layout.addWidget(poses_header)

        poses_hint = QLabel("Hover for 1 second to preview a pose.")
        poses_hint.setObjectName("gestureCardSubtitle")
        poses_hint.setWordWrap(True)
        poses_layout.addWidget(poses_hint)

        poses_list = QListWidget()
        poses_list.setObjectName("gestureBindsPosesList")
        poses_list.setMouseTracking(True)
        poses_list.setSelectionMode(QListWidget.NoSelection)
        poses_list.itemClicked.connect(self._on_gesture_pose_clicked)
        poses_list.itemEntered.connect(self._on_gesture_pose_hover_enter)
        poses_list.viewport().installEventFilter(self)
        self._gesture_binds_poses_list = poses_list
        self._refresh_gesture_binds_poses_list()
        poses_layout.addWidget(poses_list, 1)

        columns.addWidget(poses_box, 2)
        body_layout.addLayout(columns, 1)

        # ---- Save bar -------------------------------------------------------
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_btn = QPushButton("Save Changes")
        save_btn.setObjectName("primaryAction")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._save_gesture_bindings)
        save_row.addWidget(save_btn)
        body_layout.addLayout(save_row)
        self._gesture_binds_save_button = save_btn

        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        # Style for the active-gesture buttons + the poses list.
        panel.setStyleSheet(
            (panel.styleSheet() or "")
            + f"""
            QPushButton#gestureBindActiveButton {{
                text-align: left;
                padding: 8px 12px;
                border-radius: 8px;
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.10);
                color: {self.config.text_color or "#E5F6FF"};
            }}
            QPushButton#gestureBindActiveButton:hover {{
                background: rgba(255, 255, 255, 0.10);
            }}
            QPushButton#gestureBindActiveButton[pendingRebind="true"] {{
                background: rgba(29, 233, 182, 0.18);
                border: 1px solid {accent};
                color: {accent};
            }}
            QListWidget#gestureBindsPosesList {{
                background: rgba(10, 28, 39, 0.55);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 4px;
                color: {self.config.text_color or "#E5F6FF"};
            }}
            QListWidget#gestureBindsPosesList::item {{
                padding: 8px 10px;
                border-radius: 6px;
                color: {self.config.text_color or "#E5F6FF"};
            }}
            QListWidget#gestureBindsPosesList::item:hover {{
                background: rgba(255, 255, 255, 0.08);
                color: {self.config.text_color or "#E5F6FF"};
            }}
            QListWidget#gestureBindsPosesList::item:selected {{
                background: rgba(29, 233, 182, 0.18);
                color: {self.config.text_color or "#E5F6FF"};
            }}
            """
        )
        return panel

    def _gesture_binds_registry_changed_since_last_paint(self) -> bool:
        """Stat the custom-gesture registry file and return True iff its
        mtime has advanced since the last time we rebuilt the Gesture
        Binds table. Used to skip the (somewhat expensive) rebuild on
        re-entry when nothing has actually changed."""
        try:
            from hgr.custom_gestures.registry import registry_path
            path = registry_path()
            mtime = path.stat().st_mtime if path.exists() else 0.0
        except Exception:
            mtime = 0.0
        last = getattr(self, "_gesture_binds_last_registry_mtime", None)
        if last is None:
            # First time we've ever painted Gesture Binds.
            self._gesture_binds_last_registry_mtime = mtime
            return True
        if mtime != last:
            self._gesture_binds_last_registry_mtime = mtime
            return True
        return False

    def _populate_gesture_binds_table(self) -> None:
        """Fill the bindings grid with one row per static + custom action.
        Header row 0 is left untouched; rows 1..N are cleared and rebuilt."""
        grid = getattr(self, "_gesture_binds_grid", None)
        if grid is None:
            return
        # Drop any non-header rows from a prior population (custom gestures
        # may have been added/removed since the last build).
        to_remove = []
        for i in range(grid.count()):
            item = grid.itemAt(i)
            if item is None:
                continue
            r, _c, _rs, _cs = grid.getItemPosition(i)
            if r >= 1:
                to_remove.append(item)
        for item in to_remove:
            w = item.widget()
            grid.removeItem(item)
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._gesture_binds_active_buttons.clear()

        for row_idx, (action_id, action_label, _default_pose) in enumerate(
            self._collect_gesture_bind_actions(), start=1
        ):
            label = QLabel(action_label)
            label.setObjectName("gestureCardBody")
            label.setWordWrap(True)
            grid.addWidget(label, row_idx, 0)

            current_pose = resolve_gesture_binding(self.config, action_id)
            # If a pending change is in flight (user clicked a pose but hasn't
            # saved), prefer that so the row reflects what they're about to save.
            pending = self._gesture_binds_pending_changes.get(action_id) \
                if hasattr(self, "_gesture_binds_pending_changes") else None
            if pending:
                current_pose = pending
            btn = QPushButton(self._pose_label_for_id(current_pose))
            btn.setObjectName("gestureBindActiveButton")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("gestureBindActionId", action_id)
            btn.setAttribute(Qt.WA_Hover, True)
            btn.installEventFilter(self)
            btn.clicked.connect(lambda _checked=False, a=action_id: self._on_gesture_bind_active_clicked(a))
            self._gesture_binds_active_buttons[action_id] = btn
            grid.addWidget(btn, row_idx, 1)

    def _refresh_gesture_binds_poses_list(self) -> None:
        lw = getattr(self, "_gesture_binds_poses_list", None)
        if lw is None:
            return
        lw.clear()
        for pose_id, label, _img, _desc in self._all_pose_entries():
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, pose_id)
            lw.addItem(item)

    def _on_gesture_bind_active_clicked(self, action_id: str) -> None:
        # Clear any prior pending state's pressed style.
        prev = self._gesture_binds_pending_action
        if prev and prev != action_id:
            prev_btn = self._gesture_binds_active_buttons.get(prev)
            if prev_btn is not None:
                prev_btn.setProperty("pendingRebind", False)
                prev_btn.style().unpolish(prev_btn)
                prev_btn.style().polish(prev_btn)
        self._gesture_binds_pending_action = action_id
        btn = self._gesture_binds_active_buttons.get(action_id)
        if btn is not None:
            btn.setProperty("pendingRebind", True)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if self._gesture_binds_pill is not None:
            self._gesture_binds_pill.setVisible(True)
        # Make sure the panel can receive Esc key presses.
        self.setFocus()

    def _clear_gesture_bind_pending(self) -> None:
        action_id = self._gesture_binds_pending_action
        self._gesture_binds_pending_action = None
        if action_id:
            btn = self._gesture_binds_active_buttons.get(action_id)
            if btn is not None:
                btn.setProperty("pendingRebind", False)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        if self._gesture_binds_pill is not None:
            self._gesture_binds_pill.setVisible(False)

    def _on_gesture_pose_clicked(self, item) -> None:
        if not self._gesture_binds_pending_action:
            return
        pose_id = item.data(Qt.UserRole) if item is not None else None
        if not pose_id:
            return
        action_id = self._gesture_binds_pending_action
        self._gesture_binds_pending_changes[action_id] = pose_id
        btn = self._gesture_binds_active_buttons.get(action_id)
        if btn is not None:
            btn.setText(self._pose_label_for_id(pose_id))
        self._clear_gesture_bind_pending()

    def _save_gesture_bindings(self) -> None:
        if not self._gesture_binds_pending_changes:
            TouchlessNotice.show_info(self, "Gesture Binds", "No changes to save.")
            return
        current = dict(getattr(self.config, "gesture_bindings", None) or {})
        current.update(self._gesture_binds_pending_changes)
        # Drop entries that match the default — keeps the JSON tidy.
        defaults = {a: d for a, _l, d in _GESTURE_BIND_ACTIONS}
        cleaned = {k: v for k, v in current.items() if defaults.get(k) != v}
        self.config.gesture_bindings = cleaned
        try:
            save_config(self.config)
        except Exception as exc:
            TouchlessNotice.show_warn(self, "Save failed", f"Could not write settings: {exc}")
            return
        self._gesture_binds_pending_changes.clear()
        TouchlessNotice.show_info(
            self,
            "Gesture Binds",
            "Bindings saved. Restart Touchless or restart your camera session for them to take effect.",
        )

    # -------- Hover preview popup ---------------------------------------
    def _start_gesture_pose_hover_timer(self, pose_id: str) -> None:
        if not pose_id:
            return
        # If the popup is already showing this pose, do nothing.
        if (
            self._gesture_binds_hover_popup is not None
            and self._gesture_binds_hover_pose_id == pose_id
        ):
            return
        # Hide any popup that's currently showing for a DIFFERENT pose,
        # then start a fresh 2s countdown.
        if self._gesture_binds_hover_popup is not None:
            self._hide_gesture_pose_preview()
        self._gesture_binds_hover_pose_id = pose_id
        if self._gesture_binds_hover_timer is not None:
            self._gesture_binds_hover_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda pid=pose_id: self._show_gesture_pose_preview(pid))
        timer.start(1000)
        self._gesture_binds_hover_timer = timer

    def _on_gesture_pose_hover_enter(self, item) -> None:
        if item is None:
            return
        pose_id = item.data(Qt.UserRole)
        self._start_gesture_pose_hover_timer(pose_id)

    def _hide_gesture_pose_preview(self) -> None:
        if self._gesture_binds_hover_timer is not None:
            self._gesture_binds_hover_timer.stop()
            self._gesture_binds_hover_timer = None
        if self._gesture_binds_hover_popup is not None:
            self._gesture_binds_hover_popup.hide()
            self._gesture_binds_hover_popup.deleteLater()
            self._gesture_binds_hover_popup = None
        self._gesture_binds_hover_pose_id = None

    def _show_gesture_pose_preview(self, pose_id: str) -> None:
        if not pose_id or pose_id != self._gesture_binds_hover_pose_id:
            return
        # Find the entry in the combined list (static + custom).
        entry = None
        for e in self._all_pose_entries():
            if e[0] == pose_id:
                entry = e
                break
        if entry is None:
            return
        _pid, label, image_filename, description = entry

        accent = self.config.accent_color or "#1DE9B6"
        text_color = self.config.text_color or "#E5F6FF"

        popup = QFrame(self)
        popup.setObjectName("gestureBindsPreviewPopup")
        popup.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        popup.setAttribute(Qt.WA_DeleteOnClose, True)
        popup.setStyleSheet(
            f"""
            QFrame#gestureBindsPreviewPopup {{
                background: rgba(15, 23, 42, 0.98);
                border: 1px solid {accent};
                border-radius: 12px;
            }}
            QLabel#previewTitle {{
                color: {text_color};
                font-size: 16px;
                font-weight: 600;
            }}
            QLabel#previewBody {{
                color: rgba(229, 246, 255, 0.85);
                font-size: 13px;
            }}
            """
        )
        # Wider, shorter shape — wider than the Control Guide cards (~480px) and
        # not as tall, since we drop the Requirements section.
        popup.setFixedWidth(520)

        h = QHBoxLayout(popup)
        h.setContentsMargins(14, 14, 14, 14)
        h.setSpacing(14)

        # Image (or placeholder for custom gestures).
        image_box = QLabel()
        image_box.setAlignment(Qt.AlignCenter)
        image_box.setFixedSize(160, 160)
        image_box.setStyleSheet("background: rgba(10, 28, 39, 0.72); border-radius: 10px; color: rgba(229, 246, 255, 0.55);")
        media_path = self._resolve_gesture_pose_image(image_filename) if image_filename else None
        if media_path is not None:
            pix = QPixmap(str(media_path))
            if not pix.isNull():
                image_box.setPixmap(pix.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                image_box.setText("(no image)")
        else:
            image_box.setText("Custom\ngesture")
        h.addWidget(image_box, 0, Qt.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(8)
        title_lbl = QLabel(label)
        title_lbl.setObjectName("previewTitle")
        title_lbl.setWordWrap(True)
        body_lbl = QLabel(description)
        body_lbl.setObjectName("previewBody")
        body_lbl.setWordWrap(True)
        text_layout.addWidget(title_lbl)
        text_layout.addWidget(body_lbl)
        text_layout.addStretch(1)
        h.addLayout(text_layout, 1)

        # Position near the cursor without clipping off-screen.
        cursor_pos = QCursor.pos()
        screen = self.screen() if hasattr(self, "screen") else None
        screen_rect = screen.availableGeometry() if screen is not None else None
        popup.adjustSize()
        target_x = cursor_pos.x() + 16
        target_y = cursor_pos.y() + 16
        if screen_rect is not None:
            if target_x + popup.width() > screen_rect.right():
                target_x = max(screen_rect.left(), cursor_pos.x() - popup.width() - 16)
            if target_y + popup.height() > screen_rect.bottom():
                target_y = max(screen_rect.top(), cursor_pos.y() - popup.height() - 16)
        popup.move(target_x, target_y)
        popup.show()
        self._gesture_binds_hover_popup = popup

    def _open_custom_gesture_creator(self) -> None:
        from PySide6.QtWidgets import QDialog
        from .custom_gestures_recorder import RecordingWindow
        from .custom_gestures_wizard import CreateGestureWizard

        accent = self.config.accent_color or "#1DE9B6"
        wizard = CreateGestureWizard(accent_color=accent, parent=self)
        if wizard.exec() != QDialog.DialogCode.Accepted or wizard.result_payload is None:
            return
        result = wizard.result_payload
        # Pass the worker if it's running so the recorder can share its
        # frame stream — otherwise the recorder opens its own camera so
        # the user doesn't have to start the main live viewer first.
        worker = getattr(self, "_worker", None)
        try:
            recorder = RecordingWindow(
                worker=worker,
                accent_color=accent,
                name=result.name,
                description=result.description,
                action=result.action,
                parent=self,
                config=self.config,
            )
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[custom-gestures] RecordingWindow construction failed:\n{tb}")
            QMessageBox.critical(
                self,
                "Could not open recording window",
                f"{type(exc).__name__}: {exc}\n\n"
                "Full traceback printed to the terminal.",
            )
            return
        recorder.saved.connect(lambda _name: self._custom_gestures_panel.refresh_cards())
        recorder.exec()

    def _open_custom_gesture_sandbox(self) -> None:
        from .custom_gestures_sandbox import SandboxWindow

        accent = self.config.accent_color or "#1DE9B6"
        # As with the recorder, pass the worker if alive — sandbox falls
        # back to its own camera otherwise.
        worker = getattr(self, "_worker", None)
        sandbox = SandboxWindow(
            worker=worker,
            accent_color=accent,
            parent=self,
            config=self.config,
        )
        sandbox.show()

    def _open_custom_gesture_editor(self, name: str) -> None:
        """Edit metadata + action of an already-recorded gesture without
        re-recording samples. Opens the wizard pre-populated with the
        existing values; on accept, updates the registry in place."""
        from PySide6.QtWidgets import QDialog
        from hgr.custom_gestures.registry import GestureRegistry
        from .custom_gestures_wizard import CreateGestureWizard

        accent = self.config.accent_color or "#1DE9B6"
        registry = GestureRegistry()
        registry.load()
        existing = registry.get(name)
        if existing is None:
            QMessageBox.warning(self, "Not found", f"No gesture named {name!r}.")
            return

        # Reverse-engineer the wizard fields from the saved action payload.
        action_kind = existing.action.kind
        payload = existing.action.payload or {}
        if action_kind == "keystroke":
            initial_value = str(payload.get("key", ""))
        elif action_kind == "hotkey":
            keys = payload.get("keys") or []
            initial_value = "+".join(str(k) for k in keys)
        elif action_kind == "text":
            initial_value = str(payload.get("text", ""))
        elif action_kind == "open_url":
            initial_value = str(payload.get("url", ""))
        elif action_kind == "run_command":
            initial_value = str(payload.get("command", ""))
        else:
            initial_value = ""
        initial_hold = float(payload.get("hold_s", 1.0)) if payload else 1.0
        initial_cooldown = float(payload.get("cooldown_s", 2.0)) if payload else 2.0

        wizard = CreateGestureWizard(
            accent_color=accent,
            parent=self,
            edit_mode=True,
            initial_name=existing.name,
            initial_description=existing.description,
            initial_hold=initial_hold,
            initial_cooldown=initial_cooldown,
            initial_action_kind=action_kind if action_kind != "noop" else None,
            initial_action_value=initial_value,
            original_name=existing.name,
        )
        if wizard.exec() != QDialog.DialogCode.Accepted or wizard.result_payload is None:
            return
        result = wizard.result_payload

        # Save back in place — keep the existing recorded samples, swap
        # only the metadata + action. If the user changed the name,
        # remove the old entry first.
        if result.name != existing.name:
            registry.remove(existing.name)
        try:
            registry.add(
                name=result.name,
                samples=existing.samples,
                action=result.action,
                description=result.description,
                overwrite=True,
                handedness=existing.handedness,  # preserve recorded hand
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Edit failed", str(exc))
            return
        registry.save()
        self._custom_gestures_panel.refresh_cards()

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
            "Choose from the list of cameras connected to your device. "
            "Click Preview to see the selected camera's view."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        # Preview button row — opens a Touchless-themed live preview of
        # whichever camera is currently selected in the dropdown above.
        preview_row = QHBoxLayout()
        preview_row.addStretch(1)
        self.camera_preview_button = QPushButton("Preview")
        self.camera_preview_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.camera_preview_button.clicked.connect(self._open_camera_preview)
        preview_row.addWidget(self.camera_preview_button)
        box_layout.addLayout(preview_row)

        self.camera_already_mirrored_checkbox = QCheckBox("This camera source is already mirrored (skip Touchless's flip)")
        self.camera_already_mirrored_checkbox.setObjectName("cameraMirroredCheckbox")
        self.camera_already_mirrored_checkbox.setStyleSheet(
            checkbox_style_tpl.format(name="cameraMirroredCheckbox", text=self.config.text_color, accent=self.config.accent_color)
        )
        self.camera_already_mirrored_checkbox.setChecked(bool(getattr(self.config, "camera_source_is_mirrored", False)))
        self.camera_already_mirrored_checkbox.toggled.connect(self._on_camera_already_mirrored_toggled)
        box_layout.addWidget(self.camera_already_mirrored_checkbox)

        # ============================================================
        # 2. PHONE CAMERA VIA QR CODE
        #    (The legacy "Via HTTP URL" section was removed — QR pairing
        #    via the embedded HTTPS server replaces it cleanly. The
        #    related toggles still exist in AppConfig for backwards
        #    compatibility but the UI surface is gone.)
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

        # Legacy checkbox kept hidden — the camera dropdown is now
        # the single source-of-truth control (a "Phone Camera (QR)"
        # entry appears in it when paired). The hidden widget keeps
        # existing toggle-handler code paths from breaking until we
        # do a full cleanup pass.
        self.use_phone_camera_qr_checkbox = QCheckBox("Use phone camera (QR) as source")
        self.use_phone_camera_qr_checkbox.setObjectName("usePhoneQrCheckbox")
        self.use_phone_camera_qr_checkbox.setStyleSheet(
            checkbox_style_tpl.format(name="usePhoneQrCheckbox", text=self.config.text_color, accent=self.config.accent_color)
        )
        self.use_phone_camera_qr_checkbox.setChecked(bool(getattr(self.config, "phone_camera_qr_active", False)))
        self.use_phone_camera_qr_checkbox.setEnabled(already_paired)
        self.use_phone_camera_qr_checkbox.toggled.connect(self._on_use_phone_camera_qr_toggled)
        self.use_phone_camera_qr_checkbox.setVisible(False)
        box_layout.addWidget(self.use_phone_camera_qr_checkbox)

        # Initial text: if a phone has already identified itself
        # (server auto-started + phone reconnected before the user
        # opened Settings), surface its name. Otherwise show the
        # neutral "tap Start on your phone..." prompt.
        initial_status = "Phone paired — tap Start on your phone's browser to connect." if already_paired else ""
        try:
            srv = getattr(self, "_phone_camera_qr_server", None)
            if already_paired and srv is not None:
                cached_label = getattr(srv, "connected_phone_label", None)
                if cached_label:
                    self._phone_connected_label = str(cached_label)
                    initial_status = self._phone_paired_status_text()
        except Exception:
            pass
        self.phone_camera_qr_status_label = QLabel(initial_status)
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
        # 5. LITE MODE
        # ============================================================
        box_layout.addWidget(_section_header("Lite Mode"))

        lite_mode_note = QLabel(
            "Improves processing by ~2.5x for simple gestures and commands. "
            "For very extreme angles or heavy occlusion may be slightly less stable. "
            "\"Lite\" badge will appear in live viewers when activated."
        )
        lite_mode_note.setObjectName("cameraNote")
        lite_mode_note.setWordWrap(True)
        box_layout.addWidget(lite_mode_note)

        lite_mode_row = QHBoxLayout()
        self.lite_mode_button = QPushButton()
        self.lite_mode_button.setCheckable(True)
        self.lite_mode_button.setChecked(bool(getattr(self.config, "lite_mode", False)))
        self.lite_mode_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.lite_mode_button.clicked.connect(self._on_lite_mode_button_toggled)
        lite_mode_row.addWidget(self.lite_mode_button)
        lite_mode_row.addStretch(1)
        box_layout.addLayout(lite_mode_row)
        self._refresh_lite_mode_button_label()

        # ============================================================
        # 6. GPU MODE
        # ============================================================
        box_layout.addWidget(_section_header("GPU Mode"))

        gpu_mode_note = QLabel(
            "Uses your graphics card to speed up hand tracking when available. "
            "If your machine can't run it, Touchless quietly falls back to the "
            "regular path so gestures keep working."
        )
        gpu_mode_note.setObjectName("cameraNote")
        gpu_mode_note.setWordWrap(True)
        box_layout.addWidget(gpu_mode_note)

        gpu_mode_row = QHBoxLayout()
        self.gpu_mode_button = QPushButton()
        self.gpu_mode_button.setCheckable(True)
        self.gpu_mode_button.setChecked(bool(getattr(self.config, "gpu_mode", False)))
        self.gpu_mode_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.gpu_mode_button.clicked.connect(self._on_gpu_mode_button_toggled)
        gpu_mode_row.addWidget(self.gpu_mode_button)
        gpu_mode_row.addStretch(1)
        box_layout.addLayout(gpu_mode_row)
        # Probe what GPU paths are reachable and surface it via the
        # tooltip — but DON'T disable the toggle on a probe miss. The
        # runtime loader falls back to CPU MediaPipe transparently if
        # no GPU path is reachable, so toggling on with no GPU is at
        # worst a no-op. Earlier versions disabled the button when
        # the probe came back empty, which on some machines (probe
        # false-negative, packaged-app DLL discovery edge cases) made
        # GPU Mode appear permanently unavailable even when the user
        # had a perfectly capable GPU.
        try:
            from ...gesture.tracking.gpu_probe import probe_gpu_paths
            probe = probe_gpu_paths()
            if probe.has_any_gpu_path:
                self.gpu_mode_button.setToolTip(probe.path_summary())
            else:
                self.gpu_mode_button.setToolTip(
                    probe.path_summary()
                    + " — toggling on is safe; runtime falls back to CPU MediaPipe automatically."
                )
        except Exception:
            pass
        self._refresh_gpu_mode_button_label()

        # ============================================================
        # 7. SAVE CAMERA SELECTION (at the bottom)
        # ============================================================
        box_layout.addWidget(_section_header("Save Camera Selection"))

        save_hint = QLabel(
            "Pick a camera from the list above and click Save to remember it. "
            "Use Auto-Select to let Touchless choose for you each launch."
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
        self._install_scroll_wheel_forwarder(scroll)
        layout.addWidget(scroll, 1)
        return panel

    def _install_scroll_wheel_forwarder(self, scroll_area: QScrollArea) -> None:
        """Make wheel-scrolling reliable inside a settings panel that
        contains QComboBox / QSpinBox / QDoubleSpinBox / QSlider /
        QAbstractSpinBox children. Without this, those widgets can
        silently consume the wheel event without changing their value
        (especially after a focus transition), and the panel just
        stops scrolling until the user clicks elsewhere — exactly the
        'sometimes scroll doesn't work' bug the user reported.
        Applied wherever a settings panel embeds a QScrollArea."""
        from PySide6.QtWidgets import QComboBox, QAbstractSpinBox, QAbstractSlider
        if scroll_area is None:
            return
        widget = scroll_area.widget()
        if widget is None:
            return
        forwarder = getattr(self, "_wheel_forwarder", None)
        if forwarder is None:
            forwarder = _ScrollWheelForwarder(self)
            self._wheel_forwarder = forwarder
        forwarder.attach(scroll_area)
        # Walk every potentially-trapping child and install the
        # forwarder. ClickFocus means the widget only gets focus
        # when explicitly clicked — wheel scrolls outside that
        # scope no longer change values by accident.
        for child_cls in (QComboBox, QAbstractSpinBox, QAbstractSlider):
            for child in widget.findChildren(child_cls):
                child.setFocusPolicy(Qt.ClickFocus)
                child.installEventFilter(forwarder)

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

    def _open_camera_preview(self) -> None:
        """Show a live preview of the camera currently selected in the
        Settings → Camera dropdown. Falls back to whichever camera
        Auto-Select would pick (first device that opens cleanly) when
        the dropdown is on the auto entry, so the Preview button works
        out of the box without forcing the user to pick first."""
        if not hasattr(self, "camera_combo"):
            return
        camera_index = None
        try:
            idx_data = self.camera_combo.currentData()
            camera_index = int(idx_data) if idx_data is not None else None
        except (TypeError, ValueError):
            camera_index = None
        # Auto-Select fallback: scan the first few indices and grab
        # whichever opens. Mirrors what `open_preferred_or_first_available`
        # does in the live engine, so Preview shows the same camera
        # the user would actually get.
        if camera_index is None or camera_index < 0:
            camera_index = self._auto_select_camera_index()
            if camera_index is None:
                TouchlessNotice.show_warn(
                    self,
                    "Camera Preview",
                    "No working camera detected. Plug in a webcam or pick a phone-camera source first.",
                )
                return
        label_text = str(self.camera_combo.currentText() or "").strip()
        if not label_text or label_text.lower().startswith("auto"):
            label_text = f"Camera {camera_index} (auto-selected)"
        dialog = CameraPreviewDialog(
            self.config, camera_index, camera_label=label_text, parent=self
        )
        dialog.show()

    @staticmethod
    def _auto_select_camera_index() -> "int | None":
        """Walk the first 8 indices and return the first one that opens
        cleanly. Used when the user clicks Preview without an explicit
        camera selection in the dropdown (Auto-Select mode)."""
        try:
            for idx in range(8):
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if hasattr(cv2, "CAP_DSHOW") else cv2.VideoCapture(idx)
                try:
                    if cap is None or not cap.isOpened():
                        continue
                    ok, _frame = cap.read()
                    if ok:
                        return idx
                finally:
                    try:
                        cap.release()
                    except Exception:
                        pass
        except Exception:
            return None
        return None

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
        # Keep the manual-pair flow's status label in sync with the
        # connected-device label that the server will emit once the
        # phone loads the page. If the phone already announced itself
        # during the QR dialog, _phone_connected_label is already set
        # and we reflect it immediately; otherwise we show URL-only
        # and the status callback updates the line as soon as the
        # phone identifies itself.
        if hasattr(self, "_phone_camera_qr_server") and self._phone_camera_qr_server is not None:
            try:
                self._phone_camera_qr_server.set_status_callback(self._forward_phone_server_status)
            except Exception:
                pass
        # Pull whatever the server already knows (the QR dialog may
        # have caught the phone's identity before this commit step).
        sl = getattr(self._phone_camera_qr_server, "connected_phone_label", None) if self._phone_camera_qr_server is not None else None
        if sl:
            self._phone_connected_label = str(sl)
        self.phone_camera_qr_status_label.setText(self._phone_paired_status_text())
        self.phone_camera_qr_disconnect_button.setVisible(True)
        self.phone_camera_qr_button.setText("Show QR Code")
        # Rebuild camera dropdown so the new "Phone Camera (QR)"
        # entry appears, then select it (since this pair flow sets
        # phone_camera_qr_active=True above).
        self._rebuild_camera_combo()
        self._refresh_camera_combo_selection(self._PHONE_CAMERA_DROPDOWN_VALUE)
        self._restart_camera_for_phone_toggle()
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText("Last action: phone camera paired via QR")

    # ---- Phone-server status bridge ------------------------------------
    def _forward_phone_server_status(self, event: str, data: dict) -> None:
        """Server-thread entry point. Re-emit as a Qt Signal so the
        slot runs on the GUI thread before touching any widgets."""
        try:
            self._phone_server_status_signal.emit(event, dict(data) if data else {})
        except Exception:
            pass

    def _on_phone_server_status_event(self, event: str, data) -> None:
        """GUI-thread receiver for phone-camera server status events.

        Cares about `phone_identified` (UA parsed → friendly label) and
        the connection lifecycle events that should refresh the
        Settings → Camera "Paired — ..." line."""
        if not isinstance(data, dict):
            data = {}
        label = str(data.get("label") or "").strip()
        if event == "phone_identified" and label:
            self._phone_connected_label = label
            self._refresh_phone_status_label()
        elif event in ("client_connected", "phone_page_loaded"):
            if label:
                self._phone_connected_label = label
            self._refresh_phone_status_label()

    def _phone_paired_status_text(self) -> str:
        """Compose the 'Paired — <device> — <url>' line. Falls back to
        just the URL when no phone has connected yet this session."""
        server = getattr(self, "_phone_camera_qr_server", None)
        info = getattr(server, "info", None) if server is not None else None
        url = getattr(info, "url", "") if info is not None else ""
        device = self._phone_connected_label
        if device and url:
            return f"Paired — {device} — {url}"
        if device:
            return f"Paired — {device}"
        if url:
            return f"Paired — {url}"
        return "Paired"

    def _refresh_phone_status_label(self) -> None:
        """Re-render the QR status label. Safe to call from anywhere on
        the GUI thread; no-op if the label hasn't been built yet (e.g.,
        the user is on the home page and hasn't opened Settings)."""
        label_widget = getattr(self, "phone_camera_qr_status_label", None)
        if label_widget is None:
            return
        if not bool(getattr(self.config, "phone_camera_qr_paired", False)):
            return
        label_widget.setText(self._phone_paired_status_text())

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
        # Rebuild camera dropdown to drop the now-stale "Phone Camera
        # (QR)" entry and snap selection back to whatever local
        # preference was saved.
        self._rebuild_camera_combo()
        self._refresh_camera_combo_selection(self.config.preferred_camera_index)
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

        Routes through start_engine(skip_tutorial_prompt=True) so the
        full hot-swap path (signal-disconnect-before-stop, viewer
        continuity, proper worker replacement) is used. The previous
        implementation called worker.stop() + worker.start() on the
        same instance, which fired running_state_changed(False) and
        triggered _cleanup_thread_if_stopped, blanking the mini and
        live viewers to 'Press START to begin' idle text mid-swap.
        """
        worker = getattr(self, "_worker", None)
        if worker is None:
            return
        try:
            self.start_engine(skip_tutorial_prompt=True)
        except Exception:
            # Fall back to the old behavior if anything goes wrong.
            stop_fn = getattr(worker, "stop", None)
            start_fn = getattr(worker, "start", None)
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

    def _refresh_lite_mode_button_label(self) -> None:
        if not hasattr(self, "lite_mode_button"):
            return
        on = bool(getattr(self.config, "lite_mode", False))
        self.lite_mode_button.setText("Lite Mode: ON" if on else "Lite Mode")
        self.lite_mode_button.setChecked(on)

    def _on_lite_mode_button_toggled(self, checked: bool) -> None:
        self.config.lite_mode = bool(checked)
        save_config(self.config)
        self._refresh_lite_mode_button_label()
        worker = getattr(self, "_worker", None)
        if worker is not None and hasattr(worker, "set_lite_mode"):
            worker.set_lite_mode(self.config.lite_mode)
        # Push the new state into the live + mini viewers so the
        # blue "Lite" badge flips immediately, even before the next
        # frame from the worker arrives.
        for viewer_attr in ("live_view_window", "mini_live_viewer"):
            viewer = getattr(self, viewer_attr, None)
            if viewer is not None and hasattr(viewer, "set_lite_mode_active"):
                try:
                    viewer.set_lite_mode_active(self.config.lite_mode)
                except Exception:
                    pass
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText(
                "Last action: Lite Mode on" if self.config.lite_mode else "Last action: Lite Mode off"
            )

    def _refresh_gpu_mode_button_label(self) -> None:
        if not hasattr(self, "gpu_mode_button"):
            return
        on = bool(getattr(self.config, "gpu_mode", False))
        self.gpu_mode_button.setText("GPU Mode: ON" if on else "GPU Mode")
        self.gpu_mode_button.setChecked(on)

    def _on_gpu_mode_button_toggled(self, checked: bool) -> None:
        self.config.gpu_mode = bool(checked)
        save_config(self.config)
        self._refresh_gpu_mode_button_label()
        worker = getattr(self, "_worker", None)
        if worker is not None and hasattr(worker, "set_gpu_mode"):
            try:
                worker.set_gpu_mode(self.config.gpu_mode)
            except Exception:
                pass
        if hasattr(self, "last_action_label"):
            self.last_action_label.setText(
                "Last action: GPU Mode on" if self.config.gpu_mode else "Last action: GPU Mode off"
            )


    def _build_microphone_panel(self) -> QWidget:
        panel, layout = self._make_content_panel(
            "Microphone",
            "Pick the microphone Touchless uses for voice commands and dictation. Auto-select uses the system default.",
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
            "Choose from the list of microphones connected to your device. "
            "Auto-select uses whichever input is the system default."
        )
        note.setObjectName("cameraNote")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        # ============================================================
        # PHONE MICROPHONE (QR)
        # ============================================================
        box_layout.addWidget(_section_header("Phone Microphone (QR)"))

        phone_mic_note = QLabel(
            "Pair your phone via the QR button below, then tick the box to route "
            "its mic into Touchless. Phone mics are usually cleaner than laptop mics."
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
            "Pick a microphone from the list above and click Save to remember it. "
            "Use Auto-Select to let Touchless use the system default each launch."
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
            "Click Start Mic Test to watch the volume bar move while you talk. "
            "Click Stop, then Playback to hear what was captured. "
            "The Gain slider boosts voice command audio; dictation reads the system "
            "input level directly."
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
        self._install_scroll_wheel_forwarder(scroll)
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
            "Part 6: hold left-hand 'one' to start the voice listener, then speak a command. The tutorial confirms each phrase before advancing — a quick check that your microphone is wired up and the listener trigger feels right.",
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

    @staticmethod
    def _builtin_release_history() -> list:
        # Built-in fallback list. Used to backfill any version not
        # present in the GitHub release feed (older releases that
        # were never published as GitHub Releases, or releases
        # published without a body) and as a complete substitute
        # when GitHub itself can't be reached. Sorted newest-first
        # in the dataclass list — the merge step below preserves
        # that order for any version the GitHub fetch didn't cover.
        from ..updater import ReleaseHistoryEntry
        from ... import __version__ as RUNNING_VERSION

        def make(version: str, published: str, body: str) -> "ReleaseHistoryEntry":
            return ReleaseHistoryEntry(
                version=version,
                body=body.strip(),
                published_at=published,
                html_url=f"https://github.com/markovk-tini/Touchless/releases/tag/v{version}",
                is_current=(version == RUNNING_VERSION),
            )

        return [
            make("1.0.9a", "2026-05-03", """
**Patch release: code signing, custom gestures, and license switch.**

Code signing
- **Every installer + bundled exe is now signed** under "Konstantin Markov" via Azure Trusted Signing. Cert chains to Microsoft's publicly-trusted root, so Windows Defender and most antivirus engines accept the install without flagging it. SmartScreen reputation will build over the first few weeks of typical download volume.

Custom gestures
- **Handedness-aware**: every saved gesture remembers whether you trained it with your left or right hand, and only fires for that hand. Live overlay shows the bound hand on each card.
- **Live banner during use**: when you hold a custom gesture pose, its name appears over the matching hand exactly like a built-in gesture.
- **Two-hand support**: left-bound gestures fire even when both hands are visible (the runner picks the matching hand instead of always the primary).
- **Better thumb tracking**: thumb curl detection now stays accurate when you tilt your wrist back during a fist — fixes a long-standing under-detection.
- **Pose conflict checks** are simpler: if you record a pose that already exists, you get a clear "this pose already exists as X" message with an Override option that swaps the new gesture in cleanly (instead of leaving two same-shape gestures fighting in live use).
- **Built-in conflict matrix** rebuilt against the real recognizer outputs (Volume pose, Wheel pose, OK sign, Mute, One/Two/Three/Four, Fist) with per-hand action mapping.
- **Wizard polish**: Enter no longer accidentally closes the survey window; only Escape closes it, and Enter activates Start when the form is fully valid.
- **Recording UI**: live "Hand: Left/Right" badge during capture so you can see what MediaPipe is detecting before you save.

License
- **Switched from GPL v3 to FSL-1.1-Apache-2.0** (Functional Source License). Source stays public for audit/learning, mandatory attribution stays, but commercial-fork protection is added for the next 2 years; the license auto-converts to Apache 2.0 after that.

<!-- full-installer-url: https://hgr-downloads.touchless.app/windows/v1.0.9a/Touchless_Installer.exe -->
<!-- full-installer-size: 0 -->
"""),
            make("1.0.9", "2026-05-01", """
**Major release: GPU acceleration, polished mouse mode, and big quality-of-life pass.**

GPU & performance
- **GPU Mode (real)**: hand tracking now runs through ONNX Runtime + DirectML on any DX12 GPU (NVIDIA / AMD / Intel). Falls back to MediaPipe CPU automatically if the GPU path isn't reachable. Toggle in Settings -> Camera.
- **Decoupled display pipeline**: live camera frames now paint at camera FPS independently of inference, so the camera feed stays smooth even when the engine is busy.
- **Camera-lag eliminated** during heavy events. Opening Chrome, Spotify, or any GPU-heavy app no longer leaves the camera feed seconds behind real motion. Dual-stage staleness detection drops backlog within a couple frames.
- **Wheel paint cache** (Spotify / Chrome / YouTube / Drawing / Utility wheels): 4.1× faster paint, ~5 ms / frame freed on the main UI thread.
- **Hand-landmark drawing batched** (single drawLines / drawPoints per frame instead of 84 separate calls).

Drawing
- **Pen-lift** detection rewritten: open thumb for 0.2 s lifts the pen reliably; tilt + rotation no longer mis-fire.
- **Stroke smoothing** via quadratic Bezier through midpoints — strokes look like ink, not connected dots.
- **Velocity-adaptive cursor smoothing** — the index-finger cursor stays put when your hand is still, tracks fast strokes responsively.
- **Shape mode resets** when you exit drawing mode and on app restart (color, thickness, eraser kind still persist as you'd expect).
- **Swipe-right to clear** now fires reliably from the draw-grace window, not only from hover.

Mouse mode
- **Red control-area box** is back, with monitor layout drawn proportionally inside it. The cursor dot inside the box mirrors your actual mouse position across multi-monitor setups.
- **Cursor smoothing** retuned — no more rubber-banding when landing on small targets.
- **Mouse Mode: On/Off pill** confirms toggles.
- **Tutorial Part 5** now shows the same red box and cursor mapping as the live app.

Gesture detection
- **Volume pose** strict gate: requires direct fingertip-to-fingertip closeness so a peace sign / two with fingers apart no longer triggers volume control.
- **Live-view bbox per hand**: red default, green when that hand has a recognized gesture, with handedness + gesture name in a banner. Both hands are first-class — either can drive its own active state.
- **Skeleton + larger round joints** for cleaner readability.

Tutorial
- **Part 6 (Voice Command)** added to the description.
- Per-finger instructions for every static pose (which fingers extend, which curl, palm orientation, hold duration).
- Tutorial popup uses the Touchless title-bar color, with the app logo, and Yes/No buttons reordered with Yes as the brighter primary action.

Settings
- **Rich search**: type a gesture name, voice command, or panel keyword and the dropdown shows matching entries. Click to open the right tab, expand the right collapsible, and scroll the entry into view.
- **Search clear X** uses your accent color and the search resets when you leave Settings.
- **Wheel scrolling fixed** in Camera and Microphone panels (combo / spinbox / slider widgets no longer eat scroll events).
- **Updates panel** falls back to a built-in release-history list if GitHub can't be reached.
- **Mini live viewer** now stays reliably on top, including across full-screen window transitions.

Admin elevation
- Touchless launches with administrator elevation by default starting in 1.0.9. **Why**: clip recording uses Windows screen capture, which can't see frames from games running at higher integrity levels without admin. The first launch after this update will show a UAC prompt — click Yes. Per-user install path and the auto-updater both still work.

<!-- full-installer-url: https://touchless.example.com/v1.0.9/Touchless_Installer.exe -->
<!-- full-installer-size: 0 -->
"""),
            make("1.0.8", "2026-04-15", """
- **Lite Mode** end-to-end: manual switch to MediaPipe model_complexity=0 for ~2.5x faster CPU inference at the same gesture quality.
- **GPU Mode foundation**: tasks_runtime adapter + harder runtime probe + .task asset bundling so the GPU path can engage on first install.
- **Spotify launcher** now resolves the Microsoft Store install correctly (URI handler tried first), and the auto-launch policy waits for a real client window before declaring success.
- **Touchless-themed Save popup** for camera + microphone selection: matching title-bar color, surface background, and accent buttons.
- **Updates panel** got a "skip this version" memory and a "Launching {x}..." overlay during install.
- Numerous **clip-recording fixes**: off-thread export, processing pill never gets stuck, save prompt always lands on the GUI thread.
- **Control Guide** rename (formerly Gesture Guide) plus a dedicated Voice Commands section.
- **Drawing**: pen-lift detection rewritten with a 0.2s thumb-open hold; tilt + rotation tolerance; quadratic-Bezier-through-midpoints stroke smoothing.
"""),
            make("1.0.7", "2026-03-09", """
- **Phone toast notifications** when the PC detects a gesture or voice command, so you can confirm input was received without looking at the laptop.
- **Spotify** play/pause/repeat/shuffle now handle non-JSON 200 responses (Spotify Web API occasionally returns empty bodies).
- **Voice listener** trigger works one-handed again, with a short stability gate so ambiguous Left labels don't fire spuriously.
- **Camera hot-swap** continuity: swapping cameras via Save no longer blanks the mini viewer; the live frame stream stays on-screen across the swap.
- **Phone QR toggle** uses the same hot-swap path as Save Camera, so flipping between local and phone camera is seamless.
- Verbose Spotify error logs to make device-routing problems easier to diagnose from the user's side.
"""),
            make("1.0.6", "2026-02-12", """
- **Phone camera over Wi-Fi**: scan a QR code, the phone streams video to Touchless via embedded HTTPS, with persistent pairing and a "Use phone camera" toggle.
- **Phone microphone**: stream phone audio for voice commands and dictation via /audio POST. AudioWorklet-side boost + user-gain support for quiet phones.
- **In-app auto-updater** matures into a hybrid release model: GitHub for metadata, Cloudflare for the full installer payload. Per-user install path means no UAC prompt on update.
- **Quality of life**: Settings -> Camera dropdown shows real device names, scrollable Microphone panel, Touchless-themed dialogs across the board.
- **Single-instance lock** (clicking the desktop shortcut while running just refocuses the existing window).
- Updates panel landed: live version, manual check, release history list.
- Several phone-mic stability fixes: clipping, end-of-speech detection, recording cutoff, save-mic responsiveness.
- LICENSE switched to GPL v3.
"""),
            make("1.0.1", "2025-12-20", """
- **In-app auto-updater** (initial cut). Touchless can now check GitHub for newer releases on launch and install them with a single click — no separate download step.
- Foundation work for the Updates panel: version display, manual "Check for Updates" button, dialog wired to the silent installer.
"""),
            make("1.0.0", "2025-12-01", """
- **Initial Touchless release.**
- Real-time hand gesture recognition via MediaPipe Hands.
- Spotify control (play / pause / next / previous / shuffle / repeat) over the Web API.
- Chrome control (back / forward / refresh / new-tab / close-tab / pin / mute) via window-level keyboard automation.
- System volume + mute via the Windows Audio Session API.
- Voice listener with whisper.cpp-backed dictation.
- Mouse-mode pointer control + click via gestures.
- On-screen drawing canvas overlay.
- Six-part guided tutorial.
"""),
        ]

    def _merge_with_builtin_release_history(self, github_entries: list) -> list:
        """Augment the GitHub-fetched list with built-in entries for
        any version GitHub doesn't have (or has but with empty
        notes), and refresh is_current on every entry against the
        currently-running version. Result is sorted newest-first by
        published_at, with GitHub-sourced entries preferred when
        both have the same version + non-empty body."""
        from ..updater import ReleaseHistoryEntry
        from ... import __version__ as RUNNING_VERSION

        merged: dict[str, "ReleaseHistoryEntry"] = {}
        for entry in github_entries:
            try:
                version = entry.version
            except Exception:
                continue
            merged[version] = entry
        for builtin in self._builtin_release_history():
            existing = merged.get(builtin.version)
            if existing is None or not (existing.body or "").strip():
                merged[builtin.version] = builtin
        # Re-stamp is_current — GitHub data may be stale relative
        # to a freshly-installed build.
        results = []
        for entry in merged.values():
            if entry.is_current != (entry.version == RUNNING_VERSION):
                entry = ReleaseHistoryEntry(
                    version=entry.version,
                    body=entry.body,
                    published_at=entry.published_at,
                    html_url=entry.html_url,
                    is_current=(entry.version == RUNNING_VERSION),
                )
            results.append(entry)
        # Sort newest-first by published_at; entries without dates
        # fall to the bottom.
        results.sort(key=lambda e: (e.published_at or ""), reverse=True)
        return results

    def _on_updates_history_loaded(self, entries: list) -> None:
        self._updates_history_loaded = True
        self._clear_updates_history_widgets()
        merged = self._merge_with_builtin_release_history(entries or [])
        if not merged:
            empty = QLabel("No releases published yet.")
            empty.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 12px; padding: 8px;")
            self._updates_history_layout.insertWidget(0, empty)
            return
        for entry in merged:
            self._updates_history_layout.insertWidget(
                self._updates_history_layout.count() - 1,
                self._build_release_entry_widget(entry),
            )

    def _on_updates_history_failed(self, reason: str) -> None:
        # GitHub unreachable — fall back to the built-in history so
        # the user still has something useful (offline machine, GH
        # outage, network blocked). Show a small dim note above so
        # they know the list isn't live.
        self._updates_history_loaded = False
        self._clear_updates_history_widgets()
        builtin = self._merge_with_builtin_release_history([])
        if builtin:
            note = QLabel(
                f"Couldn't reach GitHub ({reason}) — showing the built-in release history instead."
            )
            note.setWordWrap(True)
            note.setStyleSheet("color: rgba(255,200,140,0.80); font-size: 11px; padding: 6px 8px;")
            self._updates_history_layout.insertWidget(0, note)
            for entry in builtin:
                self._updates_history_layout.insertWidget(
                    self._updates_history_layout.count() - 1,
                    self._build_release_entry_widget(entry),
                )
        else:
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
        # Show more of each release at a glance — the previous
        # 180px cap clipped most release bodies after about two
        # bullet points. 360px fits ~10-12 lines comfortably; long
        # bodies still scroll inside the QTextBrowser, but most
        # users will see the whole changelog without needing to.
        # The minimum keeps short releases from looking cramped
        # next to fuller ones.
        notes.setMinimumHeight(140)
        notes.setMaximumHeight(360)
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
        # Clear any active settings search so re-entering Settings
        # starts on the default view rather than mid-search. The
        # textChanged signal restores tab visibility and hides the
        # results dropdown.
        try:
            if hasattr(self, "_settings_search_input"):
                self._settings_search_input.clear()
        except Exception:
            pass
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
        # Refresh the Gesture Binds poses list each time the section is
        # shown so custom gestures recorded during this session appear
        # without requiring a restart.
        if index == SECTION_GESTURE_BINDS:
            # Lag-fix: only rebuild the table + poses list when the
            # custom-gesture registry has actually changed since we
            # last drew the panel. The earlier unconditional rebuild
            # added a few hundred ms of grid allocation + disk reads
            # on every navigation into Gesture Binds, which felt slow.
            if self._gesture_binds_registry_changed_since_last_paint():
                try:
                    self._populate_gesture_binds_table()
                except Exception:
                    pass
                try:
                    self._refresh_gesture_binds_poses_list()
                except Exception:
                    pass
        else:
            # Cancel any pending rebind when navigating away.
            if getattr(self, "_gesture_binds_pending_action", None):
                try:
                    self._clear_gesture_bind_pending()
                except Exception:
                    pass
            try:
                self._hide_gesture_pose_preview()
            except Exception:
                pass
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
        # Re-render the settings-search clear-X icon in the latest
        # accent color (in case the user changed the theme).
        try:
            if hasattr(self, "_settings_search_clear_action"):
                self._settings_search_clear_action.setIcon(
                    self._build_search_clear_icon(self.config.accent_color)
                )
        except Exception:
            pass
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
        QPushButton#actionHistoryExpand {{
            background: transparent;
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 8px;
            color: rgba(229, 246, 255, 0.65);
            padding: 0px 4px;
            font-size: 16px;
            min-width: 28px;
            min-height: 24px;
        }}
        QPushButton#actionHistoryExpand:hover {{
            background: rgba(29, 233, 182, 0.12);
            border: 1px solid rgba(29, 233, 182, 0.45);
            color: {self.config.accent_color};
        }}
        QPushButton#actionHistoryExpand:checked {{
            background: rgba(29, 233, 182, 0.18);
            border: 1px solid {self.config.accent_color};
            color: {self.config.accent_color};
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
        QPushButton:disabled {{
            /* Visibly inert state for any disabled QPushButton. The
               Start / End buttons rely on this so the user can see
               which one is currently active: when the engine is
               running, START is greyed; when stopped, END is greyed.
               Same rule applies anywhere else setEnabled(False) is
               used (Save Camera while no selection, Update While
               applying, etc.). */
            background-color: rgba(255, 255, 255, 0.04);
            color: rgba(229, 246, 255, 0.30);
            border: 1px solid rgba(255, 255, 255, 0.08);
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

    # Special data value the camera_combo carries for the "Phone
    # Camera (QR)" entry. Any string here that won't collide with an
    # int camera index. The save handler dispatches on type: int =
    # local camera, "phone_qr" = phone QR source, None = auto-select.
    _PHONE_CAMERA_DROPDOWN_VALUE = "phone_qr"

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
        # Phone camera (QR) is treated as just another camera source
        # in this dropdown — only listed once a phone has been paired
        # via the Connect Phone (QR) button. Selecting it and clicking
        # Save sets phone_camera_qr_active=True; selecting a local
        # device or Auto-select sets it back to False. This replaces
        # the older "Use phone camera (QR) as source" checkbox so
        # there's only one canonical "which camera am I using" control.
        if bool(getattr(self.config, "phone_camera_qr_paired", False)):
            self.camera_combo.addItem("Phone Camera (QR)", self._PHONE_CAMERA_DROPDOWN_VALUE)
        # Honor an active phone selection when rebuilding (e.g. on
        # combo refresh after the user just paired). If phone is
        # active, select that entry; otherwise show preferred local
        # index (or auto).
        if (
            bool(getattr(self.config, "phone_camera_qr_active", False))
            and bool(getattr(self.config, "phone_camera_qr_paired", False))
        ):
            self._refresh_camera_combo_selection(self._PHONE_CAMERA_DROPDOWN_VALUE)
        else:
            self._refresh_camera_combo_selection(self.config.preferred_camera_index)
        self.camera_combo.blockSignals(False)

    def _refresh_camera_combo_selection(self, camera_index) -> None:
        """Move the combo cursor to the entry whose data matches
        camera_index. Accepts an int local index, the
        _PHONE_CAMERA_DROPDOWN_VALUE sentinel, or None for auto."""
        if not hasattr(self, "camera_combo"):
            return
        if isinstance(camera_index, str) and camera_index == self._PHONE_CAMERA_DROPDOWN_VALUE:
            for i in range(self.camera_combo.count()):
                if self.camera_combo.itemData(i) == self._PHONE_CAMERA_DROPDOWN_VALUE:
                    self.camera_combo.setCurrentIndex(i)
                    return
            self.camera_combo.setCurrentIndex(0)
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
        # Settings → Camera status line, mirrored from the home card.
        if hasattr(self, "camera_page_status"):
            if preferred is not None:
                self.camera_page_status.setText(f"Saved camera: {preferred.display_name}")
            elif self._discovered_cameras:
                names = ", ".join(camera.display_name for camera in self._discovered_cameras)
                self.camera_page_status.setText(f"Detected cameras: {names}")
            else:
                self.camera_page_status.setText("Detected cameras: none")
        # Microphone line on the home card mirrors the camera line:
        # show the saved choice when set, fall back to whichever device
        # auto-select would actually use right now.
        self._refresh_microphone_label()

    @staticmethod
    def _resolve_default_microphone_name() -> str:
        """Best-effort: return the human-readable name of whichever mic
        sounddevice would pick when no input device is specified. Used
        by the home Microphone line in Auto-select mode so the user
        sees a real device name (e.g., "Razer Seiren X") instead of
        a generic "N available — choose in Settings"."""
        try:
            import sounddevice as sd
        except Exception:
            return ""
        try:
            default = sd.default.device
        except Exception:
            default = None
        try:
            input_idx = None
            if isinstance(default, (list, tuple)) and len(default) >= 1:
                input_idx = default[0]
            elif isinstance(default, int):
                input_idx = default
            if input_idx is None or input_idx < 0:
                return ""
            info = sd.query_devices(int(input_idx))
            if isinstance(info, dict):
                name = str(info.get("name", "") or "").strip()
                return name
        except Exception:
            pass
        return ""

    def _refresh_microphone_label(self) -> None:
        """Update the home-page Microphone line. Pulls the active
        choice from config.preferred_microphone_name + the discovered
        list. In Auto-select mode (no preferred name) the label shows
        the actual default device the OS would hand sounddevice, so
        the user sees a real name instead of a count."""
        if not hasattr(self, "microphone_label"):
            return
        # Phone-mic source wins when actively routed.
        if (
            bool(getattr(self.config, "phone_camera_qr_use_mic", False))
            and bool(getattr(self.config, "phone_camera_qr_paired", False))
        ):
            self.microphone_label.setText("Microphone: Phone (QR)")
            return
        preferred = str(getattr(self.config, "preferred_microphone_name", "") or "").strip()
        mics = list(getattr(self, "_discovered_microphones", []) or [])
        if preferred and (not mics or preferred in mics):
            self.microphone_label.setText(f"Microphone: {preferred} (saved)")
            return
        # Auto-select mode (no saved preference). Resolve the device
        # sounddevice would actually open and surface that name.
        default_name = self._resolve_default_microphone_name()
        if default_name:
            self.microphone_label.setText(f"Microphone: {default_name} (auto)")
            return
        if mics:
            self.microphone_label.setText(f"Microphone: {mics[0]} (auto)")
            return
        self.microphone_label.setText("Microphone: none found")

    def _on_action_history_expand_toggled(self, expanded: bool) -> None:
        """Expand Recent Actions to fill whatever vertical room the
        home page has left after the buttons / hero / legend, or
        collapse back to the default ~140 px slot.

        Implementation note: instead of forcing a hard min-height on
        the list, we swap the body layout's stretch factor between
        the Runtime Status card and the bottom spacer. With stretch=1
        on the card, it absorbs all available space; the list (which
        is the only Expanding child of the card) grows with it. The
        bottom-of-window margin and the legend stay reachable because
        the card never goes past the available space — Qt's layout
        respects the window height as a hard cap.
        """
        if not hasattr(self, "action_history_list"):
            return
        for widget in getattr(self, "_action_history_collapsible", []) or []:
            try:
                widget.setVisible(not expanded)
            except Exception:
                pass
        # Drop both height caps so the list is purely layout-driven.
        # The list itself is QSizePolicy.Expanding, and inside the
        # card it's the only widget that wants to grow vertically,
        # so it claims whatever room the card has.
        if expanded:
            self.action_history_list.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
            self.action_history_list.setMinimumHeight(120)
        else:
            self.action_history_list.setMaximumHeight(140)
            self.action_history_list.setMinimumHeight(0)
        # Swap the body layout's stretch factors. body_layout has:
        #   hero, subtitle, button_row, info_card, legend, debug_row,
        #   final addStretch(1)
        # When expanded we want info_card to claim the slack the
        # bottom stretch was holding; when collapsed restore.
        body = getattr(self, "_home_body_layout", None)
        card = getattr(self, "home_status_card", None)
        if body is not None and card is not None:
            try:
                body.setStretchFactor(card, 1 if expanded else 0)
                # Zero out the trailing addStretch so all the available
                # vertical room flows into the card instead of being
                # split 50/50 with the bottom spacer.
                last_idx = body.count() - 1
                body.setStretch(last_idx, 0 if expanded else 1)
            except Exception:
                pass
        if hasattr(self, "action_history_expand_button"):
            self.action_history_expand_button.setText("⤡" if expanded else "⤢")
            self.action_history_expand_button.setToolTip(
                "Collapse Recent Actions" if expanded else "Expand Recent Actions"
            )

    def _build_action_history_legend(self) -> QWidget:
        """Single-row legend widget showing each category dot beside
        its label so users learn what the coloured dots mean. Lives
        directly below the Runtime Status box on the home page."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel as _QLabel, QWidget
        legend = QWidget()
        legend.setObjectName("actionHistoryLegend")
        legend.setAttribute(Qt.WA_StyledBackground, True)
        legend.setStyleSheet(
            "QWidget#actionHistoryLegend { background: transparent; }"
            f" QLabel {{ color: rgba(229, 246, 255, 0.65); background: transparent;"
            "  font-size: 11px; }}"
        )
        row = QHBoxLayout(legend)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(12)
        entries = [
            ("media", "Media"),
            ("audio", "Audio"),
            ("voice", "Voice"),
            ("mouse", "Mouse"),
            ("drawing", "Drawing"),
            ("capture", "Capture"),
            ("other", "Other"),
        ]
        for category, name in entries:
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(5)
            dot = _QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(
                f"background-color: {self._category_dot_color(category)};"
                " border-radius: 4px;"
            )
            cell_layout.addWidget(dot, 0, Qt.AlignVCenter)
            label = _QLabel(name)
            cell_layout.addWidget(label, 0, Qt.AlignVCenter)
            row.addWidget(cell)
        row.addStretch(1)
        return legend

    def _prompt_for_camera_choice(self, cameras: list[CameraInfo], prompt_text: str) -> Optional[tuple[int, bool]]:
        dialog = CameraSelectionDialog(self.config, cameras, prompt_text, self)
        if dialog.exec() != QDialog.Accepted or dialog.selected_camera_index is None:
            return None
        return dialog.selected_camera_index, dialog.remember_choice

    def _resolve_camera_for_start(self, cameras: list[CameraInfo]) -> Optional[int]:
        # Trust a saved preferred_camera_index BEFORE consulting the
        # discovered list. The list_available_cameras() probe (used for
        # the dropdown / start-up scan) is best-effort: it walks
        # indices 0..N and requests one frame each, and on some
        # machines that probe misses cameras the engine then opens
        # cleanly via open_camera_by_index — different backend, faster
        # frame-grab, etc. Symptom reported by users: the tutorial
        # works (it bypasses this validation), the main app silently
        # picks a different camera or shows the chooser. Letting the
        # engine try the saved index first and falling back to the
        # scan list only when there's no saved preference fixes that.
        preferred = self.config.preferred_camera_index
        if preferred is not None:
            return int(preferred)

        if not cameras:
            return None
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
        selected_data = self.camera_combo.currentData()
        # Capture the friendly name from the combo BEFORE we lose
        # easy access to it, so the confirmation popup tells the user
        # exactly which camera is now their saved choice.
        selected_name = ""
        try:
            selected_name = str(self.camera_combo.currentText() or "").strip()
        except Exception:
            selected_name = ""

        # Dispatch on combo data type:
        #   string == _PHONE_CAMERA_DROPDOWN_VALUE → phone QR source
        #   int                                    → local camera index
        #   None                                   → auto-select
        # Setting phone_camera_qr_active here is the new canonical
        # control replacing the old "Use phone camera (QR) as
        # source" checkbox.
        chose_phone_qr = (
            isinstance(selected_data, str)
            and selected_data == self._PHONE_CAMERA_DROPDOWN_VALUE
        )
        if chose_phone_qr:
            self.config.phone_camera_qr_active = True
            # Leave preferred_camera_index unchanged so it can serve
            # as fallback if the phone is turned off / unpaired.
        else:
            self.config.phone_camera_qr_active = False
            self.config.preferred_camera_index = selected_data if isinstance(selected_data, int) else None

        # Cache for confirmation popup wording.
        phone_qr_active = chose_phone_qr and self._current_phone_camera_qr_server() is not None
        phone_url_active = bool(getattr(self.config, "phone_camera_enabled", False)) and bool(str(getattr(self.config, "phone_camera_url", "") or "").strip())

        save_config(self.config)
        self._refresh_camera_labels()
        # Sync the legacy checkbox UI to whatever we just decided so
        # both controls stay coherent until we remove the checkbox
        # in a future cleanup pass.
        if hasattr(self, "use_phone_camera_qr_checkbox"):
            self.use_phone_camera_qr_checkbox.blockSignals(True)
            self.use_phone_camera_qr_checkbox.setChecked(chose_phone_qr)
            self.use_phone_camera_qr_checkbox.blockSignals(False)

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
        elif selected_data is None:
            self.last_action_label.setText("Last action: camera set to auto-select")
            confirmation = (
                "Camera preference saved. Touchless will pick the best available camera at startup."
            )
        else:
            # selected_data here is an int (local camera index).
            label = selected_name if selected_name else f"index {selected_data}"
            self.last_action_label.setText(f"Last action: saved camera {label}")
            confirmation = (
                f"Camera preference saved. Touchless will now use:\n\n{label}"
            )
        if engine_was_running:
            confirmation += "\n\nThe camera is being switched live — gestures may pause for 1-3 seconds while the new camera initializes."
        TouchlessNotice.show_info(self, "Camera Saved", confirmation)

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
        # Plural alias kept for backwards compat with existing callers
        # in the microphone settings flow. Forwards to the single
        # home-card label refresher.
        self._refresh_microphone_label()

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
        TouchlessNotice.show_info(self, "Microphone Saved", confirmation)

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
            phone_qr_paired = (
                bool(getattr(self.config, "phone_camera_qr_active", False))
                and self._current_phone_camera_qr_server() is not None
            )
            if not cameras and not phone_qr_paired:
                # Pure no-camera start. The phone QR path is its own
                # source so a paired phone is enough to start without
                # any local webcam — only fail here when neither is
                # available.
                QMessageBox.warning(self, "Touchless", "No available camera was found.")
                self.status_label.setText("Status: no camera found")
                return

            if cameras:
                selected_camera_index = self._resolve_camera_for_start(cameras)
                if selected_camera_index is None:
                    self.status_label.setText("Status: start cancelled")
                    self.last_action_label.setText("Last action: camera selection cancelled")
                    return
            else:
                # No local cameras but phone QR is paired — let the
                # phone source drive the engine. _open_camera reads
                # camera_index_override=None as 'pick the phone QR
                # source if active', which is what we want here.
                selected_camera_index = None

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
                # NOTE: don't call detach_from_worker on the viewers
                # here — that blanks the display to "Press START to
                # begin..." idle text, which is misleading mid-session.
                # The viewer's attach_to_worker further down handles
                # the disconnect-old + connect-new transition without
                # blanking, keeping the previous frame visible until
                # the new camera produces one.
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

            if phone_qr_active and selected_camera_index is None:
                self.camera_label.setText("Camera: Phone (QR)")
            elif phone_url_active and selected_camera_index is None:
                self.camera_label.setText("Camera: Phone (URL)")
            else:
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


    # ---- Recent Actions row helpers -----------------------------------
    @staticmethod
    def _categorize_action_label(label: str) -> str:
        """Bucket an ActionEvent label into a high-level category so the
        row dot can be colour-coded. Categories are picked to be
        scannable at a glance — was that a media event, a voice
        result, an audio toggle? — rather than perfectly granular."""
        s = (label or "").lower()
        if not s or s == "?":
            return "other"
        if s.startswith(("spotify_", "youtube_", "chrome_")):
            return "media"
        if s.startswith("volume_") or "mute" in s:
            return "audio"
        if (
            s.startswith(("voice_", "dictation"))
            or s in ("voice", "dictation")
        ):
            return "voice"
        if s.startswith(("mouse_",)) or s in ("mouse_mode_on", "mouse_mode_off"):
            return "mouse"
        if s.startswith(("drawing_",)) or "draw" in s:
            return "drawing"
        if (
            s.startswith(("screenshot", "recording", "clip", "screen_"))
            or "screen_record" in s
            or "screenshot" in s
        ):
            return "capture"
        return "other"

    @staticmethod
    def _category_dot_color(category: str) -> str:
        """Hex colour string for the leading dot on each Recent
        Actions row, keyed by category. The neutral 'other' bucket
        uses a subtle grey so unknown / non-categorised actions don't
        scream as much as the named ones."""
        return {
            "media": "#1DE9B6",   # accent green
            "audio": "#FFB347",   # warm orange
            "voice": "#82BBFF",   # soft blue
            "mouse": "#C9A0FF",   # lavender
            "drawing": "#FF7AA2", # pink
            "capture": "#FFD166", # yellow
            "other": "#7E8B97",   # neutral grey
        }.get(category, "#7E8B97")

    @staticmethod
    def _relative_timestamp(now: float, ts: float) -> str:
        """Compose a 'just now' / 'Ns ago' / 'Nm ago' string for the
        right-aligned timestamp on each row. Handles future-stamp
        clock-skew by clamping to 0."""
        try:
            delta = max(0.0, float(now) - float(ts or 0.0))
        except (TypeError, ValueError):
            return ""
        if delta < 5:
            return "just now"
        if delta < 60:
            return f"{int(delta)}s ago"
        if delta < 3600:
            return f"{int(delta // 60)}m ago"
        if delta < 86400:
            return f"{int(delta // 3600)}h ago"
        return f"{int(delta // 86400)}d ago"

    def _build_action_history_row(
        self,
        event,
        count: int,
        timestamp: float,
    ) -> "tuple[QWidget, QLabel]":
        """Render a single Recent Actions row.

        Layout: <dot> <prefix> <text>  <stretch>  <ts>
        Returns (row_widget, timestamp_label) so the periodic refresh
        timer can update the timestamp in place without rebuilding
        the whole row."""
        label = str(getattr(event, "label", "") or "")
        display = (
            str(getattr(event, "display_text", "") or "")
            or label
            or "?"
        )
        if getattr(event, "is_undo", False):
            prefix = "↶ "
        elif getattr(event, "undoable", False):
            prefix = "• "
        else:
            prefix = "· "
        if count > 1:
            display = f"{display}  × {count}"

        category = self._categorize_action_label(label)
        dot_color = self._category_dot_color(category)

        from PySide6.QtWidgets import QHBoxLayout, QLabel as _QLabel, QWidget
        row = QWidget()
        row.setObjectName("actionHistoryRow")
        row.setAttribute(Qt.WA_TranslucentBackground, True)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        dot = _QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(
            f"background-color: {dot_color}; border-radius: 5px;"
        )
        layout.addWidget(dot, 0, Qt.AlignVCenter)

        text_label = _QLabel(f"{prefix}{display}")
        text_label.setStyleSheet(
            f"color: {self.config.text_color or '#E5F6FF'}; background: transparent;"
        )
        text_label.setWordWrap(False)
        layout.addWidget(text_label, 1, Qt.AlignVCenter)

        ts_label = _QLabel(self._relative_timestamp(time.time(), timestamp))
        ts_label.setObjectName("actionHistoryTimestamp")
        ts_label.setStyleSheet(
            "color: rgba(229, 246, 255, 0.55); background: transparent; font-size: 11px;"
        )
        ts_label.setProperty("eventTimestamp", float(timestamp or 0.0))
        layout.addWidget(ts_label, 0, Qt.AlignVCenter)

        return row, ts_label

    def _on_action_history_changed(self, events: object) -> None:
        if not hasattr(self, "action_history_list"):
            return
        try:
            event_list = list(events or [])
        except TypeError:
            event_list = []
        self.action_history_list.clear()
        # Track timestamp QLabels so the periodic refresh timer can
        # update only the relative-time text without rebuilding the
        # full row each tick.
        self._action_history_timestamp_labels = []
        any_undoable = False

        # Most-recent-first, capped at 12 raw events so a long burst
        # doesn't push useful older entries off-screen before they're
        # collapsed.
        recent = list(reversed(event_list[-12:]))

        # Collapse consecutive identical labels into "× N" rows.
        # Identity = same label string; display text variants for the
        # same label still group cleanly. Newest event in the run keeps
        # its timestamp / undoable flag; the count reflects the run
        # length.
        collapsed = []
        idx = 0
        while idx < len(recent):
            head = recent[idx]
            head_label = str(getattr(head, "label", "") or "")
            run_count = 1
            j = idx + 1
            while j < len(recent):
                nxt = recent[j]
                if str(getattr(nxt, "label", "") or "") != head_label:
                    break
                if getattr(nxt, "is_undo", False) != getattr(head, "is_undo", False):
                    break
                run_count += 1
                j += 1
            collapsed.append((head, run_count, float(getattr(head, "timestamp", 0.0) or 0.0)))
            idx = j

        for event, count, ts in collapsed:
            if getattr(event, "undoable", False):
                any_undoable = True
            row_widget, ts_label = self._build_action_history_row(event, count, ts)
            item = QListWidgetItem()
            item.setSizeHint(row_widget.sizeHint())
            self.action_history_list.addItem(item)
            self.action_history_list.setItemWidget(item, row_widget)
            self._action_history_timestamp_labels.append(ts_label)

        if hasattr(self, "undo_action_button"):
            self.undo_action_button.setEnabled(any_undoable)

        # Lazy-init the relative-time refresh timer the first time the
        # history is populated. Updates every 3 s — fast enough that
        # "just now" → "5s ago" lands without obvious lag, slow enough
        # that we don't burn cycles when the home page is foregrounded
        # for long periods.
        if not getattr(self, "_action_history_timestamp_timer", None):
            timer = QTimer(self)
            timer.setInterval(3000)
            timer.timeout.connect(self._refresh_action_history_timestamps)
            timer.start()
            self._action_history_timestamp_timer = timer

    def _refresh_action_history_timestamps(self) -> None:
        """Update only the relative-time labels on each row. Cheap —
        a few QLabel.setText calls on existing widgets."""
        labels = getattr(self, "_action_history_timestamp_labels", None) or []
        if not labels:
            return
        now = time.time()
        for label in labels:
            try:
                ts = float(label.property("eventTimestamp") or 0.0)
            except (TypeError, ValueError):
                continue
            label.setText(self._relative_timestamp(now, ts))

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
        # Helper: pop the bottom-center "Saved in: <path>" pill on
        # any save outcome that ended with a real file on disk.
        # Discards skip it (no file to point at).
        def _show_saved_pill(final_path: Path) -> None:
            try:
                self.saved_location_overlay.show_saved(
                    f"Saved in: {final_path}", total_ms=3000, fade_ms=600
                )
            except Exception:
                pass

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
                _show_saved_pill(moved_path)
                return
        if source_path.exists():
            self.last_action_label.setText(f"Last action: saved {label} to {source_path}")
            _show_saved_pill(source_path)

    def _save_drawing_snapshot(self) -> None:
        # Brief processing-pill flash during the (typically fast)
        # PNG write so the user sees the same UX cadence
        # as the heavier clip / recording saves.
        try:
            self.processing_overlay.show_processing("Processing drawing")
        except Exception:
            pass
        try:
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
        finally:
            try:
                self.processing_overlay.hide_processing()
            except Exception:
                pass

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
        try:
            self.processing_overlay.show_processing("Processing screenshot")
        except Exception:
            pass
        try:
            path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(None))
            if path is not None:
                self.last_action_label.setText(f"Last action: saved screenshot to {path}")
                self._queue_post_action_save_prompt("screenshots", path)
            else:
                self.last_action_label.setText("Last action: could not save screenshot")
        finally:
            try:
                self.processing_overlay.hide_processing()
            except Exception:
                pass
    def _save_custom_region_screenshot(self, region: QRect) -> None:
        try:
            self.processing_overlay.show_processing("Processing screenshot")
        except Exception:
            pass
        try:
            path = self._save_screenshot_pixmap(self._grab_global_region_pixmap(region))
            if path is not None:
                self.last_action_label.setText(f"Last action: saved custom screenshot to {path}")
                self._queue_post_action_save_prompt("screenshots", path)
            else:
                self.last_action_label.setText("Last action: could not save custom screenshot")
        finally:
            try:
                self.processing_overlay.hide_processing()
            except Exception:
                pass
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
    def _run_clip_export_ffmpeg(
        self, duration_seconds: int, target_region: QRect
    ) -> tuple[bool, Path | None, float]:
        """Thread-safe variant of _export_recent_clip_ffmpeg that
        does NOT touch any QWidget or worker state — used from the
        background clip-export thread. Returns
        (success, output_path, actual_seconds_written). The GUI
        callback handles label/save-prompt updates."""
        was_active = (
            self._clip_cache_backend == "ffmpeg" and self._clip_cache_process is not None
        )
        if was_active:
            self._stop_clip_cache_ffmpeg(delete_files=False)
        try:
            entries = self._parse_ffmpeg_clip_manifest()
            if not entries:
                return (False, None, 0.0)
            selected: list[dict] = []
            covered = 0.0
            for entry in reversed(entries):
                segment_seconds = max(
                    1e-3,
                    float(entry.get("end_time", 0.0))
                    - float(entry.get("start_time", 0.0)),
                )
                selected.append(entry)
                covered += segment_seconds
                if covered >= float(duration_seconds):
                    break
            if not selected:
                return (False, None, 0.0)
            selected.reverse()
            total_duration = sum(
                max(
                    1e-3,
                    float(entry.get("end_time", 0.0))
                    - float(entry.get("start_time", 0.0)),
                )
                for entry in selected
            )
            start_trim = max(0.0, total_duration - float(duration_seconds))
            output_path = self._clip_output_specs(duration_seconds)[0][0]
            capture_region = (
                QRect(self._clip_cache_region)
                if self._clip_cache_region is not None
                else QRect(self._screens_union_geometry())
            )
            inputs: list[str] = []
            for entry in selected:
                inputs.extend(["-i", str(Path(entry["path"]).resolve())])
            n = len(selected)
            concat_in = "".join(f"[{i}:v]" for i in range(n))
            filter_chain = [f"{concat_in}concat=n={n}:v=1:a=0"]
            crop_filter = self._clip_crop_filter(capture_region, target_region)
            if crop_filter:
                filter_chain.append(crop_filter)
            filter_chain.append(
                f"trim=start={start_trim:.3f}:duration={float(duration_seconds):.3f}"
            )
            filter_chain.append("setpts=PTS-STARTPTS")
            filter_complex = ",".join(filter_chain) + "[vout]"
            command = [
                self._ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-an",
                *self._ffmpeg_encoder_args(
                    purpose="clip_export", fps=self._clip_cache_fps
                ),
                str(output_path),
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if (
                completed.returncode == 0
                and output_path.exists()
                and output_path.stat().st_size > 1024
            ):
                actual_seconds = min(float(duration_seconds), max(0.0, total_duration))
                return (True, output_path, actual_seconds)
            return (False, None, 0.0)
        finally:
            self._cleanup_ffmpeg_clip_cache_files()
            if (
                was_active
                and self._worker is not None
                and getattr(self._worker, "is_running", False)
            ):
                # subprocess.Popen + attribute writes only — no
                # QWidget / QObject calls, so safe from a thread.
                self._start_clip_cache_ffmpeg()

    def _run_clip_export_opencv(
        self, duration_seconds: int, target_region: QRect
    ) -> tuple[bool, Path | None, float]:
        """Thread-safe variant of _export_recent_clip_opencv. Same
        contract as _run_clip_export_ffmpeg.

        Note: this path uses cv2.VideoCapture for reading cached
        segments, which opens a new fd per file inside the worker
        thread — fine. The output writer is also opened+closed
        inside this method on the worker thread."""
        if self._clip_cache_segment_writer is not None:
            # Caller path may have an in-progress writer; rotate
            # to flush it. _rotate_clip_cache_segment is invoked
            # by the cv2-based capture timer normally; calling it
            # from a worker thread is OK because it's just file
            # I/O + a writer release.
            self._rotate_clip_cache_segment()
        segments = [
            meta
            for meta in self._clip_cache_segments
            if Path(meta.get("path")).exists()
            and int(meta.get("frame_count", 0) or 0) > 0
        ]
        if not segments:
            return (False, None, 0.0)
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
            return (False, None, 0.0)
        selected_segments.reverse()
        estimated_frames = sum(
            max(0, int(meta.get("frame_count", 0) or 0) - int(skip))
            for meta, skip in selected_segments
        )
        output_fps = max(
            1.0,
            min(30.0, float(estimated_frames) / max(1e-3, covered_seconds)),
        )
        output_writer = None
        output_path: Path | None = None
        for candidate_path, codec_name in self._clip_output_specs(duration_seconds):
            candidate_writer = self._open_video_writer(
                candidate_path,
                codec_name,
                target_region.width(),
                target_region.height(),
                output_fps,
            )
            if candidate_writer is not None:
                output_writer = candidate_writer
                output_path = candidate_path
                break
        if output_writer is None or output_path is None:
            return (False, None, 0.0)
        written = 0
        try:
            for meta, skip_frames in selected_segments:
                path = Path(meta.get("path"))
                capture_region = (
                    meta.get("region")
                    or self._clip_cache_region
                    or self._screens_union_geometry()
                )
                capture_region = QRect(capture_region)
                cap = cv2.VideoCapture(str(path))
                local_index = 0
                try:
                    while True:
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            break
                        if local_index >= int(skip_frames):
                            cropped = self._crop_cached_frame_to_region(
                                frame, capture_region, target_region
                            )
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
        if (
            written > 0
            and output_path.exists()
            and output_path.stat().st_size > 1024
        ):
            actual_seconds = written / float(output_fps) if output_fps > 0 else 0.0
            return (True, output_path, actual_seconds)
        return (False, None, 0.0)

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
            output_path = self._clip_output_specs(duration_seconds)[0][0]
            capture_region = QRect(self._clip_cache_region) if self._clip_cache_region is not None else QRect(self._screens_union_geometry())
            # Use the concat *filter* instead of the concat demuxer.
            # Segments were recorded with -reset_timestamps 1, which
            # makes each .mkv start at PTS 0; the concat demuxer's
            # PTS-chaining logic was giving truncated output (e.g.,
            # 60s clip → 36s) when a few segments had recently
            # wrapped, because some segments were treated as
            # overlapping the timeline of earlier ones. The concat
            # filter joins frame-by-frame and produces a
            # guaranteed-monotonic PTS, so trim=start=X:duration=N
            # then keeps exactly N seconds without surprises. Each
            # input file has the same resolution/codec/fps (we
            # recorded them with one ffmpeg pass), which is the
            # requirement for the concat filter.
            inputs: list[str] = []
            for entry in selected:
                inputs.extend(["-i", str(Path(entry["path"]).resolve())])
            n = len(selected)
            concat_in = "".join(f"[{i}:v]" for i in range(n))
            filter_chain = [f"{concat_in}concat=n={n}:v=1:a=0"]
            crop_filter = self._clip_crop_filter(capture_region, target_region)
            if crop_filter:
                filter_chain.append(crop_filter)
            filter_chain.append(
                f"trim=start={start_trim:.3f}:duration={float(duration_seconds):.3f}"
            )
            filter_chain.append("setpts=PTS-STARTPTS")
            filter_complex = ",".join(filter_chain) + "[vout]"
            command = [
                self._ffmpeg_path,
                "-hide_banner", "-loglevel", "error", "-y",
                *inputs,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-an",
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

        def _kickoff_export(region: QRect) -> None:
            # Defer to the next event-loop turn so the dialog has a
            # chance to actually close + the picker repaint settles
            # BEFORE we display the processing overlay and start the
            # ffmpeg subprocess. Without the QTimer.singleShot(0)
            # bounce, the dialog's close() call is queued but doesn't
            # process until after our slot returns — meaning the
            # user would still see the picker overlapping the
            # processing overlay for one frame.
            self._export_clip_async(duration_seconds, QRect(region))

        if len(options) == 1:
            _kickoff_export(QRect(options[0][1]))
            return True

        dialog = CaptureMonitorDialog(self.config, f"clip {duration_seconds} sec", options, self)
        self._capture_monitor_dialog = dialog
        self._capture_monitor_selection_mode = f"clip_{duration_seconds}s"
        self.last_action_label.setText("Last action: choose monitor for clip with your hand")
        self._set_worker_utility_capture_selection_active(True)

        def _on_selected(region: QRect) -> None:
            # Order matters: clear the dialog bookkeeping first so
            # subsequent picker checks see "no dialog open", then
            # bounce through the event loop so the dialog's
            # CaptureMonitorDialog.close() (which fires AFTER the
            # selection_made signal returns from our slot) gets to
            # process its hide before we paint the processing
            # overlay on top.
            self._clear_capture_monitor_dialog_state()
            chosen = QRect(region.normalized())
            QTimer.singleShot(0, lambda r=chosen: _kickoff_export(r))

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

    def _export_clip_async(self, duration_seconds: int, region: QRect) -> None:
        # Off-main-thread clip export. The ffmpeg subprocess for a
        # 60 s concat + crop + trim + encode takes 3-8 s; running
        # it inline used to freeze the UI completely. Now:
        #
        #   1. The picker dialog is closed by the time we get here
        #      (caller used singleShot(0) to defer this).
        #   2. Processing overlay shown immediately.
        #   3. ffmpeg runs on a daemon thread; main thread free.
        #   4. Worker thread NEVER touches QWidgets or worker state
        #      — it stashes its result on `self._clip_export_result`
        #      (a dict capturing success + path + actual_seconds +
        #      error) and bounces a finish callback to GUI thread.
        #   5. GUI callback hides the overlay, updates the label,
        #      and fires the save-location prompt (which involves
        #      starting voice capture — must be main-thread).
        if self._clip_export_thread is not None and self._clip_export_thread.is_alive():
            self.last_action_label.setText("Last action: clip already exporting")
            return
        target = self._normalized_record_region(region)
        if target.isNull() or target.width() <= 1 or target.height() <= 1:
            self.last_action_label.setText("Last action: clip canceled")
            return
        try:
            self.processing_overlay.show_processing(f"Processing {duration_seconds}s clip")
        except Exception:
            pass

        # Reset shared result slot for this run. Worker thread
        # writes here; GUI callback reads.
        self._clip_export_result = {
            "success": False,
            "output_path": None,
            "actual_seconds": 0.0,
            "error": None,
        }

        # Connect the cross-thread bridge signal exactly once. We
        # use a uniqueConnection-style guard so re-entry here
        # doesn't stack duplicate slots if the user fires multiple
        # clip exports across the lifetime of the app.
        if not getattr(self, "_clip_export_signal_wired", False):
            self._clip_export_finished_signal.connect(self._on_clip_export_finished_main_thread)
            self._clip_export_signal_wired = True

        def _runner() -> None:
            try:
                if self._ffmpeg_ready() and self._clip_cache_backend == "ffmpeg":
                    success, output_path, actual_seconds = self._run_clip_export_ffmpeg(
                        duration_seconds, target
                    )
                else:
                    success, output_path, actual_seconds = self._run_clip_export_opencv(
                        duration_seconds, target
                    )
                self._clip_export_result = {
                    "success": bool(success),
                    "output_path": output_path,
                    "actual_seconds": float(actual_seconds or 0.0),
                    "error": None,
                }
            except Exception as exc:
                self._clip_export_result = {
                    "success": False,
                    "output_path": None,
                    "actual_seconds": 0.0,
                    "error": f"{type(exc).__name__}: {exc!s}",
                }
            # Bounce to the GUI thread via Qt signal — works from
            # any thread, doesn't require a local event loop.
            try:
                self._clip_export_finished_signal.emit()
            except Exception:
                pass

        self._clip_export_thread = threading.Thread(
            target=_runner, name="hgr-clip-export", daemon=True
        )
        self._clip_export_thread.start()

    def _on_clip_export_finished_main_thread(self) -> None:
        """GUI-thread completion for the off-thread clip export.
        Hides the processing overlay, updates the action label,
        and (on success) fires the save-location voice prompt."""
        try:
            self.processing_overlay.hide_processing()
        except Exception:
            pass
        result = dict(getattr(self, "_clip_export_result", {}) or {})
        success = bool(result.get("success", False))
        output_path = result.get("output_path")
        actual_seconds = float(result.get("actual_seconds", 0.0) or 0.0)
        error = result.get("error")
        if error:
            self.last_action_label.setText(
                f"Last action: clip export failed ({error})"
            )
            return
        if not success or output_path is None:
            self.last_action_label.setText("Last action: no recent clip available yet")
            return
        self.last_action_label.setText(
            f"Last action: saved {actual_seconds:.1f}s clip to {output_path}"
        )
        # Save-location voice prompt — must run on GUI thread
        # because it starts voice capture (sounddevice + QObject).
        try:
            self._queue_post_action_save_prompt("clips", Path(output_path))
        except Exception:
            pass
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
        # Show the processing pill during ffmpeg / cv2 finalize.
        # ffmpeg finalize can take 0.5-2 s as it writes the trailer
        # and flushes; without the pill the UI looks unresponsive
        # in that window.
        try:
            self.processing_overlay.show_processing("Processing recording")
        except Exception:
            pass
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
            try:
                self.processing_overlay.hide_processing()
            except Exception:
                pass
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
            # A disabled button can't be "hovered" for the styling
            # purposes — its hover glow would be misleading. Treat
            # disabled as not-hovered so any leftover hgrHover from
            # before the disable is cleared on the first sync.
            hovered = (
                button.isVisible()
                and button.isEnabled()
                and button.rect().contains(button.mapFromGlobal(QCursor.pos()))
            )
            pressed = bool(button.isDown())
            if button.property("hgrHover") != hovered:
                button.setProperty("hgrHover", hovered)
            if button.property("hgrPressed") != pressed:
                button.setProperty("hgrPressed", pressed)
            self._refresh_button_hover_visual(button)

        def _sync_all_button_hover_states(self) -> None:
            # Walk every tracked QPushButton and force a re-sync.
            # Cheap (a few dozen buttons, property check + maybe a
            # restyle) and acts as a watchdog for the cases below
            # where Qt doesn't deliver a per-button hover event:
            #   - cursor leaves the main window entirely
            #   - a modal dialog covered a button and stole its
            #     pending HoverLeave, then closed
            #   - a button got setEnabled(False) without an event
            #     reaching it first
            for btn in self.findChildren(QPushButton):
                self._sync_button_visual_state(btn)

        def leaveEvent(self, event):  # noqa: N802
            super().leaveEvent(event)
            # Cursor crossed outside the main window — any sticky
            # hgrHover that didn't get cleared by a per-button
            # HoverLeave (modal dialogs, fast cursor motion, focus
            # transitions) gets resolved here.
            self._sync_all_button_hover_states()

        def changeEvent(self, event):  # noqa: N802
            super().changeEvent(event)
            # Re-sync on activation transitions so a popup closing
            # and re-activating the main window can't leave a
            # button with stale hover styling.
            if event.type() == QEvent.ActivationChange:
                self._sync_all_button_hover_states()

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
                    # EnabledChange covers the start_engine path:
                    # button.setEnabled(False) when a click kicks
                    # off engine startup. Without this we relied on
                    # a stale hover event arriving after the
                    # disable, which often never came.
                    QEvent.EnabledChange,
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
        # Treat disabled / off-screen buttons as not-hovered so any
        # leftover hgrHover from before the disable is cleared on
        # the first sync. Without isEnabled() the start_engine
        # flow (Start click -> setEnabled(False) -> camera picker
        # covers the button) leaves hgrHover stuck True; Qt then
        # doesn't deliver another HoverLeave because the button is
        # now disabled, so the glow stays.
        try:
            on_screen = bool(button.window() and button.window().isVisible())
        except Exception:
            on_screen = True
        hovered = (
            button.isVisible()
            and button.isEnabled()
            and on_screen
            and button.rect().contains(button.mapFromGlobal(QCursor.pos()))
        )
        pressed = bool(button.isDown())
        if button.property("hgrHover") != hovered:
            button.setProperty("hgrHover", hovered)
        if button.property("hgrPressed") != pressed:
            button.setProperty("hgrPressed", pressed)
        self._refresh_button_hover_visual(button)

    def _sync_all_button_hover_states(self) -> None:
        # Walk every tracked QPushButton and force a re-sync.
        # Acts as a watchdog for cases where Qt doesn't deliver
        # a per-button hover event:
        #   - cursor leaves the main window entirely
        #   - a modal dialog covered a button and stole its
        #     pending HoverLeave, then closed
        #   - a button got setEnabled(False) without an event
        #     reaching it first
        #   - the splash sequence shows the window off-screen
        #     briefly (WA_DontShowOnScreen) before the real
        #     show, leaving stale hover from the off-screen pose
        for btn in self.findChildren(QPushButton):
            self._sync_button_visual_state(btn)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # Splash flow shows the window with WA_DontShowOnScreen,
        # paints once, hides, then shows for real. The off-screen
        # show pass can leave a button with hgrHover=True if the
        # cursor's global position happened to fall inside an
        # off-screen-positioned button's rect. Force re-sync once
        # the window is actually visible so any stale True is
        # corrected.
        QTimer.singleShot(0, self._sync_all_button_hover_states)

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        # Cursor crossed outside the main window — any sticky
        # hgrHover that didn't get cleared by a per-button
        # HoverLeave (modal dialogs, fast cursor motion, focus
        # transitions) gets resolved here.
        self._sync_all_button_hover_states()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        # Re-sync on activation transitions so a popup closing
        # and re-activating the main window can't leave a button
        # with stale hover styling.
        if event.type() == QEvent.ActivationChange:
            self._sync_all_button_hover_states()

    def keyPressEvent(self, event):  # noqa: N802
        # Esc cancels an in-progress Gesture Binds rebind without saving
        # any partial change.
        if event.key() == Qt.Key_Escape and getattr(self, "_gesture_binds_pending_action", None):
            self._clear_gesture_bind_pending()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        # Gesture Binds list viewport: hide hover preview when the cursor
        # leaves the list entirely. itemEntered fires on item-to-item moves
        # but never fires when the cursor exits the viewport.
        poses_list = getattr(self, "_gesture_binds_poses_list", None)
        if poses_list is not None and obj is poses_list.viewport():
            if event.type() in (QEvent.Leave, QEvent.HoverLeave):
                self._hide_gesture_pose_preview()
        # Gesture Binds active-gesture buttons: same 2s hover preview as
        # the All Gesture Poses list, keyed off the currently-bound pose
        # for that action (including any unsaved pending change).
        if isinstance(obj, QPushButton) and obj.property("gestureBindActionId"):
            etype = event.type()
            if etype in (QEvent.Enter, QEvent.HoverEnter):
                action_id = obj.property("gestureBindActionId")
                pose_id = self._gesture_binds_pending_changes.get(action_id) \
                    if hasattr(self, "_gesture_binds_pending_changes") else None
                if not pose_id:
                    pose_id = resolve_gesture_binding(self.config, action_id)
                if pose_id:
                    self._start_gesture_pose_hover_timer(pose_id)
            elif etype in (QEvent.Leave, QEvent.HoverLeave):
                self._hide_gesture_pose_preview()
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
                QEvent.Hide,
                # EnabledChange covers the start_engine path:
                # button.setEnabled(False) when a click kicks off
                # engine startup. Without this we relied on a
                # stale hover event arriving after the disable,
                # which often never came on Windows.
                QEvent.EnabledChange,
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
        # Treat disabled / off-screen buttons as not-hovered so any
        # leftover hgrHover from before the disable is cleared on
        # the first sync. Without isEnabled() the start_engine
        # flow (Start click -> setEnabled(False) -> camera picker
        # covers the button) leaves hgrHover stuck True; Qt then
        # doesn't deliver another HoverLeave because the button is
        # now disabled, so the glow stays.
        try:
            on_screen = bool(button.window() and button.window().isVisible())
        except Exception:
            on_screen = True
        hovered = (
            button.isVisible()
            and button.isEnabled()
            and on_screen
            and button.rect().contains(button.mapFromGlobal(QCursor.pos()))
        )
        pressed = bool(button.isDown())
        if button.property("hgrHover") != hovered:
            button.setProperty("hgrHover", hovered)
        if button.property("hgrPressed") != pressed:
            button.setProperty("hgrPressed", pressed)
        self._refresh_button_hover_visual(button)

    def _sync_all_button_hover_states(self) -> None:
        # Walk every tracked QPushButton and force a re-sync.
        # Acts as a watchdog for cases where Qt doesn't deliver
        # a per-button hover event:
        #   - cursor leaves the main window entirely
        #   - a modal dialog covered a button and stole its
        #     pending HoverLeave, then closed
        #   - a button got setEnabled(False) without an event
        #     reaching it first
        #   - the splash sequence shows the window off-screen
        #     briefly (WA_DontShowOnScreen) before the real
        #     show, leaving stale hover from the off-screen pose
        for btn in self.findChildren(QPushButton):
            self._sync_button_visual_state(btn)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        # Splash flow shows the window with WA_DontShowOnScreen,
        # paints once, hides, then shows for real. The off-screen
        # show pass can leave a button with hgrHover=True if the
        # cursor's global position happened to fall inside an
        # off-screen-positioned button's rect. Force re-sync once
        # the window is actually visible so any stale True is
        # corrected.
        QTimer.singleShot(0, self._sync_all_button_hover_states)

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        # Cursor crossed outside the main window — any sticky
        # hgrHover that didn't get cleared by a per-button
        # HoverLeave (modal dialogs, fast cursor motion, focus
        # transitions) gets resolved here.
        self._sync_all_button_hover_states()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        # Re-sync on activation transitions so a popup closing
        # and re-activating the main window can't leave a button
        # with stale hover styling.
        if event.type() == QEvent.ActivationChange:
            self._sync_all_button_hover_states()

    def keyPressEvent(self, event):  # noqa: N802
        # Esc cancels an in-progress Gesture Binds rebind without saving
        # any partial change.
        if event.key() == Qt.Key_Escape and getattr(self, "_gesture_binds_pending_action", None):
            self._clear_gesture_bind_pending()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event):  # noqa: N802
        # Gesture Binds list viewport: hide hover preview when the cursor
        # leaves the list entirely. itemEntered fires on item-to-item moves
        # but never fires when the cursor exits the viewport.
        poses_list = getattr(self, "_gesture_binds_poses_list", None)
        if poses_list is not None and obj is poses_list.viewport():
            if event.type() in (QEvent.Leave, QEvent.HoverLeave):
                self._hide_gesture_pose_preview()
        # Gesture Binds active-gesture buttons: same 2s hover preview as
        # the All Gesture Poses list, keyed off the currently-bound pose
        # for that action (including any unsaved pending change).
        if isinstance(obj, QPushButton) and obj.property("gestureBindActionId"):
            etype = event.type()
            if etype in (QEvent.Enter, QEvent.HoverEnter):
                action_id = obj.property("gestureBindActionId")
                pose_id = self._gesture_binds_pending_changes.get(action_id) \
                    if hasattr(self, "_gesture_binds_pending_changes") else None
                if not pose_id:
                    pose_id = resolve_gesture_binding(self.config, action_id)
                if pose_id:
                    self._start_gesture_pose_hover_timer(pose_id)
            elif etype in (QEvent.Leave, QEvent.HoverLeave):
                self._hide_gesture_pose_preview()
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
                QEvent.Hide,
                # EnabledChange covers the start_engine path:
                # button.setEnabled(False) when a click kicks off
                # engine startup. Without this we relied on a
                # stale hover event arriving after the disable,
                # which often never came on Windows.
                QEvent.EnabledChange,
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
    # Force the dialog widget itself to hide — CaptureMonitorDialog
    # already calls self.close() inside _choose, but if that didn't
    # fire (race / direct state-clear from another path) we want
    # the picker off the screen NOW so subsequent overlays
    # (countdown, processing) don't paint behind it.
    if dialog is not None:
        try:
            dialog.hide()
        except Exception:
            pass
        try:
            dialog.deleteLater()
        except Exception:
            pass


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
