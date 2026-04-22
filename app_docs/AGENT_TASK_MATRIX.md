# Agent Task Matrix

Use the smallest number of agents that gives real separation of work.

| Task type | Primary skill | Secondary skill | Required finishing skills |
|---|---|---|---|
| Static/dynamic gesture logic | `gesture-engineering` | `ui-runtime` if UI state involved | `test-wave`, `cleanup-wave`, `code-reviewer` |
| Voice parsing / follow-up prompts | `voice-intents` | `ui-runtime` if chooser or mic UI involved | `test-wave`, `cleanup-wave`, `code-reviewer` |
| UI freeze / modal / overlay issues | `ui-runtime` | `gesture-engineering` if hand control involved | `test-wave`, `cleanup-wave`, `code-reviewer` |
| Secondary hand-controlled selector windows | `secondary-selector-ui` | `gesture-engineering` | `test-wave`, `cleanup-wave`, `code-reviewer` |
| Packaging / runtime paths / installers | `packaging-release` | varies | `test-wave`, `cleanup-wave`, `code-reviewer` |
| Save-location settings and output-save flows | `save-output-flow` | `packaging-release` if path handling changes | `test-wave`, `cleanup-wave`, `code-reviewer` |

## Recommended wave order

1. Implementation wave
2. Test wave
3. Cleanup wave
4. Reviewer wave

## Example routing for the current requested task

### Hand control for drawing + screen wheel secondary windows
- primary: `secondary-selector-ui`
- secondary: `gesture-engineering`
- finish with: `test-wave`, `cleanup-wave`, `code-reviewer`

### Save Locations settings page
- primary: `save-output-flow`
- secondary: `packaging-release`
- finish with: `test-wave`, `cleanup-wave`, `code-reviewer`

### Post-action voice save prompt flow
- primary: `voice-intents`
- secondary: `save-output-flow`
- tertiary if needed: `ui-runtime`
- finish with: `test-wave`, `cleanup-wave`, `code-reviewer`
