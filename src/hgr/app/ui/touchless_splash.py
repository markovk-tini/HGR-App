from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QGuiApplication,
    QPainter,
)
from PySide6.QtWidgets import QWidget


class TouchlessSplash(QWidget):
    _WORD = "Touchless"
    _LETTER_STAGGER_MS = 140
    _LETTER_DURATION_MS = 560
    _WAVE_OFFSET_PX = 24
    _SIDE_PADDING = 80
    _VERTICAL_PADDING = 90

    def __init__(self, accent_color: str, parent: QWidget | None = None) -> None:
        # Qt.SplashScreen adds a subtle DWM frame/shadow on Windows. Drop it
        # and use a plain frameless tool window so only our painted letters
        # show up on screen.
        super().__init__(
            parent,
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint,
        )
        # Render the letters straight to a translucent window -- no child
        # widgets, no layouts, no QGraphicsEffects. That way nothing can draw
        # a frame or faint outline around the word.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)

        self._accent_color = QColor(accent_color)
        self._font = self._pick_display_font()
        self._metrics = QFontMetrics(self._font)

        # Pre-compute each letter's horizontal offset inside the window so
        # paintEvent is cheap and the baseline never jitters.
        self._letter_positions: list[int] = []
        cursor_x = self._SIDE_PADDING
        extra_spacing = int(round(self._metrics.averageCharWidth() * 0.08))
        for char in self._WORD:
            self._letter_positions.append(cursor_x)
            cursor_x += self._metrics.horizontalAdvance(char) + extra_spacing
        total_width = cursor_x + self._SIDE_PADDING
        total_height = self._metrics.height() + self._VERTICAL_PADDING * 2

        self._letter_opacities: list[float] = [0.0] * len(self._WORD)
        self._letter_offsets: list[float] = [float(self._WAVE_OFFSET_PX)] * len(self._WORD)

        self._finished = False
        self._animations: list[QVariantAnimation] = []

        self.resize(total_width, total_height)
        self._center_on_screen()

    @staticmethod
    def _pick_display_font() -> QFont:
        # Humanist / softly-curved faces that ship with Windows. Corbel and
        # Candara have rounded terminals (no sharp corners), and we tilt them
        # with italic for the slight slant the user asked for.
        preferred = (
            "Corbel",
            "Candara",
            "Calibri",
            "Segoe UI Variable Display",
            "Segoe UI",
        )
        available = set(QFontDatabase.families())
        family = next((name for name in preferred if name in available), "Segoe UI")
        font = QFont(family)
        font.setPointSize(96)
        font.setWeight(QFont.DemiBold)
        font.setItalic(True)
        font.setLetterSpacing(QFont.PercentageSpacing, 108)
        font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
        return font

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.availableGeometry()
        self.move(
            geom.center().x() - self.width() // 2,
            geom.center().y() - self.height() // 2,
        )

    def start_animation(self) -> None:
        for index in range(len(self._WORD)):
            delay = index * self._LETTER_STAGGER_MS

            opacity_anim = QVariantAnimation(self)
            opacity_anim.setDuration(self._LETTER_DURATION_MS)
            opacity_anim.setStartValue(0.0)
            opacity_anim.setEndValue(1.0)
            opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
            opacity_anim.valueChanged.connect(
                lambda value, i=index: self._apply_opacity(i, value)
            )

            offset_anim = QVariantAnimation(self)
            offset_anim.setDuration(self._LETTER_DURATION_MS)
            offset_anim.setStartValue(float(self._WAVE_OFFSET_PX))
            offset_anim.setEndValue(0.0)
            offset_anim.setEasingCurve(QEasingCurve.OutBack)
            offset_anim.valueChanged.connect(
                lambda value, i=index: self._apply_offset(i, value)
            )

            QTimer.singleShot(delay, opacity_anim.start)
            QTimer.singleShot(delay, offset_anim.start)
            self._animations.extend([opacity_anim, offset_anim])

        QTimer.singleShot(self.total_animation_ms(), self._on_finished)

    def _apply_opacity(self, index: int, value) -> None:
        self._letter_opacities[index] = float(value)
        self.update()

    def _apply_offset(self, index: int, value) -> None:
        self._letter_offsets[index] = float(value)
        self.update()

    def _on_finished(self) -> None:
        self._finished = True

    def is_finished(self) -> bool:
        return self._finished

    def total_animation_ms(self) -> int:
        return (len(self._WORD) - 1) * self._LETTER_STAGGER_MS + self._LETTER_DURATION_MS

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        # Force the window's backing store to fully transparent pixels before
        # we draw the letters. Without this, some Windows compositor paths
        # leave a faint rectangular fill behind that reads as a border.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setFont(self._font)

        baseline_y = (self.height() + self._metrics.ascent() - self._metrics.descent()) // 2

        for index, char in enumerate(self._WORD):
            opacity = max(0.0, min(1.0, self._letter_opacities[index]))
            if opacity <= 0.001:
                continue
            offset_y = int(round(self._letter_offsets[index]))
            color = QColor(self._accent_color)
            color.setAlphaF(opacity)
            painter.setPen(color)
            painter.drawText(
                self._letter_positions[index],
                baseline_y + offset_y,
                char,
            )

        painter.end()

    @staticmethod
    def run_with(callback_build_window, accent_color: str, app) -> QWidget:
        """Show splash, play the "Touchless" reveal, then build AND fade in
        the main window before the splash disappears. The fade-in hides the
        hollow window frame that Windows would otherwise show for the first
        few paint cycles of the main window."""
        splash = TouchlessSplash(accent_color)
        splash.show()
        splash.raise_()
        # Paint the splash before animations start.
        for _ in range(4):
            app.processEvents()

        splash.start_animation()
        while not splash.is_finished():
            app.processEvents()

        # Build the main window behind the finished splash. Start fully
        # transparent so its initial unpainted frame never flashes.
        window = callback_build_window()
        window.setWindowOpacity(0.0)
        window.show()
        window.raise_()
        # Let the main window go through its first paint cycle while still
        # invisible. Only then do we fade it in.
        for _ in range(6):
            app.processEvents()

        fade_in = QPropertyAnimation(window, b"windowOpacity", window)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setDuration(260)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)
        fade_in.start(QPropertyAnimation.DeleteWhenStopped)

        # Close the splash immediately -- the main window is already painted
        # (but still opacity 0), so as it fades up the splash disappears in
        # sync with no blank frame in between.
        splash.close()
        return window
