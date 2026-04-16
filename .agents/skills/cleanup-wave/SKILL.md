# cleanup-wave

Run after test wave and before final reviewer pass.

## Goal
Reduce noise and keep the diff clean without changing behavior.

## Required checks
- remove temporary logs and debug leftovers
- remove dead code created during iteration
- keep imports and comments tidy
- confirm the diff still matches the task only
- confirm summary lists the exact changed files
