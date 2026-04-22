from __future__ import annotations

import unittest

from hgr.app.integration.noop_engine import GestureWorker


class YouTubeWheelHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = GestureWorker.__new__(GestureWorker)

    def test_youtube_wheel_omits_seek_items(self) -> None:
        self.assertEqual(
            [key for key, _label, _angle in self.worker._youtube_wheel_items()],
            [
                "fullscreen",
                "theater",
                "mini_player",
                "captions",
                "like",
                "dislike",
                "share",
                "speed_down",
                "speed_up",
            ],
        )


if __name__ == "__main__":
    unittest.main()
