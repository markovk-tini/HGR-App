# PLANS.md

Use this format for any risky, multi-file, or multi-subsystem task.

## Task

One sentence describing the exact requested change.

## Why this is risky

State what could break if the change is done carelessly.

## User-visible success criteria

- 
- 
- 

## Must not change

- 
- 
- 

## Relevant files and modules

- 
- 
- 

## Recent context to review first

- `docs/RECENT_WORK.md`
- most relevant subsystem docs
- any touched files from the most recent related session

## Likely root cause or failure path

Describe the suspected source before editing.

## Recommended phase split for complex tasks

1. inspection and failure-path confirmation
2. implementation wave 1: core interaction fix
3. implementation wave 2: settings / persistence if needed
4. implementation wave 3: follow-up voice flow if needed
5. targeted verification
6. test wave
7. cleanup wave
8. reviewer wave
9. update `docs/RECENT_WORK.md`

## Execution plan

1. Inspect
2. Confirm failure path
3. Patch the smallest safe surface area
4. Keep temporary windows hand-controllable without freezing the live loop
5. Run targeted verification
6. Run test wave
7. Run cleanup wave
8. Update `docs/RECENT_WORK.md`

## Test wave checklist

- [ ] Requested behavior works
- [ ] Live camera feed remains responsive
- [ ] Special selector windows show a visible cursor if required
- [ ] Main mouse semantics still work if touched
- [ ] No unrelated gesture behavior changed
- [ ] No unrelated voice behavior changed
- [ ] No UI wording/layout/timing drift
- [ ] No packaging/runtime-path breakage
- [ ] No tutorial/debugger/overlay regression

## Cleanup wave checklist

- [ ] Remove dead code created during debugging
- [ ] Remove temporary prints/logs unless explicitly desired
- [ ] Keep comments accurate and minimal
- [ ] Keep imports clean
- [ ] Keep diff scoped to the task
- [ ] Ensure summary names exact changed files

## Final session summary format

- Root cause or best current hypothesis:
- Files changed:
- What changed:
- What was explicitly preserved:
- Test wave results:
- Cleanup wave results:
- Remaining risks:
