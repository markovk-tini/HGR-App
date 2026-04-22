from __future__ import annotations

import platform


class MediaController:
    def __init__(self) -> None:
        self._available = platform.system() == 'Windows'

    @property
    def available(self) -> bool:
        return self._available

    def play_pause(self) -> bool:
        if not self._available:
            return False
        try:
            import ctypes

            keybd_event = ctypes.windll.user32.keybd_event
            vk_media_play_pause = 0xB3
            key_event_up = 0x0002
            keybd_event(vk_media_play_pause, 0, 0, 0)
            keybd_event(vk_media_play_pause, 0, key_event_up, 0)
            return True
        except Exception:
            return False
