from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


_AUTO_DISMISS_MS = 10_000
_FADE_MS = 450


class LowFpsSuggestionOverlay(QWidget):
    """Transparent blue toast offering to enable Low FPS Mode.

    Shown by the engine when measured FPS stays below the threshold for an
    extended window. User can activate the mode with the embedded button,
    close the toast manually with the X, or dismiss via left-fist gesture
    (the engine calls `dismiss()` in that path). Auto-dismisses after
    ~10 seconds with a short fade-out.
    """

    activateRequested = Signal()
    dismissed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade.setDuration(_FADE_MS)
        self._fade.finished.connect(self._on_fade_finished)

        self._auto_dismiss = QTimer(self)
        self._auto_dismiss.setSingleShot(True)
        self._auto_dismiss.timeout.connect(self._begin_fade_out)

        self._fading_out = False

        self._build_ui()
        self.setFixedSize(480, 172)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("lowFpsSuggestionPanel")
        panel.setStyleSheet(
            """
            QFrame#lowFpsSuggestionPanel {
                background: rgba(16, 70, 132, 220);
                border: 1px solid rgba(120, 190, 255, 180);
                border-radius: 14px;
            }
            QLabel#lowFpsSuggestionTitle {
                color: #E8F3FF;
                font-size: 13px;
                font-weight: 600;
                background: transparent;
            }
            QLabel#lowFpsSuggestionBody {
                color: #D4E8FF;
                font-size: 12px;
                background: transparent;
            }
            QPushButton#lowFpsSuggestionActivate {
                color: #0B3D91;
                background: #B8E1FF;
                border: none;
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton#lowFpsSuggestionActivate:hover {
                background: #D6EEFF;
            }
            QPushButton#lowFpsSuggestionActivate:pressed {
                background: #9CCBF0;
            }
            QPushButton#lowFpsSuggestionClose {
                color: #D4E8FF;
                background: transparent;
                border: none;
                font-size: 18px;
                font-weight: 600;
                padding: 0;
            }
            QPushButton#lowFpsSuggestionClose:hover {
                color: #FFFFFF;
            }
            """
        )
        outer.addWidget(panel)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 12, 12, 14)
        panel_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        title = QLabel("Your FPS is running low")
        title.setObjectName("lowFpsSuggestionTitle")
        header_row.addWidget(title, 1)

        close_btn = QPushButton("×")
        close_btn.setObjectName("lowFpsSuggestionClose")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self._on_close_clicked)
        header_row.addWidget(close_btn, 0, Qt.AlignRight | Qt.AlignTop)
        panel_layout.addLayout(header_row)

        body = QLabel(
            "Try out Low FPS Mode to improve gesture detection when your FPS is low. "
            "You can always find this option in Settings → Camera. "
            "It may take a few moments to switch modes."
        )
        body.setObjectName("lowFpsSuggestionBody")
        body.setWordWrap(True)
        panel_layout.addWidget(body, 1)

        actions_row = QHBoxLayout()
        actions_row.addStretch(1)
        activate_btn = QPushButton("Low FPS Mode")
        activate_btn.setObjectName("lowFpsSuggestionActivate")
        activate_btn.setCursor(Qt.PointingHandCursor)
        activate_btn.clicked.connect(self._on_activate_clicked)
        actions_row.addWidget(activate_btn, 0)
        panel_layout.addLayout(actions_row)

    def show_suggestion(self) -> None:
        """Position in the top-right of the primary screen and fade in."""
        self._fading_out = False
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            margin = 24
            x = geo.right() - self.width() - margin
            y = geo.top() + margin
            self.move(x, y)
        self._opacity_effect.setOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._auto_dismiss.start(_AUTO_DISMISS_MS)

    def dismiss(self) -> None:
        """Manual or gesture-driven dismiss; fade out and hide."""
        if not self.isVisible() or self._fading_out:
            return
        self._begin_fade_out()

    def _on_activate_clicked(self) -> None:
        self._auto_dismiss.stop()
        self.activateRequested.emit()
        self._begin_fade_out()

    def _on_close_clicked(self) -> None:
        self._auto_dismiss.stop()
        self._begin_fade_out()

    def _begin_fade_out(self) -> None:
        if self._fading_out:
            return
        self._fading_out = True
        self._auto_dismiss.stop()
        self._fade.stop()
        self._fade.setStartValue(float(self._opacity_effect.opacity()))
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._fading_out:
            self.hide()
            self._fading_out = False
            self.dismissed.emit()
