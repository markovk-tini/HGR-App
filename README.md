# Touchless

Touchless is a Windows desktop assistant that lets you control your PC hands-free through hand gestures (via webcam) and voice commands. It combines real-time hand-pose recognition, gesture routing, local speech transcription, and a polished PySide6 interface so everyday desktop tasks — volume, media, browser, windows, dictation, app launching — can be driven without touching mouse or keyboard.

> Formerly developed under the working title *HGR App*. The project was renamed to **Touchless** as it moved out of prototype phase.

---

## What it does

At a high level, Touchless turns camera-tracked hand motions and spoken commands into desktop actions.

**Control areas:**

- **System** — volume up/down, mute/unmute, brightness. Volume can drive master volume or a per-app session (Chrome, Spotify, YouTube).
- **Mouse mode** — use a hand in the air as a cursor, with pinch-to-click and drag.
- **Drawing mode** — draw an on-screen overlay with gestures, saveable as PNG.
- **Browser** — open Chrome, back/forward, refresh, new/incognito tab, Chrome gesture wheel.
- **Media** — Spotify play/pause/skip/shuffle/repeat, YouTube gesture mode for the active Chrome tab.
- **Windows** — minimize, maximize, close active window.
- **Dictation** — live offline speech-to-text via whisper.cpp, streamed word-by-word into any focused text field.
- **Voice commands** — free-form spoken commands routed to Chrome, Spotify, File Explorer, Outlook, system settings, or the installed-app catalog.

**Camera inputs Touchless supports:**

- Any local Windows webcam (USB, internal, OEM virtual).
- IP-camera-style phone apps (IP Webcam on Android, Iriun / EpocCam on iOS) via an HTTPS stream URL.
- **Phone camera via QR code** — scan a QR, open a short-lived HTTPS page hosted inside Touchless, stream your phone's camera directly to the PC over the LAN. No phone-side app install required. Works on iOS and Android.

---

## User experience

The main app launches a custom-frameless PySide6 window with a home page and a settings stack. The home page includes **Start**, **End**, **Settings**, a runtime status card, and a **Live View** / debugger entry point. The settings page includes sections for Instructions, Gesture Guide, Camera, Microphone, Colors, Save Locations, and Tutorial. The tutorial window is a guided walkthrough built on top of the same runtime the main app uses.

Live gesture feedback shows through:
- A mini live viewer (corner preview)
- A full live-view window
- An in-app debugger with raw landmarks, confidence scores, and recognizer internals.

---

## Technical overview

Touchless is organized as a layered runtime:

1. **Camera + tracking**
   - Captures frames from the selected/default camera (local, phone URL, or phone-QR).
   - Produces landmarks and handedness information using MediaPipe.
   - Auto-switches into a lower-resolution, lower-complexity mode ("Low FPS Mode") when the host machine is CPU-starved, e.g. while a fullscreen game has foreground focus. Users are offered a single-click toggle if their measured FPS stays low.

2. **Gesture recognition**
   - Classifies static poses such as fist, one, two, three, four, mute, volume pose, wheel poses.
   - Tracks dynamic gestures such as swipe left, swipe right, and repeat/refresh motions.
   - Stabilizes results across frames with hold timing, cooldowns, and mode gating.

3. **Control routing**
   - Dispatches recognized gestures to subsystem controllers for Chrome, Spotify, YouTube, mouse, volume, drawing, window management, and voice/dictation activation.

4. **Voice pipeline**
   - Left-hand gestures toggle voice command / dictation modes.
   - Local speech transcription via whisper.cpp with automatic CUDA → Vulkan → CPU backend fallback, plus a SAPI fallback on Windows.
   - A grammar-correction pass via llama.cpp + Qwen 2.5 cleans up dictation output (email addresses, phone numbers, URLs, punctuation).
   - Voice commands are parsed by an intent classifier and routed to Chrome, Spotify, desktop/system app launching, Outlook, settings, or file handling.

5. **UI feedback**
   - On-screen overlays for volume, mouse, drawing, voice status, and low-FPS suggestions.
   - Per-session dual-bar overlay for simultaneous system-volume and per-app-volume control.

---

## Repository layout

