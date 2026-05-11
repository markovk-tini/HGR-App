from __future__ import annotations

import math
import time
from datetime import datetime
from itertools import cycle
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QColorDialog, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from ..ui.native_overlay import apply_overlay


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
        # Pinch-grab live transform. Translation + scale: the user
        # can move strokes around with a one-hand pinch and stretch
        # / squish them with a two-hand pinch (distance change
        # between the palms drives scale). Per-stroke movement is
        # still Phase 2 — for now the whole canvas transforms as a
        # unit. The rasteriser is set _raster_dirty after a bake so
        # it knows the stroke list no longer matches the pixels.
        self._grab_dx_norm: float = 0.0
        self._grab_dy_norm: float = 0.0
        self._grab_scale: float = 1.0
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
        aspect = min(width, height) / max(width, height, 1.0)

        # Compare how well the stroke fits a rectangle (points hug the four
        # bbox edges) vs a circle (points stay at avg_r from center). Whichever
        # residual is smaller wins. A wobbly square with rounded corners still
        # hugs the edges much closer than it hugs a circle, so this is far more
        # forgiving than radius-deviation + corner-count heuristics.
        rect_residual = 0.0
        for px, py in pts:
            rect_residual += min(abs(px - min_x), abs(px - max_x), abs(py - min_y), abs(py - max_y))
        rect_residual = rect_residual / len(pts) / span

        if avg_r > 0:
            circle_residual = sum(abs(math.hypot(p[0] - cx, p[1] - cy) - avg_r) for p in pts) / len(pts) / avg_r
        else:
            circle_residual = 1.0

        # Bias toward rectangle: circle must beat rect by a clear margin.
        is_circle = (
            circle_residual < rect_residual * 0.75
            and aspect > 0.72
            and corner_count >= 6
        )
        is_triangle = (
            not is_circle
            and corner_count == 3
            and rect_residual > 0.08
            and len(simplified) >= 3
        )

        if is_circle:
            steps = 72
            result: list[tuple[float, float]] = []
            for i in range(steps + 1):
                angle = 2.0 * math.pi * i / steps
                result.append((cx + avg_r * math.cos(angle), cy + avg_r * math.sin(angle)))
            return result
        if is_triangle:
            tri = [simplified[0], simplified[1], simplified[2]]
            return [tri[0], tri[1], tri[2], tri[0]]
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
        # Save with a transparent background so the resulting PNG is
        # just the strokes — no solid black/white rectangle around
        # them. The custom-gesture "show_overlay_drawing" action
        # depends on this so a saved drawing can be re-displayed as
        # a click-through overlay on top of any app. Backwards-
        # compatible for users who just want the file: a transparent
        # PNG opens fine in every viewer / editor and shows the
        # stroke colors against whatever the viewer's background is.
        output = QImage(self._canvas.size(), QImage.Format_ARGB32_Premultiplied)
        output.fill(Qt.transparent)
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

    def set_grab_transform(self, dx_norm: float, dy_norm: float, scale: float) -> None:
        """Apply a live translate + scale transform to the displayed
        canvas during a pinch-grab. Translation is in normalised
        screen units (1.0 = full width / height). Scale is a
        multiplier on the canvas's natural size — driven by the
        distance between the two pinching palms when the user is
        bimanual-pinching. Clamped to [0.1, 10.0] so a fast
        accidental two-hand pinch can't shrink the canvas to
        nothing or blow it off the screen. apply_grab_to_canvas()
        bakes both translate AND scale into the canvas pixels at
        grab-end so subsequent strokes / saves reflect the new
        position + size."""
        self._grab_dx_norm = float(dx_norm)
        self._grab_dy_norm = float(dy_norm)
        self._grab_scale = max(0.1, min(10.0, float(scale)))
        self.update()

    def reset_grab_transform(self) -> None:
        self._grab_dx_norm = 0.0
        self._grab_dy_norm = 0.0
        self._grab_scale = 1.0
        self.update()

    def apply_grab_to_canvas(self) -> None:
        """Bake the current live grab transform (translate + scale)
        into the canvas pixels so subsequent strokes draw on top of
        the moved/stretched content and saving captures it. No-op
        when nothing has changed. Pushes a history entry first so
        a left-swipe undo restores the pre-grab canvas (revert any
        movement AND any stretching in one step)."""
        if (
            self._grab_dx_norm == 0.0
            and self._grab_dy_norm == 0.0
            and self._grab_scale == 1.0
        ):
            return
        self._ensure_canvas_size()
        if self._canvas.isNull():
            self._grab_dx_norm = 0.0
            self._grab_dy_norm = 0.0
            self._grab_scale = 1.0
            return
        # History entry: snapshot of the canvas BEFORE the move so
        # an undo restores the pre-grab position + size in one
        # step. Goes through push_undo_state so the snapshot uses
        # the same _clone_canvas / _clone_strokes helpers the rest
        # of the undo machinery does — keeps the entry shape
        # identical to a stroke commit, which means the existing
        # undo_last_action path restores it without any changes.
        try:
            self.push_undo_state()
        except Exception:
            pass
        dx_px = int(self._grab_dx_norm * self.width())
        dy_px = int(self._grab_dy_norm * self.height())
        new_canvas = QImage(self._canvas.size(), QImage.Format_ARGB32_Premultiplied)
        new_canvas.fill(Qt.transparent)
        # Rasterise the source canvas at the new scale, then blit
        # it onto the same-size new_canvas at the translated
        # position. Centering the scaled blit on the canvas
        # midpoint (rather than the top-left) means a pure scale
        # change keeps the strokes anchored where they already
        # were instead of pushing everything down-and-right as it
        # grew — that matched the user's mental model in testing.
        painter = QPainter(new_canvas)
        if self._grab_scale != 1.0:
            scaled_w = max(1, int(self._canvas.width() * self._grab_scale))
            scaled_h = max(1, int(self._canvas.height() * self._grab_scale))
            scaled = self._canvas.scaled(
                scaled_w,
                scaled_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            cx = self._canvas.width() // 2
            cy = self._canvas.height() // 2
            blit_x = cx - scaled.width() // 2 + dx_px
            blit_y = cy - scaled.height() // 2 + dy_px
            painter.drawImage(blit_x, blit_y, scaled)
        else:
            painter.drawImage(dx_px, dy_px, self._canvas)
        painter.end()
        self._canvas = new_canvas
        # Mark stored strokes as out of sync — they still have the
        # pre-translation coordinates because Phase 1 only moves
        # the rasterised pixels. Per-stroke point updates land in
        # Phase 2 along with the sidecar stroke storage. Setting
        # _raster_dirty here means the rasteriser knows it can't
        # rebuild from strokes alone without also re-applying the
        # baked offset.
        self._raster_dirty = True
        self._grab_dx_norm = 0.0
        self._grab_dy_norm = 0.0
        self._grab_scale = 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self._canvas.isNull():
            # Live grab applies translate + scale on every paint
            # while the user is pinching. Both clear back to
            # identity on apply_grab_to_canvas() (bakes into the
            # canvas pixels) or reset_grab_transform() (cancels
            # without baking). Scale is centred on the canvas
            # midpoint so a pure stretch grows outward in all
            # directions instead of pushing everything down-right.
            tx_px = int(self._grab_dx_norm * self.width())
            ty_px = int(self._grab_dy_norm * self.height())
            if self._grab_scale != 1.0:
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                scaled_w = max(1, int(self._canvas.width() * self._grab_scale))
                scaled_h = max(1, int(self._canvas.height() * self._grab_scale))
                scaled = self._canvas.scaled(
                    scaled_w,
                    scaled_h,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                cx = self.width() // 2
                cy = self.height() // 2
                bx = cx - scaled.width() // 2 + tx_px
                by = cy - scaled.height() // 2 + ty_px
                painter.drawImage(bx, by, scaled)
            else:
                painter.drawImage(tx_px, ty_px, self._canvas)

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


class ProcessingOverlay(QWidget):
    """Bottom-center "Processing ..." pill with animated dots.

    Visual idiom matches VoiceStatusOverlay (blue translucent
    panel, teal border, light text), so the user reads them as
    the same family. Mouse-transparent.

        overlay = ProcessingOverlay()
        overlay.show_processing("Processing 60s clip")
        # ... do work on a worker thread ...
        overlay.hide_processing()
    """

    # Pill geometry. The overlay WINDOW is sized exactly to the
    # pill (no extra transparent margin around it), otherwise the
    # transparent margin reads on screen as a faint rectangular
    # halo around the pill — what the user reported as a
    # "transparent border." Qt's WA_TranslucentBackground keeps
    # the transparent area invisible *in theory*, but some
    # compositors / GPU drivers leave a 1-pixel residue at the
    # window edge.
    _PILL_WIDTH = 220
    _PILL_HEIGHT = 88
    _SCREEN_BOTTOM_GAP = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        # Window flags mirror VoiceStatusOverlay (which renders
        # correctly on this user's stack). We deliberately do NOT
        # use Qt.WindowTransparentForInput: that flag interacts
        # badly with translucent layered windows on some Win32/GPU
        # combinations and was blocking paint events from landing.
        # WA_TransparentForMouseEvents already covers the
        # "ignore mouse input" goal.
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
        self._label = "Processing"
        # Two progress fields: _progress_target is the goal pushed
        # by set_progress() callers at each init checkpoint, and
        # _progress is the currently-rendered fraction. _tick eases
        # _progress toward _progress_target every frame so the bar
        # animates SMOOTHLY between checkpoints instead of jumping
        # in one big step. Also _progress keeps creeping forward at
        # a slow idle rate so the bar never looks stuck even if no
        # new checkpoint arrives for a while.
        self._progress = 0.0
        self._progress_target = 0.0
        # Idle creep: when target hasn't advanced recently, push
        # the target up slowly so the bar visibly moves even
        # between checkpoints. Capped at 0.92 so we never overrun
        # the "real work done" signal.
        self._progress_idle_creep_rate = 0.05  # fraction/sec
        self._progress_idle_creep_cap = 0.92
        self._last_tick_time = time.monotonic()
        # When True, the timer will hide the pill once _progress
        # has eased to ~1.0. Set by complete_and_hide(); lets the
        # bar visibly fill before disappearing.
        self._hide_when_complete = False
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.resize(self._PILL_WIDTH, self._PILL_HEIGHT)

    def _place_on_screen(self) -> None:
        # Bottom-center of the primary screen — same pattern the
        # voice status overlay uses, so the user reads them as
        # related.
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.move(40, 40)
            return
        geo = screen.availableGeometry()
        self.resize(self._PILL_WIDTH, self._PILL_HEIGHT)
        x = geo.center().x() - self._PILL_WIDTH // 2
        y = geo.bottom() - self._PILL_HEIGHT - self._SCREEN_BOTTOM_GAP
        self.move(x, y)

    def _tick(self) -> None:
        # Smooth-easing tick: nudge _progress toward _progress_target,
        # and (when no new checkpoint has arrived) creep _progress_target
        # forward slowly so the bar never sits still long enough to
        # look broken.
        if not self.isVisible():
            return
        now = time.monotonic()
        dt = max(0.0, min(0.1, now - self._last_tick_time))
        self._last_tick_time = now
        # Idle creep: lifts the target toward the cap when not at
        # the hide-on-complete phase. Stops once a real checkpoint
        # pushes the target above the cap.
        if not self._hide_when_complete and self._progress_target < self._progress_idle_creep_cap:
            self._progress_target = min(
                self._progress_idle_creep_cap,
                self._progress_target + self._progress_idle_creep_rate * dt,
            )
        # Ease toward target. Cap dt for the easing factor to 40 ms
        # so that when the UI thread comes back from a long block
        # the bar doesn't snap straight to the target in one frame —
        # it catches up over several 16 ms ticks instead, which the
        # eye reads as smooth motion rather than a jump.
        delta = self._progress_target - self._progress
        if abs(delta) > 0.0005:
            dt_capped = min(dt, 0.04)
            factor = 1.0 - math.exp(-10.0 * dt_capped)
            self._progress += delta * factor
            if self._progress > 1.0:
                self._progress = 1.0
        if self._hide_when_complete and self._progress >= 0.998:
            self._progress = 1.0
            self._hide_when_complete = False
            self.repaint()  # final 100 % frame
            self._timer.stop()
            self.hide()
            return
        self.repaint()

    def set_progress(self, fraction: float) -> None:
        """Bump the progress TARGET. The displayed bar eases toward
        it in _tick() so jumps between checkpoints look smooth
        instead of stepped."""
        try:
            new_target = max(0.0, min(1.0, float(fraction)))
        except Exception:
            return
        # Don't shrink the target — protects against out-of-order updates.
        if new_target < self._progress_target:
            return
        self._progress_target = new_target

    def complete_and_hide(self) -> None:
        """Smoothly fill the bar to 100 %, then hide. Use this on
        the success path so the user sees the bar actually finish
        before the pill disappears (otherwise we hide while the bar
        is mid-fill and the eye doesn't register the completion)."""
        self._progress_target = 1.0
        self._hide_when_complete = True
        # Make sure the timer is running so _tick can do the
        # easing. If the pill was hidden somehow without stopping
        # the timer, the isVisible() guard in _tick is a no-op.
        if not self._timer.isActive():
            self._timer.start()

    def show_processing(self, label: str = "Processing") -> None:
        self._label = str(label or "Processing")
        self._progress = 0.0
        self._progress_target = 0.0
        self._hide_when_complete = False
        self._last_tick_time = time.monotonic()
        self._place_on_screen()
        self.show()
        self.raise_()
        # Force a synchronous paint + event flush BEFORE returning.
        # show_processing("Starting Touchless") is called immediately
        # before start_engine blocks the UI thread with worker spin-
        # up; without forcing the paint here, Qt schedules it async,
        # the UI thread is then busy for ~200 ms+, and DWM ends up
        # composting an empty frame (which the user sees as a
        # transparent rectangle with no pill content).
        self.repaint()
        try:
            QApplication.processEvents()
        except Exception:
            pass
        # apply_overlay() strips the DWM rectangle halo around the
        # layered window (DwmSetWindowAttribute disables the system
        # border-color + non-client rendering). Must be called AFTER
        # show() and AFTER the first repaint -- before that the HWND
        # isn't fully realized and DwmSetWindowAttribute returns
        # E_HANDLE silently.
        apply_overlay(self)
        self._timer.start()

    def hide_processing(self) -> None:
        self._timer.stop()
        self.hide()

    def _draw_loading_dots(self, painter: QPainter, cx: float, cy: float, accent: QColor) -> None:
        # Verbatim copy of VoiceStatusOverlay._draw_loading_dots so
        # the indicator reads exactly like the voice "recognising"
        # dots.
        painter.setPen(Qt.NoPen)
        phase = time.monotonic() * 6.0
        for index in range(5):
            wave = max(0.0, math.sin(phase - index * 0.48))
            pulse = 0.38 + 0.62 * wave
            dot = QColor(accent)
            dot.setAlpha(int(90 + 150 * pulse))
            painter.setBrush(dot)
            x = cx + (index - 2) * 14
            y = cy - 2 - 6 * wave
            size = 8.0 + 3.0 * pulse
            painter.drawEllipse(QRectF(x - size / 2.0, y - size / 2.0, size, size))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # Same palette as VoiceStatusOverlay's command panel —
        # translucent blue body, teal border, light foreground.
        panel = QColor(25, 73, 143, 164)
        border = QColor(29, 233, 182, 210)
        text_color = QColor(232, 246, 255, 238)
        accent = QColor(29, 233, 182)

        # Inset the rect by 0.5 px to keep the antialiased border
        # fully inside the window bounds (otherwise the outermost
        # half-pixel of the border would clip).
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(panel)
        painter.drawRoundedRect(rect, 18.0, 18.0)

        # Stacked layout: label on top, progress bar below. Bar
        # steps forward as init checkpoints complete (volume API
        # bound, voice listener ready, etc.) -- on a stepped bar
        # the discontinuities read as real progress instead of a
        # broken animation, which is what we get for free even when
        # the UI thread is intermittently blocked during startup.
        font = QFont("Segoe UI", 12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(text_color))
        label_rect = QRectF(rect.left() + 12, rect.top() + 14, rect.width() - 24, 24)
        painter.drawText(label_rect, Qt.AlignCenter, self._label)

        # Progress bar: thin rounded track + accent-coloured fill
        # whose width = progress * track_width.
        bar_h = 6.0
        bar_y = rect.bottom() - 20
        bar_left = rect.left() + 18
        bar_right = rect.right() - 18
        bar_w = bar_right - bar_left
        track = QColor(accent.red(), accent.green(), accent.blue(), 55)
        fill = QColor(accent.red(), accent.green(), accent.blue(), 235)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(bar_left, bar_y, bar_w, bar_h), bar_h / 2.0, bar_h / 2.0)
        fill_w = bar_w * float(self._progress)
        if fill_w > 0.5:
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(bar_left, bar_y, fill_w, bar_h), bar_h / 2.0, bar_h / 2.0)


