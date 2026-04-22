from __future__ import annotations

import cv2
from PySide6.QtGui import QGuiApplication

from .mouse_gesture import MouseDebugState


def draw_mouse_control_box_overlay(
    frame,
    *,
    debug_state: MouseDebugState,
    mode_enabled: bool,
) -> None:
    if not mode_enabled or debug_state.camera_control_bounds is None:
        return

    frame_h, frame_w = frame.shape[:2]
    x1 = int(round(debug_state.camera_control_bounds[0] * frame_w))
    y1 = int(round(debug_state.camera_control_bounds[1] * frame_h))
    x2 = int(round(debug_state.camera_control_bounds[2] * frame_w))
    y2 = int(round(debug_state.camera_control_bounds[3] * frame_h))
    if x2 <= x1 + 12 or y2 <= y1 + 12:
        return

    overlay = frame.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), (48, 56, 236), thickness=-1)
    cv2.addWeighted(overlay, 0.08, frame, 0.92, 0.0, frame)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (28, 36, 255), thickness=4)

    label = "Mouse control area"
    label_origin = (x1 + 10, y1 - 10 if y1 >= 28 else y1 + 22)
    cv2.putText(frame, label, label_origin, cv2.FONT_HERSHEY_SIMPLEX, 0.72, (28, 36, 255), 2, cv2.LINE_AA)

    if debug_state.camera_anchor_position is not None:
        anchor_x = int(round(debug_state.camera_anchor_position[0] * frame_w))
        anchor_y = int(round(debug_state.camera_anchor_position[1] * frame_h))
        cv2.circle(frame, (anchor_x, anchor_y), 7, (255, 248, 212), thickness=1)
        cv2.line(frame, (anchor_x - 8, anchor_y), (anchor_x + 8, anchor_y), (255, 248, 212), 1, cv2.LINE_AA)
        cv2.line(frame, (anchor_x, anchor_y - 8), (anchor_x, anchor_y + 8), (255, 248, 212), 1, cv2.LINE_AA)


