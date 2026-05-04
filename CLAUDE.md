# CLAUDE.md — AI Agent Working Notes for Touchless

This file is the consolidated memory for any AI agent (Claude, Codex,
ChatGPT, etc.) working on Touchless. Read this first, then dive into
the relevant subsystem doc under `docs/`.

## Project identity

**Touchless** is a hand-gesture + voice desktop control application
for Windows. PySide6 UI on top of a real-time OpenCV / MediaPipe
pipeline. The product goal is **polished, stable, end-user behavior**
— not just isolated detection accuracy.

- Primary author: Konstantin Markov
- License: FSL-1.1-Apache-2.0 (see `LICENSE`)
- Distribution: Inno Setup installer (Windows, per-user install under
  `%LOCALAPPDATA%\Programs\Touchless`)
- Code-signed via Azure Artifact Signing (see `signing/`)

## Core operating rules

1. **Preserve existing behavior** unless the task explicitly changes
   it. If you're tempted to "improve" unrelated code, don't.
2. **Minimal, targeted diffs.** No surrounding cleanup, no refactors
   bundled into bug fixes.
3. **Don't change** unrelated UI text, styling, timing, gesture
   mappings, or file structure.
4. Every task must include a **non-regression check** for nearby
   logic before reporting done.
5. **High-risk areas** that need extra care: event loops, worker
   threads, timers, gesture gating, mouse control, drawing mode,
   modal windows, voice follow-up flows, packaging/runtime paths.

## Priority system (when in doubt)

1. Prevent regressions
2. Keep the live camera/UI responsive
3. Keep gesture gating correct by mode
4. Keep mouse and click behavior stable
5. Keep voice command routing reliable
6. Keep build/runtime paths packaging-safe

## Historical regression patterns (always check these)

These have bitten us before. When a task resembles one of these,
test the matching path **before** finishing:

- Fixing one gesture mode accidentally changes unrelated gesture
  behavior
- Drawing mode changes break gesture wheel actions
- Modal/chooser windows freeze the live camera or hand-driven cursor
- Voice follow-up timing becomes misaligned after a fix
- UI changes sneak in during functional patches
- Runtime path / import fixes accidentally alter packaged behavior

## Special rule: hand-controlled secondary windows

If a temporary window must remain hand-controllable while the live
camera keeps running, **do not** use blocking modal UI (`exec()`,
`QMessageBox.exec()`, manual wait loops, `time.sleep`, or any design
that stops the live update loop).

Instead:
- Keep the live camera / gesture pipeline running.
- Open a modeless / non-blocking Qt window (`show()`, not `exec()`).
- Show a visible local selection cursor inside that window.
- Route hand-cursor updates into that window while it's active.
- Reuse main mouse semantics unless the task explicitly changes them:
  - Center of palm = cursor movement
  - Index finger = left click
  - Middle finger = right click
- While a selector window is active, swallow unrelated desktop mouse
  actions so the selector owns interaction.

## Subsystem cheat sheet

| Subsystem | Key file(s) | Reference doc |
|---|---|---|
| Gesture classification + gating | `src/hgr/gesture/recognition/static_recognizer.py`, `src/hgr/gesture/recognition/engine.py` | `docs/GESTURES.md`, `docs/ARCHITECTURE.md` |
| Custom gestures | `src/hgr/custom_gestures/`, `src/hgr/app/ui/custom_gestures_*.py` | `src/hgr/custom_gestures/README.md` |
| Voice command + dictation | `src/hgr/debug/voice_command_listener.py`, `src/hgr/voice/` | `docs/VOICE_PIPELINE.md` |
| Mouse control / drawing / wheels | `src/hgr/app/integration/noop_engine.py` | `docs/SECONDARY_HAND_CURSOR_WINDOWS.md` |
| Save locations + post-action save flow | `src/hgr/app/ui/main_window.py` | `docs/SAVE_LOCATIONS_AND_POST_ACTION_SAVE_FLOW.md` |
| Runtime + packaging | `src/hgr/utils/runtime_paths.py`, `builder/windows/` | `docs/PACKAGING.md`, `docs/RELEASE_PROCESS.md` |
| Open bugs / planned work | — | `OPEN_ISSUES.md` (root) |
| Testing | `tests/`, `run_test.py` | `docs/TESTING_AND_DEBUG.md` |

## Workflow checklist for any non-trivial task

1. Read `OPEN_ISSUES.md` to see if the task overlaps active bugs.
2. Read the relevant subsystem doc(s) from the table above.
3. **Inspect before editing**: mode flags, signal/slot flow, worker
   threads, timers, live-view ownership, whether code is shared with
   tutorial / debugger / overlay / mouse / drawing / wheels / voice.
4. Make the smallest change that satisfies the task.
5. Verify only the targeted behavior changed.
6. Verify no cross-mode gesture leakage.
7. Verify no UI wording / layout / timing drift.
8. Verify the live camera feed still responds.
9. Run the test suite if the change has any chance of touching tested
   logic: `pytest tests/ -q`.
10. Update `OPEN_ISSUES.md` if you closed an item or discovered new
    risk.

## Required final summary format

After any non-trivial coding session:

- **Root cause / hypothesis:** (what was actually broken)
- **Files changed:** (exact paths)
- **What changed:** (the behavior delta)
- **What was preserved:** (unrelated behavior explicitly kept)
- **Regression checks performed:** (what you actually tested)
- **Remaining risk:** (what could still go wrong)

## Build / release commands

```cmd
:: Full build + sign + installer + auto-update zip
builder\windows\build_windows.bat

:: Skip signing for fast dev builds
set SKIP_SIGNING=1
builder\windows\build_windows.bat
```

For shipping, see `docs/RELEASE_PROCESS.md`.

## Memory persistence note

Per-session AI memory lives in
`C:\Users\Konstantin Markov\.claude\projects\c--HGR-App-v1-0-0\memory\`
when using Claude Code. That auto-memory complements (does not
replace) this file. Treat THIS file as the **always-true repo
contract**; auto-memory is for evolving session-specific context.