class SavedLocationOverlay(QWidget):
    """Bottom-center pill that briefly shows where a file was just
    saved, then fades away.

    Same blue/teal palette as ProcessingOverlay and
    VoiceStatusOverlay so the user reads them as the same family.
    Width auto-fits the path text, capped at 80 % of screen width;
    paths longer than that are middle-elided so the user still
    sees the leading drive letter and the filename.

    Lifecycle:
        overlay.show_saved("Saved in: C:/.../foo.mp4", fade_after_ms=3000)
    The overlay then animates its windowOpacity from 1.0 → 0.0
    over the last ~600 ms of the visible window and hides itself
    when the animation finishes.
    """

    _PILL_HEIGHT = 56
    _PILL_PADDING_X = 28
    _SCREEN_BOTTOM_GAP = 64
    _MIN_WIDTH = 280
    _MAX_WIDTH_FRAC = 0.80  # of screen width

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self._text = ""
        self._displayed_text = ""
        # Hold-then-fade timers. Hold duration = total_ms - fade_ms.
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._begin_fade)
        # Fade animation tick (16 ms ~ 60 fps).
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._tick_fade)
        self._fade_total_ms = 600
        self._fade_remaining_ms = 0
        self.resize(self._MIN_WIDTH, self._PILL_HEIGHT)

    def show_saved(self, text: str, *, total_ms: int = 3000, fade_ms: int = 600) -> None:
        self._text = str(text or "")
        # Stop any prior cycle so a new save replaces the old pill
        # cleanly.
        self._hold_timer.stop()
        self._fade_timer.stop()
        self._fade_remaining_ms = 0
        self._fit_to_text()
        self._place_on_screen()
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self.repaint()
        try:
            QApplication.processEvents()
        except Exception:
            pass
        apply_overlay(self)
        self._fade_total_ms = max(50, int(fade_ms))
        hold_ms = max(0, int(total_ms) - self._fade_total_ms)
        self._hold_timer.start(hold_ms)

    def _begin_fade(self) -> None:
        self._fade_remaining_ms = self._fade_total_ms
        self._fade_timer.start()

    def _tick_fade(self) -> None:
        self._fade_remaining_ms -= self._fade_timer.interval()
        if self._fade_remaining_ms <= 0:
            self._fade_timer.stop()
            self.hide()
            self.setWindowOpacity(1.0)
            return
        self.setWindowOpacity(max(0.0, self._fade_remaining_ms / float(self._fade_total_ms)))

    def _fit_to_text(self) -> None:
        # Compute pill width from the rendered text width, with
        # min/max bounds. If the text exceeds the max, switch to
        # middle-elision so we keep the drive prefix + the file
        # name visible.
        screen = self.screen() or QGuiApplication.primaryScreen()
        screen_w = screen.availableGeometry().width() if screen is not None else 1280
        max_pill_w = max(self._MIN_WIDTH, int(screen_w * self._MAX_WIDTH_FRAC))
        font = QFont("Segoe UI", 12)
        font.setBold(True)
        metrics = QFontMetrics(font)
        full_w = metrics.horizontalAdvance(self._text) + 2 * self._PILL_PADDING_X
        if full_w <= max_pill_w:
            self._displayed_text = self._text
            self.resize(max(self._MIN_WIDTH, full_w), self._PILL_HEIGHT)
            return
        # Too long: elide middle so leading drive + trailing name
        # both stay visible.
        target_text_w = max_pill_w - 2 * self._PILL_PADDING_X
        self._displayed_text = metrics.elidedText(self._text, Qt.ElideMiddle, target_text_w)
        self.resize(max_pill_w, self._PILL_HEIGHT)

    def _place_on_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.move(40, 40)
            return
        geo = screen.availableGeometry()
        x = geo.center().x() - self.width() // 2
        y = geo.bottom() - self.height() - self._SCREEN_BOTTOM_GAP
        self.move(x, y)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        panel = QColor(25, 73, 143, 164)
        border = QColor(29, 233, 182, 210)
        text_color = QColor(232, 246, 255, 238)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(panel)
        painter.drawRoundedRect(rect, 18.0, 18.0)

        font = QFont("Segoe UI", 12)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(text_color))
        painter.drawText(rect, Qt.AlignCenter, self._displayed_text)


