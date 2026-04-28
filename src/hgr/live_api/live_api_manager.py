"""Top-level orchestrator for the Live API prototype.

`LiveApiManager` is what the main window owns. It exposes Qt signals
for UI updates (state changes, status text, errors) and coordinates:

  * AudioStream     -> microphone capture
  * ScreenContext   -> screenshot capture
  * RealtimeClient  -> websocket to OpenAI Realtime API
  * ToolExecutor    -> dispatches tool calls into existing Touchless

All long-running work happens off the UI thread:
  * audio capture runs on the sounddevice thread
  * the websocket runs on its own daemon reader thread
  * a periodic screen-capture timer runs on a worker thread
  * tool execution runs on the websocket reader thread by default

Signals are emitted via QObject.signal so consumers in the UI
thread receive them via the normal Qt queued-connection mechanism.
"""
from __future__ import annotations

import enum
import json
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from .audio_stream import AudioStream
from .config import LiveApiConfig, load_config
from .live_api_logger import LiveApiLogger
from .realtime_client import RealtimeClient
from .schemas import all_tool_schemas
from .screen_context import ScreenContext
from .tool_executor import ToolExecutor
from .tool_registry import ToolRegistry


SYSTEM_INSTRUCTIONS = (
    "You are Touchless Live Agent, a voice and screen-aware assistant "
    "controlling the user's own Windows PC through approved tools. "
    "Always use tools for computer actions — never claim an action is "
    "done unless the tool result confirms it. Screenshots are of the "
    "FULL multi-monitor virtual desktop; analyze the image content "
    "directly rather than relying on any window-title hint. The "
    "'Touchless' window in the screenshot is your own UI, not the "
    "user's target — ignore it and look elsewhere on screen for what "
    "the user is asking about. \n"
    "App naming hints for open_app on Windows: VS Code is 'code' (not "
    "'Visual Studio Code'); Chrome is 'chrome'; Spotify is 'spotify'; "
    "Notepad is 'notepad'; File Explorer is 'explorer'. If open_app "
    "returns an error, retry with a shorter / lower-case binary name. \n"
    "Prefer direct file and app actions over visual clicking. Ask for "
    "confirmation before destructive or risky actions. If unsure about "
    "screen state, call get_screen_context first. Never request or "
    "expose secrets. Never bypass security, DRM, anti-cheat, CAPTCHA, "
    "or non-skippable ads."
)


class LiveApiState(enum.Enum):
    OFF = "off"
    CONNECTING = "connecting"
    LISTENING = "listening"
    THINKING = "thinking"
    EXECUTING = "executing"
    ERROR = "error"


# A confirmation callback the manager will invoke for risky tools.
# The UI is expected to hand one over via `set_confirm_callback`.
ConfirmCallback = Callable[[str, str], bool]


