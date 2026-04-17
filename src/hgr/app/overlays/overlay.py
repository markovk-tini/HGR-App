from __future__ import annotations

import math
from datetime import datetime
from itertools import cycle
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QColorDialog, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget


class HelloOverlay(QWidget):
    def __init__(self, font_size: int = 72, parent=None):
        super().__init__(parent)
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        transparent_flag = getattr(Qt, "WindowTransparentForInput", None)
        if transparent_flag is not None:
            flags |= transparent_flag
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.message = "HELLO USER!"
        self.font_size = font_size
        self.current_color = QColor("#1DE9B6")
        self._color_cycle = cycle([
            "#1DE9B6",
            "#7C4DFF",
            "#FF5252",
            "#FFD740",
            "#40C4FF",
            "#69F0AE",
            "#FF6E40",
        ])
        self._resize_to_primary_screen()

    def _resize_to_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1200, 800)
            return
        self.setGeometry(screen.availableGeometry())

    def show_message(self) -> None:
        self.current_color = QColor(next(self._color_cycle))
        self._resize_to_primary_screen()
        self.show()
        self.raise_()
        self.update()

    def hide_message(self) -> None:
        self.hide()

    def set_font_size(self, font_size: int) -> None:
        self.font_size = font_size
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(self.current_color)
        font = QFont("Arial", self.font_size, QFont.Bold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, self.message)


