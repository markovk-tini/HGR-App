from __future__ import annotations

import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

_HAS_GESTURE_UI_DEPS = (
    importlib.util.find_spec("cv2") is not None
    and importlib.util.find_spec("PySide6") is not None
)

if _HAS_GESTURE_UI_DEPS:
    from hgr.gesture.ui.test_window import GestureTestWindow
else:
    GestureTestWindow = None


@unittest.skipUnless(_HAS_GESTURE_UI_DEPS, "Gesture UI dependencies are unavailable in this environment")
class GestureDictationToggleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = GestureTestWindow.__new__(GestureTestWindow)
        self.window._voice_candidate = "neutral"
        self.window._voice_candidate_since = 0.0
        self.window._voice_cooldown_until = 0.0
        self.window._voice_latched_label = None
        self.window._voice_listening = False
        self.window._dictation_active = False
        self.window._reset_voice_candidate = GestureTestWindow._reset_voice_candidate.__get__(self.window, GestureTestWindow)
        self.window._start_voice_command = Mock()
        self.window._start_dictation_capture = Mock()
        self.window._stop_dictation_capture = Mock()

    def test_left_two_starts_dictation_after_hold(self) -> None:
        prediction = SimpleNamespace(stable_label="two")

        self.window._handle_left_hand_voice(prediction, 1.0)
        self.window._handle_left_hand_voice(prediction, 1.6)

        self.window._start_dictation_capture.assert_called_once_with()
        self.window._start_voice_command.assert_not_called()

    def test_left_two_stops_active_dictation_after_hold(self) -> None:
        self.window._dictation_active = True
        self.window._voice_listening = True
        self.window._voice_candidate = "two"
        self.window._voice_candidate_since = 1.0
        prediction = SimpleNamespace(stable_label="two")

        self.window._handle_left_hand_voice(prediction, 1.6)

        self.window._stop_dictation_capture.assert_called_once_with()

    def test_left_one_still_starts_command_mode(self) -> None:
        prediction = SimpleNamespace(stable_label="one")

        self.window._handle_left_hand_voice(prediction, 2.0)
        self.window._handle_left_hand_voice(prediction, 2.6)

        self.window._start_voice_command.assert_called_once_with()
        self.window._start_dictation_capture.assert_not_called()

    def test_start_dictation_capture_uses_hgr_streaming_path(self) -> None:
        self.window._voice_listening = False
        self.window.text_input_controller = Mock()
        self.window.text_input_controller.available = True
        self.window.dictation_processor = Mock()
        self.window._start_voice_capture = Mock()

        GestureTestWindow._start_dictation_capture(self.window)

        self.window.dictation_processor.reset.assert_called_once_with()
        self.window._start_voice_capture.assert_called_once_with(mode="dictation", preferred_app=None)

    def test_stop_dictation_capture_sets_stop_event_for_streaming_session(self) -> None:
        self.window._dictation_active = True
        self.window._dictation_backend = "local_stream"
        self.window._voice_display_text = "old"
        self.window._voice_stop_event = Mock()
        self.window.voice_status_overlay = Mock()

        GestureTestWindow._stop_dictation_capture(self.window)

        self.window._voice_stop_event.set.assert_called_once_with()
        self.assertFalse(self.window._dictation_active)