def draw_mouse_monitor_overlay(
    frame,
    *,
    mouse_controller,
    debug_state: MouseDebugState,
    mode_enabled: bool,
) -> None:
    if not getattr(mouse_controller, "available", False):
        return

    bounds = mouse_controller.virtual_bounds()
    screens = QGuiApplication.screens()
    if bounds is None or not screens:
        return

    left, top, width, height = bounds
    width = max(1, int(width))
    height = max(1, int(height))
    frame_h, frame_w = frame.shape[:2]
    panel_w = max(220, min(340, int(frame_w * 0.34)))
    panel_h = max(136, min(224, int(frame_h * 0.24)))
    panel_x = 14
    panel_y = 14
    panel_x2 = min(frame_w - 14, panel_x + panel_w)
    panel_y2 = min(frame_h - 14, panel_y + panel_h)
    if panel_x2 <= panel_x + 50 or panel_y2 <= panel_y + 50:
        return

    overlay = frame.copy()
    cv2.rectangle(overlay, (panel_x, panel_y), (panel_x2, panel_y2), (15, 24, 42), thickness=-1)
    cv2.addWeighted(overlay, 0.78, frame, 0.22, 0.0, frame)
    border_color = (36, 220, 184) if mode_enabled else (112, 155, 188)
    cv2.rectangle(frame, (panel_x, panel_y), (panel_x2, panel_y2), border_color, thickness=2)
    cv2.putText(frame, "Desktop Map", (panel_x + 12, panel_y + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.54, (230, 240, 248), 1, cv2.LINE_AA)

    status_text = "mouse active" if debug_state.mode_enabled else "mouse idle"
    cv2.putText(frame, status_text, (panel_x + 12, panel_y + 42), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (184, 206, 224), 1, cv2.LINE_AA)

    content_x = panel_x + 12
    content_y = panel_y + 54
    content_w = max(40, panel_x2 - content_x - 12)
    content_h = max(40, panel_y2 - content_y - 12)
    scale = min(content_w / width, content_h / height)
    draw_w = int(round(width * scale))
    draw_h = int(round(height * scale))
    draw_x = content_x + (content_w - draw_w) // 2
    draw_y = content_y + (content_h - draw_h) // 2

    map_overlay = frame.copy()
    cv2.rectangle(map_overlay, (draw_x, draw_y), (draw_x + draw_w, draw_y + draw_h), (8, 14, 26), thickness=-1)
    cv2.addWeighted(map_overlay, 0.56, frame, 0.44, 0.0, frame)
    cv2.rectangle(frame, (draw_x, draw_y), (draw_x + draw_w, draw_y + draw_h), (92, 124, 154), thickness=1)

    primary_screen = QGuiApplication.primaryScreen()
    for index, screen in enumerate(screens, start=1):
        geo = screen.geometry()
        sx1 = draw_x + int(round((geo.x() - left) * scale))
        sy1 = draw_y + int(round((geo.y() - top) * scale))
        sx2 = draw_x + int(round((geo.x() + geo.width() - left) * scale))
        sy2 = draw_y + int(round((geo.y() + geo.height() - top) * scale))
        fill = (39, 72, 108) if screen != primary_screen else (58, 122, 96)
        cv2.rectangle(frame, (sx1, sy1), (sx2, sy2), fill, thickness=-1)
        cv2.rectangle(frame, (sx1, sy1), (sx2, sy2), (228, 236, 243), thickness=1)
        label = f"{index}{'*' if screen == primary_screen else ''}"
        cv2.putText(
            frame,
            label,
            (sx1 + 6, min(sy2 - 6, sy1 + 16)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.40,
            (245, 250, 252),
            1,
            cv2.LINE_AA,
        )

    cursor_position = mouse_controller.current_position()
    if cursor_position is None:
        return

    cursor_x = draw_x + int(round((cursor_position[0] - left) * scale))
    cursor_y = draw_y + int(round((cursor_position[1] - top) * scale))
    cv2.circle(frame, (cursor_x, cursor_y), 5, (255, 255, 255), thickness=-1)
    cv2.circle(frame, (cursor_x, cursor_y), 9, (36, 220, 184), thickness=1)

    if debug_state.mode_enabled and debug_state.cursor_reach_bounds is not None:
        reach_x1 = draw_x + int(round(debug_state.cursor_reach_bounds[0] * draw_w))
        reach_y1 = draw_y + int(round(debug_state.cursor_reach_bounds[1] * draw_h))
        reach_x2 = draw_x + int(round(debug_state.cursor_reach_bounds[2] * draw_w))
        reach_y2 = draw_y + int(round(debug_state.cursor_reach_bounds[3] * draw_h))
        ratio_overlay = frame.copy()
        cv2.rectangle(ratio_overlay, (reach_x1, reach_y1), (reach_x2, reach_y2), (36, 220, 184), thickness=-1)
        cv2.addWeighted(ratio_overlay, 0.12, frame, 0.88, 0.0, frame)
        cv2.rectangle(frame, (reach_x1, reach_y1), (reach_x2, reach_y2), (36, 220, 184), thickness=1)

    if debug_state.cursor_anchor_position is not None:
        anchor_x = draw_x + int(round(debug_state.cursor_anchor_position[0] * draw_w))
        anchor_y = draw_y + int(round(debug_state.cursor_anchor_position[1] * draw_h))
        cv2.line(frame, (draw_x, anchor_y), (draw_x + draw_w, anchor_y), (64, 184, 170), 1, cv2.LINE_AA)
        cv2.line(frame, (anchor_x, draw_y), (anchor_x, draw_y + draw_h), (64, 184, 170), 1, cv2.LINE_AA)
        cv2.circle(frame, (anchor_x, anchor_y), 7, (84, 240, 214), thickness=1)
        cv2.line(frame, (anchor_x - 5, anchor_y), (anchor_x + 5, anchor_y), (84, 240, 214), 1, cv2.LINE_AA)
        cv2.line(frame, (anchor_x, anchor_y - 5), (anchor_x, anchor_y + 5), (84, 240, 214), 1, cv2.LINE_AA)
