# Recent Work

Keep newest entries at the top.

Use this format after every meaningful coding session.

---
## 2026-04-20 - YouTube UIA wheel actions and fading no-captions prompt

### Requested task
- Stop YouTube wheel actions from visibly typing into Chrome’s address bar before the action fires, and make the `No captions available for this video` prompt appear in the bottom-middle blue box and fade away after about 3 seconds.

### Root cause or best current hypothesis
- The YouTube controller was still using a `javascript:` bookmarklet-style injection through Chrome’s omnibox for several wheel actions, so text visibly appeared in the address bar before the in-page action completed.
- The no-captions prompt was already routed through the shared info-hint overlay, but it was only shown briefly and that overlay mode hard-cut at the end instead of fading.

### Files changed
- `src/hgr/debug/youtube_controller.py`
- `src/hgr/gesture/ui/voice_status_overlay.py`
- `src/hgr/app/integration/noop_engine.py`
- `tests/test_youtube_controller.py`
- `tests/test_voice_status_overlay.py`

### What changed
- Reworked YouTube wheel button actions to avoid the omnibox injection path during normal use:
  - theater now uses the YouTube keyboard shortcut path directly
  - captions now prefers a Windows UI Automation button invoke and falls back to the `C` shortcut
  - like, dislike, and share now use a Windows UI Automation button invoke path instead of typing a `javascript:` action into Chrome
- Kept the no-captions detection on the worker side, but increased the overlay hold time to 3 seconds.
- Updated the shared info-hint overlay so it fades out instead of disappearing instantly once its hold time ends.

### What was explicitly preserved
- Existing YouTube wheel contents and routing were preserved.
- Existing skip-ad, fullscreen, mini-player, speed-up, and speed-down behavior were preserved.
- The no-captions prompt still uses the shared bottom-middle overlay system instead of introducing a separate YouTube-only window.

### Test wave
- targeted regression checks passed:
  - `tests.test_youtube_controller`
  - `tests.test_youtube_wheel`
  - `tests.test_voice_status_overlay`
- syntax verification passed for the changed modules with bytecode redirected to a temporary cache outside the repo tree

### Cleanup wave
- removed the temporary `Documents\\hgr_pycache` directory after compile verification
- kept the diff limited to YouTube action execution, the shared overlay fade behavior, focused tests, and this note

---
## 2026-04-20 - Volume-mode swipe isolation and low-FPS drift rejection

### Requested task
- Reduce accidental swipe detection from small low-FPS hand drift, and make volume control own interaction so Spotify/system volume gestures do not leak left/right swipe actions into app controls.

### Root cause or best current hypothesis
- The low-FPS swipe path had become permissive enough that a small horizontal drift could sometimes clear the dynamic swipe score floor.
- Volume handling ran before app routing, but `_handle_app_controls(...)` still received the same dynamic swipe label afterward, so swipes could leak into Chrome/Spotify/YouTube routes while the volume overlay was visible.

### Files changed
- `src/hgr/gesture/recognition/dynamic_recognizer.py`
- `src/hgr/app/integration/noop_engine.py`
- `tests/test_gesture_engine.py`
- `tests/test_low_fps_mode.py`

### What changed
- Added an extra low-FPS horizontal-commit gate so small net drift no longer looks like a committed swipe.
- Tightened the low-FPS signed-step gate slightly so a real directional move is still needed before a swipe is emitted.
- Made volume overlay / active volume mode short-circuit app routing for that frame, so wheels and media routers no longer see swipe dynamics while volume control owns the interaction.
- Added focused regression coverage for low-FPS drift rejection and worker-level volume-mode swipe isolation.

### What was explicitly preserved
- The 10-FPS test mode and `<18 FPS` low-FPS threshold remain unchanged.
- Volume tracking behavior itself was preserved; only post-volume app routing was isolated.
- Normal non-volume swipe routing was preserved.

### Test wave
- targeted regression checks passed:
  - `tests.test_low_fps_mode`
  - `tests.test_gesture_engine.GestureEngineTest.test_low_fps_swipe_right_detects_at_ten_fps_spacing`
  - `tests.test_gesture_engine.GestureEngineTest.test_low_fps_swipe_right_stays_low_for_small_horizontal_drift`
  - `tests.test_volume_gesture`
- syntax verification passed for the changed modules with bytecode redirected to a temporary cache outside the repo tree

### Cleanup wave
- removed the temporary `Documents\\hgr_pycache` directory after compile verification
- kept the diff limited to low-FPS swipe scoring, volume-mode gating, focused tests, and this note

---
## 2026-04-20 - Low-FPS threshold shift, 10 FPS test mode, and broader detector speedup

### Requested task
- Treat low FPS as below `18 FPS`, add a settings button that forces the app to run at `10 FPS` for testing, and make the low-FPS swipe path more tolerant around that band while also reducing some normal-path detector cost.

### Root cause or best current hypothesis
- The worker still auto-entered low-FPS mode at `<20 FPS`, which no longer matched the desired threshold.
- The settings UI only exposed a forced low-FPS-mode toggle, not a true frame-rate cap for testing how the app behaves around `10 FPS`.
- The normal path was still letting MediaPipe process full-size runtime frames, so the recent loop optimizations were helping mostly the low-FPS path instead of also reducing some normal-camera cost.

### Files changed
- `src/hgr/config/app_config.py`
- `src/hgr/app/ui/main_window.py`
- `src/hgr/app/integration/noop_engine.py`
- `src/hgr/gesture/recognition/dynamic_recognizer.py`
- `tests/test_low_fps_mode.py`
- `tests/test_gesture_engine.py`