class LiveApiManager(QObject):
    state_changed = Signal(object, str)        # (LiveApiState, status text)
    error_occurred = Signal(str)
    transcript_received = Signal(str)          # user speech transcript text
    assistant_text = Signal(str)               # assistant text deltas/snippets
    tool_event = Signal(str, dict)             # ("called"/"completed", info)

    def __init__(
        self,
        *,
        config: Optional[LiveApiConfig] = None,
        external_action_router: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        text_only: bool = False,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or load_config()
        # text_only=True means we don't open the mic / VAD pipeline; the
        # only inputs come from explicit `send_user_text` calls. Used by
        # the typed-command UI in Phase 1A. When voice comes back in
        # Phase 1B, leave this False.
        self._text_only = bool(text_only)
        self._logger: Optional[LiveApiLogger] = None
        self._client: Optional[RealtimeClient] = None
        self._audio: Optional[AudioStream] = None
        self._screen: Optional[ScreenContext] = None
        self._executor: Optional[ToolExecutor] = None
        self._registry: Optional[ToolRegistry] = None
        self._screen_thread: Optional[threading.Thread] = None
        self._screen_stop = threading.Event()
        self._screen_request = threading.Event()
        self._state = LiveApiState.OFF
        self._lock = threading.RLock()
        self._confirm_callback: Optional[ConfirmCallback] = None
        self._external_action_router = external_action_router
        self._pending_tool_calls: Dict[str, Dict[str, Any]] = {}
        # Set to True by stop() to make late WS-thread callbacks no-op.
        self._teardown_in_progress = False
        # Layer 0 deterministic command router — initialized lazily on
        # first start() once the per-session logger exists.
        self._command_router = None

    # ---- public API ----

    @property
    def config(self) -> LiveApiConfig:
        return self._config

    @property
    def state(self) -> LiveApiState:
        return self._state

    def set_confirm_callback(self, cb: Optional[ConfirmCallback]) -> None:
        self._confirm_callback = cb

    def is_running(self) -> bool:
        return self._state not in (LiveApiState.OFF, LiveApiState.ERROR)

    def start(self) -> None:
        with self._lock:
            if self._state not in (LiveApiState.OFF, LiveApiState.ERROR):
                return
            self._teardown_in_progress = False
            if not self._config.enabled:
                self._emit_error("Live API is disabled (TOUCHLESS_LIVE_API_ENABLED=false)")
                return
            backend_kind = (self._config.backend or "cloud").strip().lower()
            # Cloud needs an OpenAI key. Local doesn't.
            if backend_kind == "cloud" and not self._config.api_key:
                self._emit_error("OPENAI_API_KEY is not set")
                return

            self._logger = LiveApiLogger(
                log_dir=self._config.log_dir,
                debug_text_logging=self._config.debug_text_logging,
            )
            self._logger.event(
                "session_start",
                backend=backend_kind,
                model=self._config.model if backend_kind == "cloud" else self._config.local_llm_model_filename,
                send_screen_always=self._config.send_screen_always,
                send_screen_interval_sec=self._config.send_screen_interval_sec,
            )

            self._screen = ScreenContext(
                max_width=self._config.screen_max_width,
                jpeg_quality=self._config.screen_jpeg_quality,
                logger=self._logger,
                debug_save_dir=(self._config.log_dir / "screenshots") if self._config.debug_save_screenshots else None,
            )
            self._executor = ToolExecutor(
                config=self._config,
                logger=self._logger,
                screen_context=self._screen,
                confirm_callback=self._confirm_callback,
                external_action_router=self._external_action_router,
            )
            self._registry = ToolRegistry(self._executor)
            # Layer 0 router. Lazy-import keeps the manager loadable on
            # systems where Touchless's voice modules can't initialize
            # (e.g. headless CI without sounddevice).
            try:
                from .command_router import CommandRouter
                self._command_router = CommandRouter(logger=self._logger)
            except Exception as exc:
                self._logger.exception("command_router_init_failed", exc)
                self._command_router = None

            backend_kind = (self._config.backend or "cloud").strip().lower()
            if backend_kind == "local":
                # Local backend exposes the same shape as RealtimeClient
                # (start/stop/join, send_audio_chunk, send_tool_result,
                # request_response, on_event/on_connected/on_closed/
                # on_error). LiveApiManager treats them interchangeably.
                from .local_backend import LocalBackend
                self._client = LocalBackend(
                    config=self._config,
                    logger=self._logger,
                    tools=all_tool_schemas(),
                    system_instructions=SYSTEM_INSTRUCTIONS,
                    on_event=self._handle_event,
                    on_connected=self._on_ws_connected,
                    on_closed=self._on_ws_closed,
                    on_error=self._on_ws_error,
                    require_audio=not self._text_only,
                )
            else:
                self._client = RealtimeClient(
                    config=self._config,
                    logger=self._logger,
                    tools=all_tool_schemas(),
                    system_instructions=SYSTEM_INSTRUCTIONS,
                    on_event=self._handle_event,
                    on_connected=self._on_ws_connected,
                    on_closed=self._on_ws_closed,
                    on_error=self._on_ws_error,
                )

            if self._text_only:
                # No mic in text-only mode — the user types commands in
                # the UI instead of speaking. AudioStream stays None and
                # send_audio_chunk is never called.
                self._audio = None
            else:
                self._audio = AudioStream(
                    sample_rate=self._config.audio_sample_rate,
                    chunk_ms=self._config.audio_chunk_ms,
                    on_chunk=self._on_audio_chunk,
                    logger=self._logger,
                )

            self._set_state(LiveApiState.CONNECTING, "Connecting to Realtime API...")
            if not self._client.start():
                self._emit_error("WebSocket failed to start")
                return

    def stop(self) -> None:
        # Snapshot the things we need to tear down OUTSIDE the lock so the
        # WebSocket reader thread (which acquires the lock indirectly via
        # signal callbacks) can't deadlock against us. Setting the public
        # _state to OFF first also makes any in-flight callback bail
        # cleanly via their None-checks.
        with self._lock:
            if self._state == LiveApiState.OFF:
                return
            self._teardown_in_progress = True
            audio = self._audio
            client = self._client
            screen_thread = self._screen_thread
            logger = self._logger
            self._audio = None
            self._client = None
            self._screen_thread = None
            self._executor = None
            self._registry = None
            self._screen = None
            self._command_router = None
            # Don't drop the logger yet — we still want the stop events
            # written. Cleared once everything joined.
            self._screen_stop.set()
            self._screen_request.set()
            if logger is not None:
                logger.event("session_stop_requested")
            self._set_state(LiveApiState.OFF, "Off")

        # Heavy/joining work outside the lock.
        if audio is not None:
            try:
                audio.stop()
            except Exception:
                if logger is not None:
                    logger.warning("audio_stop_exception")
        if client is not None:
            try:
                client.stop()
                # Joining the reader thread guarantees no more callbacks
                # fire after we close the logger.
                client.join(timeout=2.0)
            except Exception:
                if logger is not None:
                    logger.warning("client_stop_exception")
        if screen_thread is not None:
            try:
                screen_thread.join(timeout=2.0)
            except Exception:
                pass

        with self._lock:
            if logger is not None:
                logger.event("session_stopped")
                logger.close()
            self._logger = None
            self._screen_stop.clear()
            self._screen_request.clear()
            self._pending_tool_calls.clear()

    def request_screen_now(self) -> None:
        """Wake the screen worker so it captures and sends immediately."""
        self._screen_request.set()

    def send_user_text(self, text: str) -> bool:
        """Inject a typed user message into the current session.

        Routing flow:
          1. Echo the user's text in the chat (transcript_received).
          2. Try Layer 0 router — if it matches a known intent (open
             chrome, search X, play next song, ...), execute it
             instantly and DO NOT call the LLM. The chat shows what
             happened via tool_event signals.
          3. Otherwise forward to the backend (LLM agent loop) as before.

        UI-thread safe: returns True if the manager accepted the input;
        False otherwise so the UI can show a hint.
        """
        text = (text or "").strip()
        if not text:
            return False
        client = self._client
        if client is None or not getattr(client, "connected", False):
            return False
        if self._state in (LiveApiState.OFF, LiveApiState.ERROR, LiveApiState.CONNECTING):
            return False
        # Echo the user's typed message via the same signal voice
        # transcripts use, so the chat UI doesn't need a special path.
        self.transcript_received.emit(text)

        # ---- Layer 0: deterministic router ----
        router = self._command_router
        if router is not None:
            try:
                routed = router.try_route(text)
            except Exception as exc:
                if self._logger:
                    self._logger.exception("router_unhandled", exc)
                routed = None
            if routed is not None and routed.matched:
                # Show what the router did using the same tool_event
                # plumbing the LLM tool calls use. The chat panel will
                # render "[router] action → ok / failed".
                action_label = routed.intent_action or "router"
                self.tool_event.emit("called", {"name": f"router/{action_label}", "info": routed.message})
                self.tool_event.emit(
                    "completed",
                    {
                        "name": f"router/{action_label}",
                        "status": "ok" if routed.success else "failed",
                    },
                )
                # Emit a one-line assistant confirmation so the chat
                # turn closes cleanly instead of looking abandoned.
                summary = routed.message or ("Done." if routed.success else "Couldn't run that command.")
                self.assistant_text.emit(summary)
                self._set_state(LiveApiState.LISTENING, "Ready (type a command)")
                return True

        # ---- Layer 1: LLM agent (router didn't match) ----
        ok = bool(client.send_text_message(text))
        if ok:
            client.request_response()
        return ok

    # ---- websocket callbacks ----

    def _on_ws_connected(self) -> None:
        if self._logger:
            self._logger.event("session_ws_connected", text_only=self._text_only)
        # Start mic + (cloud-only) screen worker once the backend is up.
        # Local backend has no vision in Phase 1, so the screen worker
        # would just burn CPU/memory capturing JPEGs nobody reads.
        if self._audio is not None:
            self._audio.start()
        if (self._config.backend or "cloud").strip().lower() == "cloud":
            self._start_screen_worker()
        if self._text_only:
            self._set_state(LiveApiState.LISTENING, "Ready (type a command)")
        else:
            self._set_state(LiveApiState.LISTENING, "Listening")

    def _on_ws_closed(self, reason: Optional[str]) -> None:
        if self._logger:
            self._logger.event("session_ws_closed", reason=reason)
        if self._state != LiveApiState.OFF:
            # Spontaneous close — surface as error so user can retry.
            self._set_state(LiveApiState.ERROR, f"Connection closed ({reason or 'unknown'})")

    def _on_ws_error(self, message: str) -> None:
        self._emit_error(message)

    # ---- audio ----

    def _on_audio_chunk(self, pcm16: bytes) -> None:
        client = self._client
        if client is None or not client.connected:
            return
        client.send_audio_chunk(pcm16)

    # ---- screen worker ----

    def _start_screen_worker(self) -> None:
        if self._screen_thread is not None:
            return
        self._screen_stop.clear()
        self._screen_request.clear()
        self._screen_thread = threading.Thread(
            target=self._screen_loop, name="LiveApiScreen", daemon=True
        )
        self._screen_thread.start()

    def _screen_loop(self) -> None:
        # Initial frame as soon as we're connected.
        self._capture_and_send_screen("session_start")
        interval = max(1.0, float(self._config.send_screen_interval_sec))
        while not self._screen_stop.is_set():
            triggered = self._screen_request.wait(timeout=interval)
            if self._screen_stop.is_set():
                break
            reason = "explicit_request" if triggered else "interval"
            if triggered:
                self._screen_request.clear()
            if not self._config.send_screen_always and not triggered:
                continue
            self._capture_and_send_screen(reason)

    def _capture_and_send_screen(self, reason: str) -> None:
        # Snapshot to locals so stop() can null these without racing us.
        screen = self._screen
        client = self._client
        logger = self._logger
        if screen is None or client is None or not client.connected:
            return
        started = time.time()
        try:
            frame = screen.capture()
        except Exception as exc:
            if logger:
                logger.exception("screen_capture_unhandled", exc)
            return
        if frame is None:
            return
        try:
            # Neutral caption: don't lead with the active window title
            # because the model otherwise parrots it back instead of
            # actually reading the image. The window title is still a
            # weak hint, just demoted.
            ok = client.send_screen_image(
                frame.b64,
                caption=(
                    "User's full multi-monitor screen capture below. "
                    f"(reason: {reason}; foreground hint: "
                    f"{frame.active_window_title or 'unknown'})"
                ),
            )
        except Exception as exc:
            if logger:
                logger.exception("screen_send_unhandled", exc)
            return
        if logger:
            logger.event(
                "screen_send",
                reason=reason,
                ok=ok,
                jpeg_kb=round(len(frame.jpeg_bytes) / 1024.0, 2),
                window=frame.active_window_title,
            )
            logger.latency("screen_send", started, ok=ok)

    # ---- realtime event router ----

    def _handle_event(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        # Transcripts of the user's speech (audio in -> text).
        if kind == "conversation.item.input_audio_transcription.completed":
            transcript = str(event.get("transcript") or "")
            if self._logger:
                self._logger.text("transcript_user", transcript)
            self.transcript_received.emit(transcript)
            return

        if kind in {"response.audio_transcript.delta", "response.text.delta"}:
            delta = str(event.get("delta") or "")
            if delta:
                if self._logger:
                    self._logger.text("assistant_delta", delta)
                self.assistant_text.emit(delta)
            return

        if kind in {"response.audio_transcript.done", "response.text.done"}:
            if self._logger:
                self._logger.event("assistant_done")
            self._set_state(LiveApiState.LISTENING, "Listening")
            return

        if kind == "response.created":
            self._set_state(LiveApiState.THINKING, "Thinking")
            return

        if kind == "response.done":
            # Log the full response payload so we can see when the model
            # produced no tool calls / no text deltas (often happens when
            # the model is uncertain or the transcription was a fragment).
            if self._logger:
                resp = event.get("response") or {}
                output = resp.get("output") or []
                output_kinds = [str(item.get("type") or "") for item in output if isinstance(item, dict)]
                self._logger.event(
                    "response_done_summary",
                    status=resp.get("status"),
                    output_kinds=output_kinds,
                    output_count=len(output),
                    usage=resp.get("usage"),
                )
            return

        if kind == "response.function_call_arguments.done":
            self._dispatch_function_call(event)
            return

        if kind == "error":
            err = event.get("error") or {}
            # Log the FULL error payload so the next debug pass can see
            # exactly what the server rejected (model, code, param, etc).
            if self._logger:
                self._logger.event("server_error_payload", payload=err)
            message = str(err.get("message") or err)
            code = str(err.get("code") or err.get("type") or "")
            param = str(err.get("param") or "")
            details = message
            if code:
                details = f"[{code}] {details}"
            if param:
                details = f"{details} (param={param})"
            self._emit_error(f"API error: {details}")
            return

        # Catch-all: many session/lifecycle events arrive — just log them.
        if self._logger:
            self._logger.event("event_passthrough", event_type=kind)

    def _dispatch_function_call(self, event: Dict[str, Any]) -> None:
        name = str(event.get("name") or "")
        call_id = str(event.get("call_id") or "")
        raw_args = event.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except Exception:
            args = {}
        if self._logger:
            self._logger.event("function_call_received", tool=name, call_id=call_id)
        self.tool_event.emit("called", {"name": name, "call_id": call_id})
        self._set_state(LiveApiState.EXECUTING, f"Executing tool: {name}")

        executor = self._executor
        client = self._client
        if executor is None or client is None:
            return

        try:
            output = executor.execute(name, args)
        except Exception as exc:  # defensive — executor already catches
            output = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        if self._logger:
            self._logger.event("function_call_completed", tool=name, status=output.get("status"))
        self.tool_event.emit("completed", {"name": name, "call_id": call_id, "status": output.get("status")})

        client.send_tool_result(call_id, output)
        client.request_response()
        self._set_state(LiveApiState.THINKING, "Thinking")

        # If the tool just changed something on screen, schedule a fresh
        # screenshot so the model sees the result of its own action.
        if name in {
            "click_screen", "type_text", "press_hotkey", "open_app",
            "open_url", "create_folder", "create_file", "write_file",
            "append_file", "skip_youtube_ad",
        }:
            self.request_screen_now()

    # ---- state helpers ----

    def _set_state(self, state: LiveApiState, status_text: str) -> None:
        # During/after teardown, drop late callbacks from the WebSocket
        # reader thread so we don't re-enter ERROR after the user
        # already clicked Stop.
        if self._teardown_in_progress and state != LiveApiState.OFF:
            return
        self._state = state
        logger = self._logger
        if logger is not None:
            try:
                logger.event("state_changed", state=state.value, status=status_text)
            except Exception:
                pass
        try:
            self.state_changed.emit(state, status_text)
        except Exception:
            pass

    def _emit_error(self, message: str) -> None:
        if self._logger:
            self._logger.error("session_error", message=message)
        self._set_state(LiveApiState.ERROR, f"Error: {message}")
        self.error_occurred.emit(message)
