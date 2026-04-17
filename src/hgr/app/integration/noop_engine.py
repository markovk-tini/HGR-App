from __future__ import annotations

import math
import queue
import re
import threading
import time
from types import SimpleNamespace
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ...debug.chrome_controller import ChromeController
from ...debug.chrome_gesture_router import ChromeGestureRouter
from ...debug.mouse_controller import MouseController
from ...debug.mouse_gesture import MouseGestureTracker
from ...debug.mouse_overlay import draw_mouse_control_box_overlay, draw_mouse_monitor_overlay
from ...debug.live_dictation_streamer import LiveDictationStreamer
from ...debug.screen_volume_overlay import ScreenVolumeOverlay
from ...debug.spotify_controller import SpotifyController
from ...debug.spotify_gesture_router import SpotifyGestureRouter
from ...debug.text_input_controller import TextInputController
from ...debug.voice_command_listener import VoiceCommandListener
from ...debug.volume_controller import VolumeController
from ...debug.volume_gesture import VolumeGestureTracker
from ...gesture.recognition.engine import GestureRecognitionEngine
from ...gesture.rendering.overlay import draw_hand_overlay
from ...gesture.ui.test_window import SpotifyWheelOverlay
from ...gesture.ui.voice_status_overlay import VoiceStatusOverlay
from ...voice.command_processor import VoiceCommandContext, VoiceCommandProcessor
from ...voice.dictation import DictationProcessor
from ..camera.camera_utils import open_camera_by_index, open_preferred_or_first_available


