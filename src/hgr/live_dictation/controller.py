"""High-level dictation controller.

Glues together:
  * an ASR backend (sherpa-onnx, or Windows System.Speech fallback)
  * the :class:`StableCommitter` that converts partials into safe deltas
  * :func:`type_text` which injects those deltas into the focused window

Callers interact through :meth:`start`, :meth:`stop`, and an
:class:`DictationObserver` that receives state/debug updates. The
controller is thread-safe: callbacks fire on the ASR worker thread,
but state transitions and typing are guarded by internal locks.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from . import typing_injector
from .stable_commit import CommitResult, StableCommitter
from .states import DictationState

log = logging.getLogger(__name__)


class DictationObserver(Protocol):
    def on_state_changed(self, state: DictationState, message: str | None) -> None: ...

    def on_debug(self, message: str) -> None: ...

    def on_typed(self, text: str) -> None: ...


class _NullObserver:
    def on_state_changed(self, state: DictationState, message: str | None) -> None:
        pass

    def on_debug(self, message: str) -> None:
        pass

    def on_typed(self, text: str) -> None:
        pass


@dataclass
class DictationOptions:
    """Tunables for the dictation controller."""

    prefer_sherpa: bool = True
    """If True, try sherpa-onnx first and fall back to System.Speech."""

    type_commits: bool = True
    """Whether to inject committed deltas via SendInput. Disable for dry runs."""


class DictationController:
    def __init__(
        self,
        observer: DictationObserver | None = None,
        options: DictationOptions | None = None,
    ) -> None:
        self._observer: DictationObserver = observer or _NullObserver()
        self._options = options or DictationOptions()
        self._committer = StableCommitter()
        self._state = DictationState.OFF
        self._state_lock = threading.Lock()
        self._worker = None
        self._backend_name = "idle"
        self._type_lock = threading.Lock()

    @property
    def state(self) -> DictationState:
        return self._state

    @property
    def backend(self) -> str:
        return self._backend_name

    def is_active(self) -> bool:
        return self._state not in (DictationState.OFF, DictationState.ERROR)

    # -- lifecycle ---------------------------------------------------

    def start(self) -> bool:
        if self.is_active():
            return True
        if not typing_injector.available():
            self._transition(DictationState.ERROR, "typing injection only works on Windows")
            return False

        self._committer.reset_utterance()
        worker, backend_name, err = self._pick_backend()
        if worker is None:
            self._transition(DictationState.ERROR, err or "no dictation backend available")
            return False

        self._worker = worker
        self._backend_name = backend_name
        self._observer.on_debug(f"dictation: using backend {backend_name}")

        if not worker.start():
            # _handle_worker_state has already surfaced the reason
            self._worker = None
            self._backend_name = "idle"
            return False

        self._transition(DictationState.LISTENING, None)
        return True

    def stop(self) -> None:
        if self._state == DictationState.OFF:
            return
        worker = self._worker
        self._worker = None
        self._transition(DictationState.OFF, None)
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                log.exception("worker stop failed")
        self._backend_name = "idle"

    # -- backend selection ------------------------------------------

    def _pick_backend(self):
        from .sherpa_worker import SherpaWorker
        from .system_speech_worker import SystemSpeechWorker

        tried: list[str] = []

        if self._options.prefer_sherpa:
            ok, reason = SherpaWorker.available()
            if ok:
                return (
                    SherpaWorker(
                        on_partial=self._handle_partial,
                        on_endpoint=self._handle_endpoint,
                        on_state=self._handle_worker_state,
                    ),
                    "sherpa-onnx",
                    None,
                )
            tried.append(f"sherpa-onnx: {reason}")
            log.info("sherpa backend unavailable: %s", reason)

        ok, reason = SystemSpeechWorker.available()
        if ok:
            return (
                SystemSpeechWorker(
                    on_partial=self._handle_partial,
                    on_endpoint=self._handle_endpoint,
                    on_state=self._handle_worker_state,
                ),
                "system-speech",
                None,
            )
        tried.append(f"system-speech: {reason}")
        return None, "idle", "; ".join(tried)

    # -- worker callbacks -------------------------------------------

    def _handle_worker_state(self, kind: str, message: str | None) -> None:
        mapping = {
            "listening": DictationState.LISTENING,
            "speaking": DictationState.SPEAKING,
            "error": DictationState.ERROR,
            "off": DictationState.OFF,
        }
        state = mapping.get(kind)
        if state is None:
            return
        self._transition(state, message)

    def _handle_partial(self, text: str) -> None:
        try:
            result = self._committer.on_partial(text)
        except Exception:
            log.exception("committer failed on partial")
            return
        self._emit_commit(result, "partial")

    def _handle_endpoint(self, text: str) -> None:
        self._transition(DictationState.FINALIZING, None)
        try:
            result = self._committer.on_endpoint(text)
        except Exception:
            log.exception("committer failed on endpoint")
            self._transition(DictationState.LISTENING, None)
            return
        self._emit_commit(result, "endpoint")
        self._transition(DictationState.LISTENING, None)

    def _emit_commit(self, result: CommitResult, source: str) -> None:
        if not result.to_type:
            return
        self._observer.on_debug(
            f"{source} commit: {result.to_type!r} (preview={result.preview!r})"
        )
        if not self._options.type_commits:
            return
        # Serialize typing so bursty endpoint + partial commits can't
        # interleave mid-word.
        with self._type_lock:
            try:
                typing_injector.type_text(result.to_type)
            except Exception:
                log.exception("type_text failed")
                return
        try:
            self._observer.on_typed(result.to_type)
        except Exception:
            log.exception("on_typed observer failed")

    # -- state plumbing ---------------------------------------------

    def _transition(self, new_state: DictationState, message: str | None) -> None:
        with self._state_lock:
            if self._state == new_state:
                return
            self._state = new_state
        try:
            self._observer.on_state_changed(new_state, message)
        except Exception:
            log.exception("state observer failed")
