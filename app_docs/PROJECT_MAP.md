# Project Map

Use this as a quick orientation file.

## Major areas

- UI shell and windows
- Gesture tracking/classification/dispatch
- Mouse mode and click control
- Drawing mode and gesture wheel actions
- Voice transcription, parsing, follow-up, and intent routing
- File/folder/app opening flows
- Debug/tutorial/overlay windows
- Packaging/runtime paths/installers

## Practical rule

Before editing a file, identify what shared surfaces it touches:
- live camera loop
- gesture mode flags
- mouse mode state
- drawing mode state
- modal or blocking dialogs
- voice listening state
- runtime paths or packaged assets

If a file touches any of those, the blast radius is larger than it looks.