class GestureWorker(QObject):
    status_changed = Signal(str)
    command_detected = Signal(str)
    camera_selected = Signal(str)
    error_occurred = Signal(str)
    running_state_changed = Signal(bool)
    debug_frame_ready = Signal(object, object)
    save_prompt_completed = Signal(object)

    def __init__(self, config, camera_index_override: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.camera_index_override = camera_index_override
        self._running = False
        self._cap = None
        self._camera_info = None
        self.engine: GestureRecognitionEngine | None = None
        self._last_status_text = ""
        self._last_spotify_action = "-"
        self._last_chrome_action = "-"
        self._last_time = time.time()
        self._fps = 0.0
        self._dynamic_hold_label = "neutral"
        self._dynamic_hold_until = 0.0

        self.volume_controller = VolumeController()
        self.volume_overlay = ScreenVolumeOverlay(config)
        self.volume_overlay.attach_controller(self.volume_controller)
        self.voice_status_overlay = VoiceStatusOverlay(config)
        try:
            self.voice_status_overlay.selectionChosen.connect(self._handle_voice_overlay_selection)
        except Exception:
            pass
        self.volume_tracker = VolumeGestureTracker()
        self._volume_message = self.volume_controller.message
        self._volume_mode_active = False
        self._volume_level: float | None = self.volume_controller.get_level()
        self._volume_status_text = "idle"
        self._volume_muted = self._read_system_mute()
        self._volume_overlay_visible = False
        self._mute_block_until = 0.0
        self._volume_dual_active = False
        self._volume_app_level: float | None = None
        self._volume_app_label = ""
        self._volume_app_process = ""
        self._volume_bar_selected = "sys"
        self._volume_init_palm_x: float | None = None
        self._volume_app_check_until = 0.0
        self._chrome_active_cache = False
        self._chrome_active_cache_until = 0.0
        self._spotify_active_cache = False
        self._spotify_active_cache_until = 0.0
        self._chrome_wheel_candidate = "neutral"
        self._chrome_wheel_candidate_since = 0.0
        self._chrome_wheel_visible = False
        self._chrome_wheel_anchor = None
        self._chrome_wheel_selected_key: str | None = None
        self._chrome_wheel_selected_since = 0.0
        self._chrome_wheel_pose_grace_until = 0.0
        self._chrome_wheel_cooldown_until = 0.0
        self._chrome_wheel_cursor_offset: tuple[float, float] | None = None
        self._spotify_wheel_candidate = "neutral"
        self._spotify_wheel_candidate_since = 0.0
        self._spotify_wheel_visible = False
        self._spotify_wheel_anchor = None
        self._spotify_wheel_selected_key: str | None = None
        self._spotify_wheel_selected_since = 0.0
        self._spotify_wheel_pose_grace_until = 0.0
        self._spotify_wheel_cooldown_until = 0.0
        self._spotify_wheel_cursor_offset: tuple[float, float] | None = None
        self.spotify_wheel_overlay = SpotifyWheelOverlay(config)
        self.chrome_wheel_overlay = SpotifyWheelOverlay(config)
        self.mouse_controller = MouseController()
        self.mouse_tracker = self._build_mouse_tracker()
        self._last_mouse_update = SimpleNamespace(
            mode_enabled=False,
            cursor_position=None,
            left_click=False,
            left_press=False,
            left_release=False,
            right_click=False,
            scroll_steps=0,
        )
        self._mouse_mode_enabled = False
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"

        self.chrome_controller = ChromeController()
        self.chrome_router = ChromeGestureRouter(static_hold_seconds=0.5, static_cooldown_seconds=1.5, dynamic_cooldown_seconds=1.5)
        self._chrome_mode_enabled = False
        self._chrome_control_text = self.chrome_controller.message

        self.spotify_controller = SpotifyController()
        self.spotify_router = SpotifyGestureRouter(static_hold_seconds=0.5, static_cooldown_seconds=1.5, dynamic_cooldown_seconds=1.5)
        self._spotify_control_text = self.spotify_controller.message
        self._spotify_info_text = "-"
        self._spotify_vol_lock = threading.Lock()
        self._spotify_vol_target: int | None = None
        self._spotify_vol_last_sent: int | None = None
        self._spotify_vol_worker: threading.Thread | None = None

        self.voice_listener = VoiceCommandListener(preferred_input_device=getattr(config, "preferred_microphone_name", None))
        self.voice_processor = VoiceCommandProcessor(
            chrome_controller=self.chrome_controller,
            spotify_controller=self.spotify_controller,
        )
        self.live_dictation_streamer = LiveDictationStreamer()
        self.text_input_controller = TextInputController()
        self.dictation_processor = DictationProcessor()
        self._app_hint_thread: threading.Thread | None = None
        self._prime_voice_app_hints_async()
        self._prime_voice_runtime_async()
        self._voice_control_text = self.voice_listener.message
        self._voice_heard_text = "-"
        self._voice_candidate = "neutral"
        self._voice_candidate_since = 0.0
        self._voice_cooldown_until = 0.0
        self._voice_latched_label: str | None = None
        self._voice_one_two_triggered_at: float = 0.0
        self._voice_queue: queue.Queue[tuple[int, object]] = queue.Queue()
        self._voice_thread: threading.Thread | None = None
        self._voice_request_id = 0
        self._voice_stop_event: threading.Event | None = None
        self._voice_listening = False
        self._dictation_active = False
        self._dictation_backend = "idle"
        self._voice_mode = "ready"
        self._voice_display_text = "-"
        self._dictation_toggle_release_required = False
        self._dictation_stop_rearm_at = 0.0
        self._dictation_release_candidate_since = 0.0
        self._tutorial_mode_enabled = False
        self._tutorial_step_key: str | None = None
        self._drawing_mode_enabled = False
        self._drawing_toggle_candidate_since = 0.0
        self._drawing_toggle_cooldown_until = 0.0
        self._drawing_cursor_norm: tuple[float, float] | None = None
        self._drawing_tool = "hidden"
        self._drawing_control_text = "drawing mode off"
        self._drawing_render_target = "screen"
        self._drawing_brush_hex = str(getattr(config, "accent_color", "#1DE9B6") or "#1DE9B6")
        self._drawing_brush_thickness = 8
        self._drawing_eraser_thickness = 18
        self._drawing_eraser_mode = "normal"
        self._camera_draw_canvas: np.ndarray | None = None
        self._camera_draw_history: list[tuple[np.ndarray, list[dict], bool]] = []
        self._camera_draw_strokes: list[dict] = []
        self._camera_draw_active_stroke_points: list[tuple[float, float]] = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point: tuple[int, int] | None = None
        self._camera_draw_erasing = False
        self._drawing_request_token = 0
        self._drawing_swipe_cooldown_until = 0.0
        self._drawing_request_action = ""
        self._drawing_shape_mode = False
        self._drawing_draw_grace_until = 0.0
        self._drawing_erase_grace_until = 0.0
        self._drawing_wheel_candidate = "neutral"
        self._drawing_wheel_candidate_since = 0.0
        self._drawing_wheel_visible = False
        self._drawing_wheel_anchor = None
        self._drawing_wheel_selected_key: str | None = None
        self._drawing_wheel_selected_since = 0.0
        self._drawing_wheel_pose_grace_until = 0.0
        self._drawing_wheel_cooldown_until = 0.0
        self._drawing_wheel_cursor_offset: tuple[float, float] | None = None
        self.drawing_wheel_overlay = SpotifyWheelOverlay(config)
        self._utility_wheel_candidate = "neutral"
        self._utility_wheel_candidate_since = 0.0
        self._utility_wheel_visible = False
        self._utility_wheel_anchor = None
        self._utility_wheel_selected_key: str | None = None
        self._utility_wheel_selected_since = 0.0
        self._utility_wheel_pose_grace_until = 0.0
        self._utility_wheel_cooldown_until = 0.0
        self._utility_wheel_cursor_offset: tuple[float, float] | None = None
        self.utility_wheel_overlay = SpotifyWheelOverlay(config)
        self._utility_request_token = 0
        self._utility_request_action = ""
        self._utility_recording_active = False
        self._utility_recording_stop_candidate_since = 0.0
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._utility_capture_clicks_armed = False
        self._utility_capture_clicks_armed = False
        self._utility_capture_clicks_armed = False
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm: tuple[float, float] | None = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._window_expand_candidate_since = 0.0
        self._window_contract_candidate_since = 0.0
        self._window_close_candidate_since = 0.0
        self._window_gesture_cooldown_until = 0.0
        self._window_pair_smoothed_distance: float | None = None
        self._window_pair_last_seen_at = 0.0
        self._window_pair_overlay = None
        self._window_sequence_start_state: str | None = None
        self._window_sequence_start_candidate: str | None = None
        self._window_sequence_start_candidate_since = 0.0
        self._window_sequence_target_candidate: str | None = None
        self._window_sequence_target_candidate_since = 0.0
        self._gestures_enabled = True
        self._selection_prompt_active = False
        self._selection_prompt_title = "Which file/folder?"
        self._selection_prompt_items: list[tuple[int, str, str]] = []
        self._selection_prompt_instruction = "Say the corresponding number."
        self._save_prompt_active = False
        self._save_prompt_text = "Where would you like to save this file?"

        self._timer = QTimer(self)
        self._timer.setInterval(15)
        self._timer.timeout.connect(self._tick)

    def _prime_voice_app_hints_async(self) -> None:
        if self._app_hint_thread is not None and self._app_hint_thread.is_alive():
            return

        def _worker() -> None:
            try:
                self.voice_listener.set_app_hints(self.voice_processor.desktop_controller.application_hint_names())
            except Exception:
                pass

        self._app_hint_thread = threading.Thread(
            target=_worker,
            name="hgr-app-hints",
            daemon=True,
        )
        self._app_hint_thread.start()


    def _prime_voice_runtime_async(self) -> None:
        def _worker() -> None:
            try:
                self.voice_listener.prewarm()
            except Exception:
                pass

        threading.Thread(
            target=_worker,
            name="hgr-voice-prewarm",
            daemon=True,
        ).start()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def gestures_enabled(self) -> bool:
        return bool(self._gestures_enabled)

    def set_gestures_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._gestures_enabled:
            return
        self._gestures_enabled = enabled
        if not enabled:
            self.mouse_controller.release_all()
            self.mouse_tracker.reset()
            self._last_mouse_update = self._blank_mouse_update()
            self._mouse_mode_enabled = False
            self._mouse_control_text = "gestures disabled"
            self._mouse_status_text = "off"
            self._reset_chrome_wheel(clear_cooldown=False)
            self._reset_spotify_wheel(clear_cooldown=False)
            self._reset_drawing_wheel(clear_cooldown=False)
            self._reset_utility_wheel(clear_cooldown=False)
            self._reset_window_gesture_state(clear_cooldown=False)
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            self._camera_draw_last_point = None
            self._window_pair_smoothed_distance = None
            self._window_pair_overlay = None
            self._chrome_control_text = "gestures disabled"
            self._spotify_control_text = "gestures disabled"
            self._volume_mode_active = False
            self._volume_status_text = "paused"
            self._volume_overlay_visible = False
            self._update_volume_overlay()
        else:
            self._chrome_control_text = self.chrome_controller.message
            self._spotify_control_text = self.spotify_controller.message
            if self.mouse_controller.available:
                self._mouse_control_text = "mouse mode off"
                self._mouse_status_text = "off"

    def _reset_drawing_runtime(self, *, keep_mode: bool = False) -> None:
        if not keep_mode:
            self._drawing_mode_enabled = False
        self._drawing_toggle_candidate_since = 0.0
        self._drawing_cursor_norm = None
        self._drawing_tool = "hidden"
        self._drawing_swipe_cooldown_until = 0.0
        self._drawing_control_text = "drawing mode on" if self._drawing_mode_enabled else "drawing mode off"

    def _toggle_drawing_mode(self, now: float) -> None:
        self._drawing_mode_enabled = not self._drawing_mode_enabled
        self._drawing_toggle_candidate_since = 0.0
        self._drawing_toggle_cooldown_until = now + 1.0
        self._drawing_cursor_norm = None
        self._drawing_tool = "hidden"
        self._camera_draw_last_point = None
        self._reset_drawing_wheel(clear_cooldown=False)
        self._reset_chrome_wheel(clear_cooldown=False)
        self._reset_spotify_wheel(clear_cooldown=False)
        self._reset_window_gesture_state(clear_cooldown=False)
        self.chrome_router.reset()
        self.spotify_router.reset()
        self.mouse_controller.release_all()
        self.mouse_tracker.reset()
        self._last_mouse_update = self._blank_mouse_update()
        self._mouse_mode_enabled = False
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._volume_mode_active = False
        self._volume_status_text = "paused"
        self._volume_overlay_visible = False
        self._update_volume_overlay()
        self.voice_status_overlay.hide_overlay()
        state = "enabled" if self._drawing_mode_enabled else "disabled"
        self._drawing_control_text = f"drawing mode {state}"
        try:
            self.command_detected.emit(self._drawing_control_text)
        except Exception:
            pass

    def _handle_drawing_toggle(self, prediction, hand_handedness: str | None, now: float) -> bool:
        if hand_handedness != "Left" or prediction is None:
            self._drawing_toggle_candidate_since = 0.0
            return False
        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        if stable_label != "four":
            self._drawing_toggle_candidate_since = 0.0
            return False
        if now < self._drawing_toggle_cooldown_until:
            return True
        if self._drawing_toggle_candidate_since <= 0.0:
            self._drawing_toggle_candidate_since = now
            self._drawing_control_text = "hold left hand four for drawing mode"
            return True
        if now - self._drawing_toggle_candidate_since >= 0.6:
            self._toggle_drawing_mode(now)
            return True
        return True

    def _drawing_finger_extendedish(self, finger, *, primary: bool = False) -> bool:
        if finger is None:
            return False
        if finger.state in {"fully_open", "partially_curled"}:
            return True
        threshold = 0.50 if primary else 0.56
        return float(getattr(finger, "openness", 0.0) or 0.0) >= threshold

    def _drawing_finger_foldedish(self, finger) -> bool:
        if finger is None:
            return False
        if finger.state in {"closed", "mostly_curled"}:
            return True
        openness = float(getattr(finger, "openness", 0.0) or 0.0)
        curl = float(getattr(finger, "curl", 0.0) or 0.0)
        return openness <= 0.48 or curl >= 0.48

    def _drawing_draw_pose_active(self, prediction, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        outer_folded = sum(1 for name in ("middle", "ring", "pinky") if self._drawing_finger_foldedish(fingers.get(name)))
        return (
            self._drawing_finger_extendedish(fingers.get("index"), primary=True)
            and self._drawing_finger_foldedish(fingers.get("thumb"))
            and outer_folded >= 2
            and float(getattr(fingers.get("middle"), "openness", 0.0) or 0.0) <= 0.60
        )

    def _drawing_erase_pose_active(self, prediction, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        spread = hand_reading.spreads.get("index_middle")
        spread_ok = False
        if spread is not None:
            spread_ok = spread.state == "together" or float(spread.distance) <= 0.60 or float(spread.together_strength) >= 0.14
        folded_support = sum(1 for name in ("thumb", "ring", "pinky") if self._drawing_finger_foldedish(fingers.get(name)))
        return (
            self._drawing_finger_extendedish(fingers.get("index"), primary=True)
            and self._drawing_finger_extendedish(fingers.get("middle"), primary=True)
            and folded_support >= 2
            and spread_ok
        )

    def _drawing_lift_pose_active(self, hand_reading) -> bool:
        if hand_reading is None:
            return False
        thumb = hand_reading.fingers["thumb"]
        return thumb.state in {"fully_open", "partially_curled"} and thumb.openness >= 0.56

    def _drawing_wheel_pose_active(self, prediction) -> bool:
        if prediction is None:
            return False
        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        raw_label = str(getattr(prediction, "raw_label", "neutral") or "neutral")
        confidence = float(getattr(prediction, "confidence", 0.0) or 0.0)
        return stable_label == "wheel_pose" or (raw_label == "wheel_pose" and confidence >= 0.52)

    def _update_drawing_controls(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> None:
        if not self._drawing_mode_enabled:
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            return
        if hand_handedness != "Right" or hand_reading is None:
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            self._drawing_control_text = f"drawing mode enabled ({self._drawing_render_target})"
            self._camera_draw_last_point = None
            return
        try:
            cursor_x = max(0.0, min(1.0, float(hand_reading.landmarks[8][0])))
            cursor_y = max(0.0, min(1.0, float(hand_reading.landmarks[8][1])))
        except Exception:
            self._drawing_cursor_norm = None
            self._drawing_tool = "hidden"
            self._camera_draw_last_point = None
            return
        self._drawing_cursor_norm = (cursor_x, cursor_y)
        if self._drawing_lift_pose_active(hand_reading):
            self._drawing_tool = "hover"
            self._drawing_control_text = f"drawing hover ({self._drawing_render_target})"
            self._camera_draw_last_point = None
            return
        erase_active = self._drawing_erase_pose_active(prediction, hand_reading)
        draw_active = self._drawing_draw_pose_active(prediction, hand_reading)
        if erase_active:
            self._drawing_erase_grace_until = now + 0.18
        if draw_active:
            self._drawing_draw_grace_until = now + 0.18
        if erase_active or now < self._drawing_erase_grace_until:
            self._drawing_tool = "erase"
            self._drawing_control_text = f"drawing erase ({self._drawing_render_target})"
            self._camera_draw_last_point = None
            return
        if draw_active or now < self._drawing_draw_grace_until:
            self._drawing_tool = "draw"
            self._drawing_control_text = f"drawing ({self._drawing_render_target})"
            return
        self._drawing_tool = "hover"
            
        self._drawing_control_text = f"drawing hover ({self._drawing_render_target})"
        self._camera_draw_last_point = None

    def _reset_drawing_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._drawing_wheel_visible = False
        self._drawing_wheel_anchor = None
        self._drawing_wheel_cursor_offset = None
        self._drawing_wheel_selected_key = None
        self._drawing_wheel_selected_since = 0.0
        self._drawing_wheel_pose_grace_until = 0.0
        self._drawing_wheel_candidate = "neutral"
        self._drawing_wheel_candidate_since = 0.0
        if clear_cooldown:
            self._drawing_wheel_cooldown_until = 0.0
        if self.drawing_wheel_overlay.isVisible():
            self.drawing_wheel_overlay.hide_overlay()

    def _drawing_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        return (
            ("switch_view", "Switch View", 90.0),
            ("save", "Save Drawing", 18.0),
            ("shape", "Shape Mode", 306.0),
            ("pen_options", "Pen Options", 234.0),
            ("eraser_options", "Eraser Options", 162.0),
        )

    def _drawing_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._drawing_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _queue_drawing_request(self, action: str) -> None:
        self._drawing_request_token += 1
        self._drawing_request_action = str(action or "")

    def _execute_drawing_wheel_action(self, key: str) -> None:
        if key == "switch_view":
            if self._drawing_render_target == "camera":
                self._commit_camera_draw_stroke()
                self._camera_draw_last_point = None
                self._camera_draw_erasing = False
            self._drawing_render_target = "camera" if self._drawing_render_target == "screen" else "screen"
            self._drawing_control_text = f"drawing target {self._drawing_render_target}"
            self.command_detected.emit(self._drawing_control_text)
            return
        if key == "pen_options":
            self._queue_drawing_request("pen_options")
            self._drawing_control_text = "drawing pen options"
            self.command_detected.emit("Drawing pen options")
            return
        if key == "eraser_options":
            self._queue_drawing_request("eraser_options")
            self._drawing_control_text = "drawing eraser options"
            self.command_detected.emit("Drawing eraser options")
            return
        if key == "save":
            self._queue_drawing_request("save")
            self._drawing_control_text = "drawing save"
            self.command_detected.emit("Saving drawing")
            return
        if key == "shape":
            self._drawing_shape_mode = not self._drawing_shape_mode
            self._queue_drawing_request("shape_on" if self._drawing_shape_mode else "shape_off")
            self._drawing_control_text = "shape mode on" if self._drawing_shape_mode else "shape mode off"
            self.command_detected.emit(self._drawing_control_text)
            return
        self._drawing_control_text = "drawing wheel action"
        self.command_detected.emit(self._drawing_control_text)

    def _update_drawing_wheel_selection(self, hand_reading, now: float) -> None:
        if self._drawing_wheel_anchor is None:
            self._drawing_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._drawing_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._drawing_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._wheel_selection_key(float(offset[0]), float(offset[1]), self._drawing_wheel_items())
        if selection_key is None:
            self._drawing_wheel_selected_key = None
            self._drawing_wheel_selected_since = now
            self._drawing_control_text = "drawing wheel active"
            return
        if selection_key != self._drawing_wheel_selected_key:
            self._drawing_wheel_selected_key = selection_key
            self._drawing_wheel_selected_since = now
            self._drawing_control_text = f"drawing wheel: {self._drawing_wheel_label(selection_key)}"
            return
        if now - self._drawing_wheel_selected_since < 0.85:
            return
        self._execute_drawing_wheel_action(selection_key)
        self._drawing_wheel_cooldown_until = now + 1.5
        self._reset_drawing_wheel()

    def _update_drawing_wheel(self, prediction, hand_reading, now: float, *, active: bool) -> bool:
        if not active or prediction is None or hand_reading is None:
            if self._drawing_wheel_visible and now >= self._drawing_wheel_pose_grace_until:
                self._drawing_control_text = "drawing wheel closed"
                self._reset_drawing_wheel()
            else:
                self._drawing_wheel_candidate = "neutral"
                self._drawing_wheel_candidate_since = now
            return self._drawing_wheel_visible
        wheel_pose = self._drawing_wheel_pose_active(prediction)
        if self._drawing_wheel_visible:
            if wheel_pose:
                self._drawing_wheel_pose_grace_until = now + 0.25
                self._update_drawing_wheel_selection(hand_reading, now)
            elif now >= self._drawing_wheel_pose_grace_until:
                self._drawing_control_text = "drawing wheel closed"
                self._reset_drawing_wheel()
            return True
        if now < self._drawing_wheel_cooldown_until:
            if not wheel_pose:
                self._drawing_wheel_candidate = "neutral"
            return False
        if not wheel_pose:
            self._drawing_wheel_candidate = "neutral"
            self._drawing_wheel_candidate_since = now
            return False
        if self._drawing_wheel_candidate != "wheel_pose":
            self._drawing_wheel_candidate = "wheel_pose"
            self._drawing_wheel_candidate_since = now
            self._drawing_control_text = "hold wheel pose for drawing settings"
            return True
        if now - self._drawing_wheel_candidate_since < 0.85:
            return True
        self._drawing_wheel_visible = True
        self._drawing_wheel_anchor = hand_reading.palm.center.copy()
        self._drawing_wheel_cursor_offset = (0.0, 0.0)
        self._drawing_wheel_selected_key = None
        self._drawing_wheel_selected_since = now
        self._drawing_wheel_pose_grace_until = now + 0.25
        self._drawing_control_text = "drawing wheel active"
        return True

    def acknowledge_drawing_request(self, token: int | None = None) -> None:
        try:
            current_token = int(self._drawing_request_token)
        except Exception:
            current_token = 0
        if token is not None:
            try:
                requested_token = int(token)
            except Exception:
                requested_token = -1
            if requested_token != current_token:
                return
        self._drawing_request_action = ""

    def _reset_utility_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._utility_wheel_candidate = "neutral"
        self._utility_wheel_candidate_since = 0.0
        self._utility_wheel_visible = False
        self._utility_wheel_anchor = None
        self._utility_wheel_selected_key = None
        self._utility_wheel_selected_since = 0.0
        self._utility_wheel_pose_grace_until = 0.0
        self._utility_wheel_cursor_offset = None
        if clear_cooldown:
            self._utility_wheel_cooldown_until = 0.0
        if self.utility_wheel_overlay.isVisible():
            self.utility_wheel_overlay.hide_overlay()

    def _utility_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        labels = (
            ("screenshot", "Screenshot"),
            ("screenshot_custom", "Screenshot Custom"),
            ("screen_record", "Screen Record"),
            ("screen_record_custom", "Screen Record Custom"),
            ("clip_30s", "Clip 30 Sec"),
            ("clip_1m", "Clip 1 Min"),
        )
        slice_span = 360.0 / len(labels)
        return tuple(
            (key, label, (90.0 - index * slice_span) % 360.0)
            for index, (key, label) in enumerate(labels)
        )

    def _utility_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._utility_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _utility_wheel_pose_active(self, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        return (
            self._drawing_finger_extendedish(fingers.get("index"), primary=True)
            and self._drawing_finger_extendedish(fingers.get("pinky"))
            and self._drawing_finger_foldedish(fingers.get("thumb"))
            and self._drawing_finger_foldedish(fingers.get("middle"))
            and self._drawing_finger_foldedish(fingers.get("ring"))
        )

    def _queue_utility_request(self, action: str) -> None:
        self._utility_request_token += 1
        self._utility_request_action = str(action or "")

    def acknowledge_utility_request(self, token: int | None = None) -> None:
        try:
            current_token = int(self._utility_request_token)
        except Exception:
            current_token = 0
        if token is not None:
            try:
                requested_token = int(token)
            except Exception:
                requested_token = -1
            if requested_token != current_token:
                return
        self._utility_request_action = ""

    def _execute_utility_wheel_action(self, key: str) -> None:
        if key == "screenshot":
            self._queue_utility_request("screenshot_full")
            self.command_detected.emit("Full screenshot in 3 seconds")
            return
        if key == "screenshot_custom":
            self._queue_utility_request("screenshot_custom")
            self.command_detected.emit("Drag to choose screenshot area")
            return
        if key == "screen_record":
            self._queue_utility_request("record_full")
            self.command_detected.emit("Full screen record in 3 seconds")
            return
        if key == "screen_record_custom":
            self._queue_utility_request("record_custom")
            self.command_detected.emit("Drag to choose recording area")
            return
        if key == "clip_30s":
            self._queue_utility_request("clip_30s")
            self.command_detected.emit("Saving last 30 seconds")
            return
        if key == "clip_1m":
            self._queue_utility_request("clip_1m")
            self.command_detected.emit("Saving last 1 minute")
            return
        self.command_detected.emit(f"{self._utility_wheel_label(key)} coming soon")

    def _update_utility_wheel_selection(self, hand_reading, now: float) -> None:
        if self._utility_wheel_anchor is None:
            self._utility_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._utility_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._utility_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._wheel_selection_key(float(offset[0]), float(offset[1]), self._utility_wheel_items())
        if selection_key is None:
            self._utility_wheel_selected_key = None
            self._utility_wheel_selected_since = now
            return
        if selection_key != self._utility_wheel_selected_key:
            self._utility_wheel_selected_key = selection_key
            self._utility_wheel_selected_since = now
            return
        if now - self._utility_wheel_selected_since < 0.85:
            return
        self._execute_utility_wheel_action(selection_key)
        self._utility_wheel_cooldown_until = now + 1.5
        self._reset_utility_wheel()

    def _update_utility_wheel(self, hand_reading, hand_handedness: str | None, now: float) -> bool:
        active = hand_handedness == "Right" and hand_reading is not None
        if not active:
            if self._utility_wheel_visible and now >= self._utility_wheel_pose_grace_until:
                self._reset_utility_wheel()
            else:
                self._utility_wheel_candidate = "neutral"
                self._utility_wheel_candidate_since = now
            return self._utility_wheel_visible
        wheel_pose = self._utility_wheel_pose_active(hand_reading)
        if self._utility_wheel_visible:
            if wheel_pose:
                self._utility_wheel_pose_grace_until = now + 0.25
                self._update_utility_wheel_selection(hand_reading, now)
            elif now >= self._utility_wheel_pose_grace_until:
                self._reset_utility_wheel()
            return True
        if now < self._utility_wheel_cooldown_until:
            if not wheel_pose:
                self._utility_wheel_candidate = "neutral"
            return False
        if not wheel_pose:
            self._utility_wheel_candidate = "neutral"
            self._utility_wheel_candidate_since = now
            return False
        if self._utility_wheel_candidate != "utility_wheel_pose":
            self._utility_wheel_candidate = "utility_wheel_pose"
            self._utility_wheel_candidate_since = now
            return True
        if now - self._utility_wheel_candidate_since < 0.85:
            return True
        self._utility_wheel_visible = True
        self._utility_wheel_anchor = hand_reading.palm.center.copy()
        self._utility_wheel_cursor_offset = (0.0, 0.0)
        self._utility_wheel_selected_key = None
        self._utility_wheel_selected_since = now
        self._utility_wheel_pose_grace_until = now + 0.25
        return True

    def set_utility_recording_active(self, active: bool) -> None:
        self._utility_recording_active = bool(active)
        if not self._utility_recording_active:
            self._utility_recording_stop_candidate_since = 0.0

    def set_utility_capture_selection_active(self, active: bool) -> None:
        self._utility_capture_selection_active = bool(active)
        self._utility_capture_clicks_armed = False
        if not self._utility_capture_selection_active:
            self._utility_capture_cursor_norm = None
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False

    def _utility_capture_click_down(self, finger) -> bool:
        if finger is None:
            return False
        openness = float(getattr(finger, 'openness', 0.0) or 0.0)
        curl = float(getattr(finger, 'curl', 0.0) or 0.0)
        return finger.state in {'closed', 'mostly_curled'} or openness <= 0.42 or curl >= 0.52

    def _update_utility_capture_selection(self, hand_reading, hand_handedness: str | None) -> None:
        if hand_handedness != 'Right' or hand_reading is None:
            self._utility_capture_cursor_norm = None
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False
            self._utility_capture_clicks_armed = False
            return
        try:
            palm_center = getattr(hand_reading.palm, 'center', None)
            if palm_center is None or len(palm_center) < 2:
                raise ValueError('missing palm center')
            cursor_x = max(0.0, min(1.0, float(palm_center[0])))
            cursor_y = max(0.0, min(1.0, float(palm_center[1])))
        except Exception:
            self._utility_capture_cursor_norm = None
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False
            self._utility_capture_clicks_armed = False
            return
        fingers = hand_reading.fingers
        self._utility_capture_cursor_norm = (cursor_x, cursor_y)
        index_down = self._utility_capture_click_down(fingers.get('index'))
        middle_down = self._utility_capture_click_down(fingers.get('middle'))
        if not self._utility_capture_clicks_armed:
            self._utility_capture_left_down = False
            self._utility_capture_right_down = False
            if not index_down and not middle_down:
                self._utility_capture_clicks_armed = True
            return
        self._utility_capture_left_down = index_down and not middle_down
        self._utility_capture_right_down = middle_down and not index_down

    def set_drawing_brush(self, color: str | None = None, thickness: int | None = None) -> None:
        if color:
            self._drawing_brush_hex = str(color)
        if thickness is not None:
            self._drawing_brush_thickness = int(max(2, thickness))

    def set_drawing_eraser(self, thickness: int | None = None, mode: str | None = None) -> None:
        if thickness is not None:
            self._drawing_eraser_thickness = int(max(4, thickness))
        if mode:
            normalized = str(mode).strip().lower()
            if normalized in {"normal", "stroke"}:
                self._drawing_eraser_mode = normalized

    def _drawing_brush_bgr(self) -> tuple[int, int, int]:
        value = str(self._drawing_brush_hex or "#FFFFFF").strip().lstrip("#")
        if len(value) != 6:
            return (255, 255, 255)
        try:
            r = int(value[0:2], 16); g = int(value[2:4], 16); b = int(value[4:6], 16)
        except Exception:
            return (255, 255, 255)
        return (b, g, r)

    def _ensure_camera_draw_canvas(self, frame_shape) -> None:
        height, width = frame_shape[:2]
        if height <= 0 or width <= 0:
            return
        if self._camera_draw_canvas is not None and self._camera_draw_canvas.shape[:2] == (height, width):
            return
        self._camera_draw_canvas = np.zeros((height, width, 4), dtype=np.uint8)
        self._camera_draw_history = []
        self._camera_draw_strokes = []
        self._camera_draw_active_stroke_points = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False

    def _clone_camera_draw_strokes(self) -> list[dict]:
        clones: list[dict] = []
        for stroke in self._camera_draw_strokes:
            clones.append(
                {
                    "color": tuple(int(v) for v in stroke.get("color", (255, 255, 255))),
                    "thickness": int(stroke.get("thickness", 2)),
                    "points": [(float(x), float(y)) for x, y in stroke.get("points", [])],
                }
            )
        return clones

    def _camera_draw_rerender_from_strokes(self) -> None:
        if self._camera_draw_canvas is None:
            return
        self._camera_draw_canvas[:, :, :] = 0
        for stroke in self._camera_draw_strokes:
            points = stroke.get("points") or []
            if len(points) < 2:
                continue
            color = tuple(int(v) for v in stroke.get("color", (255, 255, 255)))
            thickness = int(max(1, stroke.get("thickness", 2)))
            for (x1, y1), (x2, y2) in zip(points, points[1:]):
                cv2.line(
                    self._camera_draw_canvas,
                    (int(round(x1)), int(round(y1))),
                    (int(round(x2)), int(round(y2))),
                    (*color, 255),
                    thickness,
                    cv2.LINE_AA,
                )

    @staticmethod
    def _camera_point_to_segment_distance_sq(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
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

    def _camera_draw_stroke_hits_position(self, stroke: dict, px: float, py: float, radius: float) -> bool:
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
            if self._camera_point_to_segment_distance_sq(px, py, float(ax), float(ay), float(bx), float(by)) <= limit_sq:
                return True
        return False

    def _commit_camera_draw_stroke(self) -> None:
        if not self._camera_draw_active_stroke_points:
            return
        points = list(self._camera_draw_active_stroke_points)
        if len(points) == 1:
            x, y = points[0]
            points.append((x + 0.01, y + 0.01))
        self._camera_draw_strokes.append(
            {
                "color": tuple(int(v) for v in self._drawing_brush_bgr()),
                "thickness": int(max(2, self._drawing_brush_thickness)),
                "points": points,
            }
        )
        self._camera_draw_active_stroke_points = []

    def _camera_draw_point(self, frame_shape) -> tuple[int, int] | None:
        if self._drawing_cursor_norm is None:
            return None
        height, width = frame_shape[:2]
        x = int(round(max(0.0, min(1.0, float(self._drawing_cursor_norm[0]))) * max(width - 1, 1)))
        y = int(round(max(0.0, min(1.0, float(self._drawing_cursor_norm[1]))) * max(height - 1, 1)))
        return x, y

    def _camera_draw_push_history(self) -> None:
        if self._camera_draw_canvas is None:
            return
        self._camera_draw_history.append(
            (
                self._camera_draw_canvas.copy(),
                self._clone_camera_draw_strokes(),
                bool(self._camera_draw_raster_dirty),
            )
        )
        if len(self._camera_draw_history) > 24:
            self._camera_draw_history = self._camera_draw_history[-24:]

    def _camera_draw_undo(self) -> bool:
        if not self._camera_draw_history:
            return False
        canvas, strokes, raster_dirty = self._camera_draw_history.pop()
        self._camera_draw_canvas = canvas
        self._camera_draw_strokes = strokes
        self._camera_draw_raster_dirty = bool(raster_dirty)
        self._camera_draw_active_stroke_points = []
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False
        return True

    def _perform_drawing_swipe_action(self, direction: str) -> bool:
        key = str(direction or "").strip().lower()
        if key not in {"swipe_left", "swipe_right"}:
            return False
        if self._drawing_render_target == "camera":
            self._ensure_camera_draw_canvas((480, 640, 3))
            if key == "swipe_left":
                success = self._camera_draw_undo()
                self._drawing_control_text = "drawing undo" if success else "drawing nothing to undo"
            else:
                if self._camera_draw_canvas is not None:
                    self._camera_draw_push_history()
                    self._camera_draw_canvas[:, :, :] = 0
                    self._camera_draw_strokes = []
                    self._camera_draw_active_stroke_points = []
                    self._camera_draw_raster_dirty = False
                    self._camera_draw_last_point = None
                success = True
                self._drawing_control_text = "drawing cleared"
            self.command_detected.emit(self._drawing_control_text)
            return success
        self._queue_drawing_request("undo" if key == "swipe_left" else "clear")
        self._drawing_control_text = "drawing undo" if key == "swipe_left" else "drawing cleared"
        self.command_detected.emit(self._drawing_control_text)
        return True

    def _update_camera_drawing_canvas(self, frame_shape) -> None:
        if self._drawing_render_target != "camera":
            self._commit_camera_draw_stroke()
            self._camera_draw_last_point = None
            self._camera_draw_erasing = False
            return
        self._ensure_camera_draw_canvas(frame_shape)
        if self._camera_draw_canvas is None:
            return
        point = self._camera_draw_point(frame_shape)
        if point is None:
            self._commit_camera_draw_stroke()
            self._camera_draw_last_point = None
            self._camera_draw_erasing = False
            return
        brush = self._drawing_brush_bgr()
        thickness = int(max(2, self._drawing_brush_thickness))
        if self._drawing_tool == "draw":
            self._camera_draw_erasing = False
            if self._camera_draw_last_point is None:
                self._camera_draw_push_history()
                self._camera_draw_last_point = point
                self._camera_draw_active_stroke_points = [(float(point[0]), float(point[1]))]
            cv2.line(self._camera_draw_canvas, self._camera_draw_last_point, point, (*brush, 255), thickness, cv2.LINE_AA)
            self._camera_draw_active_stroke_points.append((float(point[0]), float(point[1])))
            self._camera_draw_last_point = point
            return
        self._commit_camera_draw_stroke()
        if self._drawing_tool == "erase":
            radius = max(10, int(max(4, self._drawing_eraser_thickness) * 1.35))
            if self._drawing_eraser_mode == "stroke":
                if self._camera_draw_raster_dirty and self._camera_draw_strokes:
                    self._camera_draw_raster_dirty = False
                    self._camera_draw_rerender_from_strokes()
                if not self._camera_draw_erasing:
                    self._camera_draw_push_history()
                    self._camera_draw_erasing = True
                px = float(point[0])
                py = float(point[1])
                hit_index = None
                for idx in range(len(self._camera_draw_strokes) - 1, -1, -1):
                    if self._camera_draw_stroke_hits_position(self._camera_draw_strokes[idx], px, py, float(radius)):
                        hit_index = idx
                        break
                if hit_index is not None:
                    self._camera_draw_strokes.pop(hit_index)
                    self._camera_draw_rerender_from_strokes()
            else:
                if not self._camera_draw_erasing:
                    self._camera_draw_push_history()
                    self._camera_draw_erasing = True
                cv2.circle(self._camera_draw_canvas, point, radius, (0, 0, 0, 0), thickness=-1, lineType=cv2.LINE_AA)
                self._camera_draw_raster_dirty = True
            self._camera_draw_last_point = None
        else:
            self._camera_draw_last_point = None
            self._camera_draw_erasing = False

    def _blend_camera_drawing_overlay(self, frame) -> None:
        if self._drawing_render_target != "camera" or self._camera_draw_canvas is None:
            return
        alpha = self._camera_draw_canvas[:, :, 3:4].astype(np.float32) / 255.0
        if float(alpha.max()) > 0.0:
            overlay_rgb = self._camera_draw_canvas[:, :, :3].astype(np.float32)
            frame[:] = np.clip(frame.astype(np.float32) * (1.0 - alpha) + overlay_rgb * alpha, 0.0, 255.0).astype(np.uint8)
        point = self._camera_draw_point(frame.shape)
        if point is None or self._drawing_tool == "hidden":
            return
        radius = max(6, int(self._drawing_brush_thickness))
        if self._drawing_tool == "draw":
            cv2.circle(frame, point, radius, self._drawing_brush_bgr(), thickness=-1, lineType=cv2.LINE_AA)
            cv2.circle(frame, point, radius + 2, (255, 255, 255), thickness=2, lineType=cv2.LINE_AA)
        elif self._drawing_tool == "erase":
            cv2.circle(frame, point, max(8, int(radius * 1.5)), (255, 255, 255), thickness=2, lineType=cv2.LINE_AA)
        else:
            cv2.circle(frame, point, radius, (255, 255, 255), thickness=2, lineType=cv2.LINE_AA)

    def _reset_window_gesture_state(self, *, clear_cooldown: bool = False) -> None:
        self._window_expand_candidate_since = 0.0
        self._window_contract_candidate_since = 0.0
        self._window_close_candidate_since = 0.0
        self._window_pair_smoothed_distance = None
        self._window_pair_last_seen_at = 0.0
        self._window_pair_overlay = None
        self._window_sequence_start_state = None
        self._window_sequence_start_candidate = None
        self._window_sequence_start_candidate_since = 0.0
        self._window_sequence_target_candidate = None
        self._window_sequence_target_candidate_since = 0.0
        if clear_cooldown:
            self._window_gesture_cooldown_until = 0.0

    def _window_pair_pose_metrics(self, hand_reading, now: float | None = None):
        if hand_reading is None:
            return None
        fingers = hand_reading.fingers
        palm_scale = max(float(hand_reading.palm.scale), 1e-6)
        thumb_tip = np.array(hand_reading.landmarks[4][:2], dtype=np.float32)
        index_tip = np.array(hand_reading.landmarks[8][:2], dtype=np.float32)
        raw_distance = float(np.linalg.norm(thumb_tip - index_tip)) / palm_scale
        if self._window_pair_smoothed_distance is None:
            smoothed_distance = raw_distance
        else:
            smoothed_distance = 0.28 * raw_distance + 0.72 * self._window_pair_smoothed_distance
        thumb_ready = (
            fingers["thumb"].state in {"fully_open", "partially_curled", "mostly_curled"}
            and float(fingers["thumb"].openness) >= 0.16
        )
        index_ready = (
            fingers["index"].state in {"fully_open", "partially_curled", "mostly_curled"}
            and float(fingers["index"].openness) >= 0.16
        )
        folded_rest = all(
            fingers[name].state in {"mostly_curled", "closed"}
            or float(getattr(fingers[name], "curl", 0.0) or 0.0) >= 0.40
            or float(getattr(fingers[name], "openness", 0.0) or 0.0) <= 0.56
            for name in ("middle", "ring", "pinky")
        )
        active = bool(thumb_ready and index_ready and folded_rest)
        if active:
            self._window_pair_smoothed_distance = smoothed_distance
            if now is not None:
                self._window_pair_last_seen_at = float(now)
            self._window_pair_overlay = {
                "thumb": (float(thumb_tip[0]), float(thumb_tip[1])),
                "index": (float(index_tip[0]), float(index_tip[1])),
                "raw_distance": raw_distance,
                "distance": smoothed_distance,
            }
            return self._window_pair_overlay
        if now is not None and (now - self._window_pair_last_seen_at) <= 0.22 and self._window_pair_overlay is not None:
            return dict(self._window_pair_overlay)
        self._window_pair_smoothed_distance = None
        self._window_pair_overlay = None
        return None

    def _window_pair_state(self, distance_ratio: float) -> str:
        value = float(distance_ratio)
        if value <= 0.40:
            return "pinched"
        if value >= 0.54:
            return "apart"
        if 0.30 <= value <= 0.66:
            return "mid"
        return "neutral"

    def _window_close_pose_active(self, hand_reading) -> bool:
        if hand_reading is None:
            return False
        fingers = hand_reading.fingers
        palm_scale = max(float(hand_reading.palm.scale), 1e-6)
        thumb_index_ratio = float(np.linalg.norm((hand_reading.landmarks[4] - hand_reading.landmarks[8])[:2])) / palm_scale
        thumb_out = (fingers["thumb"].state == "fully_open" and fingers["thumb"].openness >= 0.70 and fingers["thumb"].palm_distance >= 0.72)
        folded_core = all(fingers[name].state in {"mostly_curled", "closed"} for name in ("index", "middle", "ring", "pinky"))
        return thumb_out and folded_core and thumb_index_ratio >= 0.62

    def _handle_window_control_gestures(self, hand_reading, hand_handedness: str | None, now: float) -> bool:
        if hand_handedness != "Right" or hand_reading is None:
            self._reset_window_gesture_state(clear_cooldown=False)
            return False
        if now < self._window_gesture_cooldown_until:
            return False
        controller = self.voice_processor.desktop_controller
        if self._window_close_pose_active(hand_reading):
            if self._window_close_candidate_since <= 0.0:
                self._window_close_candidate_since = now
            if now - self._window_close_candidate_since >= 1.0:
                success = controller.close_active_window()
                self._chrome_control_text = controller.message
                self._spotify_control_text = controller.message
                if success:
                    self.command_detected.emit(controller.message)
                    self._window_gesture_cooldown_until = now + 2.0
                self._reset_window_gesture_state(clear_cooldown=False)
                return True
            return True
        self._window_close_candidate_since = 0.0
        metrics = self._window_pair_pose_metrics(hand_reading, now=now)
        if metrics is None:
            self._window_sequence_start_candidate = None
            self._window_sequence_start_candidate_since = 0.0
            self._window_sequence_target_candidate = None
            self._window_sequence_target_candidate_since = 0.0
            return False
        state = self._window_pair_state(float(metrics["distance"]))
        if self._window_sequence_start_state is None:
            if state not in {"pinched", "apart"}:
                self._window_sequence_start_candidate = None
                self._window_sequence_start_candidate_since = 0.0
                return True
            if state != self._window_sequence_start_candidate:
                self._window_sequence_start_candidate = state
                self._window_sequence_start_candidate_since = now
                return True
            if now - self._window_sequence_start_candidate_since >= 0.6:
                self._window_sequence_start_state = state
                self._window_sequence_target_candidate = None
                self._window_sequence_target_candidate_since = 0.0
                label = "pinched" if state == "pinched" else "apart"
                self._chrome_control_text = f"window control start: {label}"
                self._spotify_control_text = self._chrome_control_text
            return True
        start_state = self._window_sequence_start_state
        target_state = None
        action = None
        if start_state == "pinched":
            if state == "apart":
                target_state = "apart"
                action = "maximize"
            elif state == "mid":
                target_state = "mid"
                action = "restore"
        elif start_state == "apart":
            if state == "pinched":
                target_state = "pinched"
                action = "minimize"
            elif state == "mid":
                target_state = "mid"
                action = "restore"
        if target_state is None:
            self._window_sequence_target_candidate = None
            self._window_sequence_target_candidate_since = 0.0
            return True
        if target_state != self._window_sequence_target_candidate:
            self._window_sequence_target_candidate = target_state
            self._window_sequence_target_candidate_since = now
            return True
        if now - self._window_sequence_target_candidate_since < 0.6:
            return True
        if action == "maximize":
            success = controller.maximize_active_window()
        elif action == "minimize":
            success = controller.minimize_active_window()
        else:
            success = controller.restore_active_window()
        self._chrome_control_text = controller.message
        self._spotify_control_text = controller.message
        if success:
            self.command_detected.emit(controller.message)
            self._window_gesture_cooldown_until = now + 2.0
        self._reset_window_gesture_state(clear_cooldown=False)
        return True

    def _draw_window_control_overlay(self, frame) -> None:
        overlay = self._window_pair_overlay
        if not overlay or frame is None:
            return
        frame_h, frame_w = frame.shape[:2]
        thumb = overlay.get("thumb")
        index = overlay.get("index")
        if thumb is None or index is None:
            return
        p1 = (int(round(max(0.0, min(1.0, float(thumb[0]))) * max(frame_w - 1, 1))), int(round(max(0.0, min(1.0, float(thumb[1]))) * max(frame_h - 1, 1))))
        p2 = (int(round(max(0.0, min(1.0, float(index[0]))) * max(frame_w - 1, 1))), int(round(max(0.0, min(1.0, float(index[1]))) * max(frame_h - 1, 1))))
        cv2.line(frame, p1, p2, (255, 248, 212), 2, cv2.LINE_AA)
        cv2.circle(frame, p1, 6, (36, 220, 184), thickness=-1, lineType=cv2.LINE_AA)
        cv2.circle(frame, p2, 6, (36, 220, 184), thickness=-1, lineType=cv2.LINE_AA)
        mid_x = int(round((p1[0] + p2[0]) * 0.5))
        mid_y = int(round((p1[1] + p2[1]) * 0.5))
        ratio = float(overlay.get("distance", 0.0) or 0.0)
        cv2.putText(frame, f"{ratio:.2f}", (mid_x + 8, mid_y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (255, 255, 255), 2, cv2.LINE_AA)
    def set_tutorial_context(self, enabled: bool, step_key: str | None = None) -> None:
        self._tutorial_mode_enabled = bool(enabled)
        if enabled:
            normalized_step = str(step_key or "").strip()
            self._tutorial_step_key = normalized_step or None
        else:
            self._tutorial_step_key = None
        if not self._tutorial_mode_enabled or self._tutorial_step_key != "gesture_wheel":
            self._reset_chrome_wheel()
            self._reset_spotify_wheel()
        if not self._tutorial_mode_enabled:
            self.chrome_router.reset()
            self.spotify_router.reset()

    def _build_mouse_tracker(self) -> MouseGestureTracker:
        return MouseGestureTracker(
            control_box_center_x=self.config.mouse_control_box_center_x,
            control_box_center_y=self.config.mouse_control_box_center_y,
            control_box_area=self.config.mouse_control_box_area,
            control_box_aspect_power=self.config.mouse_control_box_aspect_power,
        )

    def _blank_mouse_update(self):
        return SimpleNamespace(
            mode_enabled=False,
            cursor_position=None,
            left_click=False,
            left_press=False,
            left_release=False,
            right_click=False,
            scroll_steps=0,
        )

    def apply_config(self, config) -> None:
        self.config = config
        self.volume_overlay.apply_theme(config)
        self.voice_status_overlay.apply_theme(config)
        self.spotify_wheel_overlay.apply_theme(config)
        self.chrome_wheel_overlay.apply_theme(config)
        self.drawing_wheel_overlay.apply_theme(config)
        self.utility_wheel_overlay.apply_theme(config)
        self.mouse_tracker = self._build_mouse_tracker()

    def start(self) -> None:
        if self._running:
            return
        self._shutdown_runtime(emit_signal=False)
        self.engine = GestureRecognitionEngine(stable_frames_required=max(2, self.config.stable_frames_required // 2))
        self._volume_message = self.volume_controller.message
        self._volume_level = self.volume_controller.refresh_cache().level_scalar
        self._volume_mode_active = False
        self._volume_status_text = "idle"
        self._volume_muted = self._read_system_mute()
        self._volume_overlay_visible = False
        self._mute_block_until = 0.0
        self.volume_tracker.reset(self._volume_level, self._volume_muted)
        self.volume_overlay.hide_overlay()
        self.voice_status_overlay.hide_overlay()
        self.mouse_controller.release_all()
        self.mouse_tracker.reset()
        self._last_mouse_update = self._blank_mouse_update()
        self._mouse_mode_enabled = False
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"
        self._reset_chrome_wheel(clear_cooldown=True)
        self._reset_spotify_wheel(clear_cooldown=True)
        self.chrome_router.reset()
        self._chrome_mode_enabled = False
        self._chrome_control_text = self.chrome_controller.message
        self._last_chrome_action = "-"
        self.spotify_router.reset()
        self._spotify_control_text = self.spotify_controller.message
        self._spotify_info_text = "-"
        self._last_spotify_action = "-"
        self._reset_voice_state()
        self._reset_drawing_runtime()
        self._reset_drawing_wheel(clear_cooldown=True)
        self._reset_utility_wheel(clear_cooldown=True)
        self._camera_draw_canvas = None
        self._camera_draw_history = []
        self._camera_draw_strokes = []
        self._camera_draw_active_stroke_points = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False
        self._drawing_request_action = ""
        self._drawing_swipe_cooldown_until = 0.0
        self._drawing_request_token = 0
        self._utility_request_action = ""
        self._utility_request_token = 0
        self._utility_recording_active = False
        self._utility_recording_stop_candidate_since = 0.0
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._utility_capture_clicks_armed = False
        self._reset_window_gesture_state(clear_cooldown=True)

        camera_info, cap = self._open_camera()
        if cap is None or camera_info is None:
            self._emit_status("no camera found")
            self.error_occurred.emit("No available camera was found.")
            self.running_state_changed.emit(False)
            return

        self._camera_info = camera_info
        self._cap = cap
        self._running = True
        self._last_time = time.time()
        self._fps = 0.0
        self._dynamic_hold_label = "neutral"
        self._dynamic_hold_until = 0.0
        self.camera_selected.emit(camera_info.display_name)
        self._emit_status("HGR active")
        self.command_detected.emit("Gesture and voice control active")
        self.running_state_changed.emit(True)
        self._timer.start()

        def _warm_spotify() -> None:
            try:
                self.spotify_controller.warm_up()
            except Exception:
                pass

        import threading
        threading.Thread(target=_warm_spotify, daemon=True).start()

    def stop(self) -> None:
        if not self._running and self._cap is None and self.engine is None:
            return
        self._shutdown_runtime(emit_signal=True)

    def _shutdown_runtime(self, *, emit_signal: bool) -> None:
        self._timer.stop()
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        self._camera_info = None
        self._running = False
        self._fps = 0.0
        self._dynamic_hold_label = "neutral"
        self._dynamic_hold_until = 0.0
        self._volume_mode_active = False
        self._volume_overlay_visible = False
        self.volume_overlay.hide_overlay()
        self.voice_status_overlay.hide_overlay()
        self.mouse_controller.release_all()
        self.mouse_tracker.reset()
        self._last_mouse_update = self._blank_mouse_update()
        self._mouse_mode_enabled = False
        self._mouse_control_text = "mouse mode off" if self.mouse_controller.available else self.mouse_controller.message
        self._mouse_status_text = "off" if self.mouse_controller.available else "unavailable"
        self._reset_chrome_wheel(clear_cooldown=True)
        self._reset_spotify_wheel(clear_cooldown=True)
        self.chrome_router.reset()
        self.spotify_router.reset()
        self.volume_tracker.reset(self._volume_level, self._volume_muted)
        self._reset_voice_state()
        self._reset_drawing_runtime()
        self._reset_drawing_wheel(clear_cooldown=True)
        self._reset_utility_wheel(clear_cooldown=True)
        self._camera_draw_canvas = None
        self._camera_draw_history = []
        self._camera_draw_strokes = []
        self._camera_draw_active_stroke_points = []
        self._camera_draw_raster_dirty = False
        self._camera_draw_last_point = None
        self._camera_draw_erasing = False
        self._drawing_request_action = ""
        self._drawing_swipe_cooldown_until = 0.0
        self._utility_request_action = ""
        self._utility_recording_active = False
        self._utility_recording_stop_candidate_since = 0.0
        self._utility_capture_selection_active = False
        self._utility_capture_cursor_norm = None
        self._utility_capture_left_down = False
        self._utility_capture_right_down = False
        self._utility_capture_clicks_armed = False
        self._reset_window_gesture_state(clear_cooldown=True)
        self._emit_status("idle")
        if emit_signal:
            self.running_state_changed.emit(False)

    def _open_camera(self):
        if self.camera_index_override is not None:
            return open_camera_by_index(self.camera_index_override, max_index=self.config.camera_scan_limit)
        return open_preferred_or_first_available(self.config.preferred_camera_index, max_index=self.config.camera_scan_limit)

    def _tick(self) -> None:
        if not self._running or self._cap is None or self.engine is None:
            return
        ok, frame = self._cap.read()
        if not ok:
            return
        frame = cv2.flip(frame, 1)
        result = self.engine.process_frame(frame)
        now = time.time()
        dt = max(now - self._last_time, 1e-6)
        self._fps = 0.86 * self._fps + 0.14 * (1.0 / dt) if self._fps else (1.0 / dt)
        self._last_time = now
        self._drain_voice_results()
        if not self._dictation_active:
            try:
                self.text_input_controller.remember_active_window()
            except Exception:
                pass
        hand_handedness = result.tracked_hand.handedness if result.found and result.tracked_hand is not None else None
        monotonic_now = time.monotonic()
        if self._gestures_enabled:
            if self._drawing_mode_enabled:
                self._volume_mode_active = False
                self._volume_status_text = "paused"
                self._volume_message = "drawing mode active"
                self._volume_overlay_visible = False
                self._update_volume_overlay()
            else:
                self._handle_volume_control(result, monotonic_now, hand_handedness=hand_handedness)
            self._handle_app_controls(result.prediction, result.hand_reading, hand_handedness, monotonic_now)
        else:
            self._window_pair_pose_metrics(result.hand_reading if hand_handedness == "Right" else None, now=monotonic_now)
        self._update_chrome_wheel_overlay(monotonic_now)
        self._update_spotify_wheel_overlay(monotonic_now)
        self._update_drawing_wheel_overlay(monotonic_now)
        self._update_utility_wheel_overlay(monotonic_now)
        self.voice_status_overlay.tick(monotonic_now)
        self._update_runtime_status()
        display_frame = draw_hand_overlay(result.annotated_frame, result)
        self._draw_window_control_overlay(display_frame)
        self._update_camera_drawing_canvas(display_frame.shape)
        self._blend_camera_drawing_overlay(display_frame)
        draw_mouse_control_box_overlay(
            display_frame,
            debug_state=self.mouse_tracker.debug_state,
            mode_enabled=self._mouse_mode_enabled,
        )
        draw_mouse_monitor_overlay(
            display_frame,
            mouse_controller=self.mouse_controller,
            debug_state=self.mouse_tracker.debug_state,
            mode_enabled=self._mouse_mode_enabled,
        )
        payload = self._build_debug_payload(result, monotonic_now)
        try:
            self.debug_frame_ready.emit(display_frame, payload)
        except Exception:
            pass

    def _handle_volume_control(self, result, now: float, *, hand_handedness: str | None) -> None:
        if not self.volume_controller.available:
            self._volume_message = self.volume_controller.message
            self._volume_mode_active = False
            self._volume_status_text = "unavailable"
            self._volume_overlay_visible = False
            self._update_volume_overlay()
            return

        current_level = self.volume_controller.get_level()
        current_muted = self._read_system_mute()
        if self.mouse_tracker.mode_enabled:
            self._volume_level = current_level
            self._volume_muted = current_muted
            self._volume_message = "mouse mode active"
            self._volume_mode_active = False
            self._volume_status_text = "paused"
            self._volume_overlay_visible = False
            self._volume_dual_active = False
            self._volume_init_palm_x = None
            self._update_volume_overlay()
            return
        if self._drawing_mode_enabled:
            self._volume_level = current_level
            self._volume_muted = current_muted
            self._volume_message = "drawing mode active"
            self._volume_mode_active = False
            self._volume_status_text = "paused"
            self._volume_overlay_visible = False
            self._volume_dual_active = False
            self._volume_init_palm_x = None
            self._update_volume_overlay()
            return
        if hand_handedness == "Right" and result.prediction.dynamic_label in {"swipe_left", "swipe_right"}:
            self._mute_block_until = max(self._mute_block_until, now + 0.5)

        features = None
        candidate_scores = {}
        landmarks = None
        stable_gesture = "neutral"
        if (
            hand_handedness == "Right"
            and result.found
            and result.tracked_hand is not None
            and result.hand_reading is not None
            and self.engine is not None
        ):
            landmarks = result.tracked_hand.landmarks
            features = self._volume_features_from_hand_reading(result.hand_reading)
            candidate_scores = self.engine.last_static_scores
            stable_gesture = result.prediction.stable_label

        tracker_level = current_level
        if self._volume_dual_active and self._volume_bar_selected == "app" and self._volume_overlay_visible:
            tracker_level = self._volume_app_level if self._volume_app_level is not None else current_level

        update = self.volume_tracker.update(
            features=features,
            landmarks=landmarks,
            candidate_scores=candidate_scores,
            stable_gesture=stable_gesture,
            current_level=tracker_level,
            current_muted=current_muted,
            now=now,
            allow_mute_toggle=now >= self._mute_block_until,
        )

        entering_overlay = self._volume_overlay_visible is False and update.overlay_visible
        if entering_overlay:
            refreshed = self.volume_controller.refresh_cache()
            if refreshed.level_scalar is not None:
                current_level = refreshed.level_scalar
            refreshed_muted = self.volume_controller.get_mute()
            if refreshed_muted is not None:
                current_muted = bool(refreshed_muted)
            palm_center = getattr(getattr(result.hand_reading, "palm", None), "center", None) if result.hand_reading is not None else None
            self._volume_init_palm_x = float(palm_center[0]) if palm_center is not None else None
            self._volume_bar_selected = "sys"
            app_name, app_level = self.volume_controller.get_app_audio_info(["spotify", "chrome"])
            self._volume_dual_active = app_name is not None
            self._volume_app_process = app_name or ""
            self._volume_app_label = app_name.capitalize() if app_name else ""
            if app_name == "spotify":
                spotify_vol = self.spotify_controller.get_volume()
                self._volume_app_level = spotify_vol / 100.0 if spotify_vol is not None else (app_level or 0.5)
            else:
                self._volume_app_level = app_level
            self._volume_app_check_until = now + 3.0

        bar_switched_this_frame = False
        if update.overlay_visible and self._volume_dual_active:
            palm_center = getattr(getattr(result.hand_reading, "palm", None), "center", None) if result.hand_reading is not None else None
            if palm_center is not None and self._volume_init_palm_x is not None:
                dx = float(palm_center[0]) - self._volume_init_palm_x
                previous_bar = self._volume_bar_selected
                if dx < -0.06:
                    self._volume_bar_selected = "app"
                elif dx > 0.06:
                    self._volume_bar_selected = "sys"
                if self._volume_bar_selected != previous_bar:
                    bar_switched_this_frame = True
                    if self._volume_bar_selected == "app":
                        rebase_level = self._volume_app_level if self._volume_app_level is not None else current_level
                    else:
                        rebase_level = current_level
                    self.volume_tracker.rebase(rebase_level)

        controller_error_message: str | None = None
        controller_error_status: str | None = None
        if update.trigger_mute_toggle:
            toggled = self.volume_controller.toggle_mute()
            if toggled is not None:
                current_muted = toggled
                self.command_detected.emit("Volume mute toggled")
            else:
                controller_error_message = self.volume_controller.message or "mute failed"
                controller_error_status = "error"

        if update.active and update.level is not None and not bar_switched_this_frame:
            if self._volume_dual_active and self._volume_bar_selected == "app":
                new_app = max(0.0, min(1.0, float(update.level)))
                if self._volume_app_process == "spotify":
                    vol_pct = int(round(new_app * 100))
                    self._queue_spotify_volume(vol_pct)
                    self._volume_app_level = new_app
                else:
                    if self.volume_controller.set_app_audio_level(["chrome"], new_app):
                        self._volume_app_level = new_app
                    else:
                        controller_error_message = "app volume adjust failed"
                        controller_error_status = "error"
            else:
                if self.volume_controller.set_level(update.level):
                    current_level = update.level
                    read_back_level = self.volume_controller.get_level()
                    if read_back_level is not None:
                        current_level = read_back_level
                else:
                    controller_error_message = self.volume_controller.message or "set_level failed"
                    controller_error_status = "error"

        if update.overlay_visible and self._volume_dual_active and now >= self._volume_app_check_until and not (update.active and self._volume_bar_selected == "app"):
            self._volume_app_check_until = now + 3.0
            if self._volume_app_process == "spotify":
                def _refresh_spotify_vol():
                    vol = self.spotify_controller.get_volume()
                    if vol is not None:
                        self._volume_app_level = vol / 100.0
                threading.Thread(target=_refresh_spotify_vol, daemon=True).start()
            else:
                _, fresh_app = self.volume_controller.get_app_audio_info(["chrome"])
                if fresh_app is not None:
                    self._volume_app_level = fresh_app

        if not update.overlay_visible:
            self._volume_dual_active = False
            self._volume_init_palm_x = None

        self._volume_mode_active = update.active
        self._volume_level = current_level if current_level is not None else update.level
        self._volume_muted = current_muted
        self._volume_message = controller_error_message or update.message
        self._volume_status_text = controller_error_status or update.status
        self._volume_overlay_visible = update.overlay_visible
        self._update_volume_overlay()

    def _handle_app_controls(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> None:
        if self._tutorial_mode_enabled:
            self._handle_tutorial_controls(prediction, hand_reading, hand_handedness, now)
            return

        if self._utility_capture_selection_active:
            self._update_utility_capture_selection(hand_reading, hand_handedness)
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if self._drawing_mode_enabled:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
            return

        if self._utility_recording_active and hand_handedness == 'Right' and hand_reading is not None and self._utility_wheel_pose_active(hand_reading):
            if self._utility_recording_stop_candidate_since <= 0.0:
                self._utility_recording_stop_candidate_since = now
            elif now - self._utility_recording_stop_candidate_since >= 0.6:
                self._queue_utility_request('stop_recording')
                self.command_detected.emit('Stopping screen recording')
                self._utility_recording_stop_candidate_since = 0.0
                self._utility_wheel_cooldown_until = now + 1.2
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            return
        self._utility_recording_stop_candidate_since = 0.0

        utility_wheel_consuming = self._update_utility_wheel(hand_reading, hand_handedness, now)
        if utility_wheel_consuming:
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if self._drawing_mode_enabled:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
            return

        if self._dictation_active:
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            if hand_handedness == "Left":
                self._handle_left_hand_voice(prediction, now)
            else:
                self._reset_voice_candidate(now)
            return

        drawing_toggle_consuming = self._handle_drawing_toggle(prediction, hand_handedness, now)
        if drawing_toggle_consuming:
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if not self._drawing_mode_enabled:
                self._reset_drawing_wheel(clear_cooldown=False)
            self._chrome_control_text = self._drawing_control_text
            self._spotify_control_text = self._drawing_control_text
            return

        if self._drawing_mode_enabled:
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._reset_window_gesture_state(clear_cooldown=False)
            drawing_wheel_consuming = self._update_drawing_wheel(prediction, hand_reading, now, active=hand_handedness == "Right")
            if drawing_wheel_consuming:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
                self._chrome_control_text = self._drawing_control_text
                self._spotify_control_text = self._drawing_control_text
                return
            self._update_drawing_controls(prediction, hand_reading, hand_handedness, now)
            if hand_handedness == "Right" and hand_reading is not None and self._drawing_tool == "hover" and now >= self._drawing_swipe_cooldown_until:
                dynamic_label = str(getattr(prediction, "dynamic_label", "neutral") or "neutral")
                if dynamic_label in {"swipe_left", "swipe_right"}:
                    if self._perform_drawing_swipe_action(dynamic_label):
                        self._drawing_swipe_cooldown_until = now + 1.2
                    self._chrome_control_text = self._drawing_control_text
                    self._spotify_control_text = self._drawing_control_text
                    return
            self._chrome_control_text = self._drawing_control_text
            self._spotify_control_text = self._drawing_control_text
            return

        if self._handle_window_control_gestures(hand_reading, hand_handedness, now):
            return

        mouse_consuming = self._handle_mouse_control(
            prediction=prediction,
            hand_reading=hand_reading if hand_handedness == "Right" else None,
            hand_handedness=hand_handedness,
            now=now,
        )
        if mouse_consuming:
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._chrome_control_text = "mouse mode active"
            self._spotify_control_text = "mouse mode active"
            return

        if hand_handedness == "Left":
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._handle_left_hand_voice(prediction, now)
            return

        self._reset_voice_candidate(now)
        app_static_label = self._derive_app_static_label(prediction, hand_reading)
        right_hand_active = hand_handedness == "Right"

        chrome_wheel_consuming = self._update_chrome_wheel(
            prediction,
            hand_reading,
            now,
            active=right_hand_active,
        )
        if chrome_wheel_consuming:
            return

        spotify_wheel_consuming = self._update_spotify_wheel(
            prediction,
            hand_reading,
            now,
            active=right_hand_active,
        )
        if spotify_wheel_consuming:
            return

        chrome_snapshot = self.chrome_router.update(
            stable_label=app_static_label,
            dynamic_label=prediction.dynamic_label,
            controller=self.chrome_controller,
            now=now,
        )
        self._chrome_mode_enabled = chrome_snapshot.mode_enabled
        self._chrome_control_text = chrome_snapshot.control_text
        if chrome_snapshot.last_action != "-" and chrome_snapshot.last_action != self._last_chrome_action:
            self._last_chrome_action = chrome_snapshot.last_action
            self.command_detected.emit(chrome_snapshot.control_text)

        if chrome_snapshot.consume_other_routes or app_static_label in {"three", "three_together"}:
            return

        snapshot = self.spotify_router.update(
            stable_label=prediction.stable_label,
            dynamic_label=prediction.dynamic_label,
            controller=self.spotify_controller,
            now=now,
        )
        self._spotify_control_text = snapshot.control_text
        self._spotify_info_text = snapshot.info_text
        if snapshot.last_action != "-" and snapshot.last_action != self._last_spotify_action:
            self._last_spotify_action = snapshot.last_action
            self.command_detected.emit(snapshot.control_text)

    def _handle_tutorial_controls(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> None:
        step_key = self._tutorial_step_key or ""

        utility_wheel_consuming = self._update_utility_wheel(hand_reading, hand_handedness, now)
        if utility_wheel_consuming:
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if self._drawing_mode_enabled:
                self._drawing_tool = "hidden"
                self._drawing_cursor_norm = None
                self._camera_draw_last_point = None
            return

        if self._dictation_active:
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            if step_key == "voice_command" and hand_handedness == "Left":
                self._handle_left_hand_voice(prediction, now)
            else:
                self._reset_voice_candidate(now)
            return

        if step_key == "mouse_mode":
            mouse_consuming = self._handle_mouse_control(
                prediction=prediction,
                hand_reading=hand_reading if hand_handedness == "Right" else None,
                hand_handedness=hand_handedness,
                now=now,
            )
            self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self._reset_voice_candidate(now)
            self.chrome_router.reset()
            self.spotify_router.reset()
            if mouse_consuming:
                self._chrome_control_text = "mouse mode active"
                self._spotify_control_text = "mouse mode active"
            return

        self._reset_voice_candidate(now)
        self._update_chrome_wheel(prediction=None, hand_reading=None, now=now, active=False)

        if self._mouse_mode_enabled:
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._chrome_control_text = "mouse mode active"
            self._spotify_control_text = "mouse mode active"
            return

        if step_key == "voice_command":
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            if hand_handedness == "Left":
                self._handle_left_hand_voice(prediction, now)
            return

        if hand_handedness != "Right":
            self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
            self.chrome_router.reset()
            self.spotify_router.reset()
            return

        if step_key == "gesture_wheel":
            self.chrome_router.reset()
            self.spotify_router.reset()
            self._update_spotify_wheel(
                prediction,
                hand_reading,
                now,
                active=hand_reading is not None,
            )
            return

        self._update_spotify_wheel(prediction=None, hand_reading=None, now=now, active=False)
        self.chrome_router.reset()

        if step_key == "swipes":
            self.spotify_router.reset()
            self._chrome_control_text = "tutorial swipe practice"
            self._spotify_control_text = "tutorial swipe practice"
            return

        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        if step_key == "spotify_open":
            routed_label = "two" if stable_label == "two" else "neutral"
        elif step_key == "play_pause":
            routed_label = "fist" if stable_label == "fist" else "neutral"
        else:
            self.spotify_router.reset()
            return

        snapshot = self.spotify_router.update(
            stable_label=routed_label,
            dynamic_label="neutral",
            controller=self.spotify_controller,
            now=now,
        )
        self._spotify_control_text = snapshot.control_text
        self._spotify_info_text = snapshot.info_text
        if snapshot.last_action != "-":
            if snapshot.last_action != self._last_spotify_action:
                self._last_spotify_action = snapshot.last_action
            self.command_detected.emit(snapshot.control_text)

    def _handle_mouse_control(self, prediction, hand_reading, hand_handedness: str | None, now: float) -> bool:
        if not self.mouse_controller.available:
            self.mouse_tracker.reset()
            self._last_mouse_update = self._blank_mouse_update()
            self._mouse_mode_enabled = False
            self._mouse_status_text = "unavailable"
            self._mouse_control_text = self.mouse_controller.message
            return False

        self.mouse_tracker.set_desktop_bounds(self.mouse_controller.virtual_bounds())
        update = self.mouse_tracker.update(
            hand_reading=hand_reading,
            prediction=prediction,
            hand_handedness=hand_handedness,
            cursor_seed=self.mouse_controller.current_position_normalized(),
            now=now,
        )
        self._last_mouse_update = update

        tutorial_demo_only = self._tutorial_mode_enabled and self._tutorial_step_key == "mouse_mode"
        if not tutorial_demo_only:
            if update.cursor_position is not None:
                self.mouse_controller.move_normalized(*update.cursor_position)
            if update.left_press:
                self.mouse_controller.left_down()
            if update.left_release:
                self.mouse_controller.left_up()
            if update.left_click:
                self.mouse_controller.left_click()
            if update.right_click:
                self.mouse_controller.right_click()
            if update.scroll_steps:
                self.mouse_controller.scroll(update.scroll_steps)

        self._mouse_mode_enabled = update.mode_enabled
        self._mouse_status_text = update.status
        action_text = update.control_text
        if not tutorial_demo_only and (update.left_press or update.left_release or update.left_click or update.right_click or update.scroll_steps):
            action_text = self.mouse_controller.message
            self.command_detected.emit(action_text)
        self._mouse_control_text = action_text
        return update.consume_other_routes

    def _update_volume_overlay(self) -> None:
        msg = self._volume_message if self._volume_mode_active else self._volume_status_text
        if self._volume_dual_active:
            self.volume_overlay.set_dual_level(
                self._volume_app_level,
                self._volume_level,
                self._volume_app_label,
                muted=self._volume_muted,
                active=self._volume_mode_active,
                selected_bar=self._volume_bar_selected,
                message=msg,
            )
        else:
            self.volume_overlay.set_level(
                self._volume_level,
                muted=self._volume_muted,
                active=self._volume_mode_active,
                message=msg,
            )
        if self._volume_overlay_visible:
            if not self.volume_overlay.isVisible():
                self.volume_overlay.show_overlay()
        elif self.volume_overlay.isVisible():
            self.volume_overlay.hide_overlay()

    def _read_system_mute(self) -> bool:
        muted = self.volume_controller.get_mute()
        return bool(muted) if muted is not None else False

    def _queue_spotify_volume(self, volume_percent: int) -> None:
        volume_percent = max(0, min(100, int(volume_percent)))
        with self._spotify_vol_lock:
            self._spotify_vol_target = volume_percent
            worker = self._spotify_vol_worker
            if worker is not None and worker.is_alive():
                return
            self._spotify_vol_worker = threading.Thread(
                target=self._spotify_vol_worker_loop,
                daemon=True,
            )
            self._spotify_vol_worker.start()

    def _spotify_vol_worker_loop(self) -> None:
        min_interval = 0.12
        while True:
            with self._spotify_vol_lock:
                target = self._spotify_vol_target
                last = self._spotify_vol_last_sent
                if target is None or target == last:
                    self._spotify_vol_worker = None
                    return
                self._spotify_vol_last_sent = target
            try:
                self.spotify_controller.set_volume(target)
            except Exception:
                pass
            time.sleep(min_interval)

    def _update_runtime_status(self) -> None:
        if self._dictation_active:
            self._emit_status("dictation active")
        elif self._voice_listening:
            self._emit_status("voice listening...")
        elif self._drawing_mode_enabled:
            self._emit_status("HGR active | drawing mode on")
        elif self._chrome_mode_enabled:
            self._emit_status("HGR active | chrome mode on")
        else:
            self._emit_status("HGR active")

    def _emit_status(self, text: str) -> None:
        normalized = str(text or "").strip()
        if not normalized or normalized == self._last_status_text:
            return
        self._last_status_text = normalized
        self.status_changed.emit(normalized)

    def _build_debug_payload(self, result, now: float) -> dict:
        prediction = result.prediction
        dynamic_display = prediction.dynamic_label
        if prediction.dynamic_label != "neutral":
            self._dynamic_hold_label = prediction.dynamic_label
            self._dynamic_hold_until = now + 0.85
        elif now < self._dynamic_hold_until:
            dynamic_display = self._dynamic_hold_label
        else:
            self._dynamic_hold_label = "neutral"
            self._dynamic_hold_until = 0.0

        payload_raw_label = prediction.raw_label
        payload_stable_label = prediction.stable_label
        payload_dynamic_label = dynamic_display
        banner_text = prediction.stable_label if prediction.stable_label != "neutral" else prediction.raw_label
        if self._drawing_mode_enabled:
            payload_raw_label = "neutral"
            payload_stable_label = "neutral"
            payload_dynamic_label = "neutral"
            if self._drawing_tool == "draw":
                gesture_chip = "Drawing: draw"
            elif self._drawing_tool == "erase":
                gesture_chip = "Drawing: erase"
            elif self._drawing_tool == "hover":
                gesture_chip = "Drawing: hover"
            else:
                gesture_chip = "Drawing mode on"
        elif dynamic_display != "neutral":
            gesture_chip = f"Dynamic: {dynamic_display.replace('_', ' ')}"
        else:
            gesture_chip = f"Gesture: {banner_text}"

        handedness = ""
        if result.found and result.tracked_hand is not None:
            handedness = str(result.tracked_hand.handedness or "").lower()

        wheel_items = ()
        wheel_selected_key = None
        wheel_selection_progress = 0.0
        wheel_cursor_offset = None
        wheel_kind = "none"
        wheel_visible = False
        if self._chrome_wheel_visible:
            wheel_kind = "chrome"
            wheel_visible = True
            wheel_items = self._chrome_wheel_items()
            wheel_selected_key = self._chrome_wheel_selected_key
            wheel_cursor_offset = self._chrome_wheel_cursor_offset
            if self._chrome_wheel_selected_key is not None:
                wheel_selection_progress = min(1.0, max(0.0, (now - self._chrome_wheel_selected_since) / 1.0))
        elif self._spotify_wheel_visible:
            wheel_kind = "spotify"
            wheel_visible = True
            wheel_items = self._spotify_wheel_items()
            wheel_selected_key = self._spotify_wheel_selected_key
            wheel_cursor_offset = self._spotify_wheel_cursor_offset
            if self._spotify_wheel_selected_key is not None:
                wheel_selection_progress = min(1.0, max(0.0, (now - self._spotify_wheel_selected_since) / 1.0))

        info_lines = [
            f"Camera: {self._camera_info.display_name if self._camera_info is not None else 'waiting'}",
            "Handedness: -",
            f"Gesture raw/stable: {prediction.raw_label} / {prediction.stable_label}",
            f"Confidence: {prediction.confidence:.2f}",
            f"FPS: {self._fps:.1f}",
            "Box: -",
            "Palm: -",
            f"Dynamic: {dynamic_display.replace('_', ' ') if dynamic_display != 'neutral' else 'neutral'}",
            "Candidates: -",
            "Thumb: -",
            "Index: -",
            "Middle: -",
            "Ring: -",
            "Pinky: -",
            "Spreads: -",
            "Reasoning: no hand in frame",
            f"Volume control: {self._volume_message}",
            self._format_volume_level_text(),
            f"Spotify control: {self._spotify_control_text}",
            f"Spotify info: {self._spotify_info_text}",
            f"Chrome mode: {'on' if self._chrome_mode_enabled else 'off'}",
            f"Chrome control: {self._chrome_control_text}",
            f"Voice mode: {self._voice_mode_text()}",
            f"Voice control: {self._voice_control_text}",
            f"Voice heard: {self._voice_preview_text(self._voice_display_text)}",
            f"Mouse mode: {'on' if self._mouse_mode_enabled else 'off'} ({self._mouse_status_text})",
            f"Mouse control: {self._mouse_control_text}",
        ]
        if self._window_pair_overlay is not None:
            info_lines.append(f"Window pair distance: {float(self._window_pair_overlay.get('distance', 0.0) or 0.0):.2f}")
        info_lines.append(f"Gestures: {'on' if self._gestures_enabled else 'off'}")
        info_lines.append(f"Drawing: {'on' if self._drawing_mode_enabled else 'off'} | {self._drawing_control_text} | target={self._drawing_render_target}")

        if result.found and result.tracked_hand is not None and result.hand_reading is not None:
            hand = result.tracked_hand
            reading = result.hand_reading
            info_lines[1] = f"Handedness: {hand.handedness} ({hand.handedness_confidence:.2f})"
            info_lines[5] = (
                f"Box: x={hand.bbox.x:.2f} y={hand.bbox.y:.2f} "
                f"w={hand.bbox.width:.2f} h={hand.bbox.height:.2f}"
            )
            info_lines[6] = (
                f"Palm: roll={reading.palm.roll_deg:.1f} "
                f"pitch={reading.palm.pitch_deg:.1f} yaw={reading.palm.yaw_deg:.1f}"
            )
            info_lines[7] = f"Dynamic: {dynamic_display.replace('_', ' ') if dynamic_display != 'neutral' else 'neutral'}"
            candidate_text = ", ".join(
                f"{candidate.label}={candidate.score:.2f}" for candidate in prediction.candidates[:4]
            ) or "-"
            info_lines[8] = f"Candidates: {candidate_text}"
            for row, name in zip(range(9, 14), ("thumb", "index", "middle", "ring", "pinky")):
                finger = reading.fingers[name]
                info_lines[row] = (
                    f"{name.title()}: {finger.state} | "
                    f"open={finger.openness:.2f} curl={finger.curl:.2f} "
                    f"conf={finger.confidence:.2f} occ={finger.occluded}"
                )
            spread_text = ", ".join(
                f"{name}={spread.state}:{spread.distance:.2f}" for name, spread in reading.spreads.items()
            ) or "-"
            info_lines[14] = f"Spreads: {spread_text}"
            info_lines[15] = (
                f"Reasoning: extended={reading.finger_count_extended} "
                f"occlusion={reading.occlusion_score:.2f} shape={reading.shape_confidence:.2f}"
            )
        return {
            "result": result,
            "gesture_chip": gesture_chip,
            "info_lines": info_lines,
            "found": bool(result.found),
            "handedness": handedness,
            "raw_label": payload_raw_label,
            "stable_label": payload_stable_label,
            "dynamic_label": payload_dynamic_label,
            "confidence": float(prediction.confidence),
            "spotify_window_open": bool(self.spotify_controller.is_window_active() or self.spotify_controller.is_running()),
            "spotify_control_text": self._spotify_control_text,
            "spotify_last_action": self._last_spotify_action,
            "chrome_mode_enabled": bool(self._chrome_mode_enabled),
            "chrome_window_active": bool(self.chrome_controller.is_window_active()),
            "chrome_window_open": bool(self.chrome_controller.is_window_open()),
            "chrome_control_text": self._chrome_control_text,
            "chrome_last_action": self._last_chrome_action,
            "voice_mode_text": self._voice_mode_text(),
            "voice_listening": bool(self._voice_listening),
            "voice_control_text": self._voice_control_text,
            "voice_heard_text": self._voice_preview_text(self._voice_display_text),
            "mouse_mode_enabled": bool(self._mouse_mode_enabled),
            "mouse_cursor_position": self.mouse_tracker.debug_state.cursor_position,
            "mouse_left_click": bool(getattr(self._last_mouse_update, "left_click", False)),
            "gestures_enabled": bool(self._gestures_enabled),
            "drawing_mode_enabled": bool(self._drawing_mode_enabled),
            "drawing_tool": self._drawing_tool,
            "drawing_cursor_norm": self._drawing_cursor_norm,
            "drawing_control_text": self._drawing_control_text,
            "drawing_render_target": self._drawing_render_target,
            "drawing_request_token": int(self._drawing_request_token),
            "drawing_request_action": self._drawing_request_action,
            "drawing_shape_mode": bool(self._drawing_shape_mode),
            "utility_request_token": int(self._utility_request_token),
            "utility_request_action": self._utility_request_action,
            "utility_capture_selection_active": bool(self._utility_capture_selection_active),
            "utility_capture_cursor_norm": self._utility_capture_cursor_norm,
            "utility_capture_left_down": bool(self._utility_capture_left_down),
            "utility_capture_right_down": bool(self._utility_capture_right_down),
            "utility_recording_active": bool(self._utility_recording_active),
            "wheel_kind": wheel_kind,
            "wheel_visible": wheel_visible,
            "wheel_items": wheel_items,
            "wheel_selected_key": wheel_selected_key,
            "wheel_selection_progress": wheel_selection_progress,
            "wheel_cursor_offset": wheel_cursor_offset,
            "volume_level_scalar": self._volume_level,
            "volume_muted": self._volume_muted,
            "volume_active": self._volume_mode_active,
        }

    def _format_volume_level_text(self) -> str:
        mute_suffix = " [muted]" if self._volume_muted else ""
        if self._volume_level is None:
            return f"Volume level: -   ({self._volume_status_text}{mute_suffix})"
        return f"Volume level: {int(round(self._volume_level * 100))}%   ({self._volume_status_text}{mute_suffix})"

    def _volume_features_from_hand_reading(self, hand_reading):
        fine_states = {name: finger.state for name, finger in hand_reading.fingers.items()}
        states = {
            name: ("open" if finger.state in {"fully_open", "partially_curled"} else "closed")
            for name, finger in hand_reading.fingers.items()
        }
        open_scores = {name: float(finger.openness) for name, finger in hand_reading.fingers.items()}
        spread_states = {name: spread.state for name, spread in hand_reading.spreads.items()}
        spread_ratios = {name: float(spread.distance) for name, spread in hand_reading.spreads.items()}
        spread_together_strengths = {name: float(spread.together_strength) for name, spread in hand_reading.spreads.items()}
        spread_apart_strengths = {name: float(spread.apart_strength) for name, spread in hand_reading.spreads.items()}
        return SimpleNamespace(
            palm_scale=float(hand_reading.palm.scale),
            open_scores=open_scores,
            states=states,
            fine_states=fine_states,
            finger_count_open=sum(1 for finger in hand_reading.fingers.values() if finger.extended),
            spread_states=spread_states,
            spread_ratios=spread_ratios,
            spread_together_strengths=spread_together_strengths,
            spread_apart_strengths=spread_apart_strengths,
        )

    def _derive_app_static_label(self, prediction, hand_reading):
        stable_label = prediction.stable_label
        if hand_reading is None:
            return stable_label
        if stable_label in {"three", "neutral"} and self._is_three_together(hand_reading):
            return "three_together"
        if stable_label in {"four", "neutral"} and self._is_four_together(hand_reading):
            return "four_together"
        return stable_label

    def _is_three_together(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        return (
            self._is_chrome_open_finger(fingers["index"])
            and self._is_chrome_open_finger(fingers["middle"])
            and self._is_chrome_open_finger(fingers["ring"])
            and self._is_folded_finger(fingers["thumb"], allow_partial=True)
            and self._is_folded_finger(fingers["pinky"])
            and self._spread_is_together(hand_reading, "index_middle", max_distance=0.38, min_strength=0.56)
            and self._spread_is_together(hand_reading, "middle_ring", max_distance=0.38, min_strength=0.54)
        )

    def _is_four_together(self, hand_reading) -> bool:
        fingers = hand_reading.fingers
        return (
            all(self._is_chrome_open_finger(fingers[name]) for name in ("index", "middle", "ring", "pinky"))
            and self._is_folded_finger(fingers["thumb"], allow_partial=True)
            and self._spread_is_together(hand_reading, "index_middle", max_distance=0.40, min_strength=0.52)
            and self._spread_is_together(hand_reading, "middle_ring", max_distance=0.40, min_strength=0.52)
            and self._spread_is_together(hand_reading, "ring_pinky", max_distance=0.40, min_strength=0.52)
        )

    def _is_chrome_open_finger(self, finger) -> bool:
        return finger.state == "fully_open" or (
            finger.state == "partially_curled"
            and finger.openness >= 0.70
            and finger.curl <= 0.46
        )

    def _is_folded_finger(self, finger, *, allow_partial: bool = False) -> bool:
        allowed = {"mostly_curled", "closed"}
        if allow_partial:
            allowed.add("partially_curled")
        return finger.state in allowed

    def _spread_is_together(self, hand_reading, spread_name: str, *, max_distance: float, min_strength: float) -> bool:
        spread = hand_reading.spreads[spread_name]
        return (
            spread.state == "together"
            or spread.distance <= max_distance
            or (spread.together_strength >= min_strength and spread.apart_strength <= 0.42)
        )

    def _reset_spotify_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._spotify_wheel_candidate = "neutral"
        self._spotify_wheel_candidate_since = 0.0
        self._spotify_wheel_visible = False
        self._spotify_wheel_anchor = None
        self._spotify_wheel_selected_key = None
        self._spotify_wheel_selected_since = 0.0
        self._spotify_wheel_pose_grace_until = 0.0
        self._spotify_wheel_cursor_offset = None
        if clear_cooldown:
            self._spotify_wheel_cooldown_until = 0.0
        if self.spotify_wheel_overlay.isVisible():
            self.spotify_wheel_overlay.hide_overlay()

    def _reset_chrome_wheel(self, *, clear_cooldown: bool = False) -> None:
        self._chrome_wheel_candidate = "neutral"
        self._chrome_wheel_candidate_since = 0.0
        self._chrome_wheel_visible = False
        self._chrome_wheel_anchor = None
        self._chrome_wheel_selected_key = None
        self._chrome_wheel_selected_since = 0.0
        self._chrome_wheel_pose_grace_until = 0.0
        self._chrome_wheel_cursor_offset = None
        if clear_cooldown:
            self._chrome_wheel_cooldown_until = 0.0
        if self.chrome_wheel_overlay.isVisible():
            self.chrome_wheel_overlay.hide_overlay()

    def _spotify_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        labels = (
            ("add_playlist", "Add Playlist"),
            ("remove_playlist", "Remove Playlist"),
            ("create_playlist", "Create Playlist"),
            ("add_queue", "Add Queue"),
            ("remove_queue", "Remove Queue"),
            ("like", "Add to Liked"),
            ("remove_liked", "Remove from Liked"),
            ("shuffle", "Shuffle"),
        )
        slice_span = 360.0 / len(labels)
        return tuple(
            (key, label, (90.0 - index * slice_span) % 360.0)
            for index, (key, label) in enumerate(labels)
        )

    def _chrome_wheel_items(self) -> tuple[tuple[str, str, float], ...]:
        return (
            ("bookmark", "Bookmark This Tab", 90.0),
            ("history", "History", 30.0),
            ("downloads", "Downloads", 330.0),
            ("bookmarks", "Bookmarks Manager", 270.0),
            ("print", "Print Page", 210.0),
            ("reopen", "Reopen Tab", 150.0),
        )

    def _chrome_active_for_wheel(self, now: float) -> bool:
        if now < self._chrome_active_cache_until:
            return self._chrome_active_cache
        active = bool(self.chrome_controller.is_window_active())
        self._chrome_active_cache = active
        self._chrome_active_cache_until = now + 0.55
        return active

    def _spotify_active_for_wheel(self, now: float) -> bool:
        if now < self._spotify_active_cache_until:
            return self._spotify_active_cache
        if hasattr(self.spotify_controller, "is_active_for_wheel"):
            active = bool(self.spotify_controller.is_active_for_wheel())
        else:
            active = bool(self.spotify_controller.is_active_device_available())
        self._spotify_active_cache = active
        self._spotify_active_cache_until = now + 0.9
        return active

    def _chrome_wheel_pose_active(self, prediction, now: float) -> bool:
        if prediction is None:
            return False
        stable_label = str(getattr(prediction, "stable_label", "neutral") or "neutral")
        raw_label = str(getattr(prediction, "raw_label", "neutral") or "neutral")
        confidence = float(getattr(prediction, "confidence", 0.0) or 0.0)
        if stable_label == "chrome_wheel_pose":
            return True
        if raw_label == "chrome_wheel_pose" and confidence >= 0.50:
            return True
        if self._chrome_mode_enabled and self._chrome_active_for_wheel(now):
            if stable_label == "wheel_pose":
                return True
            if raw_label == "wheel_pose" and confidence >= 0.48:
                return True
        return False

    def _update_chrome_wheel(self, prediction, hand_reading, now: float, *, active: bool) -> bool:
        if not active or prediction is None or hand_reading is None:
            if self._chrome_wheel_visible and now >= self._chrome_wheel_pose_grace_until:
                self._chrome_control_text = "chrome wheel closed"
                self._reset_chrome_wheel()
            else:
                self._chrome_wheel_candidate = "neutral"
                self._chrome_wheel_candidate_since = now
            return self._chrome_wheel_visible

        wheel_label = self._chrome_wheel_pose_active(prediction, now)
        if self._chrome_wheel_visible:
            if wheel_label:
                self._chrome_wheel_pose_grace_until = now + 0.25
                self._update_chrome_wheel_selection(hand_reading, now)
            elif now >= self._chrome_wheel_pose_grace_until:
                self._chrome_control_text = "chrome wheel closed"
                self._reset_chrome_wheel()
            return True

        if now < self._chrome_wheel_cooldown_until:
            if not wheel_label:
                self._chrome_wheel_candidate = "neutral"
            return False

        if not wheel_label:
            self._chrome_wheel_candidate = "neutral"
            self._chrome_wheel_candidate_since = now
            return False

        if not self._chrome_active_for_wheel(now):
            self._chrome_control_text = "chrome must be active for wheel"
            self._chrome_wheel_candidate = "neutral"
            self._chrome_wheel_candidate_since = now
            return True

        if self._chrome_wheel_candidate != "chrome_wheel_pose":
            self._chrome_wheel_candidate = "chrome_wheel_pose"
            self._chrome_wheel_candidate_since = now
            self._chrome_control_text = "hold chrome wheel pose"
            return True

        if now - self._chrome_wheel_candidate_since < 1.0:
            return True

        self._chrome_wheel_visible = True
        self._chrome_wheel_anchor = hand_reading.palm.center.copy()
        self._chrome_wheel_cursor_offset = (0.0, 0.0)
        self._chrome_wheel_selected_key = None
        self._chrome_wheel_selected_since = now
        self._chrome_wheel_pose_grace_until = now + 0.25
        self._chrome_control_text = "chrome wheel active"
        return True

    def _update_chrome_wheel_selection(self, hand_reading, now: float) -> None:
        if self._chrome_wheel_anchor is None:
            self._chrome_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._chrome_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._chrome_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._wheel_selection_key(float(offset[0]), float(offset[1]), self._chrome_wheel_items())
        if selection_key is None:
            self._chrome_wheel_selected_key = None
            self._chrome_wheel_selected_since = now
            self._chrome_control_text = "chrome wheel active"
            return
        if selection_key != self._chrome_wheel_selected_key:
            self._chrome_wheel_selected_key = selection_key
            self._chrome_wheel_selected_since = now
            self._chrome_control_text = f"chrome wheel: {self._chrome_wheel_label(selection_key)}"
            return
        if now - self._chrome_wheel_selected_since < 1.0:
            return
        self._execute_chrome_wheel_action(selection_key)
        self._chrome_wheel_cooldown_until = now + 1.5
        self._reset_chrome_wheel()

    def _wheel_selection_key(self, dx: float, dy: float, items: tuple[tuple[str, str, float], ...]) -> str | None:
        radius = math.hypot(dx, dy)
        if radius < 0.59 or radius > 1.25:
            return None
        angle = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
        slice_span = 360.0 / max(1, len(items))
        half_slice = slice_span / 2.0
        angular_margin = 1.5
        for key, _label, target_angle in items:
            delta = abs((angle - target_angle + 180.0) % 360.0 - 180.0)
            if delta <= max(0.0, half_slice - angular_margin):
                return key
        return None

    def _chrome_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._chrome_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _execute_chrome_wheel_action(self, key: str) -> None:
        if key == "bookmark":
            success = self.chrome_controller.bookmark_current_tab()
        elif key == "history":
            success = self.chrome_controller.open_history()
        elif key == "downloads":
            success = self.chrome_controller.open_downloads()
        elif key == "bookmarks":
            success = self.chrome_controller.open_bookmarks_manager()
        elif key == "print":
            success = self.chrome_controller.print_page()
        else:
            success = self.chrome_controller.reopen_closed_tab()
        self._chrome_control_text = self.chrome_controller.message
        self.command_detected.emit(self._chrome_control_text)
        if success:
            self._chrome_mode_enabled = True

    def _update_spotify_wheel(self, prediction, hand_reading, now: float, *, active: bool) -> bool:
        if not active or prediction is None or hand_reading is None:
            if self._spotify_wheel_visible and now >= self._spotify_wheel_pose_grace_until:
                self._spotify_control_text = "spotify wheel closed"
                self._reset_spotify_wheel()
            else:
                self._spotify_wheel_candidate = "neutral"
                self._spotify_wheel_candidate_since = now
            return self._spotify_wheel_visible

        wheel_label = prediction.stable_label == "wheel_pose" or (
            prediction.raw_label == "wheel_pose" and prediction.confidence >= 0.56
        )
        if self._spotify_wheel_visible:
            if wheel_label:
                self._spotify_wheel_pose_grace_until = now + 0.25
                self._update_spotify_wheel_selection(hand_reading, now)
            elif now >= self._spotify_wheel_pose_grace_until:
                self._spotify_control_text = "spotify wheel closed"
                self._reset_spotify_wheel()
            return True

        if now < self._spotify_wheel_cooldown_until:
            if not wheel_label:
                self._spotify_wheel_candidate = "neutral"
            return False

        if self._chrome_mode_enabled and self._chrome_active_for_wheel(now):
            self._spotify_wheel_candidate = "neutral"
            self._spotify_wheel_candidate_since = now
            return False

        if not wheel_label:
            self._spotify_wheel_candidate = "neutral"
            self._spotify_wheel_candidate_since = now
            return False

        if not self._spotify_active_for_wheel(now):
            self._spotify_control_text = self.spotify_controller.message or "spotify inactive on device"
            self._spotify_wheel_candidate = "neutral"
            self._spotify_wheel_candidate_since = now
            return True

        if self._spotify_wheel_candidate != "wheel_pose":
            self._spotify_wheel_candidate = "wheel_pose"
            self._spotify_wheel_candidate_since = now
            self._spotify_control_text = "hold wheel pose for spotify wheel"
            return True

        if now - self._spotify_wheel_candidate_since < 1.0:
            return True

        self._spotify_wheel_visible = True
        self._spotify_wheel_anchor = hand_reading.palm.center.copy()
        self._spotify_wheel_cursor_offset = (0.0, 0.0)
        self._spotify_wheel_selected_key = None
        self._spotify_wheel_selected_since = now
        self._spotify_wheel_pose_grace_until = now + 0.25
        self._spotify_control_text = "spotify wheel active"
        return True

    def _update_spotify_wheel_selection(self, hand_reading, now: float) -> None:
        if self._spotify_wheel_anchor is None:
            self._spotify_wheel_anchor = hand_reading.palm.center.copy()
        offset = (hand_reading.palm.center - self._spotify_wheel_anchor) / max(hand_reading.palm.scale, 1e-6)
        self._spotify_wheel_cursor_offset = (float(offset[0]), float(offset[1]))
        selection_key = self._spotify_wheel_selection_key(float(offset[0]), float(offset[1]))
        if selection_key is None:
            self._spotify_wheel_selected_key = None
            self._spotify_wheel_selected_since = now
            self._spotify_control_text = "spotify wheel active"
            return
        if selection_key != self._spotify_wheel_selected_key:
            self._spotify_wheel_selected_key = selection_key
            self._spotify_wheel_selected_since = now
            self._spotify_control_text = f"spotify wheel: {self._spotify_wheel_label(selection_key)}"
            return
        if now - self._spotify_wheel_selected_since < 1.0:
            return
        self._execute_spotify_wheel_action(selection_key)
        self._spotify_wheel_cooldown_until = now + 1.5
        self._reset_spotify_wheel()

    def _spotify_wheel_selection_key(self, dx: float, dy: float) -> str | None:
        return self._wheel_selection_key(dx, dy, self._spotify_wheel_items())

    def _spotify_wheel_label(self, key: str) -> str:
        for item_key, label, _angle in self._spotify_wheel_items():
            if item_key == key:
                return label
        return key.replace("_", " ")

    def _execute_spotify_wheel_action(self, key: str) -> None:
        if key == "add_playlist":
            self._spotify_control_text = "say what playlist you would like to add to"
            self.command_detected.emit(self._spotify_control_text)
            self._start_playlist_prompt("add_playlist")
            return
        if key == "create_playlist":
            self._spotify_control_text = "what would you like to call the playlist?"
            self.command_detected.emit(self._spotify_control_text)
            self._start_playlist_prompt("create_playlist")
            return
        if key == "remove_playlist":
            success = self.spotify_controller.remove_current_track_from_current_playlist()
            self._spotify_control_text = self.spotify_controller.message
            self.command_detected.emit(self._spotify_control_text)
            if success:
                details = self.spotify_controller.get_current_track_details()
                if details is not None:
                    self._spotify_info_text = details.summary()
            return
        if key == "add_queue":
            success = self.spotify_controller.add_current_track_to_queue()
        elif key == "remove_queue":
            success = self.spotify_controller.remove_current_track_from_queue()
        elif key == "remove_liked":
            success = self.spotify_controller.remove_current_track_from_liked()
        elif key == "shuffle":
            success = self.spotify_controller.toggle_shuffle()
        else:
            success = self.spotify_controller.save_current_track()
        self._spotify_control_text = self.spotify_controller.message
        self.command_detected.emit(self._spotify_control_text)
        if success:
            details = self.spotify_controller.get_current_track_details()
            if details is not None:
                self._spotify_info_text = details.summary()

    def _start_playlist_prompt(self, action: str) -> None:
        if self._voice_listening:
            return
        self._voice_cooldown_until = time.monotonic() + 0.7
        self._start_voice_capture(mode=action, preferred_app=None)

    def _clean_playlist_reply(self, spoken_text: str) -> str:
        cleaned = str(spoken_text or "").lower()
        cleaned = cleaned.replace("feel good", "feel-good")
        cleaned = re.sub(
            r"\b(and|then|uh|um|add|remove|it|this|current|song|track|playlist|to|from|my|the|please|thanks|thank you|would like|like|called|named|titled)\b",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
        return cleaned

    def _clean_create_playlist_reply(self, spoken_text: str) -> str:
        cleaned = str(spoken_text or "").strip()
        cleaned = re.sub(
            r"^\s*(please\s+)?(can you\s+)?(create|make|new|build|set up)\s+(a\s+)?(new\s+)?(playlist\s+)?(called|named|titled)?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(uh|um|please|thanks|thank you)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
        words = [word.capitalize() if word.islower() else word for word in cleaned.split(" ") if word]
        return " ".join(words)

    def _handle_left_hand_voice(self, prediction, now: float) -> None:
        stable_label = prediction.stable_label
        trigger_labels = {"one", "two"}

        if stable_label == "fist":
            if self._voice_latched_label == "fist":
                return
            if now - float(getattr(self, "_voice_one_two_triggered_at", 0.0) or 0.0) < 1.0:
                return
            if self._voice_listening or self._dictation_active or self._save_prompt_active or self._selection_prompt_active:
                self._cancel_all_voice_stages()
                self._voice_latched_label = "fist"
                self._voice_cooldown_until = now + 0.8
            return
        if self._voice_latched_label == "fist":
            self._voice_latched_label = None

        if stable_label == self._voice_latched_label:
            if stable_label not in trigger_labels:
                self._voice_latched_label = None
            return

        if stable_label not in trigger_labels:
            if self._dictation_active and self._dictation_toggle_release_required:
                if self._dictation_release_candidate_since <= 0.0:
                    self._dictation_release_candidate_since = now
                elif now - self._dictation_release_candidate_since >= 0.35:
                    self._dictation_toggle_release_required = False
            else:
                self._dictation_release_candidate_since = 0.0
            self._reset_voice_candidate(now)
            return

        self._dictation_release_candidate_since = 0.0

        if stable_label != self._voice_candidate:
            self._voice_candidate = stable_label
            self._voice_candidate_since = now
            return

        if stable_label == "one" and (self._voice_listening or self._dictation_active):
            return
        if stable_label == "two" and self._dictation_active and self._dictation_toggle_release_required:
            return
        if stable_label == "two" and self._dictation_active and now < self._dictation_stop_rearm_at:
            return
        if now < self._voice_cooldown_until:
            return
        if now - self._voice_candidate_since < 0.5:
            return

        self._voice_latched_label = stable_label
        self._voice_cooldown_until = now + 1.25
        self._voice_one_two_triggered_at = now
        if stable_label == "two":
            if self._dictation_active:
                self._stop_dictation_capture()
            else:
                self._start_dictation_capture()
            return
        if self._selection_prompt_active:
            self._start_voice_capture(mode="selection", preferred_app=None)
            return
        self._start_voice_command()

    def _reset_voice_candidate(self, now: float) -> None:
        self._voice_candidate = "neutral"
        self._voice_candidate_since = now
        if self._voice_latched_label is not None:
            self._voice_latched_label = None

    def _voice_mode_text(self) -> str:
        if self._dictation_active:
            return "dictation"
        if self._voice_listening:
            if self._voice_mode == "general":
                return "command"
            return self._voice_mode.replace("_", " ")
        return "ready"

    def _voice_preview_text(self, text: str, *, max_chars: int = 220) -> str:
        value = str(text or "").strip()
        if not value:
            return "-"
        if len(value) <= max_chars:
            return value
        return "..." + value[-(max_chars - 3):]

    def _start_voice_command(self) -> None:
        chrome_mode_voice = self._chrome_mode_enabled and self.chrome_controller.is_window_open()
        self._start_voice_capture(mode="general", preferred_app="chrome" if chrome_mode_voice else None)

    def _start_dictation_capture(self) -> None:
        if self._voice_listening:
            return
        if not self.text_input_controller.available:
            self._voice_control_text = self.text_input_controller.message
            self.command_detected.emit(self._voice_control_text)
            return
        if not self.text_input_controller.start_windows_dictation():
            # Silent failure — no error overlay per user request. Just leave the
            # state unchanged so the user can retry the gesture.
            self._voice_control_text = self.text_input_controller.message
            return
        self._dictation_active = True
        self._dictation_toggle_release_required = True
        self._dictation_release_candidate_since = 0.0
        self._dictation_stop_rearm_at = time.monotonic() + 2.5
        self._dictation_backend = "windows"
        self._voice_mode = "dictation"
        self._voice_control_text = "dictation active"
        # Show the compact microphone indicator only (no hint text, no banner).
        self.voice_status_overlay.show_listening("")
        self._emit_status("dictation active")

    def _stop_dictation_capture(self) -> None:
        if not self._dictation_active:
            return
        self._dictation_active = False
        self._dictation_toggle_release_required = False
        self._dictation_release_candidate_since = 0.0
        self._dictation_stop_rearm_at = 0.0
        self.text_input_controller.stop_windows_dictation()
        self._voice_stop_event = None
        self._voice_request_id += 1
        self._voice_listening = False
        self._dictation_backend = "idle"
        self._voice_mode = "ready"
        self._voice_control_text = "dictation stopped"
        # Hide the mic indicator silently — no result popup.
        try:
            self.voice_status_overlay.hide_overlay()
        except Exception:
            pass

    def _start_voice_capture(self, *, mode: str, preferred_app: str | None = None) -> None:
        if self._voice_listening:
            return
        self._voice_listening = True
        self._voice_mode = mode
        self._voice_request_id += 1
        request_id = self._voice_request_id
        self._voice_stop_event = threading.Event() if mode == "dictation" else None
        self._dictation_active = mode == "dictation"
        self._dictation_backend = "whisper_loop" if mode == "dictation" else "idle"
        if mode == "dictation":
            self._voice_control_text = "dictation listening..."
        elif mode == "save_prompt":
            self._voice_control_text = self._save_prompt_text
        else:
            self._voice_control_text = "voice listening..."
        self._voice_heard_text = "-"
        self._voice_display_text = "-"

        if mode == "dictation":
            stop_event = self._voice_stop_event
            self.voice_status_overlay.show_listening()
            self.command_detected.emit(self._voice_control_text)
            self._emit_status("dictation active")

            def _dictation_worker() -> None:
                final_message = "dictation stopped"
                final_display = self.dictation_processor.full_text
                while True:
                    if stop_event is None or stop_event.is_set():
                        break
                    result = self.voice_listener.listen(
                        max_seconds=12.0,
                        stop_event=stop_event,
                        transcript_mode="dictation",
                    )
                    if stop_event is None or stop_event.is_set():
                        break
                    if result.success and result.heard_text:
                        update = self.dictation_processor.ingest(result.heard_text)
                        final_display = update.display_text or final_display
                        inserted = False
                        control_text = "dictation heard no insertable text"
                        if update.text_to_insert:
                            inserted = self.text_input_controller.insert_text(update.text_to_insert)
                            control_text = self.text_input_controller.message
                        self._voice_queue.put(
                            (
                                request_id,
                                {
                                    "event": "dictation_chunk",
                                    "success": inserted,
                                    "heard_text": update.raw_text,
                                    "control_text": control_text,
                                    "display_text": update.display_text,
                                    "partial": False,
                                },
                            )
                        )
                        continue
                    lowered = str(result.message or "").lower()
                    if any(token in lowered for token in ("failed", "error", "unavailable", "not found")):
                        final_message = result.message or final_message
                        break
                    # Silence or no usable text: keep dictation active and continue listening.
                if stop_event is None or not stop_event.is_set():
                    self._voice_queue.put(
                        (
                            request_id,
                            {
                                "event": "dictation_complete",
                                "success": bool(self.dictation_processor.full_text),
                                "heard_text": "",
                                "control_text": final_message,
                                "display_text": final_display,
                            },
                        )
                    )

            self._voice_thread = threading.Thread(target=_dictation_worker, name="hgr-app-dictation", daemon=True)
            self._voice_thread.start()
            return

        if mode == "save_prompt":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text=self._save_prompt_text,
            )
            self.command_detected.emit(self._save_prompt_text)
            self._emit_status("save prompt active")
        elif mode == "selection" and self._selection_prompt_active:
            self.voice_status_overlay.update_selection_status("Listening...")
        elif mode == "create_playlist":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text="What would you like to call the playlist?",
            )
        elif mode == "add_playlist":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text="Which playlist should I add to?",
            )
        elif mode == "remove_playlist":
            self.voice_status_overlay.show_listening(
                "Listening...",
                hint_text="Which playlist should I remove from?",
            )
        else:
            self.voice_status_overlay.show_listening()
        if mode != "save_prompt":
            self._emit_status("voice listening...")

        chrome_mode_voice = self._chrome_mode_enabled and self.chrome_controller.is_window_open()
        preferred_target = "chrome" if chrome_mode_voice and mode == "general" else preferred_app

        def _push_status(status: str, *, command_text: str = "") -> None:
            self._voice_queue.put((request_id, {"event": "status", "status": status, "command_text": command_text}))

        def _worker() -> None:
            if mode == "save_prompt":
                transcript_mode = "save_prompt"
                listen_seconds = 6.2
            else:
                transcript_mode = "playlist" if mode in {"add_playlist", "remove_playlist", "create_playlist"} else "command"
                listen_seconds = 4.2 if transcript_mode == "playlist" else 5.0
            result = self.voice_listener.listen(
                max_seconds=listen_seconds,
                status_callback=_push_status,
                transcript_mode=transcript_mode,
            )
            if mode in {"general", "selection"} and result.success:
                _push_status("processing", command_text=result.heard_text)
                context = VoiceCommandContext(preferred_app=preferred_target) if mode == "general" else None
                execution = self.voice_processor.execute(result.heard_text, context=context)
                payload = {
                    "event": "result",
                    "mode": mode,
                    "success": execution.success,
                    "target": execution.target,
                    "heard_text": execution.heard_text,
                    "control_text": execution.control_text,
                    "info_text": execution.info_text,
                    "display_text": getattr(execution, "display_text", execution.heard_text),
                }
            else:
                payload = {
                    "event": "result",
                    "mode": mode,
                    "success": result.success,
                    "target": "save_prompt" if mode == "save_prompt" else "voice",
                    "heard_text": result.heard_text,
                    "control_text": result.message,
                    "info_text": self._spotify_info_text,
                    "display_text": result.heard_text,
                }
            self._voice_queue.put((request_id, payload))

        self._voice_thread = threading.Thread(target=_worker, name="hgr-app-voice-command", daemon=True)
        self._voice_thread.start()

    def _drain_voice_results(self) -> None:
        while True:
            try:
                request_id, payload = self._voice_queue.get_nowait()
            except queue.Empty:
                break
            if request_id != self._voice_request_id:
                continue
            event = str(payload.get("event", "result"))
            if event == "status":
                status = str(payload.get("status", "") or "").strip().lower()
                command_text = str(payload.get("command_text", "") or "").strip()
                if command_text:
                    self._voice_heard_text = command_text
                    self._voice_display_text = command_text
                if status == "listening":
                    if self._voice_mode == "dictation":
                        self._voice_control_text = "dictation listening..."
                    elif self._voice_mode == "save_prompt":
                        self._voice_control_text = self._save_prompt_text
                    else:
                        self._voice_control_text = "voice listening..."
                    if self._voice_mode == "dictation":
                        self.voice_status_overlay.show_processing("Dictation active", command_text="")
                    elif self._voice_mode == "save_prompt":
                        self.voice_status_overlay.show_listening("Listening...", hint_text=self._save_prompt_text)
                    else:
                        if self._selection_prompt_active and getattr(self.voice_status_overlay, "_mode", "") == "selection":
                            self.voice_status_overlay.update_selection_status("Listening...")
                        else:
                            self.voice_status_overlay.show_listening()
                elif status == "recognizing":
                    if self._voice_mode == "dictation":
                        self._voice_control_text = "dictation active"
                        self.voice_status_overlay.show_processing("Dictation active", command_text="")
                    elif self._voice_mode == "save_prompt":
                        self._voice_control_text = "recognizing save location..."
                        self.voice_status_overlay.show_processing(
                            "Processing command...",
                            command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                        )
                    else:
                        self._voice_control_text = "recognizing..."
                        if self._selection_prompt_active and getattr(self.voice_status_overlay, "_mode", "") == "selection":
                            self.voice_status_overlay.update_selection_status("Recognizing...")
                        else:
                            self.voice_status_overlay.show_processing(
                                "Recognizing...",
                                command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                            )
                elif status == "processing":
                    if self._voice_mode == "save_prompt":
                        self._voice_control_text = "processing save location..."
                    else:
                        self._voice_control_text = "processing command..."
                    if self._selection_prompt_active and getattr(self.voice_status_overlay, "_mode", "") == "selection":
                        self.voice_status_overlay.hide_overlay()
                    elif self._voice_mode == "save_prompt":
                        self.voice_status_overlay.show_processing(
                            "Executing command...",
                            command_text=command_text or (self._voice_display_text if self._voice_display_text != "-" else ""),
                        )
                    else:
                        self.voice_status_overlay.show_processing(
                            "Processing command...",
                            command_text=command_text or (self._voice_display_text if self._voice_display_text != "-" else ""),
                        )
                continue
            if event == "dictation_chunk":
                heard_text = str(payload.get("heard_text", "") or "").strip()
                display_text = str(payload.get("display_text", "") or "").strip()
                partial = bool(payload.get("partial"))
                if heard_text:
                    self._voice_heard_text = heard_text
                if display_text:
                    self._voice_display_text = display_text
                self._voice_control_text = str(payload.get("control_text", "dictation updated"))
                if partial:
                    self._voice_control_text = "live dictating..."
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_listening()
                continue
            if event == "dictation_complete":
                self._voice_listening = False
                self._dictation_active = False
                self._dictation_backend = "idle"
                self._voice_mode = "ready"
                self._voice_stop_event = None
                display_text = str(payload.get("display_text", "") or "").strip()
                if display_text:
                    self._voice_display_text = display_text
                self._voice_control_text = str(payload.get("control_text", "dictation stopped"))
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_result(
                    "Dictation stopped",
                    command_text="",
                    duration=1.8,
                )
                continue
            self._voice_listening = False
            self._dictation_active = False
            self._dictation_backend = "idle"
            self._voice_mode = "ready"
            self._voice_stop_event = None
            mode = str(payload.get("mode", "general"))
            heard_text = str(payload.get("heard_text", "") or "").strip()
            self._voice_heard_text = heard_text or "-"
            self._voice_display_text = str(payload.get("display_text", "") or heard_text or "-")
            self._voice_control_text = str(payload.get("control_text", "voice idle"))
            target = payload.get("target")
            if mode == "general":
                if target == "spotify":
                    if payload.get("success"):
                        self._spotify_info_text = str(payload.get("info_text", self._spotify_info_text))
                    self._spotify_control_text = str(payload.get("control_text", self._spotify_control_text))
                elif target == "chrome":
                    self._chrome_control_text = str(payload.get("control_text", self._chrome_control_text))
                if target == "voice_selection":
                    self.command_detected.emit(self._voice_control_text)
                    title, items, instruction = self._parse_voice_selection_text(self._voice_display_text if self._voice_display_text != "-" else "")
                    self._selection_prompt_active = True
                    self._selection_prompt_title = title
                    self._selection_prompt_items = items
                    self._selection_prompt_instruction = instruction
                    self.voice_status_overlay.show_selection(title, items, instruction, status_text="")
                    continue
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_result(
                    "Executing command" if payload.get("success") else "Command not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "selection":
                if target == "voice_selection":
                    self.command_detected.emit(self._voice_control_text)
                    title, items, instruction = self._parse_voice_selection_text(self._voice_display_text if self._voice_display_text != "-" else "")
                    self._selection_prompt_active = True
                    self._selection_prompt_title = title
                    self._selection_prompt_items = items
                    self._selection_prompt_instruction = instruction
                    self.voice_status_overlay.show_selection(title, items, instruction, status_text="")
                    continue
                self._selection_prompt_active = False
                if str(self._voice_control_text).lower().startswith("selection cancelled"):
                    self.command_detected.emit(self._voice_control_text)
                    self.voice_status_overlay.show_result("Selection cancelled", command_text="", duration=1.4)
                    continue
                self.command_detected.emit(self._voice_control_text)
                self.voice_status_overlay.show_result(
                    "Executing command" if payload.get("success") else "Command not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "save_prompt":
                self._save_prompt_active = False
                self.command_detected.emit("save destination received" if heard_text else "using default save location")
                self.voice_status_overlay.show_result(
                    "Save preference received" if heard_text else "Using default save folder",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=1.5,
                )
                self.save_prompt_completed.emit(
                    {
                        "success": bool(payload.get("success")),
                        "heard_text": heard_text,
                        "control_text": self._voice_control_text,
                        "display_text": self._voice_display_text,
                    }
                )
                continue

            if not payload.get("success"):
                self._spotify_control_text = str(payload.get("control_text", "voice command not understood"))
                self.command_detected.emit(self._spotify_control_text)
                self.voice_status_overlay.show_result(
                    "Playlist not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "create_playlist":
                playlist_name = self._clean_create_playlist_reply(heard_text)
            else:
                playlist_name = self._clean_playlist_reply(heard_text)
            if not playlist_name:
                self._spotify_control_text = "playlist name not understood"
                self.command_detected.emit(self._spotify_control_text)
                self.voice_status_overlay.show_result(
                    "Playlist not understood",
                    command_text=self._voice_display_text if self._voice_display_text != "-" else "",
                    duration=2.0,
                )
                continue

            if mode == "add_playlist":
                success = self.spotify_controller.add_current_track_to_playlist(playlist_name)
            elif mode == "create_playlist":
                success = self.spotify_controller.create_playlist(playlist_name)
            else:
                success = self.spotify_controller.remove_current_track_from_playlist(playlist_name)
            self._spotify_control_text = self.spotify_controller.message
            self.command_detected.emit(self._spotify_control_text)
            if success:
                details = self.spotify_controller.get_current_track_details()
                if details is not None:
                    self._spotify_info_text = details.summary()
            self.voice_status_overlay.show_result(
                self.spotify_controller.message,
                command_text=playlist_name,
                duration=2.0,
            )

    def _parse_voice_selection_text(self, text: str) -> tuple[str, list[tuple[int, str, str]], str]:
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        title = "Choose a file or folder"
        if lines and "apps" in lines[0].lower():
            title = "Choose a file or app"
        instruction = "Say the corresponding number"
        items: list[tuple[int, str, str]] = []
        for line in lines:
            match = re.match(r"^(\d+)\.\s+(.*?)(?:\s+[—-]\s+(.*))?$", line)
            if match:
                number = int(match.group(1))
                label = str(match.group(2) or "").strip()
                path_text = str(match.group(3) or "").strip()
                items.append((number, label, path_text))
            elif line.lower().startswith("which "):
                instruction = line
        return title, items, instruction

    def _handle_voice_overlay_selection(self, number: int) -> None:
        try:
            result = self.voice_processor.execute(str(number))
        except Exception:
            return
        self._voice_queue.put((self._voice_request_id, {
            "event": "result",
            "mode": "general",
            "success": result.success,
            "target": result.target,
            "heard_text": result.heard_text,
            "control_text": result.control_text,
            "info_text": result.info_text,
            "display_text": getattr(result, "display_text", result.heard_text),
        }))

    def _update_spotify_wheel_overlay(self, now: float) -> None:
        if not self._spotify_wheel_visible:
            if self.spotify_wheel_overlay.isVisible():
                self.spotify_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._spotify_wheel_selected_key is not None:
            selected_label = self._spotify_wheel_label(self._spotify_wheel_selected_key)
        selection_progress = 0.0
        if self._spotify_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._spotify_wheel_selected_since) / 1.0))
        self.spotify_wheel_overlay.set_wheel(
            items=self._spotify_wheel_items(),
            selected_key=self._spotify_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._spotify_wheel_cursor_offset,
        )
        self.spotify_wheel_overlay.show_overlay()

    def _update_chrome_wheel_overlay(self, now: float) -> None:
        if not self._chrome_wheel_visible:
            if self.chrome_wheel_overlay.isVisible():
                self.chrome_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._chrome_wheel_selected_key is not None:
            selected_label = self._chrome_wheel_label(self._chrome_wheel_selected_key)
        selection_progress = 0.0
        if self._chrome_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._chrome_wheel_selected_since) / 1.0))
        self.chrome_wheel_overlay.set_wheel(
            items=self._chrome_wheel_items(),
            selected_key=self._chrome_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._chrome_wheel_cursor_offset,
        )
        self.chrome_wheel_overlay.show_overlay()

    def _update_drawing_wheel_overlay(self, now: float) -> None:
        if not self._drawing_wheel_visible:
            if self.drawing_wheel_overlay.isVisible():
                self.drawing_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._drawing_wheel_selected_key is not None:
            selected_label = self._drawing_wheel_label(self._drawing_wheel_selected_key)
        selection_progress = 0.0
        if self._drawing_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._drawing_wheel_selected_since) / 0.85))
        self.drawing_wheel_overlay.set_wheel(
            items=self._drawing_wheel_items(),
            selected_key=self._drawing_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._drawing_wheel_cursor_offset,
        )
        self.drawing_wheel_overlay.show_overlay()

    def _update_utility_wheel_overlay(self, now: float) -> None:
        if not self._utility_wheel_visible:
            if self.utility_wheel_overlay.isVisible():
                self.utility_wheel_overlay.hide_overlay()
            return
        selected_label = "Hold a slice"
        if self._utility_wheel_selected_key is not None:
            selected_label = self._utility_wheel_label(self._utility_wheel_selected_key)
        selection_progress = 0.0
        if self._utility_wheel_selected_key is not None:
            selection_progress = min(1.0, max(0.0, (now - self._utility_wheel_selected_since) / 0.85))
        self.utility_wheel_overlay.set_wheel(
            items=self._utility_wheel_items(),
            selected_key=self._utility_wheel_selected_key,
            selection_progress=selection_progress,
            status_text=selected_label,
            cursor_offset=self._utility_wheel_cursor_offset,
        )
        self.utility_wheel_overlay.show_overlay()

    def _cancel_all_voice_stages(self) -> None:
        was_dictation = bool(self._dictation_active)
        was_active = bool(
            self._voice_listening
            or self._dictation_active
            or self._save_prompt_active
            or self._selection_prompt_active
        )
        if was_dictation:
            try:
                self.text_input_controller.stop_windows_dictation()
            except Exception:
                pass
        self._reset_voice_state()
        if was_active:
            self._voice_control_text = "voice canceled"
            try:
                self.command_detected.emit(self._voice_control_text)
            except Exception:
                pass
            try:
                self.voice_status_overlay.show_result("Canceled", command_text="", duration=1.0)
            except Exception:
                pass

    def _reset_voice_state(self) -> None:
        if self._voice_stop_event is not None:
            self._voice_stop_event.set()
            self._voice_stop_event = None
        self.dictation_processor.reset()
        self._voice_candidate = "neutral"
        self._voice_candidate_since = 0.0
        self._voice_cooldown_until = 0.0
        self._voice_latched_label = None
        self._voice_request_id += 1
        self._voice_listening = False
        self._dictation_active = False
        self._dictation_backend = "idle"
        self._voice_mode = "ready"
        self._voice_control_text = self.voice_listener.message
        self._voice_heard_text = "-"
        self._voice_display_text = "-"
        self._dictation_toggle_release_required = False
        self._dictation_stop_rearm_at = 0.0
        self._dictation_release_candidate_since = 0.0
        self._selection_prompt_active = False
        self._selection_prompt_title = "Which file/folder?"
        self._selection_prompt_items = []
        self._selection_prompt_instruction = "Say the corresponding number."
        self._save_prompt_active = False
        self.voice_status_overlay.hide_overlay()
        while True:
            try:
                self._voice_queue.get_nowait()
            except queue.Empty:
                break

    def start_save_location_prompt(self) -> bool:
        if not self._running:
            return False
        if self._voice_listening:
            self._reset_voice_state()
        self._save_prompt_active = True
        self._start_voice_capture(mode="save_prompt", preferred_app=None)
        return True
