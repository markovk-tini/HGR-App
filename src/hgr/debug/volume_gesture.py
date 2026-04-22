from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VolumeGestureUpdate:
    active: bool
    level: float | None
    muted: bool
    message: str
    status: str
    overlay_visible: bool
    trigger_mute_toggle: bool = False


class VolumeGestureTracker:
    def __init__(
        self,
        *,
        confirm_frames: int = 3,
        release_frames: int = 2,
        hold_seconds: float = 1.5,
        mute_cooldown_seconds: float = 1.0,
        deadzone_fraction: float = 0.20,
        smoothing: float = 0.14,
        pose_grace_seconds: float = 0.40,
        no_hand_grace_seconds: float = 0.20,
    ) -> None:
        self.confirm_frames = int(confirm_frames)
        self.release_frames = int(release_frames)
        self.hold_seconds = float(hold_seconds)
        self.mute_cooldown_seconds = float(mute_cooldown_seconds)
        self.deadzone_fraction = float(deadzone_fraction)
        self.smoothing = float(smoothing)
        self.pose_grace_seconds = float(pose_grace_seconds)
        self.no_hand_grace_seconds = float(no_hand_grace_seconds)
        self.reset()

    def rebase(self, level: float | None) -> None:
        if level is None:
            return
        clamped = max(0.0, min(1.0, float(level)))
        self._anchor_level = clamped
        self._level = clamped
        self._rebase_pending = True

    def reset(self, level: float | None = None, muted: bool = False) -> None:
        self._active = False
        self._confirm_frames = 0
        self._release_frames = 0
        self._anchor_y: float | None = None
        self._anchor_level: float | None = level
        self._level = level
        self._muted = bool(muted)
        self._message = 'idle'
        self._status = 'idle'
        self._last_mute_toggle_time = 0.0
        self._mute_gesture_latched = False
        self._lock_until = 0.0
        self._pinky_hold_latched = False
        self._activation_ready_frames = 0
        self._last_pose_valid_time = 0.0
        self._last_seen_time = 0.0
        self._rebase_pending = False

    def update(
        self,
        *,
        features,
        landmarks,
        candidate_scores,
        stable_gesture: str,
        current_level: float | None,
        current_muted: bool,
        now: float,
        allow_mute_toggle: bool = True,
        palm_roll_deg: float | None = None,
    ) -> VolumeGestureUpdate:
        self._muted = bool(current_muted)
        if current_level is not None:
            self._level = current_level

        trigger_mute = False
        if stable_gesture == 'mute':
            if (
                allow_mute_toggle
                and not self._mute_gesture_latched
                and now - self._last_mute_toggle_time >= self.mute_cooldown_seconds
            ):
                self._last_mute_toggle_time = now
                self._mute_gesture_latched = True
                trigger_mute = True
                self._message = 'mute toggle'
                self._status = 'muted' if not current_muted else 'unmuted'
        else:
            self._mute_gesture_latched = False

        upright_ok = True
        if palm_roll_deg is not None:
            upright_ok = 23.0 <= float(palm_roll_deg) <= 157.0

        if features is None or landmarks is None or not upright_ok:
            if self._active and now - self._last_seen_time <= self.no_hand_grace_seconds and upright_ok:
                return self._snapshot(trigger_mute_toggle=trigger_mute)
            self._deactivate('idle')
            return self._snapshot(trigger_mute_toggle=trigger_mute)

        self._last_seen_time = now
        pose_score = float((candidate_scores or {}).get('volume_pose', 0.0))
        pose_valid = self._is_volume_ready_pose(features, pose_score, stable_gesture, active=self._active)
        if pose_valid:
            self._last_pose_valid_time = now
        control_y = float((landmarks[8][1] + landmarks[12][1]) * 0.5)
        if self._rebase_pending:
            self._rebase_pending = False
            self._anchor_y = control_y
        pinky_hold = self._active and self._is_pinky_hold_pose(features)

        if pinky_hold and not self._pinky_hold_latched:
            self._pinky_hold_latched = True
            self._lock_until = max(self._lock_until, now + self.hold_seconds)
            self._anchor_y = control_y
            self._anchor_level = self._level if self._level is not None else current_level
            self._message = 'hold'
            self._status = 'holding'
        elif not pinky_hold:
            self._pinky_hold_latched = False

        lock_active = self._active and now < self._lock_until
        if self._active and self._lock_until and not lock_active:
            self._lock_until = 0.0
            self._anchor_y = control_y
            self._anchor_level = self._level if self._level is not None else current_level

        pose_within_grace = self._active and not pose_valid and (now - self._last_pose_valid_time) <= self.pose_grace_seconds

        if pose_valid:
            self._confirm_frames += 1
            self._release_frames = 0
            if not self._active:
                self._activation_ready_frames += 1
        elif pose_within_grace or lock_active:
            self._confirm_frames = max(self._confirm_frames, 0)
            self._activation_ready_frames = max(self._activation_ready_frames, 0)
            self._release_frames = 0
        else:
            self._confirm_frames = 0
            self._activation_ready_frames = 0
            if self._active:
                self._release_frames += 1

        if pose_valid and not self._active and self._confirm_frames >= self.confirm_frames and self._activation_ready_frames >= self.confirm_frames:
            self._active = True
            self._anchor_y = control_y
            self._anchor_level = self._level if self._level is not None else 0.5
            self._message = 'ready'
            self._status = 'active'
        elif self._active and lock_active:
            self._message = 'hold'
            self._status = 'holding'
        elif self._active and pose_within_grace:
            self._message = 'ready'
            self._status = 'tracking'
        elif self._active and pose_valid:
            if self._anchor_y is not None and self._anchor_level is not None:
                travel_span = max(0.082, min(0.175, float(features.palm_scale) * 0.60))
                delta = self._anchor_y - control_y
                deadzone = travel_span * self.deadzone_fraction
                if abs(delta) <= deadzone:
                    target_level = self._anchor_level
                else:
                    adjusted_delta = delta - deadzone if delta > 0.0 else delta + deadzone
                    raw_level = self._anchor_level + (adjusted_delta / max(travel_span - deadzone, 1e-6))
                    target_level = max(0.0, min(1.0, raw_level))
                if self._level is not None:
                    target_level = (1.0 - self.smoothing) * float(self._level) + self.smoothing * target_level
                self._level = target_level
                self._message = 'ready'
                self._status = 'changing'
        elif self._active and self._release_frames >= self.release_frames:
            self._deactivate('stopped')

        return self._snapshot(trigger_mute_toggle=trigger_mute)

    def _deactivate(self, status: str) -> None:
        self._active = False
        self._confirm_frames = 0
        self._release_frames = 0
        self._anchor_y = None
        self._anchor_level = self._level
        self._lock_until = 0.0
        self._pinky_hold_latched = False
        self._activation_ready_frames = 0
        self._last_pose_valid_time = 0.0
        self._last_seen_time = 0.0
        self._status = status
        if status != 'idle':
            self._message = status

    def _is_volume_ready_pose(self, features, pose_score: float, stable_gesture: str, *, active: bool) -> bool:
        open_scores = features.open_scores
        spread_states = getattr(features, 'spread_states', {})
        spread_distances = getattr(features, 'spread_ratios', {})
        together_strength = float(features.spread_together_strengths.get('index_middle', 0.0))
        apart_strength = float(features.spread_apart_strengths.get('index_middle', 0.0))
        spread_distance = float(spread_distances.get('index_middle', 1.0))
        disallowed_stable = {'open_hand', 'four', 'finger_apart', 'mute'}
        if not active and stable_gesture in disallowed_stable:
            return False
        if features.finger_count_open > 2:
            return False
        index_middle_close = (
            spread_states.get('index_middle') == 'together'
            or spread_distance <= 0.44
            or (spread_distance <= 0.48 and together_strength >= 0.30 and apart_strength <= 0.52)
        )
        structural_ready = (
            self._is_volume_primary_open(features, 'index')
            and self._is_volume_primary_open(features, 'middle')
            and self._is_folded(features, 'ring')
            and self._is_folded(features, 'pinky')
            and self._is_folded(features, 'thumb', allow_partial=True)
            and open_scores['index'] >= 0.56
            and open_scores['middle'] >= 0.56
            and open_scores['ring'] <= 0.70
            and open_scores['pinky'] <= 0.70
            and open_scores['thumb'] <= 0.70
            and index_middle_close
        )
        if not structural_ready:
            return False
        return active or pose_score >= 0.08 or stable_gesture in {'volume_pose', 'two', 'finger_together', 'neutral'}

    def _is_pinky_hold_pose(self, features) -> bool:
        open_scores = features.open_scores
        spread_states = getattr(features, 'spread_states', {})
        together_strength = float(features.spread_together_strengths.get('index_middle', 0.0))
        return (
            features.finger_count_open == 3
            and self._is_volume_primary_open(features, 'index')
            and self._is_volume_primary_open(features, 'middle')
            and self._is_folded(features, 'ring')
            and self._is_folded(features, 'thumb', allow_partial=True)
            and self._is_fully_open(features, 'pinky')
            and open_scores['index'] >= 0.56
            and open_scores['middle'] >= 0.56
            and open_scores['ring'] <= 0.56
            and open_scores['thumb'] <= 0.58
            and open_scores['pinky'] >= 0.72
            and spread_states.get('index_middle') == 'together'
            and together_strength >= 0.78
        )

    def _fine_state(self, features, finger_name: str) -> str | None:
        fine_states = getattr(features, 'fine_states', None)
        if fine_states is None:
            return None
        return fine_states.get(finger_name)

    def _is_fully_open(self, features, finger_name: str) -> bool:
        fine_state = self._fine_state(features, finger_name)
        if fine_state is not None:
            return fine_state == 'fully_open'
        return features.states[finger_name] == 'open'

    def _is_volume_primary_open(self, features, finger_name: str) -> bool:
        fine_state = self._fine_state(features, finger_name)
        openness = float(features.open_scores.get(finger_name, 0.0))
        if fine_state is not None:
            return fine_state == 'fully_open' or (fine_state == 'partially_curled' and openness >= 0.56)
        return features.states[finger_name] == 'open' and openness >= 0.56

    def _is_folded(self, features, finger_name: str, *, allow_partial: bool = False) -> bool:
        fine_state = self._fine_state(features, finger_name)
        if fine_state is not None:
            allowed = {'mostly_curled', 'closed'}
            if allow_partial:
                allowed.add('partially_curled')
            return fine_state in allowed
        return features.states[finger_name] != 'open'

    def _snapshot(self, *, trigger_mute_toggle: bool) -> VolumeGestureUpdate:
        return VolumeGestureUpdate(
            active=self._active,
            level=self._level,
            muted=self._muted,
            message=self._message,
            status=self._status,
            overlay_visible=self._active,
            trigger_mute_toggle=trigger_mute_toggle,
        )
