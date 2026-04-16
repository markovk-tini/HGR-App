# Save Locations and Post-Action Save Flow

Use this doc when editing save-location settings, persistence, or the post-action voice save prompt.

## Save Locations settings requirements

Add a `Save Locations` section in app settings.

### Required categories
- drawings
- screenshots
- screen recordings
- clips

### Per-category UI requirements
- show the current folder path
- allow manual path typing
- allow browse/select-folder interaction
- save and persist the chosen folder
- handle invalid or missing paths safely

## Post-action voice save prompt requirements

After a drawing, screenshot, screen recording, or clip is completed, show a follow-up voice prompt.

### Prompt UI
- microphone icon
- prompt text: `Where would you like to save this file?`

### Accepted responses
- `auto`
- `default`
- specific folder path or folder-location phrasing
- silence / no answer
- `cancel`
- `delete`
- `nevermind`

## Required behavior mapping

### `auto` or `default`
- save to the configured default location for that output type

### specific folder path
- resolve to a folder path
- if valid, save there
- if invalid, either reprompt safely or fall back according to the task spec without breaking app flow

### silence / timeout
- auto-save to the configured default location

### `cancel`, `delete`, or `nevermind`
- do not save the file if the requested product behavior says cancel/delete should discard it
- if the app already created a temp output, clean it up safely
- return the app to a clean idle or prior-ready state

## Safety and architecture rules

- do not block the app in a way that breaks live interaction flow
- keep output-type routing explicit so drawings, screenshots, recordings, and clips cannot be mixed up
- keep path handling packaging-safe and platform-safe
- persist settings in one clear source of truth
- verify default save locations still work if the voice prompt is ignored

## Required verification

- save settings render correctly
- browse and manual path entry both work
- persistence survives app restart if applicable
- each output type uses the right configured default
- `auto` / `default` works
- specific path works
- timeout falls back correctly
- cancel/delete/nevermind leaves clean state
