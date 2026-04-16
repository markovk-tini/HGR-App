# AGENTS.md

## Project identity

HGR App is a hand-gesture plus voice desktop control application with a PySide6 UI and a real-time OpenCV/MediaPipe pipeline. The project goal is polished, stable, end-user behavior — not just isolated detection accuracy.

## What this file is for

Codex reads `AGENTS.md` files before doing work, and OpenAI recommends using them as the main reusable repo instructions layer. Keep this file short, stable, and high-value; put details in `docs/` and repo skills under `.agents/skills/`. citeturn804151search3turn804151search1turn804151search5turn804151search14

## Efficiency note

These docs should reduce repeated planning/context overhead and improve consistency, but they do **not** guarantee lower usage on every task. Codex usage is token-based, and reusable repo guidance helps by reducing repeated fresh-context setup rather than by eliminating reasoning cost. citeturn804151search5turn804151search16turn804151search17

## Core operating rules

- Preserve existing working behavior unless the task explicitly changes it.
- Favor minimal, targeted diffs over rewrites.
- Do not change unrelated UI text, styling, timing, gesture mappings, or file structure.
- Every task must include a non-regression check for nearby logic.
- If a task touches event loops, workers, timers, gesture gating, mouse control, drawing mode, modal windows, or voice follow-up flow, treat it as high risk.

## Mandatory workflow per editing session

1. Read this file.
2. Read `docs/RECENT_WORK.md` first.
3. Read only the most relevant subsystem docs in `docs/`.
4. If the task is multi-step, risky, or touches more than one subsystem, use `PLANS.md`.
5. If a matching skill exists in `.agents/skills/`, use it.
6. Use subagents only when they clearly separate implementation, testing, cleanup, or review work.
7. Run a **test wave** before finalizing.
8. Run a **cleanup wave** before finalizing.
9. Update `docs/RECENT_WORK.md` with what changed.

## Special rule for hand-controlled secondary windows

If a temporary window must remain hand-controllable while the live camera keeps running, do **not** use blocking modal UI (`exec()`, manual wait loops, sleeps, or any design that stops the live update loop).

Instead:
- keep the live camera / gesture pipeline running
- open a modeless or otherwise non-blocking Qt window
- show a visible local selection cursor in that window
- route hand cursor updates into that window while it is active
- reuse the main mouse-control semantics unless the task explicitly changes them:
  - center of palm controls cursor movement
  - index finger = left click
  - middle finger = right click
- while a special selector window is active, swallow unrelated desktop mouse actions so the selector owns interaction

## Required final output for any task

Every coding session should end with:

- root cause or working hypothesis
- files changed
- what behavior was changed
- what behavior was explicitly preserved
- regression checks performed
- remaining risk

## Non-regression rule

Each editing session must assume that unrelated working logic is fragile unless proven otherwise.

Before editing:
- inspect mode flags, signal/slot flow, worker threads, timers, and live-view ownership
- inspect whether the target code is shared with tutorial, debugger, overlay, mouse mode, drawing mode, wheel flows, or voice follow-up logic

After editing:
- verify only the tasked behavior changed
- verify no cross-mode gesture leakage was introduced
- verify no UI drift was introduced
- verify the live camera feed still responds
- verify neighboring flows that historically break in this repo

## Use agents when it is actually helpful

Use multiple agents only when there is clear task separation or parallel review value.

Recommended split:
- implementation agent for the main subsystem
- reviewer agent for blast-radius and regression checks
- test-wave agent for verification design/results
- cleanup-wave agent for comment cleanup, dead code, import hygiene, and summary quality

Do **not** use extra agents just to restate the same task. Codex supports explicit subagents, but they are most useful when you intentionally delegate distinct roles. citeturn804151search12turn804151search14

## Priority system

1. Prevent regressions
2. Keep the live camera/UI responsive
3. Keep gesture gating correct by mode
4. Keep mouse and click behavior stable
5. Keep voice command routing reliable
6. Keep build/runtime paths packaging-safe

## Key docs

- `docs/PROJECT_MAP.md`
- `docs/ARCHITECTURE.md`
- `docs/GESTURES.md`
- `docs/VOICE_PIPELINE.md`
- `docs/UI_RULES.md`
- `docs/TESTING_AND_DEBUG.md`
- `docs/SECONDARY_HAND_CURSOR_WINDOWS.md`
- `docs/SAVE_LOCATIONS_AND_POST_ACTION_SAVE_FLOW.md`
- `docs/KNOWN_ISSUES.md`
- `docs/RECENT_WORK.md`
- `docs/NEXT_STEPS.md`
- `docs/TASK_PROMPT_TEMPLATES.md`

## Prompt shorthand for this repo

- **Use plan** = follow `PLANS.md`
- **Use reviewer** = use `.agents/skills/code-reviewer/SKILL.md`
- **Run test wave** = use `.agents/skills/test-wave/SKILL.md`
- **Run cleanup wave** = use `.agents/skills/cleanup-wave/SKILL.md`
- **No regressions** = preserve unrelated behavior and explicitly verify likely blast radius
