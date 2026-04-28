"""Standalone live tester for custom gestures.

    python tools/custom_gestures/test.py [--execute]

Opens the webcam, detects a hand, feeds the normalized landmarks through
the GestureClassifier, and shows the top match live. Use --execute to
actually fire the bound action (keystrokes etc.); without it the tester is
read-only, good for validating recognition thresholds before letting the
gesture touch anything.

Activation model (with --execute):
  - The gesture must be matched continuously for HOLD_DURATION seconds
    before its action fires. A progress bar shows the hold filling up.
  - After firing, the same gesture is suppressed by the action's cooldown
    (default 2 seconds; per-action overridable via payload['cooldown_s']).
  - Switching to a different gesture resets the hold timer.

Zero integration with the main app — this is a standalone validator.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import cv2  # noqa: E402
import mediapipe as mp  # noqa: E402

from hgr.custom_gestures.action import describe, fire_once  # noqa: E402
from hgr.custom_gestures.classifier import GestureClassifier  # noqa: E402
from hgr.custom_gestures.description import (  # noqa: E402
    live_signature,
    short_curl_label,
    short_spread_label,
)
from hgr.custom_gestures.recorder import (  # noqa: E402
    landmarks_from_mediapipe,
    normalize_landmarks,
)
from hgr.custom_gestures.registry import GestureRegistry  # noqa: E402


_WINDOW_TITLE = "Custom Gesture Tester — ESC to quit"
_DEFAULT_HOLD_SECONDS = 1.0
# Brief no-match windows happen all the time (MediaPipe drops a frame, the
# pose dips a hair below threshold, etc.). If we reset the hold timer
# every time, the user can almost never accumulate a full hold. Tolerate
# up to this many seconds of "no match" before resetting.
_DEFAULT_GRACE_SECONDS = 0.2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Live-test custom gestures")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually fire bound actions on match (default: show only)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.88,
        help="Match-score threshold, 0..1 (default 0.88). Raise for stricter, lower for more forgiving.",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=_DEFAULT_HOLD_SECONDS,
        help=f"Continuous-match seconds required before the action fires (default {_DEFAULT_HOLD_SECONDS}).",
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=_DEFAULT_GRACE_SECONDS,
        help=f"Seconds of brief 'no match' tolerated mid-hold before resetting (default {_DEFAULT_GRACE_SECONDS}).",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=None,
        help="Confidence margin: best gesture must beat second-best score by this much (default 0.05).",
    )
    args = parser.parse_args(argv)
    hold_duration = max(0.0, float(args.hold))
    grace_duration = max(0.0, float(args.grace))

    registry = GestureRegistry()
    registry.load()
    gestures = registry.list()
    if not gestures:
        print(f"[test] no gestures in {registry.path}. Run train.py first.")
        return 1
    print(f"[test] loaded {len(gestures)} gesture(s) from {registry.path}")
    for g in gestures:
        print(f"   - {g.name}  ->  {describe(g.action)}  ({len(g.samples)} samples)")
    print(f"[test] hold-to-activate: {hold_duration:.1f}s; execute={args.execute}")

    cls_kwargs = {"threshold": args.threshold}
    if args.margin is not None:
        cls_kwargs["confidence_margin"] = float(args.margin)
    classifier = GestureClassifier(registry, **cls_kwargs)
    classifier.reload()

    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"[test] could not open camera {args.camera}")
        return 1

    hands = mp.solutions.hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    drawer = mp.solutions.drawing_utils
    hand_style = mp.solutions.drawing_styles.get_default_hand_landmarks_style()
    conn_style = mp.solutions.drawing_styles.get_default_hand_connections_style()

    # Hold-to-activate state.
    hold_name: Optional[str] = None
    hold_started_at: float = 0.0
    last_match_at: float = 0.0  # for grace-window reset
    fired_for_current_hold: bool = False
    hold_max_progress: float = 0.0  # diagnostic: peak hold reached this cycle

    # Last-fired display state.
    last_fired_name: Optional[str] = None
    last_fired_at: float = 0.0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.02)
                continue
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = hands.process(rgb)

            now = time.monotonic()
            label = "no hand"
            color = (180, 180, 180)
            hold_progress = 0.0
            match = None
            sig: dict = {}
            feats = None
            if result.multi_hand_landmarks:
                drawer.draw_landmarks(
                    frame,
                    result.multi_hand_landmarks[0],
                    mp.solutions.hands.HAND_CONNECTIONS,
                    hand_style,
                    conn_style,
                )
                lm = landmarks_from_mediapipe(
                    result.multi_hand_landmarks[0].landmark
                )
                # Compute the full feature vector once so we can both
                # classify AND show what curl/spread categories the system
                # currently perceives.
                feats = normalize_landmarks(lm)
                sig = live_signature(feats)
                # Pass the currently held gesture so hysteresis can keep
                # it recognized through small score dips at the boundary.
                match = classifier.classify_raw(feats, sticky_name=hold_name)
                if match is not None:
                    runner_up_str = (
                        f"  vs '{match.runner_up_name}' {match.runner_up_score:.3f}"
                        if match.runner_up_name
                        else ""
                    )
                    label = (
                        f"{match.gesture.name}  score={match.score:.3f}"
                        f"{runner_up_str}  [{describe(match.gesture.action)}]"
                    )
                    color = (40, 220, 40)
                    last_match_at = now

                    # Hold gating: same gesture must persist for hold_duration.
                    if hold_name != match.gesture.name:
                        if hold_name is not None and hold_max_progress > 0.05:
                            print(
                                f"[hold] reset: was '{hold_name}' "
                                f"({hold_max_progress * hold_duration:.2f}s), "
                                f"now '{match.gesture.name}'"
                            )
                        else:
                            print(f"[hold] start: '{match.gesture.name}'")
                        hold_name = match.gesture.name
                        hold_started_at = now
                        fired_for_current_hold = False
                        hold_max_progress = 0.0

                    held_seconds = now - hold_started_at
                    hold_progress = (
                        min(1.0, held_seconds / hold_duration)
                        if hold_duration > 0
                        else 1.0
                    )
                    hold_max_progress = max(hold_max_progress, hold_progress)

                    if (
                        args.execute
                        and not fired_for_current_hold
                        and held_seconds >= hold_duration
                    ):
                        print(
                            f"[hold] complete: '{match.gesture.name}' "
                            f"after {held_seconds:.2f}s — calling fire_once"
                        )
                        fired = fire_once(match.gesture.name, match.gesture.action)
                        fired_for_current_hold = True
                        if fired:
                            last_fired_name = match.gesture.name
                            last_fired_at = now
                            print(
                                f"[test] fired {match.gesture.name} "
                                f"({describe(match.gesture.action)})"
                            )
                        else:
                            print(
                                f"[test] fire_once returned False for "
                                f"'{match.gesture.name}' "
                                f"({describe(match.gesture.action)}) — "
                                "either cooldown or executor failed"
                            )
                else:
                    # Hand visible but no match THIS frame. Don't immediately
                    # reset the hold — give a small grace window for brief
                    # MediaPipe / threshold flicker.
                    label = "hand present, no match"
                    color = (40, 160, 220)
                    if (
                        hold_name is not None
                        and now - last_match_at >= grace_duration
                    ):
                        if hold_max_progress > 0.05:
                            print(
                                f"[hold] interrupted: '{hold_name}' dropped to "
                                f"no-match at {hold_max_progress * hold_duration:.2f}s"
                            )
                        hold_name = None
                        fired_for_current_hold = False
                        hold_max_progress = 0.0
            else:
                # No hand visible at all — also subject to the grace window.
                if (
                    hold_name is not None
                    and now - last_match_at >= grace_duration
                ):
                    if hold_max_progress > 0.05:
                        print(
                            f"[hold] interrupted: '{hold_name}' dropped to "
                            f"no-hand at {hold_max_progress * hold_duration:.2f}s"
                        )
                    hold_name = None
                    fired_for_current_hold = False
                    hold_max_progress = 0.0

            # Status text.
            cv2.putText(frame, label, (12, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)

            # Hold progress bar.
            if match is not None and hold_duration > 0 and not fired_for_current_hold:
                bar_x, bar_y, bar_w, bar_h = 12, 42, 240, 14
                cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h),
                              (60, 60, 60), -1)
                fill_w = int(bar_w * hold_progress)
                bar_color = (40, 220, 40) if hold_progress >= 1.0 else (40, 200, 220)
                cv2.rectangle(frame, (bar_x, bar_y),
                              (bar_x + fill_w, bar_y + bar_h),
                              bar_color, -1)
                cv2.putText(frame,
                            f"hold {hold_progress * hold_duration:.1f}s / {hold_duration:.1f}s",
                            (bar_x + 4, bar_y + bar_h - 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)

            # Recent-fire flash.
            if last_fired_name and now - last_fired_at < 1.5:
                cv2.putText(frame, f"FIRED: {last_fired_name}",
                            (12, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (0, 0, 255), 2, cv2.LINE_AA)

            # Live finger-state overlay (top-right). Shows curl class +
            # raw wrist-to-tip distance per finger, plus spread. The raw
            # distances let you calibrate per-finger thresholds in
            # recorder.py if a pose isn't bucketing the way you intend.
            if sig and feats is not None:
                ext_dists = feats[66:71]
                frame_h, frame_w = frame.shape[:2]
                box_x = frame_w - 240
                box_y = 8
                line_h = 18
                cv2.rectangle(frame,
                              (box_x - 4, box_y - 2),
                              (frame_w - 4, box_y + line_h * 7 + 6),
                              (0, 0, 0), -1)
                cv2.putText(frame, "Live finger state:",
                            (box_x, box_y + 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (220, 220, 220), 1, cv2.LINE_AA)
                fingers = [
                    ("Thumb", sig["thumb_curl"], float(ext_dists[0])),
                    ("Index", sig["index_curl"], float(ext_dists[1])),
                    ("Mid", sig["middle_curl"], float(ext_dists[2])),
                    ("Ring", sig["ring_curl"], float(ext_dists[3])),
                    ("Pinky", sig["pinky_curl"], float(ext_dists[4])),
                ]
                for i, (fname, c, dist) in enumerate(fingers):
                    if c == 0:
                        col = (60, 220, 60)
                    elif c == 1:
                        col = (60, 220, 180)
                    elif c == 2:
                        col = (60, 180, 220)
                    elif c == 3:
                        col = (140, 120, 220)
                    else:
                        col = (200, 80, 200)
                    cv2.putText(
                        frame,
                        f"{fname:<5} {short_curl_label(c)[:9]:<9} c{c} d={dist:.2f}",
                        (box_x, box_y + 12 + (i + 1) * line_h),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, col, 1, cv2.LINE_AA,
                    )
                spread_c = sig["spread"]
                cv2.putText(
                    frame,
                    f"Spread {short_spread_label(spread_c)} ({spread_c})",
                    (box_x, box_y + 12 + 6 * line_h),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                    (220, 220, 80), 1, cv2.LINE_AA,
                )

            cv2.imshow(_WINDOW_TITLE, frame)
            if (cv2.waitKey(1) & 0xFF) == 27:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        hands.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
