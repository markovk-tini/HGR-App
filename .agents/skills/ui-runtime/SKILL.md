# ui-runtime

Use for Qt windows, overlays, modal flows, chooser windows, camera-feed responsiveness, and runtime interaction state.

## Focus
- do not freeze live view when secondary UI appears
- keep event-loop ownership clear
- avoid blocking behavior when hand control must continue
- preserve wording/layout/timing unless explicitly requested

## Must verify
- live view still updates
- new UI does not trap the workflow incorrectly
- overlay/debug/tutorial side effects are considered
