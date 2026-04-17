"""Live dictation subsystem.

Architecture (high-level):
  gesture "two"
      -> DictationController.start()
         -> AsrBackend starts (sherpa-onnx or Windows System.Speech)
         -> emits partial/final/endpoint callbacks
         -> StableCommitter diffs partials and returns append-only deltas
         -> TypingInjector (SendInput + KEYEVENTF_UNICODE) types the delta
            into whichever window currently has focus.

The controller is safe to stop/start many times; each utterance is
finalized independently.
"""
from .controller import DictationController, DictationObserver
from .states import DictationState

__all__ = ["DictationController", "DictationObserver", "DictationState"]
