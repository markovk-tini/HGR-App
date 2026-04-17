"""Incremental commit algorithm for live dictation.

Converts a stream of (often unstable) partial ASR hypotheses into an
append-only stream of text deltas that are safe to type into the target
window. The guiding rule is: never type a word we might have to take
back. We therefore only commit the word-prefix that has already been
confirmed by two consecutive partials AND is not the very last word of
the current partial (which is always in flux until the ASR moves on or
the endpoint fires).
"""
from __future__ import annotations

from dataclasses import dataclass


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _words(text: str) -> list[str]:
    return _normalize(text).split()


def _longest_common_word_prefix(a: list[str], b: list[str]) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


@dataclass
class CommitResult:
    to_type: str           # text to inject into the target (may be empty)
    committed_words: int   # running committed word count within the utterance
    preview: str           # unstable tail, for debugging/UI


class StableCommitter:
    """Partial-transcript differ.

    Call :meth:`on_partial` for every partial hypothesis and
    :meth:`on_endpoint` when the recognizer signals an utterance boundary.
    Each call returns a :class:`CommitResult` whose ``to_type`` is the
    freshly-stable text to inject into the focused window.
    """

    def __init__(self) -> None:
        self._prev_words: list[str] = []
        self._committed_words: int = 0
        self._utterance_has_content: bool = False

    def reset_utterance(self) -> None:
        self._prev_words = []
        self._committed_words = 0
        self._utterance_has_content = False

    def on_partial(self, text: str) -> CommitResult:
        curr = _words(text)
        # Words identical at the same index in two consecutive partials
        # are considered stable, but we still keep the very last word of
        # the current partial on probation -- ASR often revises the tail
        # word before settling.
        lcp = _longest_common_word_prefix(self._prev_words, curr)
        stable = min(lcp, max(0, len(curr) - 1))

        delta = ""
        if stable > self._committed_words:
            new_words = curr[self._committed_words:stable]
            prefix = " " if self._utterance_has_content else ""
            delta = prefix + " ".join(new_words)
            self._committed_words = stable
            self._utterance_has_content = True

        self._prev_words = curr
        preview = " ".join(curr[self._committed_words:])
        return CommitResult(
            to_type=delta,
            committed_words=self._committed_words,
            preview=preview,
        )

    def on_endpoint(self, final_text: str) -> CommitResult:
        curr = _words(final_text)
        delta = ""
        if len(curr) > self._committed_words:
            new_words = curr[self._committed_words:]
            prefix = " " if self._utterance_has_content else ""
            delta = prefix + " ".join(new_words)

        # A trailing space between utterances keeps the next partial from
        # smashing into the end of this one. Only add it if something was
        # typed in this utterance -- pure-silence finalizations stay silent.
        if delta:
            out = delta + " "
        elif self._utterance_has_content:
            out = " "
        else:
            out = ""

        self.reset_utterance()
        return CommitResult(to_type=out, committed_words=0, preview="")
