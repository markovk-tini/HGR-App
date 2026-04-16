from __future__ import annotations

import math
from dataclasses import dataclass

from ..gesture.analysis.geometry import clamp01


@dataclass(frozen=True)
class MouseGestureUpdate:
    mode_enabled: bool
    consume_other_routes: bool
    cursor_position: tuple[float, float] | None
    control_text: str
    status: str
    dragging: bool
    scrolling: bool
    left_press: bool = False
    left_release: bool = False
    left_click: bool = False
    right_click: bool = False
    scroll_steps: int = 0


@dataclass(frozen=True)
class MouseDebugState:
    mode_enabled: bool
    status: str
    cursor_position: tuple[float, float] | None
    cursor_anchor_position: tuple[float, float] | None
    cursor_reach_bounds: tuple[float, float, float, float] | None
    camera_control_bounds: tuple[float, float, float, float] | None
    camera_anchor_position: tuple[float, float] | None
    dragging: bool
    scrolling: bool


@dataclass
class _FingerSequenceState:
    open_frames: int = 0
    curl_frames: int = 0
    press_started_at: float | None = None


class MouseGestureTracker:
    def __init__(
        self,
        *,
        toggle_hold_seconds: float = 0.65,
        toggle_cooldown_seconds: float = 0.90,
        open_confirm_frames: int = 2,
        curl_confirm_frames: int = 2,
        drag_hold_seconds: float = 0.42,
        cursor_smoothing: float = 0.24,
        cursor_deadzone: float = 0.018,
        horizontal_margin: float = 0.10,
        vertical_margin: float = 0.12,
        cursor_reference_width: float = 0.40,
        cursor_reference_height: float = 0.34,
        control_box_center_x: float = 0.50,
        control_box_center_y: float = 0.55,
        control_box_area: float = 0.36,
        control_box_aspect_power: float = 0.40,
        control_box_min_width: float = 0.58,
        control_box_max_width: float = 0.84,
        control_box_min_height: float = 0.42,
        control_box_max_height: float = 0.66,
        scroll_confirm_frames: int = 2,
        scroll_hold_seconds: float = 0.28,
        scroll_step_distance: float = 0.065,
        scroll_deadzone: float = 0.018,
        scroll_tip_blend: float = 0.42,
        scroll_idle_decay: float = 0.72,
        scroll_reverse_deadband: float = 1.10,
        scroll_max_steps_per_update: int = 5,
        pose_grace_seconds: float = 0.22,
        no_hand_grace_seconds: float = 0.18,
    ) -> None:
        self.toggle_hold_seconds = float(toggle_hold_seconds)
        self.toggle_cooldown_seconds = float(toggle_cooldown_seconds)
        self.open_confirm_frames = int(open_confirm_frames)
        self.curl_confirm_frames = int(curl_confirm_frames)
        self.drag_hold_seconds = float(drag_hold_seconds)
        self.cursor_smoothing = float(cursor_smoothing)
        self.cursor_deadzone = float(cursor_deadzone)
        self.horizontal_margin = float(horizontal_margin)
        self.vertical_margin = float(vertical_margin)
        self.cursor_reference_width = max(0.08, float(cursor_reference_width))
        self.cursor_reference_height = max(0.08, float(cursor_reference_height))
        self.control_box_center_x = float(control_box_center_x)
        self.control_box_center_y = float(control_box_center_y)
        self.control_box_area = float(control_box_area)
        self.control_box_aspect_power = float(control_box_aspect_power)
        self.control_box_min_width = float(control_box_min_width)
        self.control_box_max_width = float(control_box_max_width)
        self.control_box_min_height = float(control_box_min_height)
        self.control_box_max_height = float(control_box_max_height)
        self.scroll_confirm_frames = int(scroll_confirm_frames)
        self.scroll_hold_seconds = float(scroll_hold_seconds)
        self.scroll_step_distance = float(scroll_step_distance)
        self.scroll_deadzone = float(scroll_deadzone)
        self.scroll_tip_blend = float(scroll_tip_blend)
        self.scroll_idle_decay = float(scroll_idle_decay)
        self.scroll_reverse_deadband = float(scroll_reverse_deadband)
        self.scroll_max_steps_per_update = int(scroll_max_steps_per_update)
        self.pose_grace_seconds = float(pose_grace_seconds)
        self.no_hand_grace_seconds = float(no_hand_grace_seconds)
        self._desktop_aspect_ratio = 16.0 / 9.0
        self.reset()

    @property
    def mode_enabled(self) -> bool:
        return self._mode_enabled

    @property
    def debug_state(self) -> MouseDebugState:
        camera_bounds = self._camera_bounds()
        reach_bounds = None
        if self._cursor_reference_active and self._cursor_anchor_screen is not None:
            reach_bounds = (0.0, 0.0, 1.0, 1.0)
        return MouseDebugState(
            mode_enabled=bool(self._mode_enabled),
            status=self._status,
            cursor_position=None if self._cursor_position is None else tuple(self._cursor_position),
            cursor_anchor_position=None if self._cursor_anchor_screen is None else tuple(self._cursor_anchor_screen),
            cursor_reach_bounds=reach_bounds,
            camera_control_bounds=camera_bounds,
            camera_anchor_position=None if self._cursor_anchor_hand is None else tuple(self._cursor_anchor_hand),
            dragging=bool(self._dragging),
            scrolling=bool(self._scrolling),
        )

    def set_desktop_bounds(self, bounds: tuple[int, int, int, int] | None) -> None:
        if bounds is None:
            return
        _left, _top, width, height = bounds
        width = max(1.0, float(width))
        height = max(1.0, float(height))
        self._desktop_aspect_ratio = max(0.80, min(4.50, width / height))

    def reset(self) -> None:
        self._mode_enabled = False
        self._toggle_candidate = "neutral"
        self._toggle_candidate_since = 0.0
        self._toggle_cooldown_until = 0.0
        self._toggle_latched = False
        self._cursor_position: tuple[float, float] | None = None
        self._cursor_anchor_hand: tuple[float, float] | None = None
        self._cursor_anchor_screen: tuple[float, float] | None = None
        self._cursor_reference_active = False
        self._index_state = _FingerSequenceState()
        self._middle_state = _FingerSequenceState()
        self._dragging = False
        self._scrolling = False
        self._scroll_candidate_frames = 0
        self._scroll_candidate_since = 0.0
        self._scroll_pose_grace_until = 0.0
        self._scroll_last_y: float | None = None
        self._scroll_residual = 0.0
        self._scroll_velocity_ema = 0.0
        self._scroll_last_direction = 0
        self._last_seen_time = 0.0
        self._control_text = "mouse mode off"
        self._status = "off"

    def update(
        self,
        *,
        hand_reading,
        prediction,
        hand_handedness: str | None = None,
        cursor_seed: tuple[float, float] | None = None,
        now: float,
    ) -> MouseGestureUpdate:
        toggle_active = self._toggle_pose_active(prediction, hand_handedness)
        left_release, toggled = self._update_toggle(toggle_active, now)
        consume = self._mode_enabled or toggle_active or self._toggle_candidate == "left_three"

        if toggled:
            return self._snapshot(
                consume_other_routes=True,
                cursor_position=None,
                left_release=left_release,
            )

        if not self._mode_enabled:
            return self._snapshot(
                consume_other_routes=consume,
                cursor_position=None,
                left_release=left_release,
            )

        if toggle_active:
            self._clear_cursor_reference(reset_position=False)
            self._control_text = "hold left hand three to turn mouse mode off"
            self._status = "toggle"
            return self._snapshot(
                consume_other_routes=True,
                cursor_position=None,
                left_release=left_release,
            )

        if hand_reading is None:
            if self._dragging and (now - self._last_seen_time) >= self.no_hand_grace_seconds:
                self._dragging = False
                left_release = True
            if self._scrolling and (now - self._last_seen_time) >= self.no_hand_grace_seconds:
                self._stop_scrolling()
            self._clear_cursor_reference(reset_position=False)
            self._reset_sequence(self._index_state)
            self._reset_sequence(self._middle_state)
            self._control_text = "mouse waiting for hand"
            self._status = "waiting"
            return self._snapshot(
                consume_other_routes=True,
                cursor_position=None,
                left_release=left_release,
            )

        self._last_seen_time = float(now)
        scroll_steps = 0
        left_press = False
        left_click = False
        right_click = False
        cursor_position: tuple[float, float] | None = None

        scroll_pose_active = self._scroll_pose_active(prediction, hand_reading)
        if self._scrolling:
            if scroll_pose_active:
                self._scroll_pose_grace_until = now + self.pose_grace_seconds
                scroll_steps = self._update_scroll(hand_reading)
            elif now >= self._scroll_pose_grace_until:
                self._stop_scrolling()
            if self._scrolling:
                self._clear_cursor_reference(reset_position=False)
                self._control_text = "mouse scroll active"
                self._status = "scroll"
                if scroll_steps > 0:
                    self._control_text = f"mouse scroll up x{scroll_steps}"
                elif scroll_steps < 0:
                    self._control_text = f"mouse scroll down x{abs(scroll_steps)}"
                return self._snapshot(
                    consume_other_routes=True,
                    cursor_position=None,
                    left_release=left_release,
                    scroll_steps=scroll_steps,
                )
        elif not self._dragging and scroll_pose_active:
            if self._scroll_candidate_frames == 0:
                self._scroll_candidate_since = now
            self._scroll_candidate_frames += 1
            if (
                self._scroll_candidate_frames >= self.scroll_confirm_frames
                and (now - self._scroll_candidate_since) >= self.scroll_hold_seconds
            ):
                self._scrolling = True
                self._scroll_last_y = self._scroll_control_y(hand_reading)
                self._scroll_residual = 0.0
                self._scroll_velocity_ema = 0.0
                self._scroll_last_direction = 0
                self._scroll_pose_grace_until = now + self.pose_grace_seconds
                self._control_text = "mouse scroll active"
                self._status = "scroll"
                self._clear_cursor_reference(reset_position=False)
                return self._snapshot(
                    consume_other_routes=True,
                    cursor_position=None,
                    left_release=left_release,
                )
            self._clear_cursor_reference(reset_position=False)
            self._control_text = "hold wheel pose to scroll"
            self._status = "scroll_hold"
            return self._snapshot(
                consume_other_routes=True,
                cursor_position=None,
                left_release=left_release,
            )
        else:
            self._scroll_candidate_frames = 0
            self._scroll_candidate_since = 0.0

        allow_index_curled = (
            self._dragging
            or self._sequence_active(self._index_state)
            or self._click_pose_active(hand_reading, primary="index")
        )
        allow_middle_curled = (
            self._sequence_active(self._middle_state)
            or self._click_pose_active(hand_reading, primary="middle")
        )
        motion_ready = self._motion_pose_ready(
            hand_reading,
            allow_index_curled=allow_index_curled,
            allow_middle_curled=allow_middle_curled,
        )

        left_press, left_release_event, left_click = self._update_left_sequence(hand_reading, now)
        right_click = self._update_right_sequence(hand_reading, now)
        left_release = left_release or left_release_event

        if self._dragging or motion_ready:
            cursor_position = self._update_cursor(
                hand_reading,
                cursor_seed=cursor_seed,
                reanchor=not self._cursor_reference_active,
            )
            self._cursor_reference_active = True
        else:
            self._clear_cursor_reference(reset_position=False)

        if left_press:
            self._control_text = "mouse drag start"
            self._status = "drag"
        elif left_release:
            self._control_text = "mouse drag release"
            self._status = "ready" if self._mode_enabled else "off"
        elif left_click:
            self._control_text = "mouse left click"
            self._status = "ready"
        elif right_click:
            self._control_text = "mouse right click"
            self._status = "ready"
        elif self._dragging:
            self._control_text = "mouse drag active"
            self._status = "drag"
        elif cursor_position is not None:
            self._control_text = "mouse ready"
            self._status = "ready"
        else:
            self._control_text = "show open hand mouse pose"
            self._status = "waiting_pose"

        return self._snapshot(
            consume_other_routes=True,
            cursor_position=cursor_position,
            left_press=left_press,
            left_release=left_release,
            left_click=left_click,
            right_click=right_click,
            scroll_steps=scroll_steps,
        )

    def _snapshot(
        self,
        *,
        consume_other_routes: bool,
        cursor_position: tuple[float, float] | None,
        left_press: bool = False,
        left_release: bool = False,
        left_click: bool = False,
        right_click: bool = False,
        scroll_steps: int = 0,
    ) -> MouseGestureUpdate:
        return MouseGestureUpdate(
            mode_enabled=self._mode_enabled,
            consume_other_routes=bool(consume_other_routes),
            cursor_position=cursor_position,
            control_text=self._control_text,
            status=self._status,
            dragging=self._dragging,
            scrolling=self._scrolling,
            left_press=bool(left_press),
            left_release=bool(left_release),
            left_click=bool(left_click),
            right_click=bool(right_click),
            scroll_steps=int(scroll_steps),
        )

    def _update_toggle(self, toggle_active: bool, now: float) -> tuple[bool, bool]:
        if not toggle_active:
            self._toggle_candidate = "neutral"
            self._toggle_candidate_since = 0.0
            self._toggle_latched = False
            return False, False

        if self._toggle_latched or now < self._toggle_cooldown_until:
            return False, False

        if self._toggle_candidate != "left_three":
            self._toggle_candidate = "left_three"
            self._toggle_candidate_since = now
            if self._mode_enabled:
                self._control_text = "hold left hand three to turn mouse mode off"
            else:
                self._control_text = "hold left hand three to turn mouse mode on"
            self._status = "toggle"
            return False, False

        if (now - self._toggle_candidate_since) < self.toggle_hold_seconds:
            if self._mode_enabled:
                self._control_text = "hold left hand three to turn mouse mode off"
            else:
                self._control_text = "hold left hand three to turn mouse mode on"
            self._status = "toggle"
            return False, False

        self._toggle_candidate = "neutral"
        self._toggle_candidate_since = 0.0
        self._toggle_latched = True
        self._toggle_cooldown_until = now + self.toggle_cooldown_seconds
        self._mode_enabled = not self._mode_enabled
        released_drag = self._clear_interaction_state(reset_cursor=True)
        if self._mode_enabled:
            self._control_text = "mouse mode on"
            self._status = "active"
        else:
            self._control_text = "mouse mode off"
            self._status = "off"
        return released_drag, True

    def _clear_interaction_state(self, *, reset_cursor: bool) -> bool:
        released_drag = self._dragging
        self._dragging = False
        self._stop_scrolling()
        self._reset_sequence(self._index_state)
        self._reset_sequence(self._middle_state)
        self._clear_cursor_reference(reset_position=reset_cursor)
        return released_drag

    def _clear_cursor_reference(self, *, reset_position: bool) -> None:
        self._cursor_anchor_hand = None
        self._cursor_anchor_screen = None
        self._cursor_reference_active = False
        if reset_position:
            self._cursor_position = None

    def _stop_scrolling(self) -> None:
        self._scrolling = False
        self._scroll_candidate_frames = 0
        self._scroll_candidate_since = 0.0
        self._scroll_pose_grace_until = 0.0
        self._scroll_last_y = None
        self._scroll_residual = 0.0
        self._scroll_velocity_ema = 0.0
        self._scroll_last_direction = 0

    def _update_scroll(self, hand_reading) -> int:
        current_y = self._scroll_control_y(hand_reading)
        if self._scroll_last_y is None:
            self._scroll_last_y = current_y
            return 0

        delta = self._scroll_last_y - current_y
        self._scroll_last_y = current_y
        quiet_threshold = self.scroll_deadzone * 0.55
        if abs(delta) <= quiet_threshold:
            self._scroll_velocity_ema *= self.scroll_idle_decay
            if abs(self._scroll_residual) < 0.2:
                self._scroll_residual *= 0.5
            return 0

        self._scroll_velocity_ema = 0.42 * delta + 0.58 * self._scroll_velocity_ema
        base_units = delta / max(self.scroll_step_distance, 1e-6)
        speed_gain = 1.0 + min(
            1.6,
            abs(self._scroll_velocity_ema) / max(self.scroll_step_distance * 0.85, 1e-6),
        )
        distance_gain = 1.0 + min(
            1.2,
            max(0.0, abs(delta) - self.scroll_deadzone) / max(self.scroll_step_distance * 1.6, 1e-6),
        )
        units = base_units * max(1.0, 0.55 * speed_gain + 0.45 * distance_gain)
        direction = 1 if units > 0.0 else -1
        if self._scroll_last_direction and direction != self._scroll_last_direction:
            if abs(units) < self.scroll_reverse_deadband:
                self._scroll_residual = 0.0
                self._scroll_velocity_ema *= 0.35
                self._scroll_last_direction = direction
                return 0
            self._scroll_residual *= 0.25
        self._scroll_last_direction = direction
        self._scroll_residual += units
        steps = int(math.trunc(self._scroll_residual))
        if steps == 0:
            return 0
        steps = max(-self.scroll_max_steps_per_update, min(self.scroll_max_steps_per_update, steps))
        self._scroll_residual -= steps
        return steps

    def _scroll_control_y(self, hand_reading) -> float:
        palm_y = float(hand_reading.palm.center[1])
        tip_center_y = float((hand_reading.landmarks[8][1] + hand_reading.landmarks[20][1]) * 0.5)
        return (1.0 - self.scroll_tip_blend) * palm_y + self.scroll_tip_blend * tip_center_y

    def _absolute_cursor_target(self, hand_reading) -> tuple[float, float]:
        palm_center = hand_reading.palm.center
        min_x, min_y, max_x, max_y = self._camera_bounds()
        target_x = clamp01((float(palm_center[0]) - min_x) / max(1e-6, max_x - min_x))
        target_y = clamp01((float(palm_center[1]) - min_y) / max(1e-6, max_y - min_y))
        return target_x, target_y

    def _camera_bounds(self) -> tuple[float, float, float, float]:
        available_left = self.horizontal_margin
        available_top = self.vertical_margin
        available_right = 1.0 - self.horizontal_margin
        available_bottom = 1.0 - self.vertical_margin
        available_width = max(0.24, available_right - available_left)
        available_height = max(0.24, available_bottom - available_top)

        compressed_aspect = max(0.90, min(2.10, self._desktop_aspect_ratio ** self.control_box_aspect_power))
        target_area = max(0.18, min(0.44, self.control_box_area))
        width = math.sqrt(target_area * compressed_aspect)
        height = math.sqrt(target_area / compressed_aspect)
        width = min(max(width, min(self.control_box_min_width, available_width)), min(self.control_box_max_width, available_width))
        height = min(max(height, min(self.control_box_min_height, available_height)), min(self.control_box_max_height, available_height))

        center_x = min(max(self.control_box_center_x, available_left + width * 0.5), available_right - width * 0.5)
        center_y = min(max(self.control_box_center_y, available_top + height * 0.5), available_bottom - height * 0.5)
        return (
            center_x - width * 0.5,
            center_y - height * 0.5,
            center_x + width * 0.5,
            center_y + height * 0.5,
        )

    def _edge_relative_axis_target(
        self,
        *,
        value: float,
        anchor_value: float,
        anchor_screen: float,
        lower_bound: float,
        upper_bound: float,
    ) -> float:
        bounded_value = min(max(value, lower_bound), upper_bound)
        if bounded_value >= anchor_value:
            available_hand_room = max(upper_bound - anchor_value, 1e-6)
            screen_room = max(1.0 - anchor_screen, 1e-6)
            progress = clamp01((bounded_value - anchor_value) / available_hand_room)
            eased = progress * progress * (3.0 - 2.0 * progress)
            return clamp01(anchor_screen + screen_room * eased)

        available_hand_room = max(anchor_value - lower_bound, 1e-6)
        screen_room = max(anchor_screen, 1e-6)
        progress = clamp01((anchor_value - bounded_value) / available_hand_room)
        eased = progress * progress * (3.0 - 2.0 * progress)
        return clamp01(anchor_screen - screen_room * eased)

    def _update_cursor(
        self,
        hand_reading,
        *,
        cursor_seed: tuple[float, float] | None,
        reanchor: bool,
    ) -> tuple[float, float]:
        target_x, target_y = self._absolute_cursor_target(hand_reading)
        if self._cursor_position is None:
            self._cursor_position = cursor_seed if cursor_seed is not None else (target_x, target_y)
        if reanchor or self._cursor_anchor_hand is None or self._cursor_anchor_screen is None:
            if cursor_seed is not None:
                self._cursor_position = cursor_seed
            self._cursor_anchor_hand = (float(hand_reading.palm.center[0]), float(hand_reading.palm.center[1]))
            self._cursor_anchor_screen = (target_x, target_y)

        dx = target_x - self._cursor_position[0]
        dy = target_y - self._cursor_position[1]
        motion = math.hypot(dx, dy)
        if motion <= self.cursor_deadzone:
            return self._cursor_position

        alpha = min(0.72, max(self.cursor_smoothing, self.cursor_smoothing + 0.55 * motion))
        self._cursor_position = (
            clamp01(self._cursor_position[0] + alpha * dx),
            clamp01(self._cursor_position[1] + alpha * dy),
        )
        return self._cursor_position

    def _update_left_sequence(self, hand_reading, now: float) -> tuple[bool, bool, bool]:
        finger = hand_reading.fingers["index"]
        openish = self._primary_open(finger)
        curled = self._click_curled(finger)
        context_ready = self._left_click_context_ready(hand_reading)

        if self._dragging:
            if openish or not context_ready:
                self._dragging = False
                self._reset_sequence(self._index_state, preserve_open=openish)
                return False, True, False
            return False, False, False

        if not context_ready:
            self._reset_sequence(self._index_state, preserve_open=openish)
            return False, False, False

        if openish:
            left_click = False
            if (
                self._index_state.press_started_at is not None
                and self._index_state.curl_frames >= self.curl_confirm_frames
            ):
                duration = now - self._index_state.press_started_at
                if duration < self.drag_hold_seconds:
                    left_click = True
            self._index_state.open_frames = min(self._index_state.open_frames + 1, self.open_confirm_frames + 2)
            self._index_state.curl_frames = 0
            self._index_state.press_started_at = None
            return False, False, left_click

        if curled and self._index_state.open_frames >= self.open_confirm_frames:
            self._index_state.curl_frames += 1
            if (
                self._index_state.curl_frames >= self.curl_confirm_frames
                and self._index_state.press_started_at is None
            ):
                self._index_state.press_started_at = now
            if (
                self._index_state.press_started_at is not None
                and (now - self._index_state.press_started_at) >= self.drag_hold_seconds
            ):
                self._dragging = True
                return True, False, False
            return False, False, False

        self._reset_sequence(self._index_state, preserve_open=openish)
        return False, False, False

    def _update_right_sequence(self, hand_reading, now: float) -> bool:
        finger = hand_reading.fingers["middle"]
        openish = self._primary_open(finger)
        curled = self._click_curled(finger)
        context_ready = self._right_click_context_ready(hand_reading)

        if not context_ready:
            self._reset_sequence(self._middle_state, preserve_open=openish)
            return False

        if openish:
            right_click = False
            if (
                self._middle_state.press_started_at is not None
                and self._middle_state.curl_frames >= self.curl_confirm_frames
            ):
                duration = now - self._middle_state.press_started_at
                if duration <= max(self.drag_hold_seconds + 0.20, 0.58):
                    right_click = True
            self._middle_state.open_frames = min(self._middle_state.open_frames + 1, self.open_confirm_frames + 2)
            self._middle_state.curl_frames = 0
            self._middle_state.press_started_at = None
            return right_click

        if curled and self._middle_state.open_frames >= self.open_confirm_frames:
            self._middle_state.curl_frames += 1
            if (
                self._middle_state.curl_frames >= self.curl_confirm_frames
                and self._middle_state.press_started_at is None
            ):
                self._middle_state.press_started_at = now
            return False

        self._reset_sequence(self._middle_state, preserve_open=openish)
        return False

    def _reset_sequence(self, state: _FingerSequenceState, *, preserve_open: bool = False) -> None:
        state.curl_frames = 0
        state.press_started_at = None
        state.open_frames = 1 if preserve_open else 0

    def _sequence_active(self, state: _FingerSequenceState) -> bool:
        return state.press_started_at is not None or state.curl_frames > 0

    def _toggle_pose_active(self, prediction, hand_handedness: str | None) -> bool:
        if prediction is None:
            return False
        if str(hand_handedness or "").lower() != "left":
            return False
        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        raw_label = str(getattr(prediction, "raw_label", "neutral") or "neutral")
        confidence = float(getattr(prediction, "confidence", 0.0) or 0.0)
        return (
            (raw_label == "three" and confidence >= 0.58)
            or (stable_label == "three" and raw_label in {"three", "neutral"} and confidence >= 0.38)
        )

    def _scroll_pose_active(self, prediction, hand_reading) -> bool:
        if prediction is not None:
            stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
            raw_label = str(getattr(prediction, "raw_label", "neutral") or "neutral")
            confidence = float(getattr(prediction, "confidence", 0.0) or 0.0)
            if (
                (raw_label == "wheel_pose" and confidence >= 0.56)
                or (stable_label == "wheel_pose" and raw_label in {"wheel_pose", "neutral"} and confidence >= 0.38)
            ):
                return True
        return hand_reading is not None and self._scroll_pose_ready_from_hand(hand_reading)

    def _motion_pose_ready(
        self,
        hand_reading,
        *,
        allow_index_curled: bool,
        allow_middle_curled: bool,
    ) -> bool:
        fingers = hand_reading.fingers
        index_ready = self._motion_primary_ready(fingers["index"]) or (
            allow_index_curled and self._click_curled(fingers["index"])
        )
        middle_ready = self._motion_primary_ready(fingers["middle"]) or (
            allow_middle_curled and self._click_curled(fingers["middle"])
        )
        return (
            self._thumb_open(fingers["thumb"])
            and self._support_open(fingers["ring"])
            and self._support_open(fingers["pinky"])
            and index_ready
            and middle_ready
        )

    def _click_pose_active(self, hand_reading, *, primary: str) -> bool:
        if primary == "index":
            return self._left_click_context_ready(hand_reading) and self._click_curled(hand_reading.fingers["index"])
        return self._right_click_context_ready(hand_reading) and self._click_curled(hand_reading.fingers["middle"])

    def _scroll_pose_ready_from_hand(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        thumb_index = hand_reading.spreads["thumb_index"]
        ring_pinky = hand_reading.spreads["ring_pinky"]
        return (
            self._thumb_open(fingers["thumb"])
            and self._primary_open(fingers["index"])
            and self._support_open(fingers["pinky"])
            and self._scroll_folded(fingers["middle"])
            and self._scroll_folded(fingers["ring"])
            and (
                thumb_index.state == "apart"
                or thumb_index.distance >= 0.30
                or thumb_index.apart_strength >= 0.18
            )
            and (
                ring_pinky.state == "apart"
                or ring_pinky.distance >= 0.12
                or ring_pinky.apart_strength >= 0.12
            )
        )

    def _left_click_context_ready(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        return (
            self._thumb_open(fingers["thumb"])
            and self._support_open(fingers["ring"])
            and self._support_open(fingers["pinky"])
            and self._motion_primary_ready(fingers["middle"])
            and (self._motion_primary_ready(fingers["index"]) or self._click_curled(fingers["index"]))
        )

    def _right_click_context_ready(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        return (
            self._thumb_open(fingers["thumb"])
            and self._support_open(fingers["ring"])
            and self._support_open(fingers["pinky"])
            and self._motion_primary_ready(fingers["index"])
            and (self._motion_primary_ready(fingers["middle"]) or self._click_curled(fingers["middle"]))
        )

    def _thumb_open(self, finger) -> bool:
        return finger.state == "fully_open" or (
            finger.openness >= 0.60
            and finger.curl <= 0.46
            and finger.bend_distal >= 122.0
            and finger.palm_distance >= 0.54
        )

    def _motion_primary_ready(self, finger) -> bool:
        return self._primary_open(finger) or (
            finger.openness >= 0.50
            and finger.curl <= 0.58
            and finger.bend_proximal >= 126.0
            and finger.bend_distal >= 120.0
            and finger.palm_distance >= 0.52
        )

    def _primary_open(self, finger) -> bool:
        return finger.state == "fully_open" or (
            finger.openness >= 0.58
            and finger.curl <= 0.48
            and finger.bend_proximal >= 132.0
            and finger.bend_distal >= 126.0
            and (finger.reach >= 0.02 or finger.palm_distance >= 0.58)
        )

    def _support_open(self, finger) -> bool:
        return finger.state == "fully_open" or (
            finger.openness >= 0.58
            and finger.curl <= 0.46
            and finger.bend_proximal >= 136.0
            and finger.bend_distal >= 134.0
            and finger.palm_distance >= 0.60
        )

    def _thumb_folded(self, finger) -> bool:
        return finger.state in {"mostly_curled", "closed"} or (
            finger.state == "partially_curled" and finger.openness <= 0.62
        ) or (finger.openness <= 0.56 and finger.curl >= 0.42)

    def _scroll_folded(self, finger) -> bool:
        return finger.state in {"mostly_curled", "closed"} or (
            finger.state == "partially_curled"
            and finger.openness <= 0.58
            and finger.curl >= 0.42
            and finger.bend_distal <= 148.0
        )

    def _click_curled(self, finger) -> bool:
        return finger.state in {"mostly_curled", "closed"} or (
            finger.state == "partially_curled"
            and finger.openness <= 0.70
            and finger.curl >= 0.40
            and finger.bend_distal <= 146.0
        )
