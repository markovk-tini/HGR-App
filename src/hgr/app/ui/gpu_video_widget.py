"""GPU-backed video display widget.

Why this exists:
The QLabel.setPixmap path used to display the camera feed ran on
every frame:

    cv2.cvtColor(BGR→RGB)   ~3 ms at 720p
    cv2.resize(INTER_AREA)   ~1-2 ms
    QImage.copy()            ~1-2 ms
    QPixmap.fromImage()      ~1 ms (allocates GPU texture)
    setPixmap + paint event  ~1-2 ms

That's ~7-10 ms of CPU work per frame just to put a camera image on
screen. This widget skips all of it: it constructs a QImage with
Format_BGR888 (no CPU colour conversion — the GPU's texture sampler
handles BGR natively) and uses Qt's raster paint engine (D3D-backed
on Windows, GPU-accelerated by default) to draw image + landmarks.

We do NOT use QOpenGLWidget — that adds an OpenGL/ANGLE driver path
that on some Windows setups silently coalesces frames and produces
~9 fps perceived display rate even when the worker is running at
30+ fps. The default QWidget raster engine is already GPU-backed
through Qt's D3D11 paint backend, with much less driver-stack risk.

Includes a paint-rate counter that logs `[gpu_video] paint rate: N
fps` every 2 s, so display rate can be observed independently of
the worker's `actual self._fps`.
"""
from __future__ import annotations

import sys
import time
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QImage, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

# MediaPipe's 21-landmark hand connections (pairs of indices).
# Same topology the cv2-based draw_hand_overlay used to draw on the
# BGR frame — we now draw it on the GPU instead.
_HAND_CONNECTIONS: Tuple[Tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),         # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),         # index
    (5, 9), (9, 10), (10, 11), (11, 12),    # middle
    (9, 13), (13, 14), (14, 15), (15, 16),  # ring
    (13, 17), (17, 18), (18, 19), (19, 20), # pinky
    (0, 17),                                # palm base to pinky
)


