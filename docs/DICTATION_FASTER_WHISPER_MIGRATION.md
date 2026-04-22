# Dictation: faster-whisper migration (working state)

Snapshot of the dictation pipeline as of 2026-04-21, after the migration from
`whisper-stream.exe` subprocess to an in-process faster-whisper streamer.
This is the configuration that is known to work end-to-end with fast, accurate
live dictation on an RTX 4070.

## What the pipeline looks like now

```
sounddevice (16kHz mono, 100ms blocks)
  → RMS silence detection (worker thread accumulates utterance)
  → on silence ≥ 1500ms OR utterance ≥ 30s: ONE decode
  → faster-whisper large-v3-turbo, int8_float16, beam_size=1
  → DictationEvent("final", text)
  → noop_engine types it + grammar corrector polishes
```

The streamer lives in [whisper_stream.py](../src/hgr/voice/whisper_stream.py).
The wiring into the app lives in [noop_engine.py](../src/hgr/app/integration/noop_engine.py).

## The changes that made it work

### 1. Replaced whisper-stream.exe subprocess with faster-whisper
**Why it was necessary:** whisper.cpp's `stream.cpp` has a source-level bug in
its `n_new_line` calculation (`max(1, length_ms/step_ms - 1)`). With step=2000
length=5000 it commits every step and drops all audio past `--keep` (200ms).
Symptom: the first fragment of a sentence printed, then silence — even though
the user kept speaking.

**Fix:** rewrote [whisper_stream.py](../src/hgr/voice/whisper_stream.py) as an
in-process streamer using `sounddevice.InputStream` for audio capture and
`faster_whisper.WhisperModel` for decoding. No more subprocess, no more SDL
mic-index probe, no more stderr parser.

### 2. `compute_type="int8_float16"` on CUDA (not `float16`)
**Why it was necessary:** `float16` alone was giving 3-11s decode times for
5-20s utterances on an RTX 4070 — roughly real-time speed, which is broken
for turbo.

**Fix:** `int8_float16` engages the Ampere/Ada Tensor Cores properly. Decode
times dropped to 297-1016ms for 3-17s utterances (15-30x speedup).

Location: [whisper_stream.py](../src/hgr/voice/whisper_stream.py) —
`self._compute_type = "int8_float16" if cuda_ok else "int8"`.

### 3. Commit-only decoding (no mid-utterance re-decodes)
**Why it was necessary:** the original design decoded the full growing
utterance every 300ms and used LocalAgreement-2 to extract a stable prefix
for live hypothesis typing. With 3-11s decode times this was both extremely
wasteful (a 20s utterance got ~65 redundant decodes) and useless (the "live"
hypothesis was already seconds behind reality).

**Fix:** decode exactly once per utterance, at commit time (on silence or
max-utterance cap). Hypothesis events are no longer emitted. The user sees
the final transcription appear when they pause, not during speech. This
eliminates the redundant work and keeps decode time bounded.

### 4. `vad_filter=False` + `without_timestamps=True` in transcribe()
**Why it was necessary:** faster-whisper's internal VAD was trimming
mid-utterance audio in a way that caused word loss; we already do RMS-based
silence detection ourselves. Timestamp token generation is wasted work for
dictation (no UI uses per-token timing).

### 5. Silence threshold tuning
```python
_SILENCE_COMMIT_MS = 1500       # natural clause pauses don't fragment utterances
_RMS_SILENCE_THRESHOLD = 0.003  # quiet word-starts like "Hey" aren't eaten as leading silence
```

### 6. Time-gap based re-emission dedup in noop_engine
**Why it was necessary:** the old dedup in
[noop_engine.py:4491](../src/hgr/app/integration/noop_engine.py#L4491) used
`last_hypothesis_time <= last_final_time` as the "no new audio since last
commit" signal. Under commit-only decoding, hypothesis events never fire, so
`last_hypothesis_time` stays at 0 and the guard falsely drops every
legitimate repeat utterance.

**Fix:** collapsed to `since_commit < 2.0`. Whisper cannot re-emit old audio
under commit-only mode, so "duplicate" simply means "user said the same thing
within 2 seconds" — anything past that is intentional.

### 7. "Preparing Dictation Mode" overlay
Model load takes ~4 seconds. Before this fix, the listening mic appeared
immediately on hotkey but the streamer wasn't actually ready, so users who
spoke immediately lost the first word.

**Fix:** at dictation start, show `show_processing("Preparing Dictation Mode")`
(existing loading-dots renderer). When the streamer emits its `ready` event
(model loaded, mic open), swap to the listening mic overlay.

### 8. Qt cross-thread signal marshaling for the overlay swap
**Why it was necessary:** the `ready` event arrives on the dictation worker
thread. Calling `voice_status_overlay.show_listening()` directly crashes the
app — Qt widgets must only be touched from the GUI thread.

**Fix:** added `dictation_stream_ready = Signal(str)` on `GestureWorker`.
Worker thread emits it; Qt auto-queues across threads; main-thread slot
`_on_dictation_stream_ready` calls `show_listening()` safely.

## Verified working

Three-take test of a long, clause-heavy sentence
("Okay, so the main issue I'm seeing is that when I click the submit button,
nothing happens, even though the Network tab shows a 200 response from the
API. It's probably a state update bug. Can you check the reducer?"):

- Decode times: 297–1016ms for 3-17s utterances
- Full sentences captured in single commits
- No crash on overlay transition
- Grammar corrector responded in 0.2–0.9s with no timeouts

## Key files

| File | Role |
|------|------|
| [src/hgr/voice/whisper_stream.py](../src/hgr/voice/whisper_stream.py) | Streaming ASR — sounddevice capture + faster-whisper decode |
| [src/hgr/app/integration/noop_engine.py](../src/hgr/app/integration/noop_engine.py) | Dictation event handling, overlay wiring, grammar corrector glue |
| [src/hgr/gesture/ui/voice_status_overlay.py](../src/hgr/gesture/ui/voice_status_overlay.py) | `show_processing` / `show_listening` overlay |

## Known leftovers

- Unused constants in whisper_stream.py (`_MIN_DECODE_MS`,
  `_DECODE_INTERVAL_MS`, `_LocalAgreement` class, `samples_at_last_decode`,
  `last_hyp_text`) — residuals from the hypothesis-decoding design; safe to
  remove in a follow-up cleanup.
- Grammar corrector occasionally hits a 12s timeout on the very first submit
  after app start; fast thereafter. Likely cold-start or GPU contention.
  Not blocking dictation.
- Whisper sometimes hallucinates "Thank you." / "you" on trailing silence;
  `_strip_whisper_hallucinations` in noop_engine catches these.
