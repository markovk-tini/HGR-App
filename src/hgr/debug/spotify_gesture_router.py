from __future__ import annotations

from dataclasses import dataclass

from .spotify_controller import SpotifyController


@dataclass(frozen=True)
class SpotifyGestureSnapshot:
    control_text: str
    info_text: str
    last_action: str
    action_counter: int = 0


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
        self._action_counter = 0

    def _set_action(self, label: str) -> None:
        self._last_action = label
        self._action_counter += 1

    def snapshot(self) -> SpotifyGestureSnapshot:
        return SpotifyGestureSnapshot(
            control_text=self._control_text,
            info_text=self._info_text,
            last_action=self._last_action,
            action_counter=self._action_counter,
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
                self._set_action("spotify_focus_idle")
            else:
                ready = controller.focus_or_open_window()
                self._control_text = controller.message
                self._set_action("spotify_focus" if ready else "spotify_focus_failed")
                if ready and controller.is_active_device_available():
                    details = controller.get_current_track_details()
                    self._info_text = details.summary() if details is not None else "Spotify ready on device"
        elif stable_label == "fist":
            if not self._can_control_without_focus(controller):
                self._control_text = "spotify inactive on device"
                self._set_action("spotify_toggle_idle")
                return
            # Fire HTTP call on a background thread so the gesture
            # worker doesn't block on the 50-300 ms Spotify Web API
            # roundtrip. Action label is set optimistically; the
            # controller's `message` updates when the call completes
            # and the next gesture frame picks it up.
            controller.dispatch_async(controller.toggle_playback)
            self._control_text = "spotify play/pause"
            self._set_action("spotify_toggle")
        elif stable_label == "ok":
            if not self._can_control_without_focus(controller):
                self._control_text = "spotify inactive on device"
                self._set_action("spotify_shuffle_idle")
                return
            controller.dispatch_async(controller.toggle_shuffle)
            self._control_text = "spotify shuffle"
            self._set_action("spotify_shuffle")

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
                self._set_action("spotify_previous_idle")
            elif dynamic_label == "swipe_right":
                self._set_action("spotify_next_idle")
            else:
                self._set_action("spotify_repeat_idle")
            return

        self._dynamic_cooldown_until = now + self.dynamic_cooldown_seconds
        self._dynamic_latched_label = dynamic_label
        # Fire HTTP calls on a background thread — dynamic gestures
        # (swipes / repeat circle) used to spike the gesture
        # worker's frame to 200+ ms during the Spotify Web API
        # roundtrip. Cooldowns + latching above ensure we don't
        # double-fire while a dispatch is in flight.
        if dynamic_label == "swipe_left":
            controller.dispatch_async(controller.previous_track)
            self._control_text = "spotify previous track"
            self._set_action("spotify_previous")
        elif dynamic_label == "swipe_right":
            controller.dispatch_async(controller.next_track)
            self._control_text = "spotify next track"
            self._set_action("spotify_next")
        else:
            controller.dispatch_async(controller.toggle_repeat_track)
            self._control_text = "spotify repeat toggle"
            self._set_action("spotify_repeat")

    def _can_control_without_focus(self, controller: SpotifyController) -> bool:
        # Stricter than the old is_running() catch-all: a Spotify
        # protocol handler / helper process leaves is_running() True
        # even when there's no real Spotify to control, so fist
        # (toggle play/pause) used to attempt action and silently
        # auto-launch the app via play() → ensure_ready(open_if_needed=True).
        # Now we require either an active Web API device OR an
        # actual Spotify window. Right-hand 'two' and the voice
        # 'open spotify' command remain the ONLY paths that may
        # launch Spotify when it isn't running.
        if controller.is_active_device_available():
            return True
        is_window_open = getattr(controller, "is_window_open", None)
        if callable(is_window_open) and is_window_open():
            return True
        if controller.is_window_active():
            return True
        return False

# Author: Konstantin Markov
