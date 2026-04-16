# Secondary Hand Cursor Windows

Use this doc when a temporary or secondary window must remain hand-controllable while the live camera continues updating.

## Applies to

- drawing-mode pen options windows
- drawing-mode eraser options windows
- screen screenshot monitor-selection windows
- screen recording monitor-selection windows
- any future special selector popup that should behave like the tutorial rather than like a blocking desktop dialog

## Required behavior

- the live camera / gesture loop must continue running while the secondary window is open
- the secondary window must show a visible local cursor
- that cursor must be controllable by hand inside the secondary window
- hand cursor semantics should match main mouse control unless the task explicitly changes them:
  - center of palm = cursor movement
  - index finger = left click
  - middle finger = right click
- when the secondary window is active, it owns interaction input and unrelated desktop cursor actions should be swallowed or gated off
- closing, canceling, or completing the window must cleanly return control to the previous mode

## Anti-patterns to avoid

Do not use:
- `exec()`-style blocking modal dialogs when hand control must continue
- manual wait loops that block the UI thread
- `sleep()` or similar pauses after opening the selector window
- native pickers that freeze or steal the interaction loop unless there is a safe wrapper that preserves live hand control

## Design expectations

- use a modeless or otherwise non-blocking Qt window if possible
- prefer reusing the tutorial-style local cursor behavior for consistency
- prefer reusing main mouse-control logic rather than inventing a separate selector-specific click scheme
- keep selector ownership explicit with a mode flag or equivalent state gating
- clear state cleanly after select / cancel / timeout

## Required verification

- visible local cursor appears in the secondary window
- cursor moves with hand input
- index finger click works as expected
- middle finger right-click behavior is either supported or intentionally gated with a clear reason
- live camera feed remains responsive the whole time
- no unrelated gesture wheel behavior changes
- no normal desktop mouse-control regression after the secondary window closes