class GpuVideoWidget(QWidget):
    """Drop-in replacement for the QLabel video panel.

    Public API (used by mini_live_viewer / live_view_window):
      - update_frame(bgr_numpy)  → schedule GPU repaint with new frame
      - update_landmarks(hands)  → list of per-hand normalized [(x,y), ...]
      - clear_video(idle_text)   → drop the frame, optionally show idle text
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: Optional[QImage] = None
        self._image_w: int = 0
        self._image_h: int = 0
        # Per-hand display info — list of dicts with keys:
        #   landmarks: list[(x, y)] normalized
        #   bbox:      (x, y, w, h) normalized | None
        #   handedness: "Left" / "Right" / ""
        #   label:     gesture name string (empty when inactive)
        #   active:    bool — toggles bbox to green
        # Both hands are equal — the engine produces a separate
        # prediction per hand and either can drive its own active
        # state independently. There is no "primary" / "secondary"
        # distinction in the display.
        self._hands_info: List[dict] = []
        # Mouse-mode "control area" overlay. Set by update_landmarks
        # when the engine emits a payload containing mouse_overlay
        # data. Dict with keys "bounds" (x1, y1, x2, y2 normalized
        # in [0, 1] image coords) and optional "anchor" (ax, ay).
        self._mouse_overlay: Optional[dict] = None
        self._idle_text: str = ""
        self._idle_font = QFont("Segoe UI", 10)
        self._banner_font = QFont("Segoe UI", 9)
        self._banner_font.setBold(True)
        # RoundCap on the landmark pen so each drawPoint renders as
        # a round disc instead of a square — at 5 px it's visibly
        # circular and reads as a "joint dot" rather than a pixel
        # cluster. Connection pen stays square-cap (default) and
        # bumps from 2 px to 3 px for a slightly chunkier skeleton.
        self._landmark_pen = QPen(QColor(29, 233, 182), 5)
        self._landmark_pen.setCapStyle(Qt.RoundCap)
        self._connection_pen = QPen(QColor(29, 233, 182, 200), 3)
        # Bbox colors — red default, green when that hand has a
        # recognized gesture.
        self._bbox_inactive_color = QColor(232, 72, 72, 235)
        self._bbox_active_color = QColor(70, 220, 130, 235)
        # Mouse-mode control box: red border so the user
        # immediately reads "this is the active region — keep your
        # hand in here." Faint red fill for the area itself.
        self._mouse_box_color = QColor(255, 64, 56, 235)
        self._mouse_box_fill_color = QColor(255, 64, 56, 24)
        self._mouse_anchor_color = QColor(255, 248, 212, 230)
        self._banner_bg_color = QColor(0, 0, 0, 150)
        self._banner_text_color = QColor(248, 250, 252, 250)
        self._idle_color = QColor(180, 200, 220)
        self._background = QColor(7, 19, 29)
        # Paint-rate diagnostic — prints `[gpu_video] paint rate:`
        # every 2 s so we can see whether the actual on-screen
        # update rate matches the worker's emit rate. If they
        # diverge, paint events are coalescing somewhere.
        self._paint_count = 0
        self._paint_log_at = 0.0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(220, 140)
        # Disable Qt's automatic background fill — we paint the
        # whole rect ourselves in paintEvent.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    # ----- public API used by the receivers ------------------

    def update_frame(self, bgr_frame: np.ndarray) -> None:
        """Hand the widget a new BGR frame. The GPU paint will pick
        it up on the next paintGL. We `.copy()` so the worker's
        reader thread can safely overwrite its source buffer."""
        if bgr_frame is None or bgr_frame.size == 0:
            return
        try:
            h, w = bgr_frame.shape[:2]
        except Exception:
            return
        if h <= 0 or w <= 0:
            return
        # Format_BGR888 stores 3 bytes/pixel B,G,R in that order
        # — same as the cv2 numpy buffer. Qt's GL paint engine
        # handles the BGR-vs-RGB sampler swizzle on the GPU, so
        # we skip the CPU cv2.cvtColor pass entirely.
        self._image = QImage(
            bgr_frame.data, w, h, 3 * w, QImage.Format_BGR888
        ).copy()
        self._image_w = w
        self._image_h = h
        self._idle_text = ""
        self.update()

    def update_landmarks(self, payload: Optional[object]) -> None:
        """Store per-hand display info for the next paintEvent.

        Accepted payload shapes:
          - dict {"hands": [...], "mouse_overlay": {...}|None} — full
            payload from the engine when mouse mode is on (or any
            other future overlay we layer on the camera frame)
          - iterable of per-hand dicts with keys landmarks, bbox,
            handedness, label, active — the bare hands list when
            no mouse overlay is needed
          - iterable of plain list-of-(x,y) tuples — legacy shape
            from before the bbox/banner additions

        `update()` is NOT called here — the next `update_frame` will
        schedule the repaint, which keeps the overlay in sync with
        the frame it belongs to."""
        if payload is None:
            self._hands_info = []
            self._mouse_overlay = None
            return
        if isinstance(payload, dict):
            hands_info = payload.get("hands") or []
            mouse_overlay_raw = payload.get("mouse_overlay")
            if isinstance(mouse_overlay_raw, dict):
                bounds = mouse_overlay_raw.get("bounds")
                anchor = mouse_overlay_raw.get("anchor")
                if bounds is not None and len(bounds) == 4:
                    self._mouse_overlay = {
                        "bounds": tuple(float(v) for v in bounds),
                        "anchor": (
                            tuple(float(v) for v in anchor)
                            if anchor is not None and len(anchor) == 2
                            else None
                        ),
                    }
                else:
                    self._mouse_overlay = None
            else:
                self._mouse_overlay = None
        else:
            hands_info = payload
            self._mouse_overlay = None
        normalised: List[dict] = []
        for entry in hands_info:
            if entry is None:
                continue
            if isinstance(entry, dict):
                pts_raw = entry.get("landmarks") or []
                bbox = entry.get("bbox")
                handedness = str(entry.get("handedness") or "")
                label = str(entry.get("label") or "")
                active = bool(entry.get("active"))
            else:
                # Legacy: bare list of (x, y) tuples.
                pts_raw = entry
                bbox = None
                handedness = ""
                label = ""
                active = False
            pts: List[Tuple[float, float]] = []
            for pt in pts_raw:
                if pt is None:
                    continue
                try:
                    pts.append((float(pt[0]), float(pt[1])))
                except Exception:
                    continue
            if not pts and bbox is None:
                continue
            normalised.append({
                "landmarks": pts,
                "bbox": bbox,
                "handedness": handedness,
                "label": label,
                "active": active,
            })
        self._hands_info = normalised

    def clear_video(self, idle_text: str = "") -> None:
        self._image = None
        self._image_w = 0
        self._image_h = 0
        self._hands_info = []
        self._mouse_overlay = None
        self._idle_text = str(idle_text or "")
        self.update()

    # ----- paint -------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        # Qt's raster paint engine — D3D11-backed on Windows, so
        # already GPU-accelerated. drawImage with Format_BGR888
        # uploads to a texture and samples on the GPU; no CPU
        # colour conversion needed.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), self._background)
        target = self._aspect_target()
        if self._image is not None and not self._image.isNull():
            painter.drawImage(target, self._image)
            self._draw_landmarks(painter, target)
        elif self._idle_text:
            painter.setPen(QPen(self._idle_color, 1))
            painter.setFont(self._idle_font)
            painter.drawText(
                self.rect(),
                Qt.AlignCenter | Qt.TextWordWrap,
                self._idle_text,
            )
        painter.end()
        # Paint-rate diagnostic. Prints actual on-screen update
        # rate every 2 s so we can confirm whether the display is
        # tracking the worker's emit rate or coalescing.
        self._paint_count += 1
        now = time.monotonic()
        if self._paint_log_at == 0.0:
            self._paint_log_at = now
        elif now - self._paint_log_at >= 2.0:
            rate = self._paint_count / (now - self._paint_log_at)
            try:
                sys.stderr.write(
                    f"[gpu_video] paint rate: {rate:.1f} fps "
                    f"(widget={self.objectName() or type(self).__name__})\n"
                )
                sys.stderr.flush()
            except Exception:
                pass
            self._paint_count = 0
            self._paint_log_at = now

    def _aspect_target(self) -> QRect:
        if self._image_w <= 0 or self._image_h <= 0:
            return self.rect()
        wa = max(1, self.width())
        ha = max(1, self.height())
        scale = min(wa / float(self._image_w), ha / float(self._image_h))
        if scale <= 0:
            return self.rect()
        tw = max(1, int(self._image_w * scale))
        th = max(1, int(self._image_h * scale))
        x = (wa - tw) // 2
        y = (ha - th) // 2
        return QRect(x, y, tw, th)

    def _draw_landmarks(self, painter: QPainter, target: QRect) -> None:
        tx = target.x()
        ty = target.y()
        tw = target.width()
        th = target.height()

        # Mouse-mode control area. Painted first so the hand
        # skeleton/bbox overlay on top — keeps the box visually
        # behind the hand. Faint red fill + bold red border + a
        # small "Mouse control area" label so the user
        # immediately knows where to keep their hand.
        if self._mouse_overlay is not None:
            bx1, by1, bx2, by2 = self._mouse_overlay["bounds"]
            rx1 = bx1 * tw + tx
            ry1 = by1 * th + ty
            rx2 = bx2 * tw + tx
            ry2 = by2 * th + ty
            box_rect = QRectF(rx1, ry1, max(0.0, rx2 - rx1), max(0.0, ry2 - ry1))
            painter.fillRect(box_rect, self._mouse_box_fill_color)
            painter.setPen(QPen(self._mouse_box_color, 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(box_rect)
            label = "Mouse control area"
            painter.setFont(self._banner_font)
            metrics = QFontMetrics(self._banner_font)
            text_w = metrics.horizontalAdvance(label)
            label_h = metrics.height()
            if ry1 - label_h - 6 >= ty:
                bg_y = ry1 - label_h - 4
            else:
                bg_y = ry1 + 2
            bg_rect = QRectF(
                max(tx, rx1),
                bg_y,
                min(text_w + 10.0, tw - (max(tx, rx1) - tx)),
                label_h + 2.0,
            )
            painter.fillRect(bg_rect, self._banner_bg_color)
            painter.setPen(QPen(self._mouse_box_color, 1))
            painter.drawRect(bg_rect)
            painter.setPen(QPen(self._mouse_box_color, 1))
            painter.drawText(QPointF(bg_rect.x() + 5.0, bg_rect.y() + label_h - 3.0), label)
            anchor = self._mouse_overlay.get("anchor")
            if anchor is not None:
                ax = anchor[0] * tw + tx
                ay = anchor[1] * th + ty
                painter.setPen(QPen(self._mouse_anchor_color, 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(ax, ay), 7.0, 7.0)
                painter.drawLine(QPointF(ax - 8, ay), QPointF(ax + 8, ay))
                painter.drawLine(QPointF(ax, ay - 8), QPointF(ax, ay + 8))

        if not self._hands_info:
            return

        # Per-hand bbox + banner. Drawn first so the skeleton +
        # joints paint over them (avoids the bbox edge cutting
        # through a fingertip).
        painter.save()
        painter.setFont(self._banner_font)
        metrics = QFontMetrics(self._banner_font)
        banner_h = metrics.height()
        for hand in self._hands_info:
            bbox = hand.get("bbox")
            if bbox is None:
                continue
            color = self._bbox_active_color if hand.get("active") else self._bbox_inactive_color
            bx, by, bw, bh = bbox
            rx = bx * tw + tx
            ry = by * th + ty
            rw = bw * tw
            rh = bh * th
            rect = QRectF(rx, ry, rw, rh)
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

            # Banner: "Right | gesture" when the hand has a
            # recognized gesture, "Right" when neutral. Empty
            # handedness falls back to just the label (or nothing).
            handedness = hand.get("handedness", "") or ""
            label = hand.get("label", "") or ""
            if label:
                banner = f"{handedness} | {label}" if handedness else label
            else:
                banner = handedness
            if not banner:
                continue
            text_w = metrics.horizontalAdvance(banner)
            # Sit the banner just above the bbox; if the box is at
            # the top of the frame, drop the banner inside the box
            # instead so it never gets clipped off-screen.
            if ry - banner_h - 6 >= ty:
                bg_y = ry - banner_h - 4
            else:
                bg_y = ry + 2
            bg_rect = QRectF(
                max(tx, rx),
                bg_y,
                min(text_w + 10.0, tw - (max(tx, rx) - tx)),
                banner_h + 2.0,
            )
            painter.fillRect(bg_rect, self._banner_bg_color)
            painter.setPen(QPen(color, 1))
            painter.drawRect(bg_rect)
            painter.setPen(QPen(self._banner_text_color, 1))
            text_pt = QPointF(bg_rect.x() + 5.0, bg_rect.y() + banner_h - 3.0)
            painter.drawText(text_pt, banner)
        painter.restore()

        # Batch every connection across every hand into one drawLines
        # call and every joint into one drawPoints call. Replaces what
        # used to be ~84 individual painter.draw* calls per paint
        # (2 hands × (21 connections + 21 joints)) with 2 batched
        # paint ops + 2 pen swaps total. Each Qt paint call has
        # per-call overhead (transform, pen state, antialias setup);
        # batching collapses that overhead to constant.
        all_lines: list[QLineF] = []
        all_points: list[QPointF] = []
        for hand in self._hands_info:
            pts = hand.get("landmarks") or []
            n = len(pts)
            if n == 0:
                continue
            for a, b in _HAND_CONNECTIONS:
                if a >= n or b >= n:
                    continue
                ax, ay = pts[a][0], pts[a][1]
                bx, by = pts[b][0], pts[b][1]
                all_lines.append(QLineF(
                    ax * tw + tx, ay * th + ty,
                    bx * tw + tx, by * th + ty,
                ))
            for pt in pts:
                all_points.append(QPointF(pt[0] * tw + tx, pt[1] * th + ty))
        if all_lines:
            painter.setPen(self._connection_pen)
            painter.drawLines(all_lines)
        if all_points:
            painter.setPen(self._landmark_pen)
            painter.drawPoints(all_points)
