from __future__ import annotations

import unittest
from types import SimpleNamespace

from hgr.gesture.ui.test_window import GestureTestWindow


class ChromeWheelHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = GestureTestWindow.__new__(GestureTestWindow)
        self.window._chrome_mode_enabled = False
        self.window._chrome_active_cache = False
        self.window._chrome_active_cache_until = 0.0
        self.window.chrome_controller = SimpleNamespace(is_window_active=lambda: True)

    def test_wheel_selection_key_uses_deadzone_for_chrome_items(self) -> None:
        self.assertIsNone(self.window._wheel_selection_key(0.12, 0.10, self.window._chrome_wheel_items()))

    def test_wheel_selection_key_maps_upper_right_to_history(self) -> None:
        self.assertEqual(self.window._wheel_selection_key(0.42, -0.30, self.window._chrome_wheel_items()), "history")

    def test_wheel_selection_key_maps_left_to_reopen(self) -> None:
        self.assertEqual(self.window._wheel_selection_key(-0.52, -0.02, self.window._chrome_wheel_items()), "reopen")

    def test_chrome_mode_can_reuse_general_wheel_pose_for_chrome(self) -> None:
        self.window._chrome_mode_enabled = True
        prediction = SimpleNamespace(stable_label="wheel_pose", raw_label="wheel_pose", confidence=0.62)
        self.assertTrue(self.window._chrome_wheel_pose_active(prediction, 1.0))
