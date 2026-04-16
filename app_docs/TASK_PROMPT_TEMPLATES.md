# Task Prompt Templates

Use these prompts as starting points for Codex. Keep the prompt direct, concrete, and scoped. Let repo docs carry the long-term instructions.

## General template

Follow `AGENTS.md` and read `docs/RECENT_WORK.md` first. Use `PLANS.md` because this task is multi-step and high risk. Use the relevant skills from `.agents/skills/`. Keep changes minimal and do not harm unrelated logic. Run a test wave, cleanup wave, and final reviewer pass. Final summary must list exact files changed, what was preserved, and regression checks performed.

Task:
[describe exact requested behavior]

Success criteria:
- [user-visible outcome 1]
- [user-visible outcome 2]
- [user-visible outcome 3]

Must not change:
- [behavior that must stay identical]
- [behavior that must stay identical]
- [behavior that must stay identical]

## Paste-ready prompt for the current task

Follow `AGENTS.md` and read `docs/RECENT_WORK.md`, `docs/SECONDARY_HAND_CURSOR_WINDOWS.md`, and `docs/SAVE_LOCATIONS_AND_POST_ACTION_SAVE_FLOW.md` first. Use `PLANS.md` because this is a high-risk multi-subsystem task. Use subagents only where they provide real separation of work. Use these skills:
- `secondary-selector-ui` for the drawing and screen selector windows
- `gesture-engineering` for cursor routing, palm tracking, and click semantics
- `save-output-flow` for Save Locations settings and output routing
- `voice-intents` for the post-action save prompt
- finish with `test-wave`, `cleanup-wave`, and `code-reviewer`

Implement this in phases with minimal safe diffs.

Task:
1. Fix hand control for drawing-mode pen/eraser option windows and screen gesture wheel screenshot/record monitor-selection windows.
2. Add a `Save Locations` settings button/section with entries for drawings, screenshots, screen recordings, and clips. Each entry must show the current save folder, support typed path entry, and support browse/select-folder.
3. After a drawing, screenshot, screen recording, or clip is completed, show a voice prompt with a microphone and the text `Where would you like to save this file?`.

Required behavior for phase 1:
- when a drawing-mode pen options or eraser options window opens, show a visible local selection cursor in that window
- when screenshot or record monitor options open, show a visible local selection cursor in that window
- these windows must remain hand-controllable while the live camera continues updating
- reuse main mouse-control semantics unless a specific file proves a different shared implementation is required:
  - center of palm controls cursor movement
  - index finger performs left click
  - middle finger performs right click
- while a selector window is active, it owns the interaction and should not leak unrelated desktop mouse actions
- do not use blocking modal patterns that freeze the live loop

Required behavior for phase 2:
- add `Save Locations` in settings
- categories: drawings, screenshots, screen recordings, clips
- each category shows current path, supports manual typing, supports browse/select-folder, and persists safely

Required behavior for phase 3:
- after drawing/screenshot/recording/clip completion, prompt: `Where would you like to save this file?`
- accepted responses: `auto`, `default`, a specific folder location, silence, `cancel`, `delete`, `nevermind`
- `auto`/`default` saves to the configured default for that output type
- silence times out to the configured default location
- `cancel`/`delete`/`nevermind` must leave clean state and discard output if that matches current product expectations

Must not change:
- unrelated gesture mappings
- unrelated UI wording, styling, or timing
- unrelated voice command flows
- existing working tutorial behavior
- existing working main mouse-control behavior outside the targeted selector windows

Required final output:
- root cause or best current hypothesis
- exact files changed
- what changed
- what was explicitly preserved
- test wave results
- cleanup wave results
- remaining risks

## Shorter version when you already trust the docs

Follow `AGENTS.md`, `docs/RECENT_WORK.md`, and `PLANS.md`. Implement the current three-part task from `docs/NEXT_STEPS.md` using `secondary-selector-ui`, `gesture-engineering`, `save-output-flow`, and `voice-intents`. Keep the diff minimal, prevent regressions, run test/cleanup/reviewer waves, and end with exact files changed plus preserved behavior.