### What changed
- Added a persisted `force_ten_fps_test_mode` setting and a second camera-panel button that forces the runtime down to `10 FPS` for testing.
- Moved auto low-FPS activation/recovery logic to an `18 FPS` threshold band and kept the same time-based hysteresis.
- Turned the old unused low-FPS timing fields into a real per-tick frame-rate gate so testing mode now throttles the live gesture loop instead of only flipping labels.
- Reduced detector processing size in the normal path too, so faster cameras can benefit from lower per-frame MediaPipe cost instead of only the low-FPS path getting the resize help.
- Tuned the low-FPS swipe recognizer to be more forgiving around `10 FPS` with slightly smaller windows and looser motion floors.

### What was explicitly preserved
- Existing gesture mappings, UI structure, and general camera settings flow were preserved.
- Low-FPS mode is still user-forcible manually, and auto low-FPS mode still engages/disengages by measured runtime FPS instead of camera-advertised FPS.
- Non-low-FPS gesture recognition logic outside the targeted detector processing width change was preserved.

### Test wave
- targeted regression checks passed:
  - `tests.test_low_fps_mode`
  - `tests.test_gesture_engine.GestureEngineTest.test_low_fps_swipe_right_detects_at_ten_fps_spacing`
  - `tests.test_hand_detector`
- syntax verification passed for the changed modules with bytecode redirected to a temporary cache outside the repo tree
- isolated unrelated failure observed when running the broader `tests.test_gesture_engine` class: existing `pose='two'` static-pose expectation still returns `neutral`

### Cleanup wave
- kept the diff limited to config, camera settings, worker loop timing, dynamic swipe tuning, focused tests, and this note

---
## 2026-04-19 - Low-FPS runtime frame downscale for actual loop throughput

### Requested task
- Explain why the app FPS can be far below the camera’s advertised FPS, and improve the loop so low-FPS laptop cameras do less work per frame instead of only relying on softer gesture thresholds.

### Root cause or best current hypothesis
- Camera capability is only the input side; the app’s real FPS is limited by the full synchronous loop: capture, hand tracking, recognition, overlay drawing, controller/status work, and UI frame emission.
- On slower hardware, the low-FPS path was still sometimes processing larger frames than intended when the camera driver ignored capture-size requests, so MediaPipe and rendering still paid for too many pixels.

### Files changed
- `src/hgr/gesture/tracking/detector.py`
- `src/hgr/app/integration/noop_engine.py`
- `tests/test_hand_detector.py`
- `tests/test_low_fps_mode.py`

### What changed
- Added detector-side processing downscale support so the hand tracker can run on a smaller copy of the frame while preserving normalized landmark coordinates.
- Added a runtime low-FPS frame-prep step in the worker so oversized camera frames are resized down before the expensive gesture/overlay pipeline runs.
- Wired the low-FPS detector profile to use a smaller processing width, giving the laptop path a real throughput optimization instead of only relaxed recognition thresholds.
- Added focused tests for detector input resizing and low-FPS runtime frame downscaling.

### What was explicitly preserved
- Normal runtime behavior outside the low-FPS path was preserved.
- Gesture mappings, UI wording, and general app routing were not changed.
- The low-FPS path still keeps the continuity/tolerance improvements from the previous pass.

### Test wave
- targeted regression checks: `tests.test_hand_detector` and `tests.test_low_fps_mode` pass under `unittest`
- syntax verification: changed modules compile successfully with a redirected bytecode cache

### Cleanup wave
- removed the temporary `Documents\\hgr_pycache` directory after compile verification
- kept the diff limited to low-FPS frame preparation, detector processing scale, focused tests, and this recent-work note

---
## 2026-04-19 - Low-FPS dynamic gesture stabilization for laptop webcams

### Requested task
- Assess whether dynamic gestures can work on a laptop webcam running effectively around `9-12 FPS`, and improve the live tracking/dynamic pipeline for that class of camera.

### Root cause or best current hypothesis
- The failure was not just low frame rate by itself. The detector was resetting immediately on a missed frame, so motion blur during a swipe caused the hand skeleton to disappear and the dynamic history to reset.
- The low-FPS profile was also still heavier than it needed to be for a blurred laptop webcam stream, and the dynamic swipe thresholds were still a bit strict for the reduced sample count.

### Files changed
- `src/hgr/gesture/tracking/detector.py`
- `src/hgr/gesture/recognition/dynamic_recognizer.py`
- `src/hgr/app/integration/noop_engine.py`
- `tests/test_hand_detector.py`

### What changed
- Added a short detector miss-tolerance bridge so the tracker can keep the last good hand for a brief moment instead of immediately dropping the skeleton on a single blurred/missed frame.
- Exposed detector `model_complexity` and miss-tolerance tuning, then switched the low-FPS worker path to a lighter detector profile with lower confidence thresholds and shorter holdover.
- Tweaked low-FPS capture tuning to request low-latency settings like smaller buffers, `MJPG` where supported, and a 30 FPS request before the reduced resolution is applied.
- Relaxed the low-FPS dynamic swipe thresholds slightly so horizontal swipes can still score at `9-12 FPS` without needing the same motion quality as the normal profile.
- Added a focused detector test covering brief missed-frame continuity.

### What was explicitly preserved
- Normal-FPS detector behavior was preserved for the non-low-FPS path.
- Existing gesture mappings were not changed.
- The app still falls back to neutral when confidence is too low; this patch only makes low-FPS continuity and detection less brittle.

### Test wave
- targeted regression checks: `tests.test_hand_detector`, `tests.test_low_fps_mode`, and `tests.test_dynamic_gestures` pass under `unittest`
- syntax verification: changed modules compile successfully with a redirected bytecode cache
- added focused coverage for brief missed-frame hand continuity in the detector

### Cleanup wave
- removed the temporary `Documents\\hgr_pycache` directory after compile verification
- kept the diff limited to the low-FPS detector/dynamic path, one focused test, and this recent-work note

---
## 2026-04-19 - YouTube wheel expansion and low-FPS auto-recovery

