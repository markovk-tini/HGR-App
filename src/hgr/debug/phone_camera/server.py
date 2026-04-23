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
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from aiohttp import web

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

    def _emit_status(self, event: str, data: dict) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(event, data)
        except Exception:
            pass
