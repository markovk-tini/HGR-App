from __future__ import annotations


_POLL_INTERVAL = 1.0
_DUCK_MULTIPLIER = 0.6
_PEAK_THRESHOLD = 0.01
_RELEASE_GRACE_SECONDS = 3.0
_OTHER_APP_NAMES = ("spotify", "chrome")


class AppSessionDucker:
    """Reduces a fullscreen-foreground app's per-session volume while Spotify or
    Chrome (YouTube) are producing audio. Operates via pycaw ISimpleAudioVolume at
    the session layer, which avoids the OS Communications ducking path that stalls
    audio on some drivers (Razer Synapse + Genshin).
    """

    def __init__(self, volume_controller) -> None:
        self._vc = volume_controller
        self._last_poll = 0.0
        self._ducked_process: str | None = None
        self._original_level: float | None = None
        self._last_other_audio_at = 0.0

    def update(
        self,
        *,
        now: float,
        fullscreen: bool,
        foreground_process: str,
    ) -> None:
        if (now - self._last_poll) < _POLL_INTERVAL:
            return
        self._last_poll = now

        other_active = self._probe_other_audio()
        if other_active:
            self._last_other_audio_at = now
        within_grace = (now - self._last_other_audio_at) < _RELEASE_GRACE_SECONDS

        target = (foreground_process or "").lower()
        # Strip trailing .exe to match pycaw's substring matching semantics.
        if target.endswith(".exe"):
            target = target[:-4]

        should_duck = bool(
            fullscreen
            and target
            and not any(other in target for other in _OTHER_APP_NAMES)
            and (other_active or within_grace)
        )

        if should_duck:
            if self._ducked_process is None:
                self._apply_duck(target)
            elif self._ducked_process != target:
                self._release_duck()
                self._apply_duck(target)
        elif self._ducked_process is not None:
            self._release_duck()

    def force_release(self) -> None:
        if self._ducked_process is not None:
            self._release_duck()
        self._last_poll = 0.0
        self._last_other_audio_at = 0.0

    def _probe_other_audio(self) -> bool:
        try:
            peak = self._vc.get_process_audio_peak(list(_OTHER_APP_NAMES))
        except Exception:
            return False
        return peak is not None and peak > _PEAK_THRESHOLD

    def _apply_duck(self, process_name: str) -> None:
        try:
            _, level = self._vc.get_app_audio_info([process_name])
        except Exception:
            return
        if level is None:
            return
        original = float(level)
        target_level = max(0.0, min(1.0, original * _DUCK_MULTIPLIER))
        try:
            if self._vc.set_app_audio_level([process_name], target_level):
                self._ducked_process = process_name
                self._original_level = original
        except Exception:
            pass

    def _release_duck(self) -> None:
        process = self._ducked_process
        level = self._original_level
        self._ducked_process = None
        self._original_level = None
        if process is None or level is None:
            return
        try:
            self._vc.set_app_audio_level([process], float(level))
        except Exception:
            pass
