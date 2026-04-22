# test-wave

Run after implementation and before cleanup.

## Goal
Verify the requested change and the most likely neighboring regressions.

## Required checks
- requested behavior works
- adjacent flows still work
- live camera remains responsive if relevant
- voice/listening state recovers if relevant
- no unintended mode leakage

## Output
Provide a concise pass/fail list with exact files/flows touched.
