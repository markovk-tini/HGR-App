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
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)


class TouchlessSplash(QWidget):
    _WORD = "Touchless"
    _LETTER_STAGGER_MS = 80
    _LETTER_DURATION_MS = 360
    _WAVE_OFFSET_PX = 18

    def __init__(self, accent_color: str, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.SplashScreen | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._accent_color = accent_color
        self._labels: list[QLabel] = []
        self._wave_animations: list[QPropertyAnimation] = []
        self._fade_animations: list[QPropertyAnimation] = []
        self._effects: list[QGraphicsOpacityEffect] = []
        self._animation_group: QSequentialAnimationGroup | None = None
        self._finished = False

        font = QFont()
        font.setPointSize(72)
        font.setWeight(QFont.Black)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(60, 40, 60, 40)
        layout.setSpacing(0)

        for char in self._WORD:
            label = QLabel(char, self)
            label.setFont(font)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(
                f"color: {accent_color}; background: transparent;"
            )
            effect = QGraphicsOpacityEffect(label)
            effect.setOpacity(0.0)
            label.setGraphicsEffect(effect)
            layout.addWidget(label)
            self._labels.append(label)
            self._effects.append(effect)

        self.adjustSize()
        self._center_on_screen()

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

    def fade_out_and_close(self, duration_ms: int = 220) -> None:
        overall_effect = QGraphicsOpacityEffect(self)
        overall_effect.setOpacity(1.0)
        self.setGraphicsEffect(overall_effect)
        fade = QPropertyAnimation(overall_effect, b"opacity", self)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setDuration(duration_ms)
        fade.setEasingCurve(QEasingCurve.InCubic)
        fade.finished.connect(self.close)
        fade.start(QPropertyAnimation.DeleteWhenStopped)

    def total_animation_ms(self) -> int:
        return (len(self._WORD) - 1) * self._LETTER_STAGGER_MS + self._LETTER_DURATION_MS

    @staticmethod
    def run_with(callback_build_window, accent_color: str, app) -> QWidget:
        """Show splash, run its animation, then build the main window and
        close the splash once both are ready. Returns the created window."""
        splash = TouchlessSplash(accent_color)
        splash.show()
        splash.raise_()
        splash.start_animation()

        # Pump events until the intro animation has played out, regardless of
        # whether the main window construction is faster or slower than the
        # animation -- we want the full "Touchless" reveal every launch.
        animation_total = splash.total_animation_ms()
        window_ready: list[QWidget | None] = [None]

        def _build() -> None:
            window_ready[0] = callback_build_window()

        # Build the window in the background between animation frames so we
        # don't make the user wait twice. QTimer.singleShot yields to the
        # event loop so the splash paints first.
        QTimer.singleShot(0, _build)

        # Spin the event loop until both the animation has finished AND the
        # window has been constructed.
        deadline_timer = QTimer()
        deadline_timer.setSingleShot(True)
        deadline_timer.start(animation_total)
        while (not splash.is_finished()) or window_ready[0] is None:
            app.processEvents()

        splash.fade_out_and_close()
        return window_ready[0]  # type: ignore[return-value]
