# Packaging

## Rules

- Do not break runtime paths while fixing app logic.
- If a change touches assets, runtime paths, file dialogs, save paths, or platform helpers, consider packaging impact.
- Windows and macOS path handling should be explicit and conservative.

## Save-location feature guidance

The future settings area should expose user-configurable default save locations for:
- drawings
- screenshots
- screen recordings
- clips

Each item should show the current path and support:
- browse to change
- manually typing a path
- safe fallback to a valid default if needed
