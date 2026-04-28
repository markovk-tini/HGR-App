"""Standalone trainer: record a hand pose + an action, save to the custom-
gesture registry. Zero integration with the running app — this is a CLI
you launch manually:

    python tools/custom_gestures/train.py

Walks through:
  1. Pick a name / description for the gesture.
  2. Live webcam window — hold the pose while the trainer captures 15 stable
     samples (spacebar to start, ESC to cancel).
  3. Pick an action from a menu and enter its parameters.
  4. Confirmation + save.

Nothing in this script touches noop_engine or the live gesture pipeline.
To use a gesture in the running app, you'll wire the classifier into the
pipeline in a separate step (see src/hgr/custom_gestures/README.md).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

# Allow running directly without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import cv2  # noqa: E402
import mediapipe as mp  # noqa: E402
import numpy as np  # noqa: E402

from hgr.custom_gestures.action import describe  # noqa: E402
from hgr.custom_gestures.classifier import GestureClassifier  # noqa: E402
from hgr.custom_gestures.description import format_gesture_summary  # noqa: E402
from hgr.custom_gestures.recorder import (  # noqa: E402
    GestureRecorder,
    augment_samples,
    landmarks_from_mediapipe,
)
from hgr.custom_gestures.registry import (  # noqa: E402
    Action,
    CustomGesture,
    GestureRegistry,
)


_TARGET_SAMPLES = 80
_STABILITY_FRAMES = 3  # consecutive frames of hand present before capture starts
# Skip this many frames between successive captures so the recording
# stretches over ~8 seconds at 30fps with the default 80 captures. Gives
# the user time to naturally drift through small variations of the pose,
# which the classifier then learns directly instead of having to
# approximate via augmentation.
_CAPTURE_INTERVAL_FRAMES = 3
_WINDOW_TITLE = "Custom Gesture Trainer — SPACE start, ESC cancel"


def _prompt(label: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"{label}{suffix}: ").strip()
        if val:
            return val
        if default:
            return default


def _prompt_action() -> Action:
    print()
    print("What should this gesture do?")
    print("  1) Press a single key (e.g. enter, space, f5)")
    print("  2) Press a hotkey combo (e.g. ctrl+shift+t)")
    print("  3) Type a text snippet")
    print("  4) Open a URL in the default browser")
    print("  5) Run a shell command")
    print("  6) No action (placeholder)")
    choice = _prompt("Choice (1-6)", default="1")
    if choice == "1":
        key = _prompt("Key name (e.g. enter, f5, a)", default="enter")
        return Action(kind="keystroke", payload={"key": key})
    if choice == "2":
        combo = _prompt("Keys separated by +  (e.g. ctrl+shift+t)")
        keys = [k.strip() for k in combo.split("+") if k.strip()]
        return Action(kind="hotkey", payload={"keys": keys})
    if choice == "3":
        text = _prompt("Text to type")
        return Action(kind="text", payload={"text": text})
    if choice == "4":
        url = _prompt("URL")
        return Action(kind="open_url", payload={"url": url})
    if choice == "5":
        cmd = _prompt("Shell command")
        return Action(kind="run_command", payload={"command": cmd})
    return Action(kind="noop")


def _capture_loop(
    camera_index: int,
    recorder: GestureRecorder,
) -> bool:
    """Open the webcam, detect a hand, and fill the recorder with stable
    samples while the user holds the pose. Returns True on success."""
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"[train] could not open camera index {camera_index}")
        return False

    hands = mp.solutions.hands.Hands(
        static_image_mode=False,
        max_num_hands=1,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    drawer = mp.solutions.drawing_utils
    hand_style = mp.solutions.drawing_styles.get_default_hand_landmarks_style()
    conn_style = mp.solutions.drawing_styles.get_default_hand_connections_style()

    stable_frames = 0
    frames_since_capture = 0
    started = False
    cancelled = False
    print()
    print("Hold the gesture in front of the camera. While capturing,")
    print("LET YOUR HAND DRIFT NATURALLY through small variations —")
    print("a slight wiggle, tilt, or thumb shift gives the classifier")
    print("real examples of your natural range. Don't try to be perfectly still.")
    print()
    print("Press SPACE to begin capture; ESC to cancel.")
    last_print = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[train] camera read failed; retrying...")
                time.sleep(0.05)
                continue
            # Mirror for natural preview.
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = hands.process(rgb)

            hand_present = bool(result.multi_hand_landmarks)
            if hand_present:
                drawer.draw_landmarks(
                    frame,
                    result.multi_hand_landmarks[0],
                    mp.solutions.hands.HAND_CONNECTIONS,
                    hand_style,
                    conn_style,
                )
                stable_frames = min(stable_frames + 1, _STABILITY_FRAMES * 2)
            else:
                stable_frames = 0

            if (
                started
                and hand_present
                and stable_frames >= _STABILITY_FRAMES
                and frames_since_capture >= _CAPTURE_INTERVAL_FRAMES
            ):
                lm = landmarks_from_mediapipe(
                    result.multi_hand_landmarks[0].landmark
                )
                recorder.capture(lm)
                frames_since_capture = 0
            else:
                frames_since_capture += 1

            status = (
                f"Captured {recorder.count}/{recorder.target}"
                if started
                else ("Hand detected — SPACE to start" if hand_present else "Show hand")
            )
            cv2.putText(
                frame, status, (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (20, 220, 20) if started else (230, 230, 230),
                2, cv2.LINE_AA,
            )
            cv2.imshow(_WINDOW_TITLE, frame)

            now = time.monotonic()
            if started and now - last_print > 0.2:
                print(f"\r[train] samples: {recorder.count}/{recorder.target}", end="", flush=True)
                last_print = now

            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                cancelled = True
                break
            if key == 32 and not started and hand_present:  # SPACE
                started = True

            if recorder.is_complete():
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        hands.close()

    if cancelled or recorder.count == 0:
        print("\n[train] capture cancelled")
        return False
    print(f"\n[train] captured {recorder.count} samples")
    return True


def _check_conflicts(
    registry: GestureRegistry,
    candidate: CustomGesture,
    *,
    threshold: float = 0.85,
) -> None:
    """Run each sample of the just-added gesture through a classifier built
    from every OTHER gesture in the registry. If any sample scores above
    threshold for an existing gesture, warn the user — the new pose is
    similar enough to an existing one that they may confuse each other in
    live use.

    Threshold here is intentionally a touch lower than the live classifier
    threshold (0.85 vs 0.88) because we want to catch near-misses, not
    only certain-overlap cases.
    """
    others = [g for g in registry.list() if g.name != candidate.name]
    if not others:
        return

    # No registry argument — pass the others list explicitly so we don't
    # pollute the live registry while checking.
    clf = GestureClassifier(
        gestures=others,
        threshold=threshold,
        confidence_margin=0.0,  # we want raw "any nearby gesture" hits
    )
    clf.reload()

    hits: dict = {}  # other_name -> (count, max_score)
    for sample in candidate.samples:
        match = clf.classify_raw(sample.features)
        if match is None:
            continue
        prev = hits.get(match.gesture.name, (0, 0.0))
        hits[match.gesture.name] = (prev[0] + 1, max(prev[1], match.score))

    if not hits:
        print("[train] no conflicts detected with existing gestures")
        return

    total = len(candidate.samples)
    print()
    print("[train] WARNING: conflict-detection found similar existing gestures:")
    for name, (count, peak) in sorted(hits.items(), key=lambda kv: -kv[1][1]):
        pct = (count / total) * 100.0 if total else 0.0
        print(
            f"   {pct:5.1f}% of {candidate.name!r}'s samples "
            f"score above {threshold:.2f} for {name!r} (peak {peak:.3f})"
        )
    print(
        "[train] These may confuse each other in live use. Consider re-recording "
        "with a more distinct pose, or rely on the confidence-margin to "
        "suppress firings when the two are too close."
    )


def _reaugment_existing(registry: GestureRegistry, name: str) -> int:
    """Apply the current augmentation policy to an already-recorded gesture.
    Lets the user widen tolerance on a saved gesture without re-recording."""
    registry.load()
    existing = registry.get(name)
    if existing is None:
        print(f"[train] no gesture named {name!r} in {registry.path}")
        return 1
    # Take only the non-augmented originals: every 13th sample is an original
    # when produced by the current pipeline. For back-compat we treat ALL
    # stored samples as "originals" and re-augment, which simply produces more
    # samples — fine for matching, just slightly larger.
    originals = list(existing.samples)
    augmented = augment_samples(originals)
    registry.add(
        name=existing.name,
        samples=augmented,
        action=existing.action,
        description=existing.description,
        overwrite=True,
    )
    registry.save()
    print(
        f"[train] re-augmented {existing.name!r}: "
        f"{len(originals)} -> {len(augmented)} samples"
    )
    refreshed = registry.get(existing.name)
    if refreshed is not None:
        print()
        print(format_gesture_summary(refreshed))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Record a custom gesture + action")
    parser.add_argument("--camera", type=int, default=0, help="Camera device index (default 0)")
    parser.add_argument("--name", type=str, default=None, help="Gesture name (else prompt)")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing gesture with the same name")
    parser.add_argument(
        "--no-augment",
        action="store_true",
        help="Skip rotational augmentation (stores only the raw captured samples)",
    )
    parser.add_argument(
        "--reaugment",
        metavar="NAME",
        default=None,
        help="Apply augmentation to an already-saved gesture (no new recording)",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=_TARGET_SAMPLES,
        help=f"How many real captures to record (default {_TARGET_SAMPLES}). "
        "More = better tolerance for your natural pose drift, longer recording.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print summaries of every saved gesture and exit (no recording).",
    )
    parser.add_argument(
        "--show",
        metavar="NAME",
        default=None,
        help="Print the summary for a single saved gesture and exit.",
    )
    args = parser.parse_args(argv)

    if args.list or args.show:
        registry = GestureRegistry()
        registry.load()
        if args.show:
            target = registry.get(args.show)
            if target is None:
                print(f"[train] no gesture named {args.show!r} in {registry.path}")
                return 1
            print(format_gesture_summary(target))
            return 0
        gestures = registry.list()
        if not gestures:
            print(f"[train] no gestures saved in {registry.path}")
            return 0
        for i, g in enumerate(gestures):
            if i > 0:
                print()
            print(format_gesture_summary(g))
        return 0

    if args.reaugment:
        return _reaugment_existing(GestureRegistry(), args.reaugment)

    registry = GestureRegistry()
    registry.load()

    print("=== Custom Gesture Trainer ===")
    print(f"Registry: {registry.path}")
    existing = [g.name for g in registry.list()]
    if existing:
        print(f"Existing gestures: {', '.join(existing)}")

    name = args.name or _prompt("Gesture name (short, no spaces)")
    if name in existing and not args.overwrite:
        answer = _prompt(
            f"Gesture {name!r} already exists. Overwrite? (y/N)",
            default="n",
        )
        if answer.lower() not in {"y", "yes"}:
            print("[train] aborted")
            return 1

    description = _prompt("Short description (optional, blank = skip)", default="")

    recorder = GestureRecorder(target_samples=max(5, int(args.samples)))
    if not _capture_loop(args.camera, recorder):
        return 1

    action = _prompt_action()
    print(f"[train] action: {describe(action)}")

    raw_samples = recorder.finalize()
    final_samples = raw_samples if args.no_augment else augment_samples(raw_samples)
    if not args.no_augment:
        print(
            f"[train] augmented {len(raw_samples)} captures "
            f"-> {len(final_samples)} samples for rotation/tilt tolerance"
        )
    try:
        gesture = registry.add(
            name=name,
            samples=final_samples,
            action=action,
            description=description,
            overwrite=True if name in existing else False,
        )
    except ValueError as exc:
        print(f"[train] failed to add: {exc}")
        return 1

    registry.save()
    print()
    print(f"[train] saved gesture {gesture.name!r} to {registry.path}")
    print(f"[train] {len(gesture.samples)} samples, action = {describe(gesture.action)}")

    _check_conflicts(registry, gesture)

    # Print a human-readable how-to-do-it summary derived from the
    # categorical curl/spread features. Lets the user verify the recorded
    # pose and gives them a written reference for later.
    print()
    print(format_gesture_summary(gesture))

    print()
    print("Next: run  python tools/custom_gestures/test.py  to validate recognition.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
