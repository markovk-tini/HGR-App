# Recent Work

Keep newest entries at the top.

Use this format after every meaningful coding session.

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
