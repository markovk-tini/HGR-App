# Next Steps

These are the current prioritized next product steps.

## Priority 1

Improve hand control of **drawing mode** secondary windows and **screen gesture wheel** secondary windows.

### Required behavior
- if the user opens drawing-mode pen options or eraser options, a special selection window must appear with a visible local cursor
- if the user opens screen screenshot or recording monitor-selection options, a special selection window must appear with a visible local cursor
- those windows must remain hand-controllable while the live camera keeps updating
- cursor semantics should match main mouse control unless explicitly changed:
  - center of palm = cursor movement
  - index finger = left click
  - middle finger = right click
- while the selector window is active, it owns cursor input and should not leak unrelated desktop mouse actions

## Priority 2

Add a **Save Locations** settings section.

### Required settings entries
- drawings
- screenshots
- screen recordings
- clips

### Each entry should support
- showing the current location
- browse to change
- manual path typing
- safe persistence of the configured location
- validation or safe fallback behavior if a path is invalid

## Priority 3

After a drawing, screenshot, screen recording, or clip is completed, show a follow-up **voice save prompt**.

### Prompt UI
- microphone icon
- prompt text: `Where would you like to save this file?`

### Accepted responses
- `auto`
- `default`
- a specific folder location/path
- silence → auto-save to configured default
- `cancel`
- `delete`
- `nevermind`

### Design rules
- use configured save locations from settings as the fallback source of truth
- do not block the app in a way that breaks the intended interaction flow
- timeout and cancellation must leave the app in a clean state
- specific path responses should resolve to a folder path only, with safe rejection or fallback if invalid
