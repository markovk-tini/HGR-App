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
    <label class="ctl-row">
      <span class="ctl-label">Mic</span>
      <select class="ctl-select" id="micSelect">
        <option value="off" selected>off</option>
        <option value="on">send to PC</option>
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
    <div style="margin-top:8px"><strong>If Start fails on iPhone, install and trust the Touchless Root CA.</strong> You only do this once, ever — after it's trusted, Touchless works on this phone forever, including across LAN IP changes.</div>
    <ol style="margin:6px 0 6px 20px;padding:0;font-size:12px;line-height:1.5">
      <li><strong>Delete any older Touchless profile first.</strong> Settings → General → VPN &amp; Device Management → tap anything named "Touchless Phone Camera" or "Touchless Server" → Remove Profile. Earlier builds shipped certs iOS couldn't accept; leaving them installed blocks the new root from working.</li>
      <li>Tap <a href="/touchless-cert.cer" download="touchless-root-ca.cer" style="color:#1DE9B6;text-decoration:underline">Download Touchless Root CA</a>.</li>
      <li>iOS shows "Profile Downloaded." Open Settings — at the top you'll see <em>"Profile Downloaded"</em>. Tap it → Install → enter passcode → Install → Done.</li>
      <li><strong>Trust the root fully:</strong> Settings → General → About → Certificate Trust Settings. "Touchless Root CA" appears under "Enable Full Trust for Root Certificates." Toggle it on → Continue.</li>
      <li><strong>Close this Safari tab</strong> (swipe it away from the tab switcher). Safari caches cert state per-tab — a fresh tab is required for iOS to re-evaluate trust.</li>
      <li>Re-scan the QR on your PC from a fresh Safari tab and tap Start.</li>
    </ol>
    <div style="margin-top:8px;font-size:11px;opacity:0.7">If the profile doesn't appear as "Touchless Root CA" in Certificate Trust Settings, you're using an older build — pull latest Touchless on your PC and retry.</div>
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
  const micSelect = document.getElementById("micSelect");

  let stream = null;
  let canvas = null;
  let ctx = null;
  let sending = false;
  let wakeLock = null;
  let frameCount = 0;
  let lastStatsAt = 0;
  let lastSentBytes = 0;
  let lastSentAt = 0;
  let inflightPost = false;  // prevent overlapping POSTs — drop frames rather than pile up
  let consecutivePostErrors = 0;

  // Audio pipeline state.
  let audioContext = null;
  let audioSourceNode = null;
  let audioWorkletNode = null;
  let audioQueue = [];       // pending Int16 bytes not yet POSTed
  let audioQueuedBytes = 0;
  let audioInflight = false;
  let audioSampleRate = 48000;
  const AUDIO_CHUNK_BYTES = 9600;  // ~100ms of 48kHz mono Int16 (48000 * 2 * 0.1)

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
    const wantMic = micSelect.value === "on";
    const constraints = {
      audio: wantMic
        ? { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
        : false,
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
    if (wantMic) {
      try { await startAudioPipeline(stream); }
      catch (err) { console.warn("audio pipeline failed:", err); }
    } else {
      stopAudioPipeline();
    }
    return { width: settings.width || res.width, height: settings.height || res.height, fps: settings.frameRate || fps };
  }

  // --- Audio capture + upload ------------------------------------------------
  //
  // The AudioWorklet inline-registers as "pcm-capture". Each 128-sample tick
  // (browser default) arrives as Float32, we clamp + convert to Int16, and
  // post the bytes back to the main thread. Main thread batches ~100ms
  // worth (9600 bytes at 48kHz mono Int16) then POSTs to /audio.
  // PHONE_MIC_BOOST: iOS Safari's getUserMedia AGC pass delivers
  // speech at extremely low float amplitude (RMS ~0.0013 on iPhone
  // test devices — ~50x below a normal desktop mic at the same
  // distance). The PC-side voice pipeline needs at least ~0.01 RMS
  // to cross its activation threshold, so we amplify here before
  // the Int16 clamp. Peaks past the [-1,1] range just clamp — the
  // PC side re-normalizes the whole recording to 0.95 peak before
  // whisper sees it, so mild clipping doesn't degrade recognition.
  const PCM_WORKLET_SRC =
    "const BOOST = 4.0;" +
    "class PcmCapture extends AudioWorkletProcessor {" +
    "  process(inputs) {" +
    "    const ch = inputs[0] && inputs[0][0];" +
    "    if (!ch || !ch.length) return true;" +
    "    const out = new Int16Array(ch.length);" +
    "    for (let i = 0; i < ch.length; i++) {" +
    "      let s = ch[i] * BOOST; if (s > 1) s = 1; else if (s < -1) s = -1;" +
    "      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;" +
    "    }" +
    "    this.port.postMessage(out.buffer, [out.buffer]);" +
    "    return true;" +
    "  }" +
    "}" +
    "registerProcessor('pcm-capture', PcmCapture);";

  async function startAudioPipeline(mediaStream) {
    const audioTrack = mediaStream.getAudioTracks()[0];
    if (!audioTrack) return;
    // iOS Safari 14.1+ supports AudioWorklet; earlier doesn't. If it
    // isn't available, silently skip — voice still works with the
    // laptop mic on the PC side.
    if (!window.AudioContext && !window.webkitAudioContext) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    audioContext = new AC({ sampleRate: 48000 });
    audioSampleRate = audioContext.sampleRate || 48000;
    if (!audioContext.audioWorklet) {
      try { await audioContext.close(); } catch (_) {}
      audioContext = null;
      return;
    }
    const blob = new Blob([PCM_WORKLET_SRC], { type: "application/javascript" });
    const url = URL.createObjectURL(blob);
    try {
      await audioContext.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    audioSourceNode = audioContext.createMediaStreamSource(mediaStream);
    audioWorkletNode = new AudioWorkletNode(audioContext, "pcm-capture");
    audioWorkletNode.port.onmessage = (e) => {
      if (!sending) return;
      const buf = e.data;  // ArrayBuffer of Int16
      if (!buf || !(buf instanceof ArrayBuffer)) return;
      audioQueue.push(buf);
      audioQueuedBytes += buf.byteLength;
      // Fire-and-forget flush at ~100ms worth of samples.
      if (audioQueuedBytes >= AUDIO_CHUNK_BYTES) {
        flushAudioQueue();
      }
    };
    audioSourceNode.connect(audioWorkletNode);
    // Intentionally NOT connected to destination — we don't want to
    // play the mic back through the phone's speakers.
  }

  async function flushAudioQueue() {
    if (audioInflight || audioQueue.length === 0) return;
    // Coalesce pending chunks into one contiguous buffer.
    const merged = new Uint8Array(audioQueuedBytes);
    let offset = 0;
    for (const ab of audioQueue) {
      merged.set(new Uint8Array(ab), offset);
      offset += ab.byteLength;
    }
    audioQueue = [];
    audioQueuedBytes = 0;
    audioInflight = true;
    try {
      await fetch("/audio", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: merged,
        cache: "no-store",
        keepalive: false,
      });
    } catch (_) { /* drop on error; next chunk will retry */ }
    finally { audioInflight = false; }
  }

  function stopAudioPipeline() {
    if (audioWorkletNode) {
      try { audioWorkletNode.disconnect(); } catch (_) {}
      try { audioWorkletNode.port.close(); } catch (_) {}
      audioWorkletNode = null;
    }
    if (audioSourceNode) {
      try { audioSourceNode.disconnect(); } catch (_) {}
      audioSourceNode = null;
    }
    if (audioContext) {
      try { audioContext.close(); } catch (_) {}
      audioContext = null;
    }
    audioQueue = [];
    audioQueuedBytes = 0;
    audioInflight = false;
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
    // Verify the PC is reachable before we start pumping frames. If this
    // probe fails the user has a network/firewall problem, not a cert
    // one — surface that clearly instead of silently dropping frames.
    const reachable = await probeHttp();
    if (!reachable) {
      setStatus("Could not reach the PC. Make sure Touchless is still open on your PC and your phone is on the same WiFi network.", "err");
      startBtn.disabled = false;
      startBtn.textContent = "Start";
      return;
    }
    consecutivePostErrors = 0;
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
    if (!stream) return;
    // Skip the encode entirely if we're still waiting on the previous
    // POST — lets the network be the bottleneck instead of memory.
    if (inflightPost) return;
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
    inflightPost = true;
    let ok = false;
    try {
      // fetch() over HTTPS honors the user's trusted root CA (as opposed
      // to WSS, which iOS WebKit rejects even with a trusted local root).
      // This is the transport that Just Works on iOS self-signed setups.
      const resp = await fetch("/frame", {
        method: "POST",
        headers: { "Content-Type": "image/jpeg" },
        body: blob,
        cache: "no-store",
        keepalive: false,
      });
      ok = resp.ok || resp.status === 204;
    } catch (_) {
      ok = false;
    } finally {
      inflightPost = false;
    }
    if (ok) {
      consecutivePostErrors = 0;
      frameCount += 1;
      lastSentBytes += blob.size;
    } else {
      consecutivePostErrors += 1;
      if (consecutivePostErrors >= 10) {
        setStatus("Lost connection to the PC. Tap Start to reconnect.", "err");
        stopLoop();
        return;
      }
    }
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
    stopAudioPipeline();
    if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
    if (wakeLock) { try { wakeLock.release(); } catch (_) {} wakeLock = null; }
    statsEl.textContent = "";
    setStatus("Stopped.", "");
    startBtn.textContent = "Start";
    startBtn.disabled = false;
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
  micSelect.addEventListener("change", restartStreamOnSettingsChange);

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
