from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)


class TouchlessSplash(QWidget):
    _WORD = "Touchless"
    _LETTER_STAGGER_MS = 130
    _LETTER_DURATION_MS = 520
    _WAVE_OFFSET_PX = 22

    def __init__(self, accent_color: str, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.SplashScreen | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # Wipe any inherited frame/background so only the letters are visible.
        self.setStyleSheet("QWidget { background: transparent; border: none; }")

        self._accent_color = accent_color
        self._labels: list[QLabel] = []
        self._wave_animations: list[QPropertyAnimation] = []
        self._fade_animations: list[QPropertyAnimation] = []
        self._effects: list[QGraphicsOpacityEffect] = []
        self._animation_group: QSequentialAnimationGroup | None = None
        self._finished = False

        font = self._pick_display_font()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(60, 40, 60, 40)
        layout.setSpacing(0)

        for char in self._WORD:
            label = QLabel(char, self)
            label.setFont(font)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                f"color: {accent_color}; background: transparent; border: none;"
            )
            effect = QGraphicsOpacityEffect(label)
            effect.setOpacity(0.0)
            label.setGraphicsEffect(effect)
            layout.addWidget(label)
            self._labels.append(label)
            self._effects.append(effect)

        self.adjustSize()
        self._center_on_screen()

    @staticmethod
    def _pick_display_font() -> QFont:
        # Walk a short list of nicer display faces and use the first one the
        # system actually has. Segoe UI Variable Display ships with Windows 11
        # and looks noticeably cleaner at large sizes than the default.
        preferred = (
            "Segoe UI Variable Display",
            "Segoe UI Semibold",
            "Segoe UI",
            "Calibri",
            "Arial",
        )
        available = set(QFontDatabase.families())
        family = next((name for name in preferred if name in available), "Segoe UI")
        font = QFont(family)
        font.setPointSize(86)
        font.setWeight(QFont.DemiBold)
        font.setLetterSpacing(QFont.PercentageSpacing, 104)
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
        group = QParallelAnimationGroup(self)
        for index, (label, effect) in enumerate(zip(self._labels, self._effects)):
            delay = index * self._LETTER_STAGGER_MS

            fade = QPropertyAnimation(effect, b"opacity", self)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setDuration(self._LETTER_DURATION_MS)
            fade.setEasingCurve(QEasingCurve.OutCubic)

            start_pos = label.pos() + QPoint(0, self._WAVE_OFFSET_PX)
            end_pos = label.pos()
            label.move(start_pos)
            slide = QPropertyAnimation(label, b"pos", self)
            slide.setStartValue(start_pos)
            slide.setEndValue(end_pos)
            slide.setDuration(self._LETTER_DURATION_MS)
            slide.setEasingCurve(QEasingCurve.OutBack)

            letter_seq = QSequentialAnimationGroup(self)
            if delay > 0:
                letter_seq.addPause(delay)
            letter_parallel = QParallelAnimationGroup(self)
            letter_parallel.addAnimation(fade)
            letter_parallel.addAnimation(slide)
            letter_seq.addAnimation(letter_parallel)
            group.addAnimation(letter_seq)

            self._fade_animations.append(fade)
            self._wave_animations.append(slide)

        group.finished.connect(self._on_finished)
        self._animation_group = group
        group.start()

    def _on_finished(self) -> None:
        self._finished = True

    def is_finished(self) -> bool:
        return self._finished

    def total_animation_ms(self) -> int:
        return (len(self._WORD) - 1) * self._LETTER_STAGGER_MS + self._LETTER_DURATION_MS

    @staticmethod
    def run_with(callback_build_window, accent_color: str, app) -> QWidget:
        """Show splash, play the full "Touchless" reveal, then build AND show
        the main window before the splash disappears. That way the user sees
        the completed word right up until the window appears, with no gap."""
        splash = TouchlessSplash(accent_color)
        splash.show()
        splash.raise_()
        # Paint the splash before we start timing the animation. Without
        # this, QPropertyAnimation starts advancing while the window is still
        # blank.
        for _ in range(4):
            app.processEvents()

        splash.start_animation()
        # Don't starve the animation frames -- do not build the window yet.
        while not splash.is_finished():
            app.processEvents()

        # Word is fully on screen. Build the main window behind the splash.
        window = callback_build_window()
        # Show the window while the splash is still visibly on top, then pump
        # the event loop until the main window has actually painted at least
        # once. Only THEN remove the splash so the two don't leave a blank
        # frame gap between them.
        window.show()
        window.raise_()
        for _ in range(6):
            app.processEvents()

        splash.close()
        return window
