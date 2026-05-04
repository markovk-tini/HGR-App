"""Categorical signatures of Touchless's built-in static gestures.

When a user records a custom gesture, we want to warn them if the pose
they recorded matches a built-in (e.g. they recorded a "two" pose for
their custom action — the built-in 'two' will fire alongside their
custom action and they probably didn't intend that).

This is a static lookup table because the built-in classifier scores
poses via dedicated feature extractors, not stored landmark samples —
so there's nothing to compare against directly. Instead we maintain
the well-known categorical signatures here and check the user's
recording against them at save-time.

Each profile lists per-finger curl classes (0=extended..4=closed) and
a spread class (0=tight..3=wide). Use `tuple-of-options` for any
finger/spread that can naturally vary across people while still
counting as the same pose.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


# Range type — any integer in the tuple is acceptable for a match.
_R = Tuple[int, ...]

# Curl-class buckets. Class 2 ("half curled") is the ambiguous zone —
# real recordings of an "extended" finger held at a slight angle, or a
# "closed" finger that isn't fully tucked, both land in class 2. Treat
# class 2 as a wildcard so the conflict-check matches what the user
# actually recorded, not just textbook poses.
_EXTENDED: _R = (0, 1, 2)
_CLOSED: _R = (2, 3, 4)


# Handedness slot. A profile lists which hands fire the built-in
# action — "Left", "Right", or both. The conflict check rejects
# profiles whose `hands` doesn't include the recorded hand, so a user
# recording RIGHT-hand-three (which is a built-in for "focus Chrome")
# only triggers the Three warning if Right is in its hands set.
_Hands = Tuple[str, ...]


@dataclass(frozen=True)
class BuiltinProfile:
    name: str
    description: str
    thumb: _R
    index: _R
    middle: _R
    ring: _R
    pinky: _R
    spread: _R
    hands: _Hands  # ("Left",), ("Right",), or ("Left", "Right")

    def matches_signature(
        self,
        thumb: int, index: int, middle: int, ring: int, pinky: int,
        spread: int,
    ) -> bool:
        return (
            thumb in self.thumb
            and index in self.index
            and middle in self.middle
            and ring in self.ring
            and pinky in self.pinky
            and spread in self.spread
        )

    def applies_to(self, handedness: Optional[str]) -> bool:
        """True if this profile's built-in action fires on the given
        hand. None means hand isn't known — accept any profile (worst
        case: an extra warning, never a missed conflict)."""
        if handedness is None:
            return True
        return handedness in self.hands


# Curl-class wildcard — any value 0..4. Used when a profile is shape-
# defined by other fingers and the listed finger's position doesn't
# matter (e.g. the wheel/rock-on shape fires regardless of thumb).
_ANY: _R = (0, 1, 2, 3, 4)


# Curated list of the app's built-in static gestures. Each profile
# distills the recognizer's scoring formula down to a categorical
# signature so we can conflict-check a user's recording.
#
# IMPORTANT: the conflict check is HAND-AGNOSTIC (the 6-feature
# signature is just curl + spread, no left/right). Most built-ins
# have different actions per hand — descriptions list both so the
# user can see why their recording overlaps regardless of which hand
# they used.
#
# Order matters: first match wins. List from MOST-specific to
# LEAST-specific so distinctive shapes (Volume, Wheel, OK) win over
# the catch-all basic poses.
BUILTIN_PROFILES: Tuple[BuiltinProfile, ...] = (
    # --- Specific app gestures (must come before the basic 1/2/3/4) ---
    BuiltinProfile(
        name="Volume pose",
        description="index + middle held TIGHT together, others closed — right-hand: volume control",
        thumb=_CLOSED, index=_EXTENDED, middle=_EXTENDED, ring=_CLOSED, pinky=_CLOSED,
        spread=(0, 1),  # distinctly tight — wider V falls through to Two
        hands=("Right",),
    ),
    BuiltinProfile(
        name="Wheel pose (rock-on / Spider-Man)",
        description="index + pinky extended, middle + ring closed — right-hand: drawing / Spotify / Chrome wheel",
        thumb=_ANY,  # thumb-out and thumb-tucked variants both count
        index=_EXTENDED, middle=_CLOSED, ring=_CLOSED, pinky=_EXTENDED,
        spread=(0, 1, 2, 3),
        hands=("Right",),
    ),
    BuiltinProfile(
        name="OK sign",
        description="thumb + index pinched, middle/ring/pinky extended — right-hand: Spotify shuffle",
        thumb=_CLOSED, index=_CLOSED, middle=_EXTENDED, ring=_EXTENDED, pinky=_EXTENDED,
        spread=(1, 2, 3),
        hands=("Right",),
    ),
    BuiltinProfile(
        name="Mute (thumb + pinky out)",
        description="thumb + pinky extended, middle three closed — mutes audio",
        thumb=_EXTENDED, index=_CLOSED, middle=_CLOSED, ring=_CLOSED, pinky=_EXTENDED,
        spread=(0, 1, 2, 3),  # spread varies with thumb-pinky stretch
        hands=("Left", "Right"),
    ),
    # --- Basic count poses ---
    # Spread tuples are wide here because spread is shape-defined by
    # the EXTENDED fingers, and a single-extended-finger pose like
    # "one" can read spread class 2-3 (the extended index sits far
    # from the curled fingertip cluster). Restricting these to (0, 1)
    # was making real recordings slip past the conflict check.
    BuiltinProfile(
        name="One (pointing)",
        description="index extended, others closed — left-hand: voice listening / dictation",
        thumb=_CLOSED, index=_EXTENDED, middle=_CLOSED, ring=_CLOSED, pinky=_CLOSED,
        spread=(0, 1, 2, 3),
        hands=("Left",),
    ),
    BuiltinProfile(
        name="Two (peace / V)",
        description="index + middle extended, others closed — both hands wired (Spotify focus / dictation toggle)",
        thumb=_CLOSED, index=_EXTENDED, middle=_EXTENDED, ring=_CLOSED, pinky=_CLOSED,
        spread=(0, 1, 2, 3),
        hands=("Left", "Right"),
    ),
    BuiltinProfile(
        name="Three (left-hand: mouse mode)",
        description="index + middle + ring extended, thumb + pinky closed — left-hand mouse mode",
        thumb=_CLOSED, index=_EXTENDED, middle=_EXTENDED, ring=_EXTENDED, pinky=_CLOSED,
        spread=(0, 1, 2, 3),
        hands=("Left",),
    ),
    BuiltinProfile(
        name="Three (right-hand: focus Chrome)",
        description="index + middle + ring extended, thumb + pinky closed — right-hand focus/open Chrome",
        thumb=_CLOSED, index=_EXTENDED, middle=_EXTENDED, ring=_EXTENDED, pinky=_CLOSED,
        spread=(0, 1, 2, 3),
        hands=("Right",),
    ),
    BuiltinProfile(
        name="Four (left-hand: drawing toggle)",
        description="index + middle + ring + pinky extended, thumb closed — left-hand drawing mode toggle",
        thumb=_CLOSED, index=_EXTENDED, middle=_EXTENDED, ring=_EXTENDED, pinky=_EXTENDED,
        spread=(0, 1, 2, 3),
        hands=("Left",),
    ),
    BuiltinProfile(
        name="Fist (left-hand: cancel voice)",
        description="all fingers closed — left-hand cancels voice listening",
        thumb=_CLOSED, index=_CLOSED, middle=_CLOSED, ring=_CLOSED, pinky=_CLOSED,
        spread=(0, 1, 2, 3),
        hands=("Left",),
    ),
    BuiltinProfile(
        name="Fist (right-hand: play/pause)",
        description="all fingers closed — right-hand Spotify/YouTube play/pause",
        thumb=_CLOSED, index=_CLOSED, middle=_CLOSED, ring=_CLOSED, pinky=_CLOSED,
        spread=(0, 1, 2, 3),
        hands=("Right",),
    ),
)


def _mode_signature(samples: Iterable["object"]) -> Optional[Tuple[int, int, int, int, int, int]]:
    """Compute the most-common per-finger curl + spread classes across
    the supplied samples. Returns (thumb, index, middle, ring, pinky,
    spread) or None if no samples have categorical features."""
    from .registry import (
        _CURL_CLASS_FEATURE_LEN,
        _EXTENSION_FEATURE_LEN,
        _JOINT_ANGLE_FEATURE_LEN,
        _LANDMARK_FEATURE_LEN,
        _SPACING_FEATURE_LEN,
        _SPREAD_CLASS_FEATURE_LEN,
    )

    curl_offset = (
        _LANDMARK_FEATURE_LEN
        + _SPACING_FEATURE_LEN
        + _EXTENSION_FEATURE_LEN
        + _JOINT_ANGLE_FEATURE_LEN
    )
    spread_offset = curl_offset + _CURL_CLASS_FEATURE_LEN
    finger_buckets: List[List[int]] = [[], [], [], [], []]
    spread_bucket: List[int] = []
    for s in samples:
        feats = getattr(s, "features", None)
        if feats is None:
            continue
        if len(feats) < spread_offset + _SPREAD_CLASS_FEATURE_LEN:
            continue
        for i in range(5):
            finger_buckets[i].append(int(feats[curl_offset + i]))
        spread_bucket.append(int(feats[spread_offset]))
    if not spread_bucket:
        return None

    def _mode(values: List[int]) -> int:
        return Counter(values).most_common(1)[0][0] if values else 0

    return (
        _mode(finger_buckets[0]),
        _mode(finger_buckets[1]),
        _mode(finger_buckets[2]),
        _mode(finger_buckets[3]),
        _mode(finger_buckets[4]),
        _mode(spread_bucket),
    )


def find_matching_builtin(
    samples: Iterable["object"],
    handedness: Optional[str] = None,
) -> Optional[BuiltinProfile]:
    """If the supplied gesture samples' MODE signature matches a known
    built-in pose AND the built-in fires on the recorded hand, return
    that profile. None means no conflict for this hand.

    `handedness` should be "Left" or "Right" (MediaPipe's user-perspective
    label, already cv2-flipped in the pipeline). Pass None when the
    recording's hand isn't known — every shape-matching profile is
    accepted in that case."""
    sig = _mode_signature(samples)
    if sig is None:
        return None
    thumb, index, middle, ring, pinky, spread = sig
    matched: Optional[BuiltinProfile] = None
    for profile in BUILTIN_PROFILES:
        if not profile.matches_signature(thumb, index, middle, ring, pinky, spread):
            continue
        if not profile.applies_to(handedness):
            continue
        matched = profile
        break
    print(
        f"[builtin-conflict] hand={handedness or '?'} signature: "
        f"thumb={thumb} index={index} middle={middle} "
        f"ring={ring} pinky={pinky} spread={spread} "
        f"-> {matched.name if matched else 'NO MATCH'}"
    )
    return matched