### Requested task
- Make YouTube speed-up and speed-down actions force the YouTube tab/window to the foreground even when it is running in the background.
- Add `Like`, `Dislike`, and `Share` options to the YouTube gesture wheel.
- Let auto-triggered low-FPS mode return to normal mode once FPS recovers.

### Root cause or best current hypothesis
- The YouTube wheel only exposed playback/display actions, and the speed actions still depended on the older hotkey flow without explicit direct-page helpers for social actions like like/dislike/share.
- Auto low-FPS mode entered on `<20 FPS for 4s` but only exited after `>=24 FPS for 10s`, which is too strict once the app has already reduced capture/processing load and smoothed FPS around the lower band.

### Files changed
- `src/hgr/debug/youtube_controller.py`
- `src/hgr/app/integration/noop_engine.py`
- `tests/test_youtube_controller.py`
- `tests/test_youtube_wheel.py`
- `tests/test_low_fps_mode.py`

### What changed
- Made YouTube speed actions explicitly activate the YouTube tab/window before sending the playback-speed shortcut, so wheel-based speed changes surface the background YouTube tab first.
- Added direct page actions for `Like`, `Dislike`, and `Share` in the YouTube controller and wired them into the YouTube wheel.
- Expanded the YouTube wheel item list to include `like`, `dislike`, and `share`.
- Relaxed auto-recovery so low-FPS mode can exit after sustained recovery above `20 FPS`, and added normal-capture retuning when the worker returns to non-low-FPS mode.

### What was explicitly preserved
- Existing YouTube focused-mode routing, skip-ad behavior, captions/theater direct-action path, and non-YouTube wheel behavior were preserved.
- Manual forced low-FPS mode in Settings still overrides the auto-toggle logic.

### Test wave
- targeted regression checks: `tests.test_youtube_controller`, `tests.test_youtube_wheel`, and `tests.test_low_fps_mode` pass under `unittest`
- syntax verification: changed modules compile successfully with a redirected bytecode cache
- added focused coverage for direct `Like` action success and auto low-FPS recovery

### Cleanup wave
- removed the temporary `Documents\\hgr_pycache` directory after compile verification
- kept the diff limited to the YouTube controller, YouTube wheel integration, low-FPS recovery logic, focused tests, and this recent-work note

---
## 2026-04-19 - YouTube captions prompt wording and theater-mode direct actions

### Requested task
- Fix the missing `No captions available for this video` prompt and make YouTube theater mode actually work when selected from focused mode.

### Root cause or best current hypothesis
- The captions path still depended on a post-hotkey OCR pass, which was too brittle to reliably detect YouTube’s unavailable-captions toast in the live app.
- Theater mode was still only sending YouTube’s `t` keyboard shortcut, so it silently failed whenever page focus was not on the video player itself.

### Files changed
- `src/hgr/debug/youtube_controller.py`
- `src/hgr/app/integration/noop_engine.py`
- `tests/test_youtube_controller.py`

### What changed
- Added a direct YouTube page-action path for captions and theater mode by focusing the active YouTube tab, injecting a small `javascript:` action through Chrome’s omnibox, and reading the result back through a temporary window-title marker.
- `toggle_captions()` now returns the exact message `No captions available for this video` when the page reports there are no caption tracks, and only falls back to the older hotkey/OCR path if the direct page action cannot confirm a result.
- `toggle_theater()` now prefers the direct page-action button click so it works even when the player does not currently own keyboard focus.
- Updated the wheel overlay trigger so any no-captions message containing that phrase shows the blue prompt with the full requested wording.

### What was explicitly preserved
- Existing YouTube mode routing, swipe/fist mappings, skip-ad behavior, and the trimmed wheel layout were preserved.
- The older hotkey paths for captions/theater remain as fallbacks rather than being removed outright.

### Test wave
- targeted regression checks: `tests.test_youtube_controller` and `tests.test_youtube_wheel` pass under `unittest`
- syntax verification: changed modules compile successfully with a redirected bytecode cache
- added focused coverage for direct captions no-track handling and theater direct-action success

### Cleanup wave
- removed the temporary `Documents\\hgr_pycache` directory after compile verification
- kept the follow-up diff limited to the YouTube controller, focused-mode wheel integration, tests, and this recent-work note

---
## 2026-04-19 - YouTube captions feedback prompt and focused-mode stability

### Requested task
- When the YouTube wheel selects captions on a video with no captions, show a small transparent blue prompt that says `No captions available`.
- Keep YouTube controls working reliably while YouTube focused mode is active.
- Remove `seek +-10s` from the YouTube gesture wheel.

### Root cause or best current hypothesis
- The captions action only sent the `C` hotkey and never checked the player feedback toast, so HGR had no way to distinguish a real captions toggle from YouTube reporting that captions were unavailable.
- Focused-mode health still depended on a visible-window title scan for `YouTube`, which is brittle when Chrome window/title state changes around wheel interactions.

### Files changed
- `src/hgr/debug/youtube_controller.py`
- `src/hgr/gesture/ui/voice_status_overlay.py`
- `src/hgr/app/integration/noop_engine.py`
- `tests/test_youtube_controller.py`
- `tests/test_youtube_wheel.py`

### What changed
- Added a recent-YouTube-window cache in the controller so focused-mode checks can stay attached to the last known Chrome YouTube window instead of dropping immediately on a transient title miss.
- Added a captions-feedback pass after the `C` shortcut: HGR now captures the player area, OCRs the YouTube feedback toast, and maps unavailable-caption feedback to the exact message `No captions available`.
- Added a reusable info-hint overlay mode and used it to show a transparent blue prompt when captions are unavailable.
- Removed `Back 10s` and `Forward 10s` from the YouTube wheel, leaving fullscreen, theater, mini player, captions, slower, and faster.
- Added focused tests for recent-window stability, captions-unavailable feedback, and the updated wheel contents.

