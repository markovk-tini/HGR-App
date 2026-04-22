# Dictation: structured-format corrections via the grammar corrector

Snapshot of how spoken emails, phone numbers, and URLs get converted to their
standard written forms during dictation. Implemented 2026-04-21 as an
extension to the existing Qwen 2.5 3B grammar corrector prompt, NOT as a
separate regex pass.

## Where this happens

The conversion rules live in the system prompt at
[llama_server.py — `_CORRECTION_SYSTEM_PROMPT`](../src/hgr/voice/llama_server.py#L19).
Each dictation chunk gets sent to the corrector (~0.2–0.9s turnaround on CUDA).
The model returns cleaned text with format conversions already applied.

## What whisper already handles natively (don't re-solve)

From the 2026-04-21 test ("Her email is p.sharma at example.com"), whisper
by itself handled, without any post-processing:

| Spoken | Whisper output | Why it works |
|---|---|---|
| "p dot sharma" | `p.sharma` | Training data has lots of email-prefix patterns |
| "example dot com" | `example.com` | Same — domains collapse to dots |
| "v two point one point three" | `v2.1.3` | Version strings are common tokens |
| "non reversible" | `non-reversible` | Hyphenation from training data |
| "Friday's" | `Friday's` | Possessive apostrophe |

**What whisper does NOT handle:**
- `at` → `@` — too ambiguous with English "at" (meet at noon, look at this)
- Phone number grouping — outputs raw digit words or a single run
- URL paths with `/` — outputs "slash" as a word

Those three gaps are what the grammar corrector now fills.

## Rules added to the grammar corrector prompt

New rule 5 handles three structured formats. Full text is in the source;
summary:

### Email
- Trigger: preceded by "email", "address", "send it to", or surrounded by
  dotted tokens on both sides of "at".
- `p.sharma at example.com` → `p.sharma@example.com`
- `john dot smith at company dot com` → `john.smith@company.com`
- **Explicit negative cases** in the prompt so the model doesn't over-correct:
  "meet at noon", "starts at 3pm", "arrive at the office", "look at this".

### URL
- `www dot X dot Y` → `www.X.Y`
- `X dot com slash path` → `X.com/path`

### Phone
- Area-code patterns → `555-123-4567`
- "plus one" prefix → `+1-555-123-4567`
- Ambiguous digit strings left alone.

## Why prompt-based (not regex)

- Grammar corrector is already in the pipeline — zero added latency.
- Qwen 2.5 3B can use surrounding context to disambiguate "at". Regex
  cannot — `\S+\s+at\s+\S+` false-positives on every temporal "at".
- Failure mode is graceful: if the model can't tell, it leaves the text
  unchanged (rule 7: "If the input is already clean, return it exactly
  unchanged").

## Test cases to run when picking this back up

Run the standard 3-take pattern for each. The grammar corrector should
produce the **Expected** column.

| # | Spoken sentence | Expected (after corrector) |
|---|---|---|
| 1 | "Her email is p dot sharma at example dot com" | `Her email is p.sharma@example.com.` |
| 2 | "Send the invoice to billing at acme dot co dot uk by Friday" | `Send the invoice to billing@acme.co.uk by Friday.` |
| 3 | "Call me at 555 123 4567 after lunch" | `Call me at 555-123-4567 after lunch.` (note: "at" stays as "at" — phone context, not email) |
| 4 | "The URL is www dot github dot com slash anthropics" | `The URL is www.github.com/anthropics.` |
| 5 | **Negative case.** "Let's meet at noon at the office" | `Let's meet at noon at the office.` (no @ injected) |
| 6 | **Negative case.** "The server crashed at 3 dot 14 AM" | `The server crashed at 3.14 AM.` (no @ injected; leading "at" is temporal) |

If case 5 or 6 gets `@` injected, the prompt is over-triggering and needs
tightening. If case 1 or 4 doesn't get converted, the trigger phrases need
widening.

## Known leftover work

- No test harness — verification is manual. A small fixture of
  (input, expected-output) pairs against `llama_server.correct()` would let
  us iterate on the prompt without running the full app.
- The grammar corrector is a full LLM call per chunk; total latency remains
  bounded by its ~0.2–0.9s response time, which is the same as before. No
  regression expected.
- Whisper's occasional trailing-silence hallucinations ("Thank you.", "you")
  are still filtered separately by `_strip_whisper_hallucinations` in
  `noop_engine.py` — unrelated to this change.