class TrackingQualityPill(QWidget):
    """Bottom-centre desktop pill mirroring the diagnostic
    'Tracking: ...' chip from LiveViewWindow. Visible whenever
    the user has enabled the 'show tracking quality' overlay AND
    the engine is running, so the readout sticks with the user
    even when the live-view window is closed."""

    _PILL_HEIGHT = 32
    _SCREEN_BOTTOM_GAP = 28
    _PADDING_X = 18

    _STATES = {
        "good": ("Tracking: Good",          QColor(29, 233, 182),  QColor(29, 233, 182, 60), QColor(29, 233, 182, 200)),
        "fair": ("Tracking: Marginal",       QColor(245, 180, 80),  QColor(245, 180, 80, 60), QColor(245, 180, 80, 200)),
        "poor": ("Tracking: No hand seen",   QColor(255, 138, 138), QColor(255, 107, 107, 60), QColor(255, 107, 107, 200)),
        "idle": ("Tracking: —",         QColor(229, 246, 255, 200), QColor(20, 30, 50, 160), QColor(127, 127, 127, 120)),
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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
        self._state = "idle"
        self._last_hand_ts = 0.0
        self._fit_size_for_state()

    def _fit_size_for_state(self) -> None:
        text, _fg, _bg, _border = self._STATES.get(self._state, self._STATES["idle"])
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        metrics = QFontMetrics(font)
        w = metrics.horizontalAdvance(text) + 2 * self._PADDING_X
        self.resize(max(170, w), self._PILL_HEIGHT)

    def _place_on_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.center().x() - self.width() // 2
        y = geo.bottom() - self.height() - self._SCREEN_BOTTOM_GAP
        self.move(x, y)

    def update_state(self, *, found: bool, confidence: float) -> None:
        """Feed a per-engine-frame tracking observation. State
        decays to 'poor' if no hand seen for 0.6 s (down from the
        live-view chip's 1.5 s -- user reported the desktop pill
        feeling laggy with the longer window)."""
        now = time.monotonic()
        if found:
            self._last_hand_ts = now
            new_state = "good" if confidence >= 0.65 else ("fair" if confidence >= 0.45 else "poor")
        elif now - self._last_hand_ts >= 0.6:
            new_state = "poor"
        else:
            new_state = self._state
        if new_state != self._state:
            self._state = new_state
            self._fit_size_for_state()
            if self.isVisible():
                self._place_on_screen()
                self.repaint()

    def show_pill(self) -> None:
        self._fit_size_for_state()
        self._place_on_screen()
        self.show()
        self.raise_()
        self.repaint()
        try:
            apply_overlay(self)
        except Exception:
            pass

    def hide_pill(self) -> None:
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt API name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        text, fg, bg, border_color = self._STATES.get(self._state, self._STATES["idle"])
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(border_color, 1.2))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(fg))
        painter.drawText(rect, Qt.AlignCenter, text)


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

# Author: Konstantin Markov
