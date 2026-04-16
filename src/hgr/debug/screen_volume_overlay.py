from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..app.ui.native_overlay import apply_overlay
from ..config.app_config import AppConfig


class ScreenVolumeOverlay(QWidget):
    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._controller = None
        self._current_fraction = 0.0
        self._muted = False
        self._active = False
        self._message = "Idle"
        self._title = "System Volume"
        self._accent = QColor("#1DE9B6")
        self._surface = QColor(15, 23, 42, 235)
        self._text = QColor(227, 237, 246, 240)
        self._border = QColor(29, 233, 182, 66)
        self._track = QColor(255, 255, 255, 22)
        self._track_border = QColor(255, 255, 255, 34)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent; border: none;")
        self.setFixedSize(168, 320)
        self.apply_theme(config)

    def attach_controller(self, controller) -> None:
        self._controller = controller

    def apply_theme(self, config: AppConfig) -> None:
        self.config = config
        accent = QColor(str(config.accent_color or "#1DE9B6"))
        text = QColor(str(config.text_color or "#E3EDF6"))
        surface = QColor(str(config.surface_color or "#0F172A"))
        if not accent.isValid():
            accent = QColor("#1DE9B6")
        if not text.isValid():
            text = QColor("#E3EDF6")
        if not surface.isValid():
            surface = QColor("#0F172A")
        self._accent = accent
        self._text = text
        self._surface = QColor(surface.red(), surface.green(), surface.blue(), 235)
        self._border = QColor(accent.red(), accent.green(), accent.blue(), 66)
        self._track = QColor(255, 255, 255, 22)
        self._track_border = QColor(255, 255, 255, 34)
        self.update()

    def show_overlay(self) -> None:
        self._place_on_screen()
        self.show()
        self.raise_()
        self.repaint()
        apply_overlay(self)

    def hide_overlay(self) -> None:
        self.hide()

    def set_level(self, level: float | None, *, muted: bool, active: bool, message: str) -> None:
        self._muted = bool(muted)
        self._active = bool(active)
        self._message = str(message)
        self._set_fraction_from_level(level)
        self.update()

    def sync_visual_state(self) -> None:
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.sync_visual_state()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        card_rect = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        painter.setPen(QPen(self._border, 1.2))
        painter.setBrush(self._surface)
        painter.drawRoundedRect(card_rect, 18, 18)

        title_font = QFont("Segoe UI", 15)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QPen(self._accent))
        painter.drawText(
            QRectF(card_rect.left() + 14, card_rect.top() + 10, card_rect.width() - 28, 24),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._title,
        )

        bar_rect = QRectF(card_rect.center().x() - 16, card_rect.top() + 42, 32, 182)
        painter.setPen(QPen(self._track_border, 1.2))
        painter.setBrush(self._track)
        painter.drawRoundedRect(bar_rect, 14, 14)

        if self._current_fraction > 0.0:
            inner = bar_rect.adjusted(4, 4, -4, -4)
            fill_height = max(inner.width(), inner.height() * self._current_fraction)
            fill_rect = QRectF(inner.left(), inner.bottom() - fill_height, inner.width(), fill_height)
            fill_radius = min(fill_rect.width() / 2.0, 10.0)
            glow = QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 84)
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(fill_rect.adjusted(-2, -2, 2, 2), fill_radius + 2.0, fill_radius + 2.0)
            painter.setBrush(self._accent)
            painter.drawRoundedRect(fill_rect, fill_radius, fill_radius)

        level_font = QFont("Segoe UI", 22)
        level_font.setBold(True)
        painter.setFont(level_font)
        painter.setPen(QPen(self._text))
        percent = int(round(self._current_fraction * 100))
        percent_text = "--" if percent < 0 else f"{percent}%"
        painter.drawText(
            QRectF(card_rect.left() + 10, bar_rect.bottom() + 14, card_rect.width() - 20, 32),
            Qt.AlignCenter,
            percent_text,
        )

        state = "Muted" if self._muted else ("Adjusting" if self._active else "Idle")
        status_font = QFont("Segoe UI", 11)
        status_font.setBold(True)
        painter.setFont(status_font)
        painter.drawText(
            QRectF(card_rect.left() + 16, bar_rect.bottom() + 48, card_rect.width() - 32, 18),
            Qt.AlignCenter,
            state,
        )

        message_font = QFont("Segoe UI", 10)
        message_font.setBold(True)
        painter.setFont(message_font)
        painter.setPen(QPen(QColor(self._text.red(), self._text.green(), self._text.blue(), 208)))
        painter.drawText(
            QRectF(card_rect.left() + 16, bar_rect.bottom() + 66, card_rect.width() - 32, 28),
            Qt.AlignCenter | Qt.TextWordWrap,
            self._message,
        )

    def _place_on_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.move(20, 20)
            return
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - 18
        y = geo.center().y() - self.height() // 2
        y = max(geo.top() + 18, y)
        self.move(x, y)

    def _set_fraction_from_level(self, level: float | None) -> None:
        if level is None:
            self._current_fraction = 0.0
            return
        clamped = max(0.0, min(1.0, float(level)))
        self._current_fraction = clamped