### What was explicitly preserved
- Existing YouTube swipe and fist gesture mappings were not changed.
- Existing skip-ad template matching and Chrome/Spotify/voice flows were not changed.
- `Faster` and `Slower` still map to YouTube playback-speed shortcuts rather than seek behavior.

### Test wave
- targeted regression checks: `tests.test_youtube_controller` and `tests.test_youtube_wheel` pass under `unittest`
- direct helper verification: the wheel now reports `fullscreen`, `theater`, `mini_player`, `captions`, `speed_down`, `speed_up`
- syntax verification: changed modules compile successfully when bytecode is redirected to a temp cache outside the app tree

### Cleanup wave
- temp verification cache cleanup: removed the temporary `Documents\\hgr_pycache` directory after compile checks
- scope control: kept the diff limited to the YouTube controller, the shared overlay, the wheel executor, focused tests, and this recent-work entry

---
## 2026-04-19 - YouTube skip-ad background tab activation and window-state preservation

### Requested task
- Fix the `three apart` YouTube skip-ad gesture so it no longer restores maximized Chrome windows to normal size, and make skip-ad work when the YouTube tab/window is in the background.

### Root cause or best current hypothesis
- `YouTubeController._focus_youtube_window()` always called `ShowWindow(..., SW_RESTORE)`, which restores a maximized Chrome window down to its normal size before focusing it.
- Skip-ad and other YouTube page-key actions only worked when a visible Chrome window title already contained `YouTube`, so a background YouTube tab in Chrome could not be surfaced and targeted before the screen-match click.

### Files changed
- `src/hgr/debug/youtube_controller.py`
- `tests/test_youtube_controller.py`

### What changed
- Split window focusing into helpers that only restore a window when it is actually minimized, preserving maximized window state during skip-ad and other YouTube actions.
- Added background Chrome tab search for YouTube interactions: if no visible `YouTube`-titled window is already active, the controller can focus Chrome windows and cycle tabs until a YouTube tab is surfaced.
- Reused the same YouTube-tab activation path for page-key actions and skip-ad before the image-based skip-button click runs.
- Added focused tests for maximized-window preservation, minimized-window restore, and background-tab activation.

### What was explicitly preserved
- Existing YouTube gesture mappings and skip-button template matching were not changed.
- Chrome, Spotify, voice, drawing, and window-control gesture routing outside the targeted YouTube path were not changed.

### Test wave
- requested behavior: YouTube tab activation preserves maximize state and can surface background Chrome tabs before skip-ad
- neighboring regression checks: `tests.test_youtube_controller`, `tests.test_gesture_dictation`, and `tests.test_dictation` pass under `unittest`
- remaining uncertainty: fully hidden/non-visible Chrome windows are still outside the visible-window search path

### Cleanup wave
- dead code/log cleanup: removed an unused local from `skip_ad`
- comment/import cleanup: imports kept scoped to the controller and focused tests
- final diff scope check: limited to the YouTube controller, one focused test file, and this recent-work entry

---
## 2026-04-19 - Dictation replace-path refinement for grammar and interim text

### Requested task
- Review the current dictation path, improve the visible correction behavior, and outline a plan to improve captured-word accuracy.

### Root cause or best current hypothesis
- Live dictation and grammar correction both flow through `TextInputController.replace_text`, and that method previously removed the whole prior span before typing the replacement. That caused visible delete-and-retype behavior even when only a few characters changed.

### Files changed
- `src/hgr/debug/text_input_controller.py`
- `tests/test_text_input_controller.py`

### What changed
- Added `_compute_replace_edit(...)` to compute the shared prefix/suffix between the old and new dictated text.
- Updated `replace_text(...)` to preserve the unchanged suffix by moving the caret left, backspacing only the changed middle, inserting only the replacement middle, then restoring the caret.
- Added focused regression coverage for append-only edits, tail typo fixes, and grammar-style middle corrections with preserved suffixes.

### What was explicitly preserved
- Existing dictation backend selection, whisper-stream usage, grammar-corrector timing, and command-mode voice flows were not changed.
- `insert_text(...)` and `remove_text(...)` behavior stayed intact outside the new incremental replace path.

### Test wave
- requested behavior: grammar/interim replacements now compute an incremental edit instead of a whole-span wipe
- neighboring regression checks: `tests.test_dictation`, `tests.test_text_input_controller`, and `tests.test_gesture_dictation` all pass under `unittest`
- remaining uncertainty: true end-to-end caret behavior still depends on how external apps handle left/right/backspace input

### Cleanup wave
- dead code/log cleanup: no temporary debug prints added
- comment/import cleanup: helper kept local to `text_input_controller.py`; imports remain clean
- final diff scope check: limited to the text input controller, one focused test file, and this recent-work entry

## 2026-04-16 — 7 fixes Session 2 (sensitivity up, hgrclip split, save prompt hint, tutorial delay, YouTube check, close boundary, dual volume)

### Requested tasks
1. Increase pen/eraser option window cursor sensitivity (1.2 → 1.4)
2. "hgrclip" spoken as one word splits to "hgr clip" for file matching
3. Save-prompt overlay: mic AND "Where would you like to save this file?" appear together simultaneously
4. Tutorial voice step delay (5–10 min): adopt parent worker's pre-warmed VoiceCommandListener
5. Tutorial final step: verify YouTube is actually open in Chrome, not just check spoken words
6. "close notepad and codex" closing VS Code: word-boundary regex in close_named_window
7. Dual volume bars: when Spotify/Chrome audio active, show app-vol left + sys-vol right; palm left/right selects bar

