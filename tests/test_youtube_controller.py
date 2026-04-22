import unittest
from unittest.mock import patch

from hgr.debug.foreground_window import WindowInfo
from hgr.debug.youtube_controller import YouTubeController


class _HarnessYouTubeController(YouTubeController):
    def __init__(self) -> None:
        super().__init__(volume_controller=None)
        self.focus_calls: list[tuple[int, bool]] = []
        self.restore_calls: list[int] = []
        self.front_calls: list[int] = []
        self.cycle_calls = 0
        self.minimized: dict[int, bool] = {}
        self.focus_ok: dict[int, bool] = {}
        self.titles: dict[int, list[str] | str] = {}
        self.title_index: dict[int, int] = {}

    def _is_window_minimized(self, hwnd: int) -> bool:
        return bool(self.minimized.get(hwnd, False))

    def _restore_window(self, hwnd: int) -> bool:
        self.restore_calls.append(int(hwnd))
        return True

    def _bring_window_to_front(self, hwnd: int) -> bool:
        self.front_calls.append(int(hwnd))
        return bool(self.focus_ok.get(hwnd, True))

    def _focus_window_handle(self, hwnd: int, *, restore_if_minimized: bool = True) -> bool:
        self.focus_calls.append((int(hwnd), bool(restore_if_minimized)))
        return super()._focus_window_handle(hwnd, restore_if_minimized=restore_if_minimized)

    def _window_title(self, hwnd: int) -> str:
        payload = self.titles.get(int(hwnd), "")
        if isinstance(payload, list):
            index = self.title_index.get(int(hwnd), 0)
            index = max(0, min(index, len(payload) - 1))
            return str(payload[index])
        return str(payload)

    def _cycle_chrome_tab(self) -> bool:
        self.cycle_calls += 1
        for hwnd, payload in self.titles.items():
            if isinstance(payload, list):
                current = self.title_index.get(hwnd, 0)
                if current < len(payload) - 1:
                    self.title_index[hwnd] = current + 1
        return True


class YouTubeControllerTest(unittest.TestCase):
    def test_focus_window_preserves_maximized_state(self) -> None:
        controller = _HarnessYouTubeController()
        controller.minimized[42] = False
        controller.focus_ok[42] = True

        ok = controller._focus_window_handle(42, restore_if_minimized=True)

        self.assertTrue(ok)
        self.assertEqual(controller.restore_calls, [])
        self.assertEqual(controller.front_calls, [42])

    def test_focus_window_restores_minimized_window(self) -> None:
        controller = _HarnessYouTubeController()
        controller.minimized[42] = True
        controller.focus_ok[42] = True

        ok = controller._focus_window_handle(42, restore_if_minimized=True)

        self.assertTrue(ok)
        self.assertEqual(controller.restore_calls, [42])
        self.assertEqual(controller.front_calls, [42])

    @patch("hgr.debug.youtube_controller.enumerate_visible_windows")
    @patch("hgr.debug.youtube_controller.find_chrome_youtube_windows")
    def test_activate_youtube_tab_prefers_existing_youtube_window(self, youtube_windows, enumerate_windows) -> None:
        controller = _HarnessYouTubeController()
        controller.focus_ok[101] = True
        youtube_windows.return_value = [WindowInfo(hwnd=101, title="YouTube - Chrome", process_name="chrome.exe")]
        enumerate_windows.return_value = [WindowInfo(hwnd=202, title="Docs - Chrome", process_name="chrome.exe")]

        hwnd = controller._activate_youtube_tab()

        self.assertEqual(hwnd, 101)
        self.assertEqual(controller.focus_calls, [(101, True)])
        self.assertEqual(controller.cycle_calls, 0)

    @patch("hgr.debug.youtube_controller.enumerate_visible_windows")
    @patch("hgr.debug.youtube_controller.find_chrome_youtube_windows")
    def test_activate_youtube_tab_searches_background_chrome_tabs(self, youtube_windows, enumerate_windows) -> None:
        controller = _HarnessYouTubeController()
        controller.focus_ok[202] = True
        controller.titles[202] = [
            "Docs - Google Chrome",
            "Inbox - Google Chrome",
            "YouTube - Video - Google Chrome",
        ]
        youtube_windows.return_value = []
        enumerate_windows.return_value = [WindowInfo(hwnd=202, title="Docs - Google Chrome", process_name="chrome.exe")]

        hwnd = controller._activate_youtube_tab()

        self.assertEqual(hwnd, 202)
        self.assertEqual(controller.cycle_calls, 2)
        self.assertEqual(controller.focus_calls[0], (202, True))

    @patch("hgr.debug.youtube_controller.enumerate_visible_windows")
    @patch("hgr.debug.youtube_controller.find_chrome_youtube_windows")
    def test_has_youtube_tab_uses_recent_window_cache(self, youtube_windows, enumerate_windows) -> None:
        controller = _HarnessYouTubeController()
        controller._remember_youtube_window(303)
        youtube_windows.return_value = []
        enumerate_windows.return_value = [WindowInfo(hwnd=303, title="Docs - Google Chrome", process_name="chrome.exe")]

        has_tab = controller.has_youtube_tab()

        self.assertTrue(has_tab)

    def test_toggle_captions_reports_unavailable_feedback(self) -> None:
        controller = _HarnessYouTubeController()
        with (
            patch.object(controller, "_activate_youtube_tab", return_value=404),
            patch.object(controller, "_invoke_uia_named_control", return_value=True),
            patch.object(controller, "_detect_captions_feedback", return_value="unavailable"),
            patch.object(controller, "_send_key_to_youtube", return_value=False),
        ):
            ok = controller.toggle_captions()

        self.assertFalse(ok)
        self.assertEqual(controller.message, "No captions available for this video")

    def test_toggle_theater_uses_keyboard_shortcut(self) -> None:
        controller = _HarnessYouTubeController()
        with (
            patch.object(controller, "_activate_youtube_tab", return_value=505),
            patch.object(controller, "_send_key_to_youtube", return_value=True) as send_key,
        ):
            ok = controller.toggle_theater()

        self.assertTrue(ok)
        self.assertEqual(controller.message, "YouTube theater")
        send_key.assert_called_once()

    def test_like_video_uses_uia_action(self) -> None:
        controller = _HarnessYouTubeController()
        with (
            patch.object(controller, "_activate_youtube_tab", return_value=606),
            patch.object(controller, "_invoke_uia_named_control", return_value=True) as invoke_uia,
        ):
            ok = controller.like_video()

        self.assertTrue(ok)
        self.assertEqual(controller.message, "YouTube like")
        invoke_uia.assert_called_once()

    def test_share_video_uses_uia_action(self) -> None:
        controller = _HarnessYouTubeController()
        with (
            patch.object(controller, "_activate_youtube_tab", return_value=707),
            patch.object(controller, "_invoke_uia_named_control", return_value=True) as invoke_uia,
        ):
            ok = controller.share_video()

        self.assertTrue(ok)
        self.assertEqual(controller.message, "YouTube share")
        invoke_uia.assert_called_once()


if __name__ == "__main__":
    unittest.main()
