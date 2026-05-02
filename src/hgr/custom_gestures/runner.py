"""Live runner for custom gestures inside the running gesture engine.

Owns a GestureClassifier + per-frame hold/cooldown state machine. The
running GestureWorker calls process(landmarks_21x3, now) every frame
the user has a tracked hand; the runner deals with classification,
hold-to-activate, grace-window flicker tolerance, and cooldown via
action.fire_once.

This is the glue that makes a saved custom gesture actually fire its
action while the main app is running. Until this is wired in, custom
gestures only work in the standalone trainer / sandbox.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .action import fire_once
from .classifier import GestureClassifier
from .registry import CustomGesture, GestureRegistry


_DEFAULT_HOLD_SECONDS = 1.0
_DEFAULT_GRACE_SECONDS = 0.2


class CustomGestureRunner:
    """Per-worker custom-gesture controller. Cheap to construct, cheap
    to call from the hot loop.

    Threading note: the classifier matrix is built once on reload() and
    then read-only on each .process() call, so no locking is needed
    even though .reload() may be invoked from the Qt main thread while
    .process() runs on the worker thread — `np.ndarray @ vec` is a pure
    read.
    """

    def __init__(
        self,
        *,
        default_hold_seconds: float = _DEFAULT_HOLD_SECONDS,
        grace_seconds: float = _DEFAULT_GRACE_SECONDS,
    ) -> None:
        self._registry = GestureRegistry()
        self._classifier: Optional[GestureClassifier] = None
        self._hold_name: Optional[str] = None
        self._hold_started_at = 0.0
        self._last_match_at = 0.0
        self._fired_for_hold = False
        self._default_hold = float(default_hold_seconds)
        self._grace = float(grace_seconds)
        # Registry file mtime tracking — used by maybe_reload_if_changed()
        # so the live runner picks up gestures saved by the recorder /
        # sandbox / wizard without depending on every save path
        # explicitly calling worker.reload_custom_gestures().
        self._registry_mtime: float = 0.0
        self._last_mtime_check_at: float = 0.0
        self.reload()
        self._registry_mtime = self._read_registry_mtime()

    @staticmethod
    def _read_registry_mtime() -> float:
        try:
            from .registry import registry_path
            path = registry_path()
            return float(path.stat().st_mtime) if path.exists() else 0.0
        except Exception:
            return 0.0

    def maybe_reload_if_changed(self, now: float) -> None:
        """If the registry file's mtime has advanced since our last
        read, reload from disk. Throttled to one stat() per ~3 s so
        the per-frame cost is negligible. Lets the live engine pick
        up newly-saved gestures from the recorder / sandbox / wizard
        even when the save path didn't explicitly trigger a reload."""
        if now - self._last_mtime_check_at < 3.0:
            return
        self._last_mtime_check_at = now
        current = self._read_registry_mtime()
        if current > 0.0 and current != self._registry_mtime:
            self.reload()
            self._registry_mtime = current

    @property
    def has_gestures(self) -> bool:
        if self._classifier is None:
            return False
        return bool(self._classifier._gestures)  # internal, but cheap to peek

    def reload(self) -> None:
        """Re-read the registry from disk and rebuild the classifier.
        Call this after the user adds / edits / deletes a gesture so
        the live pipeline picks up changes without restarting the app.
        """
        try:
            self._registry = GestureRegistry()
            self._registry.load()
            classifier = GestureClassifier(self._registry)
            classifier.reload()
            self._classifier = classifier
        except Exception:
            self._classifier = None
        # Reset hold state so a stale half-completed hold from before
        # the reload doesn't fire post-reload.
        self._hold_name = None
        self._fired_for_hold = False

    @staticmethod
    def _hold_seconds_for(gesture: CustomGesture, default: float) -> float:
        """Per-gesture hold-to-activate duration, stored in the action
        payload by the wizard. Falls back to the runner's default."""
        try:
            payload = gesture.action.payload or {}
            value = payload.get("hold_s")
            if value is None:
                return default
            return max(0.05, float(value))
        except Exception:
            return default

    def hand_lost(self, now: float) -> None:
        """Called from the worker when there's no tracked hand this
        frame. Drops the hold after the grace window so a brief
        MediaPipe dropout doesn't reset the timer."""
        if self._hold_name is None:
            return
        if now - self._last_match_at >= self._grace:
            self._hold_name = None
            self._fired_for_hold = False

    def process(
        self,
        landmarks_21x3: np.ndarray,
        now: float,
        handedness: Optional[str] = None,
    ) -> Optional[str]:
        """Classify the current landmarks and advance hold state. Returns
        the name of the gesture whose action just fired this frame, or
        None. Safe to call when no gestures are registered (returns None).

        `handedness` is the MediaPipe label of the tracked hand for
        this frame ("Left" / "Right" / None). A gesture only fires
        when its stored handedness matches (or when either side is
        None — legacy gestures fire on any hand).
        """
        if self._classifier is None:
            return None
        if landmarks_21x3 is None:
            self.hand_lost(now)
            return None
        try:
            match = self._classifier.classify(
                landmarks_21x3, sticky_name=self._hold_name
            )
        except Exception:
            return None

        if match is None:
            self.hand_lost(now)
            return None

        # Handedness gate. Both sides None = legacy/either-hand → allow.
        # If both are concrete labels and they differ, drop the match —
        # treat it as a no-match this frame so the hold timer resets,
        # so a quick L/R swap doesn't fire a wrong-hand gesture.
        gesture_hand = match.gesture.handedness
        if (
            gesture_hand in ("Left", "Right")
            and handedness in ("Left", "Right")
            and gesture_hand != handedness
        ):
            self.hand_lost(now)
            return None

        self._last_match_at = now
        if self._hold_name != match.gesture.name:
            self._hold_name = match.gesture.name
            self._hold_started_at = now
            self._fired_for_hold = False

        held = now - self._hold_started_at
        hold_duration = self._hold_seconds_for(match.gesture, self._default_hold)
        if not self._fired_for_hold and held >= hold_duration:
            if fire_once(match.gesture.name, match.gesture.action):
                self._fired_for_hold = True
                return match.gesture.name
        return None