### Root cause or best current hypothesis
- Sensitivity 1.2 felt too slow; 1.4 matches user preference.
- "hgrclip" is one token after lowercasing; `_normalize_file_token_text` lacked hgr-prefix split.
- Save prompt: `show_listening` status callback at "listening" state called `show_listening(self._save_prompt_text)` without `hint_text=` kwarg, overwriting the hint box with the raw text.
- Tutorial delay: `TutorialWindow` created its own `VoiceCommandListener()` which loads Whisper model on first use (5–10 min); parent main app's listener is pre-warmed. Fixing by adopting parent's listener.
- Tutorial YouTube: text check only passed if transcript literally contained "youtube" and "chrome"; replaced with delayed Chrome window title inspection.
- "codex" closing VS Code: `term in normalized_query` (substring) matched "code" inside "codex" → VS Code alias added to close_terms.
- Dual volume: new feature; pycaw `ISimpleAudioVolume` per-session volume; palm x drift from activation point selects bar.

### Files changed
- `src/hgr/app/ui/main_window.py`
- `src/hgr/debug/desktop_controller.py`
- `src/hgr/app/integration/noop_engine.py`
- `src/hgr/app/ui/tutorial_window.py`
- `src/hgr/debug/chrome_controller.py`
- `src/hgr/debug/volume_controller.py`
- `src/hgr/debug/screen_volume_overlay.py`

### What changed
- `main_window.py`: `_sensitivity = 1.4` in `_HandSelectorBase._mapped_panel_global`.
- `desktop_controller.py`: `_normalize_file_token_text` adds `re.sub(r'\b(hgr)([a-z])', r'\1 \2', normalized)` after lowercasing; `close_named_window` KNOWN_APPLICATIONS expansion and window-title matching use `re.search(r'\bTERM\b', haystack)` instead of substring `in`.
- `noop_engine.py`: save_prompt `show_listening` calls use `hint_text=self._save_prompt_text` in both the initial call and the "listening" status callback. Added dual-volume state (`_volume_dual_active`, `_volume_app_level`, `_volume_app_label`, `_volume_bar_selected`, `_volume_init_palm_x`). `_handle_volume_control` detects Spotify/Chrome audio on overlay entry, tracks palm x drift to select bar, routes volume delta to correct target. `_update_volume_overlay` calls `set_dual_level` when dual active.
- `tutorial_window.py`: `_try_adopt_parent_voice_listener()` borrows parent GestureWorker's pre-warmed `voice_listener`; called at start of `_start_voice_practice()`. `_check_youtube_opened` + `_check_youtube_opened_final` use `QTimer.singleShot` to check Chrome window titles 3 s + 2 s after voice result; falls back to "voice command detected" if still not found.
- `chrome_controller.py`: `has_youtube_open()` enumerates Chrome windows via `EnumWindows`+`GetWindowTextW` and checks for "youtube" in title.
- `volume_controller.py`: `get_app_audio_info(process_names)` returns `(matched_name, level)` via pycaw `ISimpleAudioVolume`; `set_app_audio_level(process_names, scalar)` sets per-app volume.
- `screen_volume_overlay.py`: `set_dual_level(...)` method widens overlay to 300×350; `_paint_dual` draws two labeled bars (left=app, right=sys) with selected bar in full accent color and unselected dimmed.

### Test wave checklist
- [ ] Pen/eraser options cursor moves at 1.4× speed
- [ ] Saying "open hgrclip" finds HGR_Clip_* files
- [ ] Save prompt: mic icon and "Where would you like to save this file?" appear at same time
- [ ] Tutorial step 6 uses fast voice command (no 5–10 min delay)
- [ ] Tutorial step 6 passes when YouTube is visible in Chrome window title
- [ ] "close notepad and codex" does NOT close VS Code
- [ ] Volume gesture with Spotify/Chrome audio playing shows dual bar overlay
- [ ] Palm left → app vol bar highlighted; palm right → sys vol bar highlighted
- [ ] Up/down on selected bar adjusts that bar's volume only

### Remaining risks
- Dual volume palm-x threshold 0.06 normalized units; may need tuning per user hand movement range.
- `get_app_audio_info` called once per second while overlay is visible; pycaw session enum is generally < 5 ms but could spike.
- Tutorial `_try_adopt_parent_voice_listener` requires the parent worker to expose `.voice_listener` attribute; if not present, falls back to own (slow) instance.

---
## 2026-04-16 — 7 fixes (sensitivity, hand color picker, eraser highlight, file fallback, close cleanup, multi-app close)

### Requested tasks
1. Decrease selector cursor sensitivity (1.5 → 1.2)
2. Hand-controllable color wheel dialog (replace blocking QColorDialog)
3. Active eraser mode button highlighted; selected pen color swatch outlined
4. "open hgr clip" falls back to file search when no app matches
5. "open hgr clip file" correctly scores HGR_Clip_* filenames
6. No new HGR App window during file selection voice activation
7. Fix close command noise ("and close d discord", "flows discord"); multi-app close ("close discord, spotify, chrome")

### Root cause or best current hypothesis
- Sensitivity: 1.5× was slightly too fast; 1.2× provides better fine control.
- Hand color picker: `QColorDialog.getColor` is a blocking native call that freezes the gesture loop; replaced with a custom `HandColorPickerDialog(_HandSelectorBase)` grid of color buttons.
- Eraser mode: buttons had no `:checked` stylesheet so active mode was visually indistinguishable.
- Pen swatch: `_ColorSwatchButton._refresh_style` needed stronger checked state (3px border + accent outline ring).
- File fallback (item 4): `_parse_generic_open` returned None on no app match; needs file_explorer fallback.
- File scoring (item 5): `_normalize_file_query` had "hgr" in its stop-word set; files named `HGR_Clip_*` scored near zero (only "clip" token remained after filtering, giving score ~0.52 vs threshold 0.74).
- New window (item 6): `_launch_target` had no guard against launching the running executable.
- Close noise: "flows" and "and close" not in COMMAND_CORRECTIONS; "and" not in COMMON_FILLERS.
- Multi-app: close execution only called `close_named_window` for a single string.

