from __future__ import annotations

from dataclasses import dataclass

from .spotify_controller import SpotifyController


@dataclass(frozen=True)
class SpotifyGestureSnapshot:
    control_text: str
    info_text: str
    last_action: str


class SpotifyGestureRouter:
    def __init__(
        self,
        *,
        static_hold_seconds: float = 0.5,
        static_cooldown_seconds: float = 1.5,
        dynamic_cooldown_seconds: float = 0.9,
    ) -> None:
        self.static_hold_seconds = float(static_hold_seconds)
        self.static_cooldown_seconds = float(static_cooldown_seconds)
        self.dynamic_cooldown_seconds = float(dynamic_cooldown_seconds)
        self.reset()

    def reset(self) -> None:
        self._static_candidate = "neutral"
        self._static_candidate_since = 0.0
        self._static_cooldown_until = 0.0
        self._static_latched_label: str | None = None
        self._dynamic_cooldown_until = 0.0
        self._dynamic_latched_label: str | None = None
        self._control_text = "spotify idle"
        self._info_text = "-"
        self._last_action = "-"

    def snapshot(self) -> SpotifyGestureSnapshot:
        return SpotifyGestureSnapshot(
            control_text=self._control_text,
            info_text=self._info_text,
            last_action=self._last_action,
        )

    def update(
        self,
        *,
        stable_label: str,
        dynamic_label: str,
        controller: SpotifyController,
        now: float,
    ) -> SpotifyGestureSnapshot:
        self._update_dynamic(dynamic_label, controller, now)
        self._update_static(stable_label, dynamic_label, controller, now)
        return self.snapshot()

    def _update_static(self, stable_label: str, dynamic_label: str, controller: SpotifyController, now: float) -> None:
        actionable = {"two", "fist", "ok"}
        if dynamic_label == "repeat_circle" and stable_label == "one":
            self._static_candidate = "neutral"
            self._static_candidate_since = 0.0
            return
        if stable_label == self._static_latched_label:
            if stable_label not in actionable:
                self._static_latched_label = None
            return

        if stable_label not in actionable:
            self._static_candidate = "neutral"
            self._static_candidate_since = 0.0
            if self._static_latched_label is not None:
                self._static_latched_label = None
            return

        if stable_label != self._static_candidate:
            self._static_candidate = stable_label
            self._static_candidate_since = now
            return

        if now < self._static_cooldown_until:
            return
        required_hold = 1.0 if stable_label == "two" else self.static_hold_seconds
        if now - self._static_candidate_since < required_hold:
            return

        self._static_cooldown_until = now + self.static_cooldown_seconds
        self._static_latched_label = stable_label
        if stable_label == "two":
            if controller.is_window_active():
                self._control_text = "spotify already focused"
                self._last_action = "spotify_focus_idle"
            else:
                ready = controller.focus_or_open_window()
                self._control_text = controller.message
                self._last_action = "spotify_focus" if ready else "spotify_focus_failed"
                if ready and controller.is_active_device_available():
                    details = controller.get_current_track_details()
                    self._info_text = details.summary() if details is not None else "Spotify ready on device"
        elif stable_label == "fist":
            if not self._can_control_without_focus(controller):
                self._control_text = "spotify inactive on device"
                self._last_action = "spotify_toggle_idle"
                return
            toggled = controller.toggle_playback()
            self._control_text = controller.message
            self._last_action = "spotify_toggle" if toggled else "spotify_toggle_failed"
        elif stable_label == "ok":
            if not self._can_control_without_focus(controller):
                self._control_text = "spotify inactive on device"
                self._last_action = "spotify_shuffle_idle"
                return
            toggled = controller.toggle_shuffle()
            self._control_text = controller.message
            self._last_action = "spotify_shuffle" if toggled else "spotify_shuffle_failed"

    def _update_dynamic(self, dynamic_label: str, controller: SpotifyController, now: float) -> None:
        actionable = {"swipe_left", "swipe_right", "repeat_circle"}
        if dynamic_label == self._dynamic_latched_label:
            if dynamic_label == "neutral":
                self._dynamic_latched_label = None
            return

        if dynamic_label not in actionable:
            if dynamic_label == "neutral":
                self._dynamic_latched_label = None
            return

        if now < self._dynamic_cooldown_until:
            return
        if not self._can_control_without_focus(controller):
            self._control_text = "spotify inactive on device"
            if dynamic_label == "swipe_left":
                self._last_action = "spotify_previous_idle"
            elif dynamic_label == "swipe_right":
                self._last_action = "spotify_next_idle"
            else:
                self._last_action = "spotify_repeat_idle"
            return

        self._dynamic_cooldown_until = now + self.dynamic_cooldown_seconds
        self._dynamic_latched_label = dynamic_label
        if dynamic_label == "swipe_left":
            success = controller.previous_track()
            self._last_action = "spotify_previous" if success else "spotify_previous_failed"
        elif dynamic_label == "swipe_right":
            success = controller.next_track()
            self._last_action = "spotify_next" if success else "spotify_next_failed"
        else:
            success = controller.toggle_repeat_track()
            self._last_action = "spotify_repeat" if success else "spotify_repeat_failed"
        self._control_text = controller.message

    def _can_control_without_focus(self, controller: SpotifyController) -> bool:
        return (
            controller.is_active_device_available()
            or controller.is_running()
            or controller.is_window_active()
        )
