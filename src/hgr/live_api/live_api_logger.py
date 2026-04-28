"""Structured + human-readable logging for the Live API prototype.

Two sinks are written for every session (default location, configurable
via LiveApiConfig.log_dir / LIVE_API_LOG_DIR env var):

  ~/Documents/Touchless/logs/live_api/live_api_YYYYMMDD_HHMMSS.log
  ~/Documents/Touchless/logs/live_api/live_api_events_YYYYMMDD_HHMMSS.jsonl

The JSONL stream is intended for offline replay/debugging. The text
log is what a developer reads in the terminal/IDE.

Privacy:
  * The API key is NEVER logged. We strip OPENAI_API_KEY from any
    payload that passes through `redact()`.
  * Raw audio is never logged. Audio events log only frame counts
    and timing.
  * Free text (transcripts, dictated text, code) is logged as a
    short preview + length + sha256 prefix unless
    `debug_text_logging` is enabled in `LiveApiConfig`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


_REDACT_KEYS = {"api_key", "apikey", "authorization", "openai_api_key", "token"}
_PREVIEW_CHARS = 64


def _safe_text_preview(text: str) -> Dict[str, Any]:
    text = "" if text is None else str(text)
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
    preview = text[:_PREVIEW_CHARS]
    return {"length": len(text), "sha256_12": digest, "preview": preview}


def redact(payload: Any) -> Any:
    """Recursively redact api keys / secrets from a payload."""
    if isinstance(payload, dict):
        out = {}
        for key, value in payload.items():
            if str(key).lower() in _REDACT_KEYS:
                out[key] = "***redacted***"
            else:
                out[key] = redact(value)
        return out
    if isinstance(payload, list):
        return [redact(v) for v in payload]
    return payload


class LiveApiLogger:
    """Per-session logger. Owns its files; cleans up on `close()`."""

    def __init__(
        self,
        *,
        log_dir: Path,
        debug_text_logging: bool = False,
    ) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._debug_text_logging = bool(debug_text_logging)
        self._session_started_at = time.time()

        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.text_log_path = log_dir / f"live_api_{stamp}.log"
        self.jsonl_log_path = log_dir / f"live_api_events_{stamp}.jsonl"

        # Use a dedicated stdlib logger (NOT the root logger) so the
        # rest of the app's logging is untouched.
        self._logger = logging.getLogger(f"hgr.live_api.{stamp}")
        self._logger.setLevel(logging.DEBUG)
        # Drop any old handlers from a previous session reusing this name.
        for h in list(self._logger.handlers):
            self._logger.removeHandler(h)

        text_handler = logging.FileHandler(self.text_log_path, encoding="utf-8")
        text_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
        )
        self._logger.addHandler(text_handler)

        # Don't propagate up to root; the app may not configure logging at all
        # and we don't want to spam stderr.
        self._logger.propagate = False

        self._jsonl_fh = open(self.jsonl_log_path, "a", encoding="utf-8")

        self.event("session_log_init", paths={
            "text": str(self.text_log_path),
            "jsonl": str(self.jsonl_log_path),
        })

    # ---- core ----

    def event(self, kind: str, **fields: Any) -> None:
        """Write a structured JSONL event + a human-readable line."""
        if self._closed:
            return
        record = {
            "ts": time.time(),
            "rel_ts": round(time.time() - self._session_started_at, 4),
            "kind": kind,
        }
        record.update(redact(fields))
        line = json.dumps(record, default=str, ensure_ascii=False)
        with self._lock:
            try:
                self._jsonl_fh.write(line + "\n")
                self._jsonl_fh.flush()
            except Exception:
                pass
        self._logger.info("%s %s", kind, _short_kv(record))

    def text(self, kind: str, text: str, **extra: Any) -> None:
        """Log a free-text payload, redacting unless debug_text_logging is on."""
        if self._debug_text_logging:
            payload = {"text": str(text), **extra}
        else:
            payload = {**_safe_text_preview(text), **extra}
        self.event(kind, **payload)

    def exception(self, kind: str, exc: BaseException, **extra: Any) -> None:
        self.event(
            kind,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback=traceback.format_exc(),
            **extra,
        )

    def info(self, msg: str, **extra: Any) -> None:
        # `message` is a common key for callers; if they pass it in `extra`
        # we must NOT also bind it from `msg`, or Python raises
        # "got multiple values for keyword argument 'message'".
        fields = dict(extra)
        if "message" not in fields:
            fields["message"] = msg
        else:
            fields["log_message"] = msg
        self.event("info", **fields)

    def warning(self, msg: str, **extra: Any) -> None:
        fields = dict(extra)
        if "message" not in fields:
            fields["message"] = msg
        else:
            fields["log_message"] = msg
        self.event("warning", **fields)
        self._logger.warning("%s %s", msg, _short_kv(extra))

    def error(self, msg: str, **extra: Any) -> None:
        fields = dict(extra)
        if "message" not in fields:
            fields["message"] = msg
        else:
            fields["log_message"] = msg
        self.event("error", **fields)
        self._logger.error("%s %s", msg, _short_kv(extra))

    def latency(self, kind: str, started_at: float, **extra: Any) -> None:
        elapsed = round((time.time() - started_at) * 1000.0, 2)
        self.event("latency", op=kind, elapsed_ms=elapsed, **extra)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._jsonl_fh.close()
            except Exception:
                pass
            for h in list(self._logger.handlers):
                try:
                    h.close()
                except Exception:
                    pass
                self._logger.removeHandler(h)


def _short_kv(record: Dict[str, Any], limit: int = 240) -> str:
    """Compact one-line key=value rendering for the text log."""
    try:
        text = json.dumps(record, default=str, ensure_ascii=False)
    except Exception:
        text = str(record)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


# ---- module-level fallback ----

_fallback_logger: Optional[LiveApiLogger] = None


def get_fallback_logger() -> LiveApiLogger:
    """Return a process-wide default logger (used only when nobody owns one)."""
    global _fallback_logger
    if _fallback_logger is None:
        # Mirror LiveApiConfig.log_dir default so the fallback writes to
        # the same user-writable location the installed app uses.
        default_dir = Path.home() / "Documents" / "Touchless" / "logs" / "live_api"
        _fallback_logger = LiveApiLogger(log_dir=default_dir)
    return _fallback_logger
