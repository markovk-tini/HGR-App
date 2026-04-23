"""HTTPS + WebSocket server for the phone-camera feature.

Runs a single `websockets` server on its own asyncio event loop, itself
hosted on a daemon thread so the Qt event loop stays untouched. HTTP GETs
(including the root-page request for our HTML client) are handled inside
the websockets `process_request` hook — that means one port, one listener,
one library, one TLS context.

The caller gets a `PhoneCameraServer` it can `start()`, stream status
notifications out of (via `on_status`), and `stop()` cleanly. Frames
received over `/ws` are pushed into a `PhoneCameraCapture` that the
engine's existing camera-open path consumes as a drop-in VideoCapture.
"""
from __future__ import annotations

import asyncio
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Callable, Optional

from websockets.asyncio.server import serve as ws_serve
from websockets.datastructures import Headers
from websockets.http11 import Response as WsResponse

from .capture import PhoneCameraCapture
from .cert import PhoneCameraCertPaths, ensure_self_signed_cert
from .client_page import CLIENT_HTML


def _log(msg: str) -> None:
    """Stderr trace for the phone-camera server so live-server events are
    visible in the run_app.py terminal without interfering with the Qt GUI."""
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
        self._ws_server = None
        self._stop_future: Optional[asyncio.Future] = None
        self._capture = PhoneCameraCapture()
        self._active_clients = 0
        self._last_frame_at = 0.0
        self._announced_stream = False
        self._info: Optional[PhoneCameraServerInfo] = None
        self._cert: Optional[PhoneCameraCertPaths] = None

    @property
    def capture(self) -> PhoneCameraCapture:
        return self._capture

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
        # Wait briefly for the server to bind (or surface a bind error).
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
        self._ws_server = None
        self._stop_future = None
        self._capture.release()
        self._emit_status("stopped", {})

    def _request_shutdown(self) -> None:
        if self._stop_future is not None and not self._stop_future.done():
            self._stop_future.set_result(None)
        if self._ws_server is not None:
            try:
                self._ws_server.close()
            except Exception:
                pass

    async def _serve(self, ready_event: threading.Event, start_exc: list) -> None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        assert self._cert is not None
        try:
            ctx.load_cert_chain(certfile=str(self._cert.cert_path), keyfile=str(self._cert.key_path))
        except Exception as exc:
            start_exc.append(exc)
            ready_event.set()
            return

        try:
            self._ws_server = await ws_serve(
                self._handler,
                host="0.0.0.0",
                port=self._port,
                ssl=ctx,
                process_request=self._http_route,
                max_size=2 * 1024 * 1024,
                max_queue=4,
                ping_interval=20,
                ping_timeout=20,
            )
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
                self._ws_server.close()
                await self._ws_server.wait_closed()
            except Exception:
                pass

    def _peer_addr(self, connection) -> str:
        try:
            sock = getattr(connection, "socket", None)
            if sock is not None:
                peer = sock.getpeername()
                if isinstance(peer, tuple) and peer:
                    return f"{peer[0]}:{peer[1]}"
            transport = getattr(connection, "transport", None)
            if transport is not None:
                peer = transport.get_extra_info("peername")
                if isinstance(peer, tuple) and peer:
                    return f"{peer[0]}:{peer[1]}"
        except Exception:
            pass
        return "?"

    def _http_route(self, connection, request):
        """Intercept plain HTTP GETs; return None to let WebSocket upgrade proceed.

        websockets v16 passes (ServerConnection, Request) here. Returning a
        `Response` short-circuits the handshake with that payload. Returning
        None lets the caller continue to the WebSocket handshake.
        """
        try:
            path = str(getattr(request, "path", "") or "/")
        except Exception:
            path = "/"
        peer = self._peer_addr(connection)
        if path.startswith("/ws"):
            _log(f"WS  upgrade request from {peer} path={path}")
            return None
        _log(f"GET {path} from {peer}")
        if path in ("/", "/index.html"):
            # Phone's browser loaded the landing page — good signal that
            # network reachability is fine even if WSS later stalls.
            self._emit_status("phone_page_loaded", {"peer": peer})
            body = CLIENT_HTML.encode("utf-8")
            headers = Headers([
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("Cache-Control", "no-store"),
            ])
            return WsResponse(HTTPStatus.OK.value, "OK", headers, body)
        if path == "/healthz":
            body = b"ok"
            headers = Headers([
                ("Content-Type", "text/plain; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ])
            return WsResponse(HTTPStatus.OK.value, "OK", headers, body)
        if path in ("/cert", "/cert.cer", "/touchless.cer", "/touchless-cert.cer"):
            # Serve the Touchless cert for iOS trust installation. iOS
            # offers to install a .cer as a profile; after install the
            # user enables full trust in Settings -> General -> About ->
            # Certificate Trust Settings. Without this, Safari's WSS
            # connections to self-signed origins silently stall.
            try:
                body = self._cert.cert_path.read_bytes() if self._cert is not None else b""
            except Exception:
                body = b""
            if not body:
                headers = Headers([("Content-Type", "text/plain; charset=utf-8")])
                return WsResponse(HTTPStatus.INTERNAL_SERVER_ERROR.value, "No cert", headers, b"cert unavailable")
            headers = Headers([
                ("Content-Type", "application/x-x509-ca-cert"),
                ("Content-Length", str(len(body))),
                ("Content-Disposition", "attachment; filename=\"touchless.cer\""),
                ("Cache-Control", "no-store"),
            ])
            return WsResponse(HTTPStatus.OK.value, "OK", headers, body)
        body = b"Not found"
        headers = Headers([
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return WsResponse(HTTPStatus.NOT_FOUND.value, "Not Found", headers, body)

    async def _handler(self, websocket):
        peer = self._peer_addr(websocket)
        _log(f"WS  connected {peer}")
        self._active_clients += 1
        self._emit_status("client_connected", {"total": self._active_clients})
        self._announced_stream = False
        frame_count = 0
        try:
            async for message in websocket:
                if isinstance(message, (bytes, bytearray, memoryview)):
                    payload = bytes(message)
                    self._capture.push_jpeg(payload)
                    self._last_frame_at = time.monotonic()
                    frame_count += 1
                    if not self._announced_stream:
                        self._announced_stream = True
                        _log(f"WS  first frame {peer} size={len(payload)} bytes")
                        self._emit_status("streaming", {"bytes": len(payload)})
        except Exception as exc:
            _log(f"WS  handler exception {peer}: {type(exc).__name__}: {exc}")
        finally:
            self._active_clients = max(0, self._active_clients - 1)
            _log(f"WS  disconnected {peer} frames={frame_count}")
            self._emit_status("client_disconnected", {"total": self._active_clients})

    def _emit_status(self, event: str, data: dict) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(event, data)
        except Exception:
            pass
