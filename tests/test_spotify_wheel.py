from __future__ import annotations

import unittest

from hgr.gesture.ui.test_window import GestureTestWindow


class SpotifyWheelHelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.window = GestureTestWindow.__new__(GestureTestWindow)

    def test_selection_key_uses_deadzone(self) -> None:
        self.assertIsNone(self.window._spotify_wheel_selection_key(0.10, 0.08))

    def test_selection_key_maps_upper_right_to_remove_playlist(self) -> None:
        self.assertEqual(self.window._spotify_wheel_selection_key(0.42, -0.30), "remove_playlist")

    def test_selection_key_maps_left_to_remove_liked(self) -> None:
        self.assertEqual(self.window._spotify_wheel_selection_key(-0.52, -0.02), "remove_liked")

    def test_selection_key_maps_upper_left_to_shuffle(self) -> None:
        self.assertEqual(self.window._spotify_wheel_selection_key(-0.42, -0.30), "shuffle")

    def test_wheel_items_follow_requested_clockwise_order(self) -> None:
        self.assertEqual(
            [key for key, _label, _angle in self.window._spotify_wheel_items()],
            [
                "add_playlist",
                "remove_playlist",
                "add_queue",
                "remove_queue",
                "like",
                "remove_liked",
                "shuffle",
            ],
        )

    def test_clean_playlist_reply_strips_filler(self) -> None:
        cleaned = self.window._clean_playlist_reply("please add this song to my chill mix playlist")
        self.assertEqual(cleaned, "chill mix")
