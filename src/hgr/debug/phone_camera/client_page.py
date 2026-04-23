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
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
  <title>Touchless Phone Camera</title>
  <style>
    :root { color-scheme: dark; }
    html, body { margin: 0; padding: 0; background: #0B3D91; color: #E5F6FF;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      -webkit-user-select: none; user-select: none; overscroll-behavior: none; }
    body { display: flex; flex-direction: column; min-height: 100vh;
      padding: 18px 16px; box-sizing: border-box; }
    h1 { margin: 0 0 4px; font-size: 18px; font-weight: 600; }
    .subtitle { margin: 0 0 12px; font-size: 13px; opacity: 0.8; }
    .status { font-size: 14px; padding: 10px 12px; border-radius: 10px;
      background: rgba(255,255,255,0.08); margin-bottom: 10px; min-height: 22px; }
    .status.ok { background: rgba(29,233,182,0.18); }
    .status.err { background: rgba(255,99,99,0.22); }
    .preview-wrap { position: relative; flex: 1; min-height: 260px;
      background: #00112a; border-radius: 14px; overflow: hidden;
      display: flex; align-items: center; justify-content: center; }
    /* Fullscreen: some iOS Safaris don't support element.requestFullscreen,
       so we implement our own CSS fallback that stretches the preview
       to cover the viewport. .fs-active is toggled from JS. */
    .preview-wrap.fs-active { position: fixed; inset: 0; z-index: 999;
      border-radius: 0; }
    video { width: 100%; height: 100%; object-fit: cover;
      transform: scaleX(-1); /* phone-preview shows the selfie view locally */ }
    .stats { position: absolute; left: 10px; bottom: 10px; font-size: 11px;
      padding: 4px 8px; background: rgba(0,0,0,0.55); border-radius: 6px;
      font-variant-numeric: tabular-nums; }
    .fs-exit { position: absolute; right: 10px; top: 10px;
      font-size: 13px; padding: 8px 12px; background: rgba(0,0,0,0.7);
      color: #E5F6FF; border-radius: 20px; display: none; z-index: 1000;
      border: 1px solid rgba(255,255,255,0.3); }
    .preview-wrap.fs-active .fs-exit { display: inline-block; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
      margin-top: 12px; }
    .ctl-row { display: flex; align-items: center;
      background: rgba(255,255,255,0.06); border-radius: 10px; padding: 8px 12px; }
    .ctl-label { font-size: 11px; opacity: 0.7; margin-right: 8px; }
    .ctl-select { flex: 1; background: transparent; color: #E5F6FF;
      border: none; font-size: 14px; font-weight: 600; outline: none;
      -webkit-appearance: none; appearance: none; text-align: right;
      padding-right: 8px; }
    .ctl-select option { background: #0B3D91; color: #E5F6FF; }
    .buttons { display: flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
    button { flex: 1; min-width: 100px; padding: 14px 10px;
      font-size: 15px; font-weight: 600;
      border: none; border-radius: 10px; background: #1DE9B6; color: #003d2a;
      -webkit-appearance: none; }
    button:disabled { opacity: 0.5; }
    button.secondary { background: rgba(255,255,255,0.15); color: #E5F6FF; }
    .hint { font-size: 12px; opacity: 0.75; margin-top: 12px; line-height: 1.4; }
    .hint.compact { display: none; }
    body.mini h1,
    body.mini .subtitle,
    body.mini .hint { display: none; }
    body.mini .status { padding: 6px 10px; font-size: 12px; }
    body.mini .preview-wrap { min-height: 180px; }
    body.mini .controls { grid-template-columns: 1fr; }
    body.mini .ctl-row { padding: 6px 10px; }
  </style>
</head>
<body>
  <h1>Touchless Phone Camera</h1>
  <div class="subtitle">Keep this tab open while you use Touchless.</div>
  <div id="status" class="status">Tap Start to share your camera.</div>

  <div class="preview-wrap" id="previewWrap">
    <video id="preview" autoplay playsinline muted></video>
    <div class="stats" id="stats"></div>
    <button class="fs-exit" id="fsExit" type="button">Exit fullscreen</button>
  </div>

  <div class="controls">
    <label class="ctl-row">
      <span class="ctl-label">Resolution</span>
      <select class="ctl-select" id="resSelect">
        <option value="1080">1080p</option>
        <option value="720" selected>720p</option>
        <option value="480">480p</option>
        <option value="360">360p</option>
      </select>
    </label>
    <label class="ctl-row">
      <span class="ctl-label">Frame rate</span>
      <select class="ctl-select" id="fpsSelect">
        <option value="15">15 fps</option>
        <option value="24">24 fps</option>
        <option value="30" selected>30 fps</option>
        <option value="60">60 fps</option>
      </select>
    </label>
    <label class="ctl-row">
      <span class="ctl-label">Quality</span>
      <select class="ctl-select" id="qualSelect">
        <option value="0.6">low</option>
        <option value="0.75" selected>medium</option>
        <option value="0.88">high</option>
      </select>
    </label>
    <label class="ctl-row">
      <span class="ctl-label">Camera</span>
      <select class="ctl-select" id="facingSelect">
        <option value="environment" selected>rear</option>
        <option value="user">front</option>
      </select>
    </label>
  </div>

  <div class="buttons">
    <button id="start">Start</button>
    <button id="fs" class="secondary" type="button">Fullscreen</button>
    <button id="mini" class="secondary" type="button">Mini</button>
  </div>

  <div class="hint">
    <div><strong>First time setup:</strong> your browser will ask for camera permission — tap Allow.</div>
    <div style="margin-top:8px"><strong>If Start fails with a certificate warning on iPhone,</strong> you need to install and trust the Touchless certificate. Do this in order:</div>
    <ol style="margin:6px 0 6px 20px;padding:0;font-size:12px;line-height:1.5">
      <li><strong>If you previously installed any "Touchless Phone Camera" profile, delete it first:</strong> Settings → General → VPN &amp; Device Management → Touchless Phone Camera → Remove Profile. Earlier versions shipped certs iOS couldn't accept — they must be removed for the new one to install cleanly.</li>
      <li>Tap <a href="/touchless-cert.cer" download="touchless.cer" style="color:#1DE9B6;text-decoration:underline">Download Touchless cert</a>.</li>
      <li>iOS shows "Profile Downloaded." Open Settings. At the top of the main Settings screen you'll see <em>"Profile Downloaded"</em>. Tap it → Install → enter your passcode → Install → Done.</li>
      <li><strong>Now trust the cert fully:</strong> Settings → General → About → Certificate Trust Settings. You'll see "Touchless Phone Camera" under "Enable Full Trust for Root Certificates." Toggle it on → Continue.</li>
      <li><strong>Close this Safari tab completely</strong> (swipe it away from the tab switcher; don't just hit Back). Safari caches cert state per-tab and only re-evaluates on a fresh tab.</li>
      <li>Re-scan the QR code on your PC, let the new Safari tab open the page, and tap Start.</li>
    </ol>
  </div>

<script>
(() => {
  const statusEl = document.getElementById("status");
  const previewEl = document.getElementById("preview");
  const previewWrap = document.getElementById("previewWrap");
  const startBtn = document.getElementById("start");
  const fsBtn = document.getElementById("fs");
  const fsExitBtn = document.getElementById("fsExit");
  const miniBtn = document.getElementById("mini");
  const statsEl = document.getElementById("stats");
  const resSelect = document.getElementById("resSelect");
  const fpsSelect = document.getElementById("fpsSelect");
  const qualSelect = document.getElementById("qualSelect");
  const facingSelect = document.getElementById("facingSelect");

  let stream = null;
  let ws = null;
  let canvas = null;
  let ctx = null;
  let sending = false;
  let wakeLock = null;
  let frameCount = 0;
  let lastStatsAt = 0;
  let lastSentBytes = 0;
  let lastSentAt = 0;

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "status" + (kind ? " " + kind : "");
  }

  function currentRes() {
    const v = parseInt(resSelect.value, 10) || 720;
    // Map short-edge target to width/height. Phone cameras deliver
    // landscape natively; we request width:height with 16:9 aspect.
    const heightMap = { 1080: 1080, 720: 720, 480: 480, 360: 360 };
    const h = heightMap[v] || 720;
    const w = Math.round(h * 16 / 9);
    return { width: w, height: h };
  }
  function currentFps() { return parseInt(fpsSelect.value, 10) || 30; }
  function currentQuality() { return parseFloat(qualSelect.value) || 0.78; }

  async function openWebSocket() {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const url = proto + "//" + location.host + "/ws";
      const sock = new WebSocket(url);
      sock.binaryType = "arraybuffer";
      // Safari on iOS can park WSS connections in CONNECTING state
      // indefinitely when the self-signed cert isn't explicitly trusted.
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
        startBtn.disabled = false;
        startBtn.textContent = "Start";
      };
    });
  }

  async function probeHttp() {
    try {
      const resp = await fetch("/healthz", { method: "GET", cache: "no-store" });
      return resp.ok;
    } catch (_) { return false; }
  }

  async function requestWakeLock() {
    try {
      if ("wakeLock" in navigator) {
        wakeLock = await navigator.wakeLock.request("screen");
      }
    } catch (_) {}
  }

  async function openCamera() {
    if (stream) {
      stream.getTracks().forEach(t => t.stop());
      stream = null;
    }
    const res = currentRes();
    const fps = currentFps();
    const constraints = {
      audio: false,
      video: {
        facingMode: { ideal: facingSelect.value },
        width: { ideal: res.width },
        height: { ideal: res.height },
        frameRate: { ideal: fps, max: fps }
      }
    };
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    previewEl.srcObject = stream;
    const track = stream.getVideoTracks()[0];
    const settings = track.getSettings ? track.getSettings() : {};
    return { width: settings.width || res.width, height: settings.height || res.height, fps: settings.frameRate || fps };
  }

  async function startLoop() {
    startBtn.disabled = true;
    startBtn.textContent = "Connecting...";
    setStatus("Opening camera...", "");
    let s;
    try {
      s = await openCamera();
      setStatus("Camera ready at " + s.width + "x" + s.height + " @ " + Math.round(s.fps || currentFps()) + "fps. Connecting...", "");
    } catch (err) {
      setStatus("Camera denied: " + (err && err.name || err), "err");
      startBtn.disabled = false;
      startBtn.textContent = "Start";
      return;
    }
    try {
      ws = await openWebSocket();
    } catch (err) {
      const reachable = await probeHttp();
      let msg;
      if (!reachable) {
        msg = "Could not reach the PC. Make sure Touchless is still open and your phone is on the same WiFi as the PC.";
      } else if (err && err.message === "timeout") {
        msg = "Connected over HTTPS but the secure WebSocket stalled. On iPhone this is almost always the self-signed certificate — see the steps below, then close this Safari tab and re-scan the QR.";
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
    requestWakeLock();
    canvas = document.createElement("canvas");
    ctx = canvas.getContext("2d", { alpha: false });
    sending = true;
    frameCount = 0;
    lastStatsAt = performance.now();
    lastSentBytes = 0;
    lastSentAt = 0;
    sendFrames();
  }

  async function sendFrames() {
    const loop = async (ts) => {
      if (!sending) return;
      const targetInterval = 1000 / currentFps();
      if (ts - lastSentAt >= targetInterval) {
        lastSentAt = ts;
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
    const blob = await new Promise((r) => canvas.toBlob(r, "image/jpeg", currentQuality()));
    if (!blob) return;
    const buf = await blob.arrayBuffer();
    try { ws.send(buf); } catch (_) { return; }
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
  }

  function enterFullscreen() {
    // Try the native Fullscreen API (Chrome / Android Firefox). On iOS
    // Safari, element.requestFullscreen is a no-op for non-video
    // elements and the video's webkitEnterFullscreen forces landscape
    // and strips UI — so for iOS we fall back to our CSS-positioned
    // .fs-active layout that at least fills the viewport.
    previewWrap.classList.add("fs-active");
    const req = previewWrap.requestFullscreen || previewWrap.webkitRequestFullscreen;
    if (typeof req === "function") {
      try { req.call(previewWrap); } catch (_) {}
    }
  }

  function exitFullscreen() {
    previewWrap.classList.remove("fs-active");
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (typeof exit === "function") {
      try { exit.call(document); } catch (_) {}
    }
  }

  async function restartStreamOnSettingsChange() {
    if (!sending) return;
    setStatus("Updating camera settings...", "");
    try {
      const s = await openCamera();
      setStatus("Streaming (" + s.width + "x" + s.height + " @ " + Math.round(s.fps || currentFps()) + "fps). Keep this tab open.", "ok");
    } catch (err) {
      setStatus("Could not change settings: " + (err && err.name || err), "err");
    }
  }

  startBtn.addEventListener("click", () => { if (sending) stopLoop(); else startLoop(); });
  fsBtn.addEventListener("click", () => {
    if (previewWrap.classList.contains("fs-active")) exitFullscreen();
    else enterFullscreen();
  });
  fsExitBtn.addEventListener("click", exitFullscreen);
  miniBtn.addEventListener("click", () => {
    document.body.classList.toggle("mini");
    miniBtn.textContent = document.body.classList.contains("mini") ? "Expand" : "Mini";
  });
  // Changing res/fps/facing while streaming re-opens the camera with
  // the new constraints. Quality change is instantaneous (applied to
  // the next canvas.toBlob call) so no restart needed.
  resSelect.addEventListener("change", restartStreamOnSettingsChange);
  fpsSelect.addEventListener("change", restartStreamOnSettingsChange);
  facingSelect.addEventListener("change", restartStreamOnSettingsChange);

  // ESC / back-gesture to exit fullscreen
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) previewWrap.classList.remove("fs-active");
  });

  window.addEventListener("beforeunload", stopLoop);
})();
</script>
</body>
</html>
"""
