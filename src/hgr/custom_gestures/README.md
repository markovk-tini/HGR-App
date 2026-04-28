# Custom gestures

Self-contained module for letting users define their own hand gestures and
bind them to actions (keystrokes, hotkey combos, text snippets, URLs, shell
commands).

**Status: standalone. Not wired into the running app yet** — the trainer
and tester run as separate CLI scripts. Integration with the live gesture
pipeline is a one-line hook that can land in a follow-up PR without
reworking any of this module.

## Package layout

```
src/hgr/custom_gestures/
├── registry.py     — dataclasses + JSON persistence
├── recorder.py     — landmark capture + normalization
├── classifier.py   — KNN cosine-similarity matcher
├── action.py       — Action kinds + Windows SendInput executors
└── README.md       — (this file)

tools/custom_gestures/
├── train.py        — CLI to record a gesture + pick an action + save
└── test.py         — CLI live webcam validator + optional action firing

tests/test_custom_gestures.py   — 18 unit tests, no hardware required
```

## Storage

Gestures live in JSON at `~/.hgr_app/custom_gestures.json` by default.
Override via the `HGR_CUSTOM_GESTURES_PATH` environment variable (tests and
alternate profiles use this).

Schema:
```json
{
  "schema_version": 1,
  "gestures": [
    {
      "name": "open_terminal",
      "description": "Fist pose opens a terminal",
      "created_at": "2026-04-23T15:10:00+00:00",
      "action": {
        "kind": "hotkey",
        "payload": {"keys": ["ctrl", "alt", "t"]}
      },
      "samples": [
        {"features": [/* 63 floats */]},
        ...
      ]
    }
  ]
}
```

## Action kinds

| kind | payload | example |
|---|---|---|
| `keystroke` | `{"key": "enter"}` | press Enter |
| `hotkey` | `{"keys": ["ctrl", "shift", "t"]}` | Ctrl+Shift+T |
| `text` | `{"text": "my signature"}` | type a literal string |
| `open_url` | `{"url": "https://..."}` | open in default browser |
| `run_command` | `{"command": "..."}` | fire-and-forget shell command |
| `noop` | `{}` | placeholder / disabled gesture |

All payloads accept an optional `cooldown_s` (default 0.7s) to suppress
re-fires while the user continues holding the pose.

## Recognition

Each captured sample is a 63-dim feature vector: 21 MediaPipe hand
landmarks × 3 coordinates, translated so the wrist is at the origin and
scaled so the wrist→middle-finger-MCP distance is 1. **Rotation is NOT
normalized** — a thumbs-up pointing up and a thumbs-up pointing sideways
are different gestures, which matches user expectation.

The classifier stores every sample (not a per-gesture centroid) and
returns the single best match by cosine similarity against all stored
samples. Threshold defaults to `0.92` — raise for stricter matching, lower
for more forgiving. Identical re-presentations of a recorded pose
typically score > 0.97; unrelated poses drop below 0.85.

## How to record a gesture

```
python tools/custom_gestures/train.py
```

The CLI walks through:
1. Name + description.
2. Webcam window opens. Hold the pose; press SPACE to start capturing
   80 samples (~8 seconds — recording is paced so you have time to drift
   naturally). ESC to cancel.
3. Menu prompts for the action kind and its parameters.
4. Registry is written atomically.

**Important: don't hold perfectly still during capture.** Let your hand
drift naturally — a small wiggle, a slight tilt, a thumb-position shift —
the classifier learns from the real range of your pose. Synthetic
augmentation (rotation/jitter) approximates generic variations on top,
but your personal drift pattern only comes through if you actually
exhibit it during the recording.

Tune the sample count if needed:

```
python tools/custom_gestures/train.py --samples 120   # more variation, longer record
python tools/custom_gestures/train.py --samples 30    # quick, less variation
```

## How to test recognition

```
python tools/custom_gestures/test.py           # read-only (shows matches)
python tools/custom_gestures/test.py --execute # actually fire actions
```

The `--execute` form honors the per-gesture cooldown and will print what
fired to stdout.

## Integration into the running app (future work)

The pipeline currently lives behind a standalone tool because it needs
user review before touching noop_engine's hot path. When we wire it in,
the hook is small:

```python
# in noop_engine.GestureWorker somewhere near the main-hand landmark
# processing, after the existing built-in gesture classifiers have had
# their turn:
match = self._custom_classifier.classify(main_hand_landmarks_21x3)
if match is not None:
    fire_once(match.gesture.name, match.gesture.action)
```

`fire_once` already handles cooldowns, so the hot path stays clean. The
classifier's `classify()` is O(N_samples × 63) per call — a few hundred
microseconds for a typical registry of a dozen gestures. Safe to run every
frame.

## Deliberate non-goals (yet)

- **Dynamic gestures** (swipes / timeseries). V1 is static-pose only. A
  future version can add a parallel DTW-based matcher alongside this one.
- **Live training during app use.** Training requires stopping the main
  camera pipeline to avoid double-opening `cv2.VideoCapture` on the same
  index. The standalone trainer sidesteps this.
- **Input-record-and-replay.** The initial brief asked about recording
  user clicks/keys. V1 uses explicit action kinds instead — more reliable
  (no fragile coordinate replays) and safer to audit. Adding a recorded-
  event kind later is straightforward: capture the event stream, store
  as `{"kind": "replay", "payload": {"events": [...]}}`, execute via
  SendInput when the gesture fires.