### Files changed
- `src/hgr/app/ui/main_window.py`
- `src/hgr/voice/command_processor.py`
- `src/hgr/debug/desktop_controller.py`

### What changed
- `main_window.py`: `_sensitivity = 1.2` (both `_mapped_panel_global` and `_capture_monitor_dialog_panel_mapped_global_v22`).
- `main_window.py`: Added `HandColorPickerDialog(_HandSelectorBase)` with 12×4×3 hue/sat/val button grid, brightness stepper, preview swatch, Apply/Cancel; `PenOptionsDialog._open_color_wheel` replaced `QColorDialog.getColor` with the new picker; `_parent_debug_signal` stored on `PenOptionsDialog` so picker receives live frames.
- `main_window.py`: `EraserOptionsDialog` Normal/Stroke buttons get a `:checked` QPushButton stylesheet (accent green background + border).
- `main_window.py`: `_ColorSwatchButton._refresh_style` checked state now uses 3px white border + accent outline ring.
- `command_processor.py`: `_parse_generic_open` else branch: when no app found and query is ≥2 tokens, returns `ParsedVoiceCommand(app_name="file_explorer", action="open", confidence=0.60, query=query)` instead of None.
- `desktop_controller.py`: Removed `"hgr"` from `_normalize_file_query` stop-word set; "hgr clip" now stays intact → `HGR_Clip_*` files score ~1.16, well above 0.74 threshold.
- `desktop_controller.py`: `_launch_target` early-exits with `success=False` when the resolved target matches `sys.executable` or ends with `"hgr app.exe"`.
- `command_processor.py`: Added `"and"` to `COMMON_FILLERS`; added `("flows", "close")`, `("close d ", "close ")`, `("and close", "close")` to `COMMAND_CORRECTIONS`.
- `command_processor.py`: `_execute_generic` close_window path splits query on `,`, `;`, `and` and calls `close_named_window` for each part, aggregating results.

### What was explicitly preserved
- All existing gesture mappings, voice command routing, drawing mode, tutorial, debugger overlay.
- `_HandSelectorBase` hand-control semantics (only sensitivity constant changed).
- `_CursorHostPanel`, `CaptureMonitorDialog`, `PenOptionsDialog` hand-cursor flow.
- Existing close command single-app path (multi-app split is a superset).
- All save-prompt and Save Locations flows unchanged.

### Test wave checklist
- [ ] Selector cursor moves at 1.2× speed (slightly calmer than before)
- [ ] Custom Color opens hand-controllable picker; selected color applies to pen
- [ ] Eraser options shows green highlight on active Normal/Stroke button
- [ ] Selected pen color swatch shows bright outline ring
- [ ] "open hgr clip" triggers file search for HGR_Clip_* files
- [ ] "open hgr clip file" finds and opens HGR_Clip_*.mp4 without specifying "30 s"
- [ ] Voice while file-selection list is active does not open a new HGR App window
- [ ] "close discord" works after ASR mishears as "and close d discord" or "flows discord"
- [ ] "close discord, spotify, chrome, and settings" closes all listed apps
- [ ] Live camera feed remains responsive throughout
- [ ] No unrelated gesture or mouse-control regression

### Remaining risks
- `HandColorPickerDialog` opens as a child of `PenOptionsDialog`; if `PenOptionsDialog` is closed before the picker, picker may lose its parent signal. Unlikely in normal use.
- File fallback confidence 0.60 may produce false positives for very generic 2-word queries when the user misspoke an app name. Acceptable trade-off.
- Multi-app close reports aggregate success (any succeeded); individual failures are in `control_text` but not surfaced separately in the UI.

---
## 2026-04-16 — 8 feature additions (sensitivity, color wheel, disambiguation, voice phrases, save names, hint box, location+name)

### Requested tasks
1. Increase selector window cursor sensitivity (1.5×)
2. Add color wheel / custom color button to PenOptionsDialog
3. Fix file disambiguation: "open hgr clip 60s" should show all matching clips, not open the first
4. Flexible voice selection phrases: "show me 2", "can you open 3", "let's see 1", etc.
5. Flexible cancellation phrases: "forget it", "scratch that", "none of these", etc.
6. Save Locations settings: add default file name prefix per output type with auto-increment counter
7. Save prompt UI: show blue hint box below mic icon with hint text
8. Save prompt: allow "Documents folder as testing recording one" → saves to Documents as Testing_recording_1

### Root cause or best current hypothesis
- Sensitivity: normalized position mapped linearly across full screen — small hand movements → small cursor movement.
- Color wheel: missing button in PenOptionsDialog; QColorDialog already used elsewhere in the codebase.
- Disambiguation: `number_compatible` in `_resolve_file_query` required exact number-set match between best and candidate files; "60s" is in both files but IDs differ, so second file was excluded.
- Voice phrases: `_extract_selection_number` regex prefix was narrow; cancellation set was incomplete.
- Save names: no config fields or UI for name prefix; all outputs used hardcoded `hgr_*_timestamp` names.
- Hint box: `show_listening` had no hint_text parameter; listening mode rendered only mic icon with no text.
- Location+name: `SavePromptProcessor.parse()` had no "X as Y" splitting logic.

### Files changed
- `src/hgr/app/ui/main_window.py`
- `src/hgr/config/app_config.py`
- `src/hgr/voice/command_processor.py`
- `src/hgr/voice/save_prompt.py`
- `src/hgr/gesture/ui/voice_status_overlay.py`
- `src/hgr/app/integration/noop_engine.py`
- `src/hgr/debug/desktop_controller.py`

