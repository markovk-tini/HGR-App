from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from hgr.app.integration.noop_engine import GestureWorker


class _EngineStub:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class LowFpsModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = GestureWorker.__new__(GestureWorker)
        self.worker.config = SimpleNamespace(low_fps_auto=True, low_fps_mode=False, force_ten_fps_test_mode=False)
        self.worker._fps = 0.0
        self.worker._low_fps_auto_engaged = False
        self.worker._low_fps_below_since = None
        self.worker._low_fps_above_since = None
        self.worker._low_fps_active = True
        self.worker._cap = None
        self.worker.engine = None
        self.worker._low_fps_last_process = 0.0

    def test_auto_low_fps_exits_after_sustained_recovery(self) -> None:
        engine = _EngineStub()
        self.worker._fps = 18.5
        self.worker._low_fps_auto_engaged = True
        self.worker._low_fps_above_since = 10.0
        self.worker.engine = engine
        self.worker._build_engine_for_fps_mode = lambda: "normal-engine"

        GestureWorker._maybe_auto_toggle_low_fps(self.worker, 16.5)

        self.assertFalse(self.worker._low_fps_auto_engaged)
        self.assertIsNone(self.worker._low_fps_above_since)
        self.assertTrue(engine.closed)
        self.assertEqual(self.worker.engine, "normal-engine")

    def test_prepare_runtime_frame_downscales_when_low_fps_active(self) -> None:
        self.worker._low_fps_active = True
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        resized = GestureWorker._prepare_runtime_frame(self.worker, frame)

        self.assertEqual(resized.shape[1], 640)
        self.assertLess(resized.shape[0], frame.shape[0])

    def test_auto_low_fps_waits_until_below_eighteen(self) -> None:
        self.worker._fps = 18.0

        GestureWorker._maybe_auto_toggle_low_fps(self.worker, 5.0)

        self.assertFalse(self.worker._low_fps_auto_engaged)
        self.assertIsNone(self.worker._low_fps_below_since)

    def test_force_ten_fps_tick_gate_waits_for_interval(self) -> None:
        self.worker.config.force_ten_fps_test_mode = True

        self.assertFalse(GestureWorker._should_skip_forced_fps_tick(self.worker, 1.0))
        self.assertTrue(GestureWorker._should_skip_forced_fps_tick(self.worker, 1.05))
        self.assertFalse(GestureWorker._should_skip_forced_fps_tick(self.worker, 1.11))

    def test_volume_overlay_blocks_app_swipe_routing(self) -> None:
        self.worker._tutorial_mode_enabled = False
        self.worker._utility_capture_selection_active = False
        self.worker._utility_recording_active = False
        self.worker._utility_recording_stop_candidate_since = 0.0
        self.worker._dictation_active = False
        self.worker._drawing_mode_enabled = False
        self.worker._volume_overlay_visible = True
        self.worker._volume_mode_active = True
        self.worker._left_hand_prediction = None
        self.worker._update_utility_wheel = lambda *args, **kwargs: False
        self.worker._update_youtube_wheel = Mock()
        self.worker._update_chrome_wheel = Mock()
        self.worker._update_spotify_wheel = Mock()
        self.worker._reset_voice_candidate = Mock()
        self.worker._handle_left_hand_voice = Mock()
        self.worker.chrome_router = SimpleNamespace(update=Mock())
        self.worker.spotify_router = SimpleNamespace(update=Mock())
        self.worker.youtube_router = SimpleNamespace(update=Mock())

        prediction = SimpleNamespace(dynamic_label="swipe_right", stable_label="neutral")

        GestureWorker._handle_app_controls(self.worker, prediction, None, "Right", 1.0)

        self.worker._update_youtube_wheel.assert_called_once_with(prediction=None, hand_reading=None, now=1.0, active=False)
        self.worker._update_chrome_wheel.assert_called_once_with(prediction=None, hand_reading=None, now=1.0, active=False)
        self.worker._update_spotify_wheel.assert_called_once_with(prediction=None, hand_reading=None, now=1.0, active=False)
        self.worker._reset_voice_candidate.assert_called_once_with(1.0)
        self.worker.chrome_router.update.assert_not_called()
        self.worker.spotify_router.update.assert_not_called()
        self.worker.youtube_router.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
