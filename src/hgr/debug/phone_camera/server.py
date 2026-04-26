"""HTTPS server for the phone-camera feature.

Runs on an aiohttp event loop hosted on a daemon thread so the Qt GUI
event loop stays untouched. Serves the phone HTML client, the root-CA
download endpoint, and accepts inbound frames via HTTP POST (which iOS
Safari honors under the user's trusted root) rather than WSS (which
iOS WebKit rejects even with a properly-trusted local root CA — the
single most-debugged quirk in this whole feature).

The POST-per-frame transport adds ~1ms of HTTP overhead per frame
compared to a long-lived WebSocket; in practice that's dwarfed by the
phone's JPEG encode time and fully acceptable on a LAN. TLS session
reuse keeps each subsequent POST cheap after the first one.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from aiohttp import web

from .audio_source import PhoneAudioSource
from .capture import PhoneCameraCapture
from .cert import PhoneCameraCertPaths, ensure_self_signed_cert
from .client_page import CLIENT_HTML


def _log(msg: str) -> None:
    try:
        sys.stderr.write(f"[phone-camera {time.strftime('%H:%M:%S')}] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


StatusCallback = Callable[[str, dict], None]


@dataclass(frozen=True)
class PhoneCameraServerInfo:
    host: str
    port: int
    url: str
    cert_path: str


class PhoneCameraServer:
    def __init__(self, port: int = 8765, on_status: Optional[StatusCallback] = None) -> None:
        self._port = int(port)
        self._on_status = on_status
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner: Optional[web.AppRunner] = None
        self._stop_future: Optional[asyncio.Future] = None
        self._capture = PhoneCameraCapture()
        # Shared phone audio source; voice pipeline reads from here when
        # the "Use phone microphone" toggle is on.
        self._audio_source = PhoneAudioSource()
        self._active_clients = 0
        self._last_frame_at = 0.0
        self._announced_stream = False
        self._announced_audio = False
        self._info: Optional[PhoneCameraServerInfo] = None
        self._cert: Optional[PhoneCameraCertPaths] = None
        # Set of asyncio.Queue objects, one per connected SSE client.
        # publish_event() iterates over these and pushes a JSON-encoded
        # event to each. The asyncio handler clears its queue from the
        # set on disconnect. Lock guards add/remove so the publish-from-
        # any-thread path doesn't race with the loop's removal.
        self._event_queues: set[asyncio.Queue] = set()
        self._event_queues_lock = threading.Lock()

    @property
    def capture(self) -> PhoneCameraCapture:
        return self._capture

    @property
    def audio_source(self) -> PhoneAudioSource:
        return self._audio_source

    @property
    def info(self) -> Optional[PhoneCameraServerInfo]:
        return self._info

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def connected_clients(self) -> int:
        return self._active_clients

    @property
    def seconds_since_last_frame(self) -> float:
        if self._last_frame_at <= 0.0:
            return float("inf")
        return time.monotonic() - self._last_frame_at

    def set_status_callback(self, on_status: Optional[StatusCallback]) -> None:
        self._on_status = on_status

    def start(self) -> PhoneCameraServerInfo:
        if self.is_running:
            assert self._info is not None
            return self._info
        self._cert = ensure_self_signed_cert()
        self._info = PhoneCameraServerInfo(
            host=self._cert.lan_ip,
            port=self._port,
            url=f"https://{self._cert.lan_ip}:{self._port}/",
            cert_path=str(self._cert.cert_path),
        )
        ready_event = threading.Event()
        start_exc: list[BaseException] = []

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            try:
                loop.run_until_complete(self._serve(ready_event, start_exc))
            except Exception as exc:
                if not start_exc:
                    start_exc.append(exc)
                    ready_event.set()
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                loop.close()
                self._loop = None

        self._thread = threading.Thread(target=_runner, daemon=True, name="PhoneCameraServer")
        self._thread.start()
        ready_event.wait(timeout=6.0)
        if start_exc:
            raise start_exc[0]
        self._emit_status("listening", {"url": self._info.url})
        return self._info

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._request_shutdown)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        self._runner = None
        self._stop_future = None
        self._capture.release()
        self._audio_source.close()
        self._emit_status("stopped", {})

    def _request_shutdown(self) -> None:
        if self._stop_future is not None and not self._stop_future.done():
            self._stop_future.set_result(None)

    async def _serve(self, ready_event: threading.Event, start_exc: list) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        assert self._cert is not None
        try:
            ctx.load_cert_chain(certfile=str(self._cert.cert_path), keyfile=str(self._cert.key_path))
        except Exception as exc:
            start_exc.append(exc)
            ready_event.set()
            return

        app = web.Application(client_max_size=8 * 1024 * 1024)  # 8 MiB per frame is plenty
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/index.html", self._handle_index)
        app.router.add_get("/healthz", self._handle_healthz)
        app.router.add_get("/cert", self._handle_cert)
        app.router.add_get("/cert.cer", self._handle_cert)
        app.router.add_get("/touchless.cer", self._handle_cert)
        app.router.add_get("/touchless-cert.cer", self._handle_cert)
        app.router.add_get("/ca.cer", self._handle_cert)
        app.router.add_get("/touchless-ca.cer", self._handle_cert)
        app.router.add_post("/frame", self._handle_frame)
        app.router.add_post("/audio", self._handle_audio)
        # Server-Sent Events stream pushed FROM the PC TO the phone.
        # Used to display gesture / voice toast notifications on the
        # phone screen so the user gets live feedback that the PC
        # actually saw what they did. iOS Safari supports EventSource
        # over HTTPS without the WSS-cert pain WebSockets hit.
        app.router.add_get("/events", self._handle_events)

        self._runner = web.AppRunner(app, handle_signals=False)
        try:
            await self._runner.setup()
            site = web.TCPSite(self._runner, host="0.0.0.0", port=self._port, ssl_context=ctx)
            await site.start()
        except Exception as exc:
            start_exc.append(exc)
            ready_event.set()
            return

        ready_event.set()
        self._stop_future = asyncio.get_running_loop().create_future()
        try:
            await self._stop_future
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await self._runner.cleanup()
            except Exception:
                pass

    def _peer(self, request: web.Request) -> str:
        try:
            peer = request.transport.get_extra_info("peername") if request.transport else None
            if isinstance(peer, tuple) and peer:
                return f"{peer[0]}:{peer[1]}"
        except Exception:
            pass
        return "?"

    async def _handle_index(self, request: web.Request) -> web.Response:
        _log(f"GET {request.path} from {self._peer(request)}")
        self._emit_status("phone_page_loaded", {"peer": self._peer(request)})
        return web.Response(
            body=CLIENT_HTML.encode("utf-8"),
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
        )

    async def _handle_healthz(self, request: web.Request) -> web.Response:
        _log(f"GET /healthz from {self._peer(request)}")
        return web.Response(text="ok", content_type="text/plain")

    async def _handle_cert(self, request: web.Request) -> web.Response:
        _log(f"GET {request.path} from {self._peer(request)}")
        try:
            body = self._cert.ca_cert_path.read_bytes() if self._cert is not None else b""
        except Exception:
            body = b""
        if not body:
            return web.Response(status=500, text="cert unavailable")
        return web.Response(
            body=body,
            headers={
                "Content-Type": "application/x-x509-ca-cert",
                "Content-Disposition": "attachment; filename=\"touchless-root-ca.cer\"",
                "Cache-Control": "no-store",
            },
        )

    async def _handle_frame(self, request: web.Request) -> web.Response:
        """Accept a single JPEG frame in the POST body."""
        peer = self._peer(request)
        first = not self._announced_stream
        try:
            payload = await request.read()
        except Exception as exc:
            _log(f"POST /frame from {peer} read error: {type(exc).__name__}")
            return web.Response(status=400, text="read failed")
        if not payload:
            return web.Response(status=204, text="")
        self._capture.push_jpeg(payload)
        self._last_frame_at = time.monotonic()
        if first:
            self._announced_stream = True
            self._active_clients = max(self._active_clients, 1)
            _log(f"POST /frame first frame from {peer} size={len(payload)} bytes")
            self._emit_status("client_connected", {"total": self._active_clients})
            self._emit_status("streaming", {"bytes": len(payload)})
        return web.Response(status=204, text="")

    async def _handle_audio(self, request: web.Request) -> web.Response:
        """Accept a chunk of raw 16-bit signed LE mono PCM.

        The phone captures audio via AudioWorklet, resamples to 48kHz
        mono Int16, and POSTs ~100ms chunks. We drop them into the
        PhoneAudioSource buffer; the voice pipeline reads from there
        when the "Use phone microphone" toggle is on.
        """
        peer = self._peer(request)
        try:
            payload = await request.read()
        except Exception as exc:
            _log(f"POST /audio from {peer} read error: {type(exc).__name__}")
            return web.Response(status=400, text="read failed")
        if not payload:
            return web.Response(status=204, text="")
        self._audio_source.push_pcm_int16(payload)
        if not self._announced_audio:
            self._announced_audio = True
            _log(f"POST /audio first chunk from {peer} size={len(payload)} bytes")
            self._emit_status("audio_streaming", {"bytes": len(payload)})
        return web.Response(status=204, text="")

    def _emit_status(self, event: str, data: dict) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(event, data)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Server-Sent Events: PC → phone notifications
    # ------------------------------------------------------------------

    async def _handle_events(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint. Each connected phone holds this open and reads
        events streamed from the PC.

        Wire format is the standard `text/event-stream`:
            data: {"kind": "gesture", "label": "Right swipe"}\\n\\n

        We send a heartbeat comment every 15s so iOS Safari's SSE
        connection doesn't get garbage-collected during long quiet
        stretches between events.
        """
        peer = self._peer(request)
        _log(f"GET /events from {peer} — SSE subscribed")
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # belt-and-suspenders: defeat any reverse-proxy buffering
            },
        )
        await response.prepare(request)

        # Per-client queue of events. publish_event() pushes onto this
        # via call_soon_threadsafe. We drain it and write SSE frames.
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        with self._event_queues_lock:
            self._event_queues.add(queue)
        try:
            # Initial hello — useful for debugging "did the phone connect?"
            await response.write(b"event: hello\ndata: {}\n\n")
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat: SSE-spec comment line. Some intermediaries
                    # (and iOS in low-power mode) drop the connection if
                    # nothing arrives for 30+ seconds.
                    try:
                        await response.write(b": heartbeat\n\n")
                    except (ConnectionResetError, asyncio.CancelledError):
                        break
                    continue
                if payload is None:
                    break
                try:
                    await response.write(payload)
                except (ConnectionResetError, asyncio.CancelledError):
                    break
        finally:
            with self._event_queues_lock:
                self._event_queues.discard(queue)
            _log(f"SSE disconnect from {peer}")
        return response

    def publish_event(self, kind: str, **fields) -> None:
        """Broadcast an event to all connected phone SSE clients.

        Safe to call from any thread — we marshal onto the asyncio
        loop. The phone's JS receives it as a JSON object on the
        EventSource and renders a toast.

        `kind` is one of:
            - "gesture": fields {label, action_text?}
            - "voice":   fields {text}
            - "status":  fields {message}    (catch-all)

        Fields are arbitrary JSON-serializable values; the phone's
        toast renderer reads `label` for gestures, `text` for voice,
        `message` for status.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        try:
            payload_obj = {"kind": str(kind), **fields}
            payload_json = json.dumps(payload_obj, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        message = f"data: {payload_json}\n\n".encode("utf-8")
        # Snapshot the queues under the lock, then dispatch. We do the
        # actual put() via call_soon_threadsafe so we don't fight the
        # event loop for queue access from a non-loop thread.
        with self._event_queues_lock:
            queues = list(self._event_queues)
        if not queues:
            return
        for queue in queues:
            try:
                loop.call_soon_threadsafe(self._enqueue_event, queue, message)
            except RuntimeError:
                # Loop is closing — drop silently.
                pass

    @staticmethod
    def _enqueue_event(queue: asyncio.Queue, message: bytes) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # Subscriber is too slow. Drop the oldest, push the new
            # one — toasts are ephemeral, freshness > completeness.
            try:
                _ = queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass
