# Voice Pipeline

## Rules

- Prefer correct intent routing over broad guesswork.
- Follow-up voice prompts should feel stateful and timely.
- When a UI follow-up choice box appears, listening state and prompt state must be explicit.
- If the system is waiting for a user choice, confirm whether the microphone should stay active, pause, or re-open.

## High-risk voice areas

- choose-file / choose-folder follow-up flows
- app vs browser ambiguity
- search query cleanup for browser intents
- save-location prompts after user-generated output
- timeout-to-default behavior
- cancel/delete/nevermind handling

## Upcoming product requirements

Future flows should support save prompts for drawings, screenshots, screen recordings, and clips with these behaviors:
- user can say `auto` or `default`
- user can say a specific folder/path
- silence should fall back to the configured default save location
- user can say `cancel`, `delete`, or `nevermind`
