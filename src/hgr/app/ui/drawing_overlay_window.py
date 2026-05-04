"""Frameless, click-through overlay that shows a saved drawing PNG on
top of every other window.

Used by the "show_overlay_drawing" custom-gesture action: the user
binds a gesture to a saved PNG filename, and firing the gesture
toggles this overlay over their current screen content. Strokes
appear "in mid-air" because the saved drawing is transparent (see
DrawOverlay.save_canvas_snapshot) and this window is itself
WA_TranslucentBackground + WA_TransparentForMouseEvents — clicks
pass straight through to whatever app is underneath.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class DrawingOverlayWindow(QWidget):
    """Always-on-top, click-through PNG viewer.

    Single instance per app — main_window owns one and toggles it
    via show_image / hide. Mirrors the HelloOverlay flag pattern so
    the OS treats it the same way (no taskbar entry, no focus steal).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        # WindowTransparentForInput is the OS-level click-through flag
        # (added in Qt 5.6+). Combined with WA_TransparentForMouseEvents
        # below, mouse events never reach the overlay AND the OS routes
        # input straight to the window underneath — no flicker or
        # input-loss when the cursor passes over the overlay's region.
        transparent_flag = getattr(Qt, "WindowTransparentForInput", None)
        if transparent_flag is not None:
            flags |= transparent_flag
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pixmap: Optional[QPixmap] = None
        self._current_path: Optional[str] = None

    def show_image(self, path: str) -> bool:
        """Load `path` and show the overlay sized + positioned to
        cover the union of all screens. The drawing's transparent
        pixels stay transparent — only the user's strokes are
        visible. Returns False if the file couldn't be loaded."""
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return False
        self._pixmap = pixmap
        self._current_path = path
        self._fit_to_virtual_screen()
        self.show()
        self.raise_()
        self.update()
        return True

    @property
    def current_path(self) -> Optional[str]:
        return self._current_path

    def _fit_to_virtual_screen(self) -> None:
        """Stretch over every connected display so the strokes
        appear at the same screen coordinates the user drew them
        at, regardless of which monitor they were on."""
        screens = [s for s in QGuiApplication.screens() if s is not None]
        if not screens:
            self.resize(1200, 800)
            return
        union = screens[0].geometry()
        for screen in screens[1:]:
            union = union.united(screen.geometry())
        self.setGeometry(union)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API name)
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Scale the saved drawing to fill the overlay while
        # preserving aspect ratio. KeepAspectRatio means letterboxed
        # gaps stay transparent (no fill paint), so the strokes line
        # up where the user drew them and the rest of the screen
        # shows through normally.
        target = self.rect()
        scaled = self._pixmap.scaled(
            target.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        x = (target.width() - scaled.width()) // 2
        y = (target.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)


def resolve_drawing_path(filename: str, drawings_dir: str | Path) -> Optional[Path]:
    """Resolve a user-typed drawing filename against the configured
    drawings directory. Accepts:
      - bare filename ('my_drawing.png') → joined with drawings_dir
      - relative path ('subdir/foo.png') → joined with drawings_dir
      - absolute path → used as-is
    Returns the resolved Path if the file exists, or None."""
    if not filename:
        return None
    candidate = Path(filename).expanduser()
    if not candidate.is_absolute():
        try:
            candidate = Path(drawings_dir).expanduser() / candidate
        except Exception:
            return None
    try:
        if candidate.is_file():
            return candidate
    except OSError:
        return None
    return None

# Author: Konstantin Markov