class DrawingSettingsDialog(QDialog):
    def __init__(self, color: QColor, thickness: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Drawing Settings")
        self.setModal(True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(420, 260)
        self._selected_color = QColor(color)
        self._auto_color_opened = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        title = QLabel("Drawing Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 800;")
        root.addWidget(title)

        preview_row = QHBoxLayout()
        preview_label = QLabel("Current color")
        self.preview_chip = QFrame()
        self.preview_chip.setFixedSize(64, 28)
        preview_row.addWidget(preview_label)
        preview_row.addWidget(self.preview_chip)
        preview_row.addStretch(1)
        root.addLayout(preview_row)

        self.color_button = QPushButton("Open Color Wheel")
        self.color_button.clicked.connect(self._open_color_picker)
        root.addWidget(self.color_button, 0, Qt.AlignLeft)

        thickness_row = QHBoxLayout()
        thickness_label = QLabel("Brush thickness")
        self.thickness_slider = QSlider(Qt.Horizontal)
        self.thickness_slider.setRange(2, 48)
        self.thickness_slider.setValue(max(2, thickness))
        self.thickness_value = QLabel(str(self.thickness_slider.value()))
        self.thickness_slider.valueChanged.connect(lambda v: self.thickness_value.setText(str(v)))
        thickness_row.addWidget(thickness_label)
        thickness_row.addWidget(self.thickness_slider, 1)
        thickness_row.addWidget(self.thickness_value)
        root.addLayout(thickness_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(apply_btn)
        root.addLayout(buttons)

        self.setStyleSheet(
            """
            QDialog {
                background: #0F172A;
                color: #E5F6FF;
                border: 1px solid rgba(29,233,182,0.35);
            }
            QLabel { color: #E5F6FF; }
            QPushButton {
                background-color: #0B3D91;
                color: #E5F6FF;
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 9px 14px;
                font-weight: 700;
            }
            QPushButton:hover { border: 1px solid #1DE9B6; }
            QSlider::groove:horizontal {
                height: 6px;
                border-radius: 3px;
                background: rgba(255,255,255,0.14);
            }
            QSlider::handle:horizontal {
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
                background: #1DE9B6;
            }
            """
        )
        self._refresh_preview()

    @property
    def selected_color(self) -> QColor:
        return QColor(self._selected_color)

    @property
    def selected_thickness(self) -> int:
        return int(self.thickness_slider.value())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._auto_color_opened:
            self._auto_color_opened = True
            QTimer.singleShot(0, self._open_color_picker)

    def _refresh_preview(self) -> None:
        self.preview_chip.setStyleSheet(
            f"background: {self._selected_color.name()}; border-radius: 8px; border: 1px solid rgba(255,255,255,0.22);"
        )

    def _open_color_picker(self) -> None:
        picker = QColorDialog(self._selected_color, self)
        picker.setWindowTitle("Choose Drawing Color")
        picker.setOption(QColorDialog.DontUseNativeDialog, False)
        if picker.exec() == QDialog.Accepted:
            chosen = picker.currentColor()
            if chosen.isValid():
                self._selected_color = QColor(chosen)
                self._refresh_preview()


class ScreenDrawOverlay(QWidget):
    def __init__(self, color: str = "#FFFFFF", thickness: int = 6, parent=None):
        super().__init__(parent)
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        transparent_flag = getattr(Qt, "WindowTransparentForInput", None)
        if transparent_flag is not None:
            flags |= transparent_flag
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.brush_color = QColor(color)
        self.brush_thickness = int(max(2, thickness))
        self.eraser_thickness = int(max(6, thickness * 2))
        self.eraser_mode = "normal"
        self._canvas = QImage()
        self._cursor_pos: Optional[QPointF] = None
        self._cursor_mode = "hidden"  # hidden / hover / draw / erase
        self._last_draw_point: Optional[QPointF] = None
        self._history: list[tuple[QImage, list[dict], bool]] = []
        self._history_limit = 24
        self._strokes: list[dict] = []
        self._active_stroke_points: list[tuple[float, float]] = []
        self._raster_dirty = False
        self.shape_mode = False
        self._resize_to_screen()

    def set_shape_mode(self, enabled: bool) -> None:
        self.shape_mode = bool(enabled)

    def _resize_to_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.setGeometry(0, 0, 1280, 720)
        else:
            self.setGeometry(screen.geometry())
        self._ensure_canvas_size()

    def _ensure_canvas_size(self) -> None:
        size = self.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        if self._canvas.size() == size:
            return
        new_canvas = QImage(size, QImage.Format_ARGB32_Premultiplied)
        new_canvas.fill(Qt.transparent)
        if not self._canvas.isNull():
            painter = QPainter(new_canvas)
            painter.drawImage(0, 0, self._canvas)
            painter.end()
        self._canvas = new_canvas

    def _clone_canvas(self) -> QImage:
        self._ensure_canvas_size()
        return self._canvas.copy() if not self._canvas.isNull() else QImage()

    def _clone_strokes(self) -> list[dict]:
        clones: list[dict] = []
        for stroke in self._strokes:
            clones.append(
                {
                    "color": QColor(stroke["color"]),
                    "thickness": int(stroke["thickness"]),
                    "points": [(float(x), float(y)) for x, y in stroke["points"]],
                }
            )
        return clones

    def _rerender_from_strokes(self) -> None:
        self._ensure_canvas_size()
        self._canvas.fill(Qt.transparent)
        painter = QPainter(self._canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        for stroke in self._strokes:
            points = stroke.get("points") or []
            if len(points) < 2:
                continue
            pen = QPen(QColor(stroke["color"]))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setWidth(int(stroke["thickness"]))
            painter.setPen(pen)
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        painter.end()

    def push_undo_state(self) -> None:
        snapshot = self._clone_canvas()
        self._history.append((snapshot, self._clone_strokes(), bool(self._raster_dirty)))
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]

    def undo_last_action(self) -> bool:
        if not self._history:
            return False
        canvas, strokes, raster_dirty = self._history.pop()
        self._canvas = canvas
        self._strokes = strokes
        self._raster_dirty = bool(raster_dirty)
        self._active_stroke_points = []
        self._last_draw_point = None
        self.update()
        return True

    def show_overlay(self) -> None:
        self._resize_to_screen()
        self.show()
        self.raise_()
        self.update()

    def hide_overlay(self) -> None:
        self.hide()

    def set_brush(self, color: QColor | str, thickness: int) -> None:
        self.brush_color = QColor(color)
        self.brush_thickness = int(max(2, thickness))
        self.update()

    def set_eraser(self, thickness: int, mode: str = "normal") -> None:
        self.eraser_thickness = int(max(6, thickness))
        new_mode = "stroke" if str(mode).strip().lower() == "stroke" else "normal"
        if new_mode == "stroke" and self._raster_dirty and self._strokes:
            self._raster_dirty = False
            self._rerender_from_strokes()
        self.eraser_mode = new_mode
        self.update()

    def set_eraser_settings(self, thickness: int, mode: str = "normal") -> None:
        self.set_eraser(thickness, mode)

    def clear_canvas(self) -> None:
        self._ensure_canvas_size()
        self._canvas.fill(Qt.transparent)
        self._strokes = []
        self._active_stroke_points = []
        self._raster_dirty = False
        self._last_draw_point = None
        self.update()

    def set_cursor(self, pos: Optional[QPointF], mode: str) -> None:
        self._cursor_pos = QPointF(pos) if pos is not None else None
        self._cursor_mode = mode
        if mode != "draw":
            self._last_draw_point = None
        self.update()

    def begin_draw(self, pos: QPointF) -> None:
        self._last_draw_point = QPointF(pos)
        self._active_stroke_points = [(float(pos.x()), float(pos.y()))]
        self.set_cursor(pos, "draw")

    def draw_to(self, pos: QPointF) -> None:
        self._ensure_canvas_size()
        if self._last_draw_point is None:
            self._last_draw_point = QPointF(pos)
            if not self._active_stroke_points:
                self._active_stroke_points = [(float(pos.x()), float(pos.y()))]
        painter = QPainter(self._canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(self.brush_color)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setWidth(self.brush_thickness)
        painter.setPen(pen)
        painter.drawLine(self._last_draw_point, QPointF(pos))
        painter.end()
        self._active_stroke_points.append((float(pos.x()), float(pos.y())))
        self._last_draw_point = QPointF(pos)
        self._cursor_pos = QPointF(pos)
        self._cursor_mode = "draw"
        self.update()

    @staticmethod
    def _point_to_segment_distance_sq(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
        abx = bx - ax
        aby = by - ay
        if abs(abx) < 1e-9 and abs(aby) < 1e-9:
            dx = px - ax
            dy = py - ay
            return dx * dx + dy * dy
        apx = px - ax
        apy = py - ay
        denom = abx * abx + aby * aby
        t = max(0.0, min(1.0, (apx * abx + apy * aby) / denom))
        cx = ax + t * abx
        cy = ay + t * aby
        dx = px - cx
        dy = py - cy
        return dx * dx + dy * dy

    def _stroke_hits_position(self, stroke: dict, px: float, py: float, radius: float) -> bool:
        points = stroke.get("points") or []
        if not points:
            return False
        threshold = max(float(radius), float(stroke.get("thickness", 0)) * 0.5 + 2.0)
        limit_sq = threshold * threshold
        if len(points) == 1:
            sx, sy = points[0]
            dx = sx - px
            dy = sy - py
            return dx * dx + dy * dy <= limit_sq
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            if self._point_to_segment_distance_sq(px, py, float(ax), float(ay), float(bx), float(by)) <= limit_sq:
                return True
        return False

    def erase_at(self, pos: QPointF) -> None:
        self._ensure_canvas_size()
        radius = max(8, int(self.eraser_thickness * 0.5))
        if self.eraser_mode == "stroke":
            if self._raster_dirty and self._strokes:
                self._raster_dirty = False
                self._rerender_from_strokes()
            px = float(pos.x())
            py = float(pos.y())
            hit_index = None
            for idx in range(len(self._strokes) - 1, -1, -1):
                stroke = self._strokes[idx]
                if self._stroke_hits_position(stroke, px, py, float(radius)):
                    hit_index = idx
                    break
            if hit_index is not None:
                self._strokes.pop(hit_index)
                self._rerender_from_strokes()
            else:
                self._cursor_pos = QPointF(pos)
                self._cursor_mode = "erase"
                self.update()
                return
        else:
            painter = QPainter(self._canvas)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.setPen(Qt.NoPen)
            painter.setBrush(Qt.transparent)
            painter.drawEllipse(pos, radius, radius)
            painter.end()
            self._raster_dirty = True
        self._cursor_pos = QPointF(pos)
        self._cursor_mode = "erase"
        self.update()

    def end_stroke(self) -> None:
        if self._active_stroke_points:
            if len(self._active_stroke_points) == 1:
                x, y = self._active_stroke_points[0]
                self._active_stroke_points.append((x + 0.01, y + 0.01))
            points = list(self._active_stroke_points)
            if self.shape_mode:
                snapped = self._snap_stroke_to_shape(points)
                if snapped and len(snapped) >= 2:
                    points = snapped
            self._strokes.append(
                {
                    "color": QColor(self.brush_color),
                    "thickness": int(self.brush_thickness),
                    "points": points,
                }
            )
            self._active_stroke_points = []
            if self.shape_mode:
                self._rerender_from_strokes()
        self._last_draw_point = None
        if self._cursor_mode == "draw":
            self._cursor_mode = "hover"
        self.update()

    def _snap_stroke_to_shape(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        pts = [(float(x), float(y)) for x, y in points]
        n = len(pts)
        if n < 3:
            return pts
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max_x - min_x
        height = max_y - min_y
        span = max(width, height, 1.0)
        if span < 14.0:
            return pts
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0
        start_end_dist = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
        closed = start_end_dist < span * 0.32

        if not closed:
            return [pts[0], pts[-1]]

        simplified = self._simplify_polyline(pts, span * 0.08)
        if len(simplified) > 1:
            if math.hypot(simplified[0][0] - simplified[-1][0], simplified[0][1] - simplified[-1][1]) < span * 0.06:
                simplified = simplified[:-1]
        corner_count = max(len(simplified), 1)

        radii = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
        avg_r = sum(radii) / len(radii) if radii else 0.0
        if avg_r > 0:
            deviation = sum(abs(r - avg_r) for r in radii) / len(radii) / avg_r
        else:
            deviation = 1.0
        aspect = min(width, height) / max(width, height, 1.0)
        is_circle = deviation < 0.18 and corner_count >= 5 and aspect > 0.55

        if is_circle:
            steps = 72
            result: list[tuple[float, float]] = []
            for i in range(steps + 1):
                angle = 2.0 * math.pi * i / steps
                result.append((cx + avg_r * math.cos(angle), cy + avg_r * math.sin(angle)))
            return result
        if corner_count == 3 and len(simplified) >= 3:
            tri = [simplified[0], simplified[1], simplified[2]]
            return [tri[0], tri[1], tri[2], tri[0]]
        if corner_count == 4:
            return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y), (min_x, min_y)]
        return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y), (min_x, min_y)]

    def _simplify_polyline(self, pts: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
        if len(pts) < 3:
            return list(pts)
        stack: list[tuple[int, int]] = [(0, len(pts) - 1)]
        keep = [False] * len(pts)
        keep[0] = True
        keep[-1] = True
        while stack:
            start, end = stack.pop()
            if end <= start + 1:
                continue
            ax, ay = pts[start]
            bx, by = pts[end]
            dmax = 0.0
            idx = start
            for i in range(start + 1, end):
                d2 = self._point_to_segment_distance_sq(pts[i][0], pts[i][1], ax, ay, bx, by)
                if d2 > dmax:
                    dmax = d2
                    idx = i
            if dmax > epsilon * epsilon:
                keep[idx] = True
                stack.append((start, idx))
                stack.append((idx, end))
        return [pts[i] for i, k in enumerate(keep) if k]

    def map_normalized_to_screen(self, x: float, y: float) -> QPointF:
        geo = self.geometry()
        return QPointF(geo.left() + x * geo.width(), geo.top() + y * geo.height())

    def save_canvas_snapshot(self, *, target_dir: Path | None = None, target_path: Path | None = None) -> Optional[Path]:
        self._ensure_canvas_size()
        bg_color = QColor("#000000")
        if self.brush_color.lightness() < 38:
            bg_color = QColor("#FFFFFF")
        output = QImage(self._canvas.size(), QImage.Format_ARGB32_Premultiplied)
        output.fill(bg_color)
        painter = QPainter(output)
        painter.drawImage(0, 0, self._canvas)
        painter.end()

        path = Path(target_path) if target_path is not None else None
        if path is None:
            base_dir = Path(target_dir) if target_dir is not None else (Path.home() / "Pictures")
            if not base_dir.exists():
                base_dir = Path.home()
            base_dir.mkdir(parents=True, exist_ok=True)
            path = base_dir / f"hgr_drawing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        saved = output.save(str(path), "PNG")
        return path if saved else None

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._ensure_canvas_size()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._canvas.isNull():
            painter.drawImage(0, 0, self._canvas)

        if self._cursor_pos is None or self._cursor_mode == "hidden":
            return

        radius = max(6, int(self.brush_thickness if self._cursor_mode == "draw" else self.eraser_thickness * 0.5))
        outline = QPen(QColor("#FFFFFF"))
        outline.setWidth(2)
        painter.setPen(outline)

        if self._cursor_mode == "draw":
            painter.setBrush(self.brush_color)
            painter.drawEllipse(self._cursor_pos, radius, radius)
        else:
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(self._cursor_pos, radius, radius)


class CountdownOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        transparent_flag = getattr(Qt, "WindowTransparentForInput", None)
        if transparent_flag is not None:
            flags |= transparent_flag
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._value = "3"
        self._resize_to_primary_screen()

    def _resize_to_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.setGeometry(0, 0, 1280, 720)
            return
        self.setGeometry(screen.geometry())

    def show_countdown(self, value: int | str) -> None:
        self._value = str(value)
        self._resize_to_primary_screen()
        self.show()
        self.raise_()
        self.update()

    def hide_countdown(self) -> None:
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._value:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box_width = 136
        box_height = 94
        rect = QRect(0, 0, box_width, box_height)
        rect.moveCenter(QPoint(self.rect().center().x(), self.rect().bottom() - 82))
        painter.setPen(QPen(QColor(255, 255, 255, 80), 1.4))
        painter.setBrush(QColor(10, 18, 26, 170))
        painter.drawRoundedRect(rect, 18, 18)
        painter.setPen(QColor('#F4FAFF'))
        font = QFont('Arial', 34, QFont.Bold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, self._value)


class RecordingIndicatorOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        flags = Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool
        transparent_flag = getattr(Qt, "WindowTransparentForInput", None)
        if transparent_flag is not None:
            flags |= transparent_flag
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pulse_on = True
        self._timer = QTimer(self)
        self._timer.setInterval(520)
        self._timer.timeout.connect(self._toggle_pulse)
        self._resize_to_primary_screen()

    def _resize_to_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.setGeometry(0, 0, 1280, 720)
            return
        self.setGeometry(screen.geometry())

    def _toggle_pulse(self) -> None:
        self._pulse_on = not self._pulse_on
        if self.isVisible():
            self.update()

    def show_indicator(self) -> None:
        self._resize_to_primary_screen()
        self._pulse_on = True
        self.show()
        self.raise_()
        self._timer.start()
        self.update()

    def hide_indicator(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box_width = 196
        box_height = 56
        rect = QRect(0, 0, box_width, box_height)
        rect.moveCenter(QPoint(self.rect().center().x(), self.rect().top() + 42))
        painter.setPen(QPen(QColor(255, 255, 255, 68), 1.2))
        painter.setBrush(QColor(10, 18, 26, 148))
        painter.drawRoundedRect(rect, 16, 16)
        dot_color = QColor(255, 62, 62, 245 if self._pulse_on else 120)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot_color)
        painter.drawEllipse(QPoint(rect.left() + 28, rect.center().y()), 8, 8)
        painter.setPen(QColor('#F4FAFF'))
        font = QFont('Arial', 18, QFont.Bold)
        painter.setFont(font)
        painter.drawText(rect.adjusted(44, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, 'Recording')


class CaptureRegionOverlay(QWidget):
    selection_finished = Signal(QRect)
    selection_canceled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._origin_global: QPoint | None = None
        self._current_global: QPoint | None = None
        self._selection_global = QRect()
        self._hand_control = False
        self._cursor_global: QPoint | None = None
        self._last_left_down = False
        self._last_right_down = False

    def _desktop_geometry(self) -> QRect:
        screens = [screen for screen in QGuiApplication.screens() if screen is not None]
        if not screens:
            return QRect(0, 0, 1280, 720)
        geometry = screens[0].geometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        return geometry

    def begin_selection(self, *, hand_control: bool = False) -> None:
        self._origin_global = None
        self._current_global = None
        self._selection_global = QRect()
        self._cursor_global = None
        self._last_left_down = False
        self._last_right_down = False
        self._hand_control = bool(hand_control)
        self.setGeometry(self._desktop_geometry())
        if self._hand_control:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CrossCursor)
        self.show()
        self.raise_()
        if not self._hand_control:
            self.activateWindow()
        self.update()

    def _local_from_global(self, point: QPoint) -> QPoint:
        origin = self.geometry().topLeft()
        return QPoint(point.x() - origin.x(), point.y() - origin.y())

    def _finish_selection(self) -> None:
        rect = QRect(self._origin_global, self._current_global).normalized() if self._origin_global is not None and self._current_global is not None else QRect()
        self._origin_global = None
        self._current_global = None
        self.hide()
        self.unsetCursor()
        if rect.width() < 8 or rect.height() < 8:
            self.selection_canceled.emit()
            return
        self.selection_finished.emit(rect)

    def _cancel_selection(self) -> None:
        self._origin_global = None
        self._current_global = None
        self._selection_global = QRect()
        self.hide()
        self.unsetCursor()
        self.selection_canceled.emit()

    def update_hand_control(self, global_point: QPoint | None, *, left_down: bool, right_down: bool) -> None:
        if not self._hand_control or not self.isVisible():
            return
        if global_point is not None:
            self._cursor_global = QPoint(global_point)
        if right_down and not self._last_right_down:
            self._last_right_down = True
            self._last_left_down = bool(left_down)
            self._cancel_selection()
            return
        self._last_right_down = bool(right_down)
        if self._cursor_global is None:
            self._last_left_down = bool(left_down)
            self.update()
            return
        if left_down and self._origin_global is None and not self._last_left_down:
            self._origin_global = QPoint(self._cursor_global)
            self._current_global = QPoint(self._cursor_global)
            self._selection_global = QRect(self._origin_global, self._current_global).normalized()
        elif left_down and self._origin_global is not None:
            self._current_global = QPoint(self._cursor_global)
            self._selection_global = QRect(self._origin_global, self._current_global).normalized()
        elif not left_down and self._origin_global is not None and self._last_left_down:
            self._current_global = QPoint(self._cursor_global)
            self._selection_global = QRect(self._origin_global, self._current_global).normalized()
            self._last_left_down = False
            self._finish_selection()
            return
        self._last_left_down = bool(left_down)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._hand_control:
            return
        if event.button() != Qt.LeftButton:
            return
        point = event.globalPosition().toPoint()
        self._origin_global = point
        self._current_global = point
        self._selection_global = QRect(point, point).normalized()
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._hand_control:
            return
        if self._origin_global is None:
            return
        self._current_global = event.globalPosition().toPoint()
        self._selection_global = QRect(self._origin_global, self._current_global).normalized()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._hand_control:
            return
        if event.button() != Qt.LeftButton or self._origin_global is None:
            return
        self._current_global = event.globalPosition().toPoint()
        self._selection_global = QRect(self._origin_global, self._current_global).normalized()
        self._finish_selection()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._cancel_selection()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(4, 10, 16, 88))
        if not self._selection_global.isNull():
            local_rect = QRect(self._local_from_global(self._selection_global.topLeft()), self._local_from_global(self._selection_global.bottomRight())).normalized()
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(local_rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor('#F4FAFF'), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(local_rect)
        if self._hand_control and self._cursor_global is not None:
            local = self._local_from_global(self._cursor_global)
            cursor_pen = QPen(QColor('#F4FAFF'), 2)
            painter.setPen(cursor_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(local, 10, 10)
            painter.drawLine(local.x() - 15, local.y(), local.x() + 15, local.y())
            painter.drawLine(local.x(), local.y() - 15, local.x(), local.y() + 15)