### What changed
- `_HandSelectorBase._mapped_panel_global` and `_capture_monitor_dialog_panel_mapped_global_v22`: added 1.5× sensitivity multiplier centered at 0.5 for both nx and ny.
- `PenOptionsDialog`: added "Custom Color…" button that calls `QColorDialog.getColor`; added `_open_color_wheel` method.
- `desktop_controller._resolve_file_query`: changed `number_compatible` from `path_numbers == best_numbers` to `query_numbers_union <= both sets`, so files sharing the query's numbers but having different IDs are all shown.
- `command_processor._extract_selection_number`: extended selection prefix regex to cover "can you open #", "let's see #", "I'll take #", etc.; expanded cancellation set to include "forget it", "scratch that", "none of these", "no", "nope", "abort", plus regex-matched patterns.
- `app_config.py`: added `SAVE_NAME_CONFIG_FIELDS`, `SAVE_NAME_DEFAULTS`, `save_name_config_field()`, `configured_save_name()` helpers; added four `*_save_name` fields to `AppConfig`.
- `main_window.py`: imported new config helpers; added `_save_name_inputs` dict; added save-name card to `_build_save_locations_panel`; added `_apply_save_name` method; added `_next_output_path` helper (scans dir for max counter, returns `prefix_N.ext`); updated `_save_drawing_snapshot`, `_save_screenshot_pixmap`, `_record_output_specs`, `_clip_output_specs` to use `_next_output_path`.
- `voice_status_overlay.py`: added `_hint_text` field; added `hint_text=""` param to `show_listening`; when hint present, resizes to 380×156 and renders transparent blue hint box below mic.
- `noop_engine.py`: save_prompt mode now calls `show_listening(hint_text=...)` with hint string.
- `save_prompt.py`: added `custom_name` field to `SavePromptDecision`; added `_split_location_as_name`, `_normalize_custom_name`; added "ignore" to default hints; parse now checks for "X as Y" first and returns `move_rename` action.
- `main_window._on_save_prompt_completed`: handles `move_rename` action by calling `_move_saved_output_as`; added `_move_saved_output_as` method.

### What was explicitly preserved
- All existing gesture mappings, voice command routing, drawing mode, tutorial, debugger overlay.
- Existing save prompt `move` and `discard` flows unchanged.
- CaptureMonitorDialog and _HandSelectorBase hand-control semantics unchanged (only sensitivity adjusted).

### Test wave checklist
- [ ] Selector cursor moves more responsively with hand (1.5× sensitivity)
- [ ] "Custom Color…" opens color dialog; selected color applied to pen
- [ ] "open hgr clip 60s" with two clips shows selection list
- [ ] "show me 2" / "can you open 1" / "let's see 3" accepted as selections
- [ ] "forget it" / "scratch that" cancels selection
- [ ] Save Locations > file name field appears, saves, persists
- [ ] Drawings/screenshots/recordings/clips save as HGR_Drawing_1.png etc.
- [ ] Save prompt shows blue hint box below mic
- [ ] "Documents folder as testing recording one" → moves and renames correctly
- [ ] Live camera feed remains responsive throughout

### Remaining risks
- `_next_output_path` iterdir scan could be slow on very large directories (thousands of files)
- Color dialog blocks hand control while open (acceptable: mouse needed for color wheel anyway)
- The "X as Y" split is purely lexical; if user says "as" in a folder name it may split incorrectly

---
## 2026-04-16 — Fix cursor visibility in selector windows and clip export crash

### Requested task
- Fix hand cursor not appearing in pen/eraser options, screenshot/record monitor-selection windows.
- Fix clip 30s/60s export crash: `AttributeError: 'CaptureMonitorDialog' object has no attribute 'exec'`.

### Root cause or best current hypothesis
- **Cursor invisible**: Both `CaptureMonitorDialog` and `_HandSelectorBase` painted the crosshair cursor in the *parent* widget's `paintEvent`. Because the `_panel` child widget renders on top of its parent, the cursor was drawn but always covered by the panel. The cursor existed but was hidden.
- **Clip crash**: `_export_recent_clip` called `_choose_full_capture_region` which calls `dialog.exec()`. `CaptureMonitorDialog` is a `QWidget`, not a `QDialog`, so it has no `exec()` method. This was introduced when the dialog was converted from `QDialog` to `QWidget` for non-blocking hand control, but `_export_recent_clip` was never updated.

### Files changed
- `src/hgr/app/ui/main_window.py`

### What changed
- Added `_CursorHostPanel(QFrame)` class: overrides `paintEvent` to draw the crosshair cursor from `parent()._cursor_global` on top of the panel's own content.
- `CaptureMonitorDialog._panel` and `_HandSelectorBase._panel`: changed from `QFrame` to `_CursorHostPanel` so the cursor appears inside the panel rather than being hidden by it.
- Both `_update_cursor_from_global` methods: added `self._panel.update()` so the panel repaints on each cursor movement.
- Both `showEvent` methods: added `self._panel.update()` on show so initial cursor renders.
- Both `paintEvent` methods on the parent widgets: removed cursor drawing (cursor now lives in the panel).
- `_export_recent_clip`: replaced blocking `_choose_full_capture_region` (which called `dialog.exec()`) with the same async non-blocking pattern used by `_begin_monitor_selection_async` — connects `selection_made`/`canceled` to closures, calls `dialog.show()`, routes `debug_frame_ready` to the dialog.

### What was explicitly preserved
- All existing gesture mappings, voice flows, drawing mode, tutorial behavior.
- `_HandSelectorBase` click arming (`_hand_clicks_armed`, `_raw_clicks_armed`) logic unchanged.
- `CaptureMonitorDialog` v22 patches (handle_debug_frame_v2, update_hand_control_v22, etc.) unchanged.
- Post-action save prompt calls for clips and screen recordings (from previous session).

