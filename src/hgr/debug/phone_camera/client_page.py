"""HTML served to the phone browser.

Kept as a Python string (rather than a separate .html asset) so PyInstaller
bundling needs no extra datas entry. The page asks for the rear camera,
captures JPEG frames from a `<canvas>`, and streams them over the
WebSocket at `/ws`.
"""

CLIENT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>Touchless Phone Camera</title>
  <style>
    html, body { margin: 0; padding: 0; background: #0B3D91; color: #E5F6FF;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      -webkit-user-select: none; user-select: none; overscroll-behavior: none; }
    body { display: flex; flex-direction: column; min-height: 100vh;
      padding: 20px 16px; box-sizing: border-box; }
    h1 { margin: 0 0 4px; font-size: 18px; font-weight: 600; }
    .subtitle { margin: 0 0 16px; font-size: 13px; opacity: 0.8; }
    .status { font-size: 14px; padding: 10px 12px; border-radius: 10px;
      background: rgba(255,255,255,0.08); margin-bottom: 12px; min-height: 22px; }
    .status.ok { background: rgba(29,233,182,0.18); }
    .status.err { background: rgba(255,99,99,0.22); }
    .preview-wrap { position: relative; flex: 1; min-height: 280px;
      background: #00112a; border-radius: 14px; overflow: hidden;
      display: flex; align-items: center; justify-content: center; }
    video { width: 100%; height: 100%; object-fit: cover;
      transform: scaleX(-1); /* phone-preview shows the selfie view locally */ }
    .stats { position: absolute; left: 10px; bottom: 10px; font-size: 11px;
      padding: 4px 8px; background: rgba(0,0,0,0.55); border-radius: 6px;
      font-variant-numeric: tabular-nums; }
    .buttons { display: flex; gap: 10px; margin-top: 14px; }
    button { flex: 1; padding: 14px 10px; font-size: 15px; font-weight: 600;
      border: none; border-radius: 10px; background: #1DE9B6; color: #003d2a;
      -webkit-appearance: none; }
    button:disabled { opacity: 0.5; }
    button.secondary { background: rgba(255,255,255,0.15); color: #E5F6FF; }
    .hint { font-size: 12px; opacity: 0.75; margin-top: 12px; line-height: 1.4; }
  </style>
</head>
<body>
  <h1>Touchless Phone Camera</h1>
  <div class="subtitle">Keep this tab open while you use Touchless.</div>
  <div id="status" class="status">Tap Start to share your camera.</div>
  <div class="preview-wrap">
    <video id="preview" autoplay playsinline muted></video>
    <div class="stats" id="stats"></div>
  </div>
  <div class="buttons">
    <button id="start">Start</button>
    <button id="flip" class="secondary" disabled>Flip</button>
  </div>
  <div class="hint">
    <div><strong>First time setup:</strong> your browser will ask for camera permission — tap Allow.</div>
    <div style="margin-top:8px"><strong>If Start hangs on "Connecting..." for more than 15 seconds on iPhone,</strong>
      the self-signed certificate needs to be installed and trusted.
      <a href="/touchless-cert.cer" download="touchless.cer" style="color:#1DE9B6;text-decoration:underline">Download Touchless cert</a> → iOS will ask to install a profile → Settings → General → VPN &amp; Device Management → install → Settings → General → About → Certificate Trust Settings → enable for Touchless. Then come back and tap Start again.
    </div>
  </div>

<script>
(() => {
  const statusEl = document.getElementById("status");
  const previewEl = document.getElementById("preview");
  const startBtn = document.getElementById("start");
  const flipBtn = document.getElementById("flip");
  const statsEl = document.getElementById("stats");

  let stream = null;
  let ws = null;
  let canvas = null;
  let ctx = null;
  let sending = false;
  let facingMode = "environment";
  let wakeLock = null;
  let frameCount = 0;
  let lastStatsAt = 0;
  let lastSentBytes = 0;

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "status" + (kind ? " " + kind : "");
  }

  async function openWebSocket() {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const url = proto + "//" + location.host + "/ws";
      const sock = new WebSocket(url);
      sock.binaryType = "arraybuffer";
      // Safari on iOS can park WSS connections in CONNECTING state
      // indefinitely when the self-signed cert isn't explicitly trusted
      // (the "Visit Website" exception on HTTPS doesn't always carry
      // over to WSS). Time out so the user sees a clear error instead
      // of a frozen "Connecting..." forever.
      const timeout = setTimeout(() => {
        try { sock.close(); } catch (_) {}
        reject(new Error("timeout"));
      }, 10000);
      sock.onopen = () => { clearTimeout(timeout); resolve(sock); };
      sock.onerror = (e) => { clearTimeout(timeout); reject(e); };
      sock.onclose = () => {
        clearTimeout(timeout);
        setStatus("Disconnected from PC. Tap Start to reconnect.", "err");
        sending = false;
        flipBtn.disabled = true;
        startBtn.disabled = false;
        startBtn.textContent = "Start";
      };
    });
  }

  async function probeHttp() {
    // Sanity check: can we reach the PC over HTTPS at all? If this
    // fails, the WSS attempt will fail too. If it succeeds but WSS
    // fails, the cert-trust-for-WSS problem (common on iOS) is the
    // likely cause.
    try {
      const resp = await fetch("/healthz", { method: "GET", cache: "no-store" });
      return resp.ok;
    } catch (_) {
      return false;
    }
  }

  async function requestWakeLock() {
    try {
      if ("wakeLock" in navigator) {
        wakeLock = await navigator.wakeLock.request("screen");
      }
    } catch (_) { /* no-op */ }
  }

  async function openCamera(mode) {
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }
    const constraints = {
      audio: false,
      video: {
        facingMode: { ideal: mode },
        width: { ideal: 1280 },
        height: { ideal: 720 },
        frameRate: { ideal: 30, max: 30 }
      }
    };
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    previewEl.srcObject = stream;
    const track = stream.getVideoTracks()[0];
    const settings = track.getSettings ? track.getSettings() : {};
    return { width: settings.width || 0, height: settings.height || 0 };
  }

  async function startLoop() {
    startBtn.disabled = true;
    startBtn.textContent = "Connecting...";
    setStatus("Opening camera...", "");
    try {
      const s = await openCamera(facingMode);
      setStatus("Camera ready at " + s.width + "x" + s.height + ". Connecting...", "");
    } catch (err) {
      setStatus("Camera denied: " + (err && err.name || err), "err");
      startBtn.disabled = false;
      startBtn.textContent = "Start";
      return;
    }
    try {
      ws = await openWebSocket();
    } catch (err) {
      // Differentiate: can the phone reach the PC over HTTPS at all?
      const reachable = await probeHttp();
      let msg;
      if (!reachable) {
        msg = "Could not reach the PC. Make sure Touchless is still open and your phone is on the same WiFi as the PC.";
      } else if (err && err.message === "timeout") {
        msg = "Connected to the PC over HTTPS but the secure WebSocket stalled. On iPhone this is usually the self-signed certificate — you may need to install and trust the Touchless certificate in iOS Settings. See the hint below, then tap Start again.";
      } else {
        msg = "Could not open the video stream. " + (err && err.name ? err.name : "") + " — tap Start to retry.";
      }
      setStatus(msg, "err");
      startBtn.disabled = false;
      startBtn.textContent = "Start";
      return;
    }
    setStatus("Streaming. Keep this tab open.", "ok");
    startBtn.textContent = "Stop";
    startBtn.disabled = false;
    flipBtn.disabled = false;
    requestWakeLock();
    canvas = document.createElement("canvas");
    ctx = canvas.getContext("2d", { alpha: false });
    sending = true;
    frameCount = 0;
    lastStatsAt = performance.now();
    lastSentBytes = 0;
    sendFrames();
  }

  async function sendFrames() {
    const targetFps = 22;
    const targetInterval = 1000 / targetFps;
    let last = 0;
    const loop = async (ts) => {
      if (!sending) return;
      if (ts - last >= targetInterval) {
        last = ts;
        await sendOneFrame();
      }
      if (sending) requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  async function sendOneFrame() {
    if (!stream || !ws || ws.readyState !== 1) return;
    const track = stream.getVideoTracks()[0];
    if (!track) return;
    const s = track.getSettings ? track.getSettings() : {};
    const w = s.width || 640;
    const h = s.height || 480;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    ctx.drawImage(previewEl, 0, 0, w, h);
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.78));
    if (!blob) return;
    const buf = await blob.arrayBuffer();
    try {
      ws.send(buf);
    } catch (_) { return; }
    frameCount += 1;
    lastSentBytes += buf.byteLength;
    const now = performance.now();
    if (now - lastStatsAt >= 1000) {
      const fps = frameCount * 1000 / (now - lastStatsAt);
      const kbps = (lastSentBytes * 8 / 1024) / ((now - lastStatsAt) / 1000);
      statsEl.textContent = fps.toFixed(1) + " fps | " + kbps.toFixed(0) + " kbps | " + w + "x" + h;
      frameCount = 0;
      lastSentBytes = 0;
      lastStatsAt = now;
    }
  }

  function stopLoop() {
    sending = false;
    if (ws) { try { ws.close(); } catch (_) {} ws = null; }
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    if (wakeLock) { try { wakeLock.release(); } catch (_) {} wakeLock = null; }
    statsEl.textContent = "";
    setStatus("Stopped.", "");
    startBtn.textContent = "Start";
    flipBtn.disabled = true;
  }

  startBtn.addEventListener("click", () => {
    if (sending) stopLoop();
    else startLoop();
  });

  flipBtn.addEventListener("click", async () => {
    if (!sending) return;
    facingMode = facingMode === "environment" ? "user" : "environment";
    try {
      const s = await openCamera(facingMode);
      setStatus("Streaming (" + facingMode + ") at " + s.width + "x" + s.height + ".", "ok");
    } catch (err) {
      setStatus("Could not flip: " + (err && err.name || err), "err");
    }
  });

  window.addEventListener("beforeunload", stopLoop);
})();
</script>
</body>
</html>
"""