```text
Touchless/
├── run_app.py                  # Main launcher
├── run_debug.py                # Debug/live-inspection launcher
├── run_test.py                 # Gesture test window launcher
├── LICENSE                     # MIT
├── README.md
├── builder/
│   └── windows/
│       └── hgr_app.spec        # PyInstaller build spec
├── installers/
│   └── windows/                # Inno Setup scripts
├── src/
│   └── hgr/
│       ├── app/
│       │   ├── main.py
│       │   ├── camera/
│       │   ├── integration/
│       │   │   └── noop_engine.py
│       │   ├── overlays/
│       │   └── ui/
│       │       ├── main_window.py
│       │       ├── tutorial_window.py
│       │       ├── live_view_window.py
│       │       ├── mini_live_viewer.py
│       │       └── phone_camera_connect_dialog.py
│       ├── debug/
│       │   ├── phone_camera/           # Embedded HTTPS + WS server + self-signed CA
│       │   ├── chrome_controller.py
│       │   ├── chrome_gesture_router.py
│       │   ├── desktop_controller.py
│       │   ├── foreground_window.py
│       │   ├── low_fps_suggestion_overlay.py
│       │   ├── media_controller.py
│       │   ├── mouse_controller.py
│       │   ├── mouse_gesture.py
│       │   ├── mouse_overlay.py
│       │   ├── screen_volume_overlay.py
│       │   ├── spotify_controller.py
│       │   ├── spotify_gesture_router.py
│       │   ├── text_input_controller.py
│       │   ├── voice_command_listener.py
│       │   ├── volume_controller.py
│       │   ├── volume_gesture.py
│       │   ├── youtube_controller.py
│       │   └── youtube_gesture_router.py
│       ├── gesture/
│       │   ├── recognition/
│       │   ├── rendering/
│       │   ├── tracking/
│       │   └── ui/test_window.py
│       ├── voice/
│       │   ├── command_processor.py
│       │   ├── dictation.py
│       │   ├── grammar_corrector.py
│       │   ├── live_dictation.py
│       │   ├── llama_server.py
│       │   ├── sapi_stream.py
│       │   ├── whisper_refiner.py
│       │   ├── whisper_stream.py
│       │   └── training_data.py
│       ├── config/
│       │   └── app_config.py
│       └── utils/
│           ├── runtime_paths.py
│           └── subprocess_utils.py
├── tests/                      # Representative test suite
├── GestureGuide/               # Static PNGs and dynamic MP4 assets
├── whisper.cpp/                # Local whisper build + models (gitignored)
├── llama.cpp/                  # Local llama build for grammar correction (gitignored)
└── assets/                     # Icons and misc application assets
```

---

## Running from source

```bash
git clone https://github.com/markovk-tini/HGR-App.git
cd HGR-App
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt   # if requirements.txt is present; otherwise install PySide6, mediapipe, opencv-python, numpy, sounddevice, pycaw, comtypes, qrcode, cryptography, aiohttp, Pillow, psutil
python run_app.py
```

Developer entry points:

- `run_app.py` — the user-facing Touchless app.
- `run_debug.py` — live inspection window with per-frame landmarks, finger states, recognizer internals.
- `run_test.py` — gesture test harness for tuning recognition thresholds.

---

## Whisper / speech setup

Touchless ships with a pre-built `whisper.cpp` tree when installed. When running from source, you need a local build. Expected layout:

```text
whisper.cpp/
├── build/                          # or build_cuda / build_vulkan / build_stream
│   └── bin/
│       └── Release/
│           └── whisper-cli.exe
└── models/
    ├── ggml-medium.en.bin
    └── ggml-silero-v5.1.2.bin      # optional VAD model
```

Optional environment variables to override paths:

```powershell
setx HGR_WHISPER_CPP        "C:\path\to\whisper-cli.exe"
setx HGR_WHISPER_CPP_MODEL  "C:\path\to\ggml-medium.en.bin"
setx HGR_WHISPER_CPP_VAD_MODEL "C:\path\to\ggml-silero-v5.1.2.bin"
```

Restart your terminal and Touchless after changing them.

---

## Testing

The tests cover gesture recognition, mouse tracking, volume pose behavior, Spotify/Chrome/YouTube controllers and routers, voice command parsing, dictation correction, and desktop/file-opening behavior.

```bash
python -m pytest
```

or:

```bash
python -m unittest discover
```

---

## Platform

Touchless is **Windows-focused** today. A significant portion of the controller layer uses Windows-specific APIs (window focusing, volume control via pycaw, SendInput-based mouse/keyboard injection, ShellExecuteW launches, SAPI fallback for speech). The gesture recognition and voice pipelines themselves are OS-agnostic; cross-platform support would mean replacing the Windows-only controllers.

---

## Packaging and distribution

Touchless builds into a standalone Windows executable via PyInstaller (see [builder/windows/hgr_app.spec](builder/windows/hgr_app.spec)), which is then wrapped in an Inno Setup installer. The build bundles:

- The main app executable.
- The full `whisper.cpp/` runtime (CUDA, Vulkan, and CPU backends; VAD model included).
- Gesture guide assets (PNGs, MP4s).
- Application icons and styling.

**Code signing** is in progress via [SignPath Foundation](https://signpath.org), which signs open-source Windows releases for free. Once approved, installers will be signed with an Authenticode cert that chains to a publicly-trusted CA, so Windows Defender and mainstream antivirus engines accept the installer without flagging it. First-install SmartScreen reputation takes a few hundred downloads to build up; testers may see a one-click "More info → Run anyway" warning on the first release.

---

## License

Touchless is licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE) for the full text.

In short:

- You may use, study, modify, and redistribute Touchless freely, for personal or commercial purposes.
- If you distribute a modified version, you **must release your source code** under GPL v3 as well. This "copyleft" rule prevents anyone from taking Touchless, making proprietary changes, and redistributing it as closed-source software.
- Any work that links against or is based on Touchless's source is also subject to GPL v3 when distributed.

© 2026 Konstantin Markov. Distributed without warranty per the GPL. If your use case needs a more permissive license for integration into a closed-source product, open an issue — dual-licensing inquiries are welcome.

---

## Status

Touchless is in active development. The stable parts include the app shell, gesture routing, volume / media / browser controllers, tutorial, and the phone-camera pipeline. Active work: code signing rollout, custom gesture creation, phone microphone support, and continuing tuning of whisper.cpp dictation under varied hardware.
