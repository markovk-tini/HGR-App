# Dictation voice commands — paused mid-test

Paused on 2026-04-22 to chase a separate bug in the volume overlay (Chrome
volume bar showing when nothing is playing from Chrome). Picking this up
later means resuming from the "Test plan" section below.

## State as of pause

### Landed on master
- Voice commands for newline / paragraph are implemented and committed in
  [noop_engine.py](../src/hgr/app/integration/noop_engine.py) via commit
  `efab5be` (merged from origin/main on 2026-04-22). The matcher and wiring
  I wrote during this session are already in the tree.
- Mistake-proof matching: the committed utterance must be **exactly** the
  command phrase (after strip-punctuation, collapse-whitespace, lowercase).
  17/17 positive/negative test cases verified during implementation.

### Uncommitted locally
- [whisper_stream.py](../src/hgr/voice/whisper_stream.py) — dead-code removal
  from the commit-only migration. Removed:
  - `_MIN_DECODE_MS`, `_DECODE_INTERVAL_MS` constants (never read)
  - `_LocalAgreement` class (instantiated but `.update()` never called)
  - `_TOKEN_TRAIL_PUNCT`, `_TOKEN_LEAD_PUNCT`, `_norm_token` helpers
  - `samples_at_last_decode`, `last_hyp_text` locals in the stream loop
  - Updated the WhisperStreamer class docstring to describe the actual
    (commit-only) architecture.

  The file parses cleanly. Commit when ready — suggested message:
  `chore: remove dead code from whisper_stream.py post commit-only migration`.

## Voice command reference

Say any of these alone (pause before AND after — the whole-utterance match is
the safety):

| Phrase | Effect |
|---|---|
| `new line` / `newline` / `next line` / `line break` | 1× Enter |
| `press enter` / `hit enter` / `press return` / `enter key` | 1× Enter |
| `new paragraph` / `paragraph break` | 2× Enter (blank line between paragraphs) |

Embedded usage is safe — "I need a **new line** of code here" will be typed
literally, because the commit contains more than just the command phrase.

## Test plan (what we were about to run)

### Two-paragraph dictation with `new paragraph`

Speak this naturally, pausing naturally between sentences:

> First, let's talk about the deployment issue from last night. The root
> cause was a missing config value in the Docker compose file that nobody
> caught during review. We rolled it back by midnight and everything
> recovered.
>
> *(pause, say "**new paragraph**", pause)*
>
> Second, for the follow-up, I'll file a ticket this morning and tag the
> platform team. We should also add a pre-deploy check that validates all
> required env vars are set before the container starts.

**What to verify:**
- Two distinct paragraphs separated by a blank line in the target window
- `[dictation] command: paragraph inserted=True text='New paragraph.'` in the log
- No literal `new paragraph` text typed into the target

### Plain `new line` test

End a sentence, pause, say "new line", pause, start a new sentence. Expect
1× Enter inserted, no literal text.

### Negative test (must NOT trigger)

Say: "I need a **new line** of code in my editor."
Expect: the full sentence typed as literal text, NO Enter insertion.

## What to watch in the log when testing

- `[dictation] command: newline` or `command: paragraph` — confirms detection.
- Grammar corrector should keep working on the text BEFORE and AFTER the
  command insertion (the `_flush_pending()` call before the Enter makes sure
  buffered text lands first, and `grammar_corrector.append("\n")` keeps the
  corrector's tracked-buffer length in sync with what's in the target
  window).

## Resume checklist

1. Commit the whisper_stream.py dead-code cleanup (or include it in the
   volume-bug commit if scope creeps).
2. Rerun the two-paragraph test above.
3. Rerun the negative test.
4. If both pass cleanly, this feature is done — move to the next item on
   the dictation roadmap (grammar-corrector cold-start timeout, or
   long-form typing + correction robustness).
