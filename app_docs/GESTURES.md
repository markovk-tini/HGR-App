# Gestures

## Rules

- Preserve existing mappings unless the task explicitly changes them.
- Mouse mode and drawing mode are especially sensitive to leakage from other gesture families.
- When in a scoped mode, unrelated gestures should generally be ignored unless explicitly designed otherwise.
- Do not silently change thresholds/timers/holds unless requested or required for a root-cause fix.

## High-risk gesture areas

- mouse cursor stability during clicking
- drawing mode activation/deactivation
- gesture wheel selection logic
- mode leakage while drawing or erasing
- gesture gating while a chooser or monitor-selection window is open
- screen control gestures that require live camera continuity

## Verification reminders

After gesture-related changes, check:
- idle behavior
- mode entry
- mode exit
- cancellation path
- neighboring gestures that often false-trigger
