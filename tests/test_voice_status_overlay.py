from __future__ import annotations

import unittest

from hgr.gesture.ui.voice_status_overlay import VoiceStatusOverlay


class VoiceStatusOverlayTest(unittest.TestCase):
    def test_info_hint_alpha_holds_then_fades(self) -> None:
        overlay = VoiceStatusOverlay.__new__(VoiceStatusOverlay)
        overlay._mode = "info_hint"
        overlay._result_started = 10.0
        overlay._info_hint_hold_seconds = 3.0
        overlay._info_hint_fade_seconds = 0.65

        self.assertEqual(overlay._info_hint_alpha(12.8), 255)
        self.assertLess(overlay._info_hint_alpha(13.25), 255)
        self.assertEqual(overlay._info_hint_alpha(13.8), 0)


if __name__ == "__main__":
    unittest.main()
