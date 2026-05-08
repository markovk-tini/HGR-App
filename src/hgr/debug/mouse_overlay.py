from __future__ import annotations

import cv2
from PySide6.QtGui import QGuiApplication

from .mouse_gesture import MouseDebugState


def draw_mouse_control_box_overlay(
    frame,
    *,
    debug_state: MouseDebugState,
    mode_enabled: bool,
    mouse_controller=None,
    active_monitor_index: int | None = None,
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

    # Optional monitor layout + virtual cursor inside the box. When
    # the user has multiple monitors, drawing each one proportionally
    # gives a 1:1 spatial mental model: hand left -> cursor left
    # in box -> actual mouse left across the desktop.
    drew_monitor_map = False
    if mouse_controller is not None:
        try:
            virtual_bounds = mouse_controller.virtual_bounds()
        except Exception:
            virtual_bounds = None
        if virtual_bounds is not None:
            v_left, v_top, v_w, v_h = virtual_bounds
            v_w = max(1.0, float(v_w))
            v_h = max(1.0, float(v_h))
            box_w = max(1, x2 - x1)
            box_h = max(1, y2 - y1)
            inset = 12
            inner_w = max(40, box_w - inset * 2)
            inner_h = max(40, box_h - inset * 2)
            scale = min(inner_w / v_w, inner_h / v_h)
            map_w = int(round(v_w * scale))
            map_h = int(round(v_h * scale))
            map_x = x1 + (box_w - map_w) // 2
            map_y = y1 + (box_h - map_h) // 2
            try:
                from PySide6.QtGui import QGuiApplication
                screens = QGuiApplication.screens()
                primary = QGuiApplication.primaryScreen()
            except Exception:
                screens = []
                primary = None
            if screens:
                # Faint backdrop so the monitor outlines read as
                # one cohesive map rather than disconnected boxes.
                backdrop = frame.copy()
                cv2.rectangle(backdrop, (map_x, map_y), (map_x + map_w, map_y + map_h), (8, 14, 26), thickness=-1)
                cv2.addWeighted(backdrop, 0.55, frame, 0.45, 0.0, frame)
                # Per-monitor highlighting:
                #   - active_monitor_index is None ("All Monitors")
                #     -> primary tinted green, secondaries blue,
                #        same as the historical default.
                #   - active_monitor_index is set to a specific
                #     screen -> that screen renders in accent green
                #     and every other screen renders DIMMER blue +
                #     thinner outline so the user can see at a
                #     glance which display the cursor is constrained
                #     to. Mirrors what _MouseControlMonitorPreview
                #     in main_window.py shows in Save Locations.
                accent_fill = (140, 220, 184)         # bright accent green
                accent_border = (220, 236, 232)       # near-white outline
                dim_fill = (32, 56, 84)               # darker blue
                dim_border = (96, 124, 156)           # softer outline
                neutral_primary_fill = (58, 122, 96)
                neutral_secondary_fill = (39, 72, 108)
                for idx, screen in enumerate(screens):
                    geo = screen.geometry()
                    sx1 = map_x + int(round((geo.x() - v_left) * scale))
                    sy1 = map_y + int(round((geo.y() - v_top) * scale))
                    sx2 = map_x + int(round((geo.x() + geo.width() - v_left) * scale))
                    sy2 = map_y + int(round((geo.y() + geo.height() - v_top) * scale))
                    if active_monitor_index is None:
                        fill = neutral_primary_fill if screen == primary else neutral_secondary_fill
                        border = (228, 236, 243)
                    else:
                        is_active = idx == active_monitor_index
                        fill = accent_fill if is_active else dim_fill
                        border = accent_border if is_active else dim_border
                    cv2.rectangle(frame, (sx1, sy1), (sx2, sy2), fill, thickness=-1)
                    cv2.rectangle(frame, (sx1, sy1), (sx2, sy2), border, thickness=1)
                drew_monitor_map = True
                # Virtual cursor dot at the actual mouse position
                # mapped into this monitor layout.
                try:
                    cursor_pos = mouse_controller.current_position()
                except Exception:
                    cursor_pos = None
                if cursor_pos is not None:
                    cx = map_x + int(round((cursor_pos[0] - v_left) * scale))
                    cy = map_y + int(round((cursor_pos[1] - v_top) * scale))
                    cv2.circle(frame, (cx, cy), 9, (255, 255, 255), thickness=-1, lineType=cv2.LINE_AA)
                    cv2.circle(frame, (cx, cy), 14, (36, 220, 184), thickness=3, lineType=cv2.LINE_AA)

    if not drew_monitor_map and debug_state.cursor_position is not None:
        # Single-monitor (or no controller passed): map the
        # normalized cursor_position (in [0, 1] of full virtual
        # desktop) directly into the box. Still gives the user
        # a "the cursor moves with my hand" affordance.
        box_w = max(1, x2 - x1)
        box_h = max(1, y2 - y1)
        cx = x1 + int(round(float(debug_state.cursor_position[0]) * box_w))
        cy = y1 + int(round(float(debug_state.cursor_position[1]) * box_h))
        cv2.circle(frame, (cx, cy), 9, (255, 255, 255), thickness=-1, lineType=cv2.LINE_AA)
        cv2.circle(frame, (cx, cy), 14, (36, 220, 184), thickness=3, lineType=cv2.LINE_AA)
    # Anchor crosshair removed — it sat motionless in the box
    # while the cursor dot moved, which read as a stale duplicate
    # cursor. The box itself communicates the active region; the
    # moving cursor dot is enough to confirm hand → mouse mapping.


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

# Author: Konstantin Markov
