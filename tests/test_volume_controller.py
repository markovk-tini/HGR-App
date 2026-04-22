from __future__ import annotations

import unittest

from hgr.debug.volume_controller import VolumeController


class _FakeEndpointVolume:
    def __init__(self, *, level: float, muted: bool) -> None:
        self.level = float(level)
        self.muted = bool(muted)

    def GetMasterVolumeLevelScalar(self) -> float:
        return self.level

    def SetMasterVolumeLevelScalar(self, scalar: float, _context) -> None:
        self.level = float(scalar)

    def GetMute(self) -> int:
        return 1 if self.muted else 0

    def SetMute(self, muted: int, _context) -> None:
        self.muted = bool(muted)


class _FailingSetEndpointVolume(_FakeEndpointVolume):
    def __init__(self, *, level: float, muted: bool) -> None:
        super().__init__(level=level, muted=muted)
        self.fail_next_set = True

    def SetMasterVolumeLevelScalar(self, scalar: float, _context) -> None:
        if self.fail_next_set:
            self.fail_next_set = False
            raise RuntimeError("stale endpoint")
        super().SetMasterVolumeLevelScalar(scalar, _context)


class VolumeControllerCacheTest(unittest.TestCase):
    def _make_controller(self, endpoint: _FakeEndpointVolume) -> VolumeController:
        controller = VolumeController.__new__(VolumeController)
        controller._available = True
        controller._message = "Volume control ready."
        controller._volume = endpoint
        controller._last_known_level = endpoint.level
        controller._last_known_muted = endpoint.muted
        controller._sync_window_seconds = 1.1
        controller._level_write_until = 0.0
        controller._mute_write_until = 0.0
        controller._test_now = 10.0
        controller._now = lambda: controller._test_now
        return controller

    def test_recent_written_level_wins_over_stale_readback(self) -> None:
        endpoint = _FakeEndpointVolume(level=0.42, muted=False)
        controller = self._make_controller(endpoint)

        self.assertTrue(controller.set_level(0.80))
        endpoint.level = 0.42
        controller._test_now = 10.2

        self.assertAlmostEqual(controller.get_level() or 0.0, 0.80, places=3)

    def test_recent_written_mute_wins_over_stale_readback(self) -> None:
        endpoint = _FakeEndpointVolume(level=0.42, muted=False)
        controller = self._make_controller(endpoint)

        self.assertTrue(controller.set_mute(True))
        endpoint.muted = False
        controller._test_now = 10.2

        self.assertTrue(bool(controller.get_mute()))

    def test_set_level_recovers_after_stale_endpoint_failure(self) -> None:
        failing = _FailingSetEndpointVolume(level=0.42, muted=False)
        healthy = _FakeEndpointVolume(level=0.42, muted=False)
        controller = self._make_controller(failing)

        def _recover(exc=None) -> bool:
            controller._volume = healthy
            controller._available = True
            controller._message = "Volume control ready."
            return True

        controller._recover_endpoint = _recover

        self.assertTrue(controller.set_level(0.80))
        self.assertAlmostEqual(healthy.level, 0.80, places=3)
