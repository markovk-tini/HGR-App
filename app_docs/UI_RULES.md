# UI Rules

## Preserve UI unless explicitly asked

Do not alter unrelated:
- wording
- highlight timing
- spacing/layout
- button locations
- live-view composition
- overlay behavior

## High-risk UI areas

- any modal that appears during a hand-controlled workflow
- live camera freezing when a secondary window appears
- chooser windows that still need cursor control
- settings pages that share state with runtime logic
- drawing/mouse/gesture-wheel overlays

## Required mindset

If a new dialog appears while the user still needs hand control, assume the live view and cursor control must continue unless the task explicitly says otherwise.
