# Architecture

## High-level architecture

HGR App combines:
- PySide6 UI shell
- OpenCV/MediaPipe hand tracking and gesture interpretation
- mouse and drawing control layers
- voice transcription and intent handling
- app/system integration helpers
- packaging/runtime-path support for Windows and macOS

## Architecture expectations

- UI responsiveness matters more than cleverness.
- Gesture gating must be mode-aware.
- Voice follow-up must not race or interrupt incorrectly.
- Modal flows must not freeze the live camera when hand control is still needed.
- Shared state ownership should be explicit.

## Common failure patterns

- event-loop blocking during modal workflows
- background worker results not marshaled safely back to UI thread
- mode flags drifting out of sync
- gesture routing not being properly scoped by active mode
- follow-up voice prompts re-enabling listening too early or too late