### Test wave
- requested behavior: cursor visible in pen options, eraser options, screenshot/record monitor dialogs; clip export no longer crashes
- neighboring regression checks: `_HandSelectorBase` accepted/canceled signal flow unchanged; `CaptureMonitorDialog` selection_made/canceled signal flow unchanged; single-monitor path for clip export skips dialog and exports directly
- remaining uncertainty: screenshot_custom and record_custom use `CaptureRegionOverlay` (a separate widget) not addressed here — if those still lack a cursor, that's a separate fix

### Cleanup wave
- No debug prints introduced
- Diff scoped to: 1 new class, 2 panel swaps, 2 `_update_cursor_from_global` additions, 2 `showEvent` additions, 2 `paintEvent` removals, 1 method rewrite

---
## 2026-04-16 — Fix missing post-action save prompt for screen recordings and clips

### Requested task
- Complete the three-phase task: hand control for selector windows, Save Locations settings, and post-action save voice prompt.

### Root cause or best current hypothesis
- Phases 1 and 2 were already fully implemented in the codebase via runtime patches at the bottom of `main_window.py`. Phase 3 was implemented for drawings and screenshots but not for screen recordings or clips: the class-body `_stop_screen_recording` and `_export_recent_clip_ffmpeg`/`_opencv` did not call `_queue_post_action_save_prompt`. Module-level patched versions did, but were never bound to `MainWindow`.

### Files changed
- `src/hgr/app/ui/main_window.py`

### What changed
- Added `self._queue_post_action_save_prompt("screen_recordings", path)` in `_stop_screen_recording` (class method, line ~4814) after a successful recording save.
- Added `self._queue_post_action_save_prompt("clips", output_path)` in `_export_recent_clip_ffmpeg` (line ~4480) and `_export_recent_clip_opencv` (line ~4562) after successful clip saves.

### What was explicitly preserved
- All existing Phase 1 hand-control patches (CaptureMonitorDialog v22, _HandSelectorBase, PenOptionsDialog, EraserOptionsDialog).
- All existing Phase 2 Save Locations settings UI and persistence.
- Drawings and screenshots post-action save prompt flow (unchanged).
- All gesture mappings, voice flows, tutorial behavior, and mouse control.

### Test wave
- requested behavior: save prompt fires after screen recordings and clips complete
- neighboring regression checks: `_stop_screen_recording` logic unchanged except added prompt call; clip exports unchanged except added prompt call; discard/cancel/default flow in SavePromptProcessor already verified
- remaining uncertainty: clip export uses `_choose_full_capture_region` (blocking dialog.exec()) for monitor selection — hand control in that dialog may not work as well as the async CaptureMonitorDialog for screenshots/recordings

### Cleanup wave
- dead code/log cleanup: no new dead code introduced
- comment/import cleanup: no imports changed
- final diff scope check: 3 one-line additions, all in targeted locations

---
## YYYY-MM-DD — Short task title

### Requested task
- 

### Root cause or best current hypothesis
- 

### Files changed
- `path/to/file.py`
- `path/to/other_file.py`

### What changed
- 

### What was explicitly preserved
- 

### Test wave
- requested behavior:
- neighboring regression checks:
- remaining uncertainty:

### Cleanup wave
- dead code/log cleanup:
- comment/import cleanup:
- final diff scope check:

---

## 2026-04-16 — Refine Codex repo docs for selector windows and save flow work

### Requested task
- Update the repo docs so Codex can more reliably handle secondary hand-controlled windows, Save Locations settings, and the post-action voice save flow.

### Root cause or best current hypothesis
- The previous docs were useful but still too generic for the next high-risk task. Codex needed a more explicit spec for selector-window interaction rules, agent routing, and the staged implementation order.

### Files changed
- `AGENTS.md`
- `PLANS.md`
- `docs/AGENT_TASK_MATRIX.md`
- `docs/NEXT_STEPS.md`
- `docs/RECENT_WORK.md`
- `docs/SECONDARY_HAND_CURSOR_WINDOWS.md`
- `docs/SAVE_LOCATIONS_AND_POST_ACTION_SAVE_FLOW.md`
- `docs/TASK_PROMPT_TEMPLATES.md`
- `.agents/skills/secondary-selector-ui/SKILL.md`
- `.agents/skills/save-output-flow/SKILL.md`

### What changed
- Added specific rules for non-blocking secondary windows with a visible local hand-controlled cursor, a concrete Save Locations and post-action save-flow spec, new prompt templates, and new skills for selector-window work and save-output flows.

### What was explicitly preserved
- No application source code was changed.

### Test wave
- requested behavior: updated docs pack now covers the current requested Codex task directly
- neighboring regression checks: verified hidden skill folders and new docs exist in the exported pack
- remaining uncertainty: final effectiveness still depends on using the prompt and copying the files into the real repo

### Cleanup wave
- dead code/log cleanup: not applicable
- comment/import cleanup: not applicable
- final diff scope check: docs-only

---

## 2026-04-16 — Initialize Codex repo memory docs

### Requested task
- Create repo docs so Codex has stable project memory and planning guidance.

### Root cause or best current hypothesis
- The repo needed stable reusable instructions to reduce repeated context reconstruction and improve consistency across sessions.

### Files changed
- `AGENTS.md`
- `PLANS.md`
- `README-CODEX-DOCS.md`
- `docs/*.md`
- `.agents/skills/*/SKILL.md`

### What changed
- Added repo-wide agent instructions, recent-work logging, test-wave and cleanup-wave workflow, task routing, and future-step planning docs.

### What was explicitly preserved
- No application source code was changed.

### Test wave
- requested behavior: docs pack created
- neighboring regression checks: verified skill folders exist in this pack
- remaining uncertainty: final repo integration depends on copying these files into the real project

### Cleanup wave
- dead code/log cleanup: not applicable
- comment/import cleanup: not applicable
- final diff scope check: docs-only
