# secondary-selector-ui

Use for temporary windows that must stay hand-controllable while the live camera continues updating.

## Focus
- non-blocking selector windows
- visible local cursor inside the selector window
- clean ownership of cursor input while selector is active
- safe exit back to prior mode

## Default assumptions
- prefer modeless Qt windows over blocking modal flows when hand control must continue
- reuse main mouse semantics unless the task explicitly changes them:
  - center of palm = cursor movement
  - index finger = left click
  - middle finger = right click

## Must verify
- selector cursor is visible
- live camera stays responsive
- click semantics still work
- selector ownership ends cleanly on select / cancel / timeout
- no desktop-mouse regression after closing the selector
