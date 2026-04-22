# HGR App

HGR App is a desktop hand-gesture and voice-control system for Windows. It combines a live camera feed, hand-pose recognition, gesture routing, voice command parsing, and a polished PySide6 user interface so a user can control everyday desktop tasks with a small set of gestures and spoken commands.

The current codebase is centered around a modern `src/hgr` application structure with a Qt main window, a shared live runtime, a guided tutorial, a settings/gesture-guide experience, and separate controllers for Spotify, Chrome, mouse input, system volume, and voice features.

---

## What the project does

At a high level, HGR App turns camera-tracked hand motions into desktop actions.

### Core control areas

- **Spotify control**
  - Open/focus Spotify
  - Play/pause
  - Next/previous track
  - Shuffle/repeat
  - Open the Spotify gesture wheel

- **Chrome control**
  - Open/focus Chrome
  - Back/forward
  - Refresh
  - New tab / incognito tab
  - Open the Chrome gesture wheel

- **System controls**
  - System volume up/down
  - Mute/unmute
  - Mouse mode with gesture-based cursor movement and clicks

- **Voice features**
  - Voice command activation from hand gestures
  - Spoken commands routed to Chrome, Spotify, system app launching, settings, Outlook, file explorer, and file opening
  - Experimental dictation workflow

- **Guided onboarding**
  - A six-part tutorial that uses the live runtime
  - A settings page with instructions, gesture guide, colors, camera controls, and tutorial access

---

## User experience

The main app launches a custom-frameless PySide6 window with a home page and a settings stack. The home page includes **Start**, **End**, **Settings**, a runtime status card, and a **Live View** / debugger entry point. The settings page includes sections for Instructions, Gesture Guide, Colors, Camera, and Tutorial. The tutorial window is a guided six-step walkthrough built on top of the same runtime concepts used by the main application.

This structure is visible in the current startup and window code:
- `run_app.py` bootstraps the project and forwards into `hgr.app.main.main()`
- `hgr.app.main.main()` creates the Qt app, loads the config, and shows `MainWindow`
- `MainWindow` builds the home page, settings page, tutorial entry point, and live/debug access

---

## Technical overview

HGR App is organized as a layered runtime:

1. **Camera + tracking**
   - Captures frames from the selected/default camera
   - Produces landmarks and handedness information

2. **Gesture recognition**
   - Classifies static poses such as fist, one, two, three, four, mute, volume pose, and wheel poses
   - Tracks dynamic gestures such as swipe left, swipe right, and repeat/refresh-like circular motions
   - Stabilizes results across frames

3. **Control routing**
   - Sends recognized gestures to the correct subsystem:
     - Spotify
     - Chrome
     - Mouse mode
     - Volume control
     - Voice activation / dictation

4. **Voice pipeline**
   - Starts a voice command session from the left-hand trigger gesture
   - Transcribes speech
   - Parses spoken intent
   - Routes the action to Chrome, Spotify, desktop/system app launching, Outlook, settings, or file handling

5. **UI feedback**
   - Updates overlays, tutorial prompts, live status, gesture guide content, and tutorial state
   - Provides a debugger/test window for inspection and tuning

---

## Repository organization

Below is the effective organization reflected by the current project files.

```text
HGR App v1.0.0/
├── run_app.py                  # Main launcher
├── run_debug.py                # Debug/live-inspection launcher
├── run_test.py                 # Gesture test window launcher
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
│       │       └── mini_live_viewer.py
│       ├── core/
│       │   ├── arbitration/
│       │   ├── classifiers/
│       │   ├── features/
│       │   ├── pipeline/
│       │   └── tracking/
│       ├── debug/
│       │   ├── debug_window.py
│       │   ├── chrome_controller.py
│       │   ├── desktop_controller.py
│       │   ├── live_dictation_streamer.py
│       │   ├── mouse_controller.py
│       │   ├── mouse_gesture.py
│       │   ├── mouse_overlay.py
│       │   ├── screen_volume_overlay.py
│       │   ├── spotify_controller.py
│       │   ├── spotify_gesture_router.py
│       │   ├── text_input_controller.py
│       │   ├── voice_command_listener.py
│       │   ├── voice_status_overlay.py
│       │   ├── volume_controller.py
│       │   └── volume_gesture.py
│       ├── gesture/
│       │   ├── recognition/
│       │   └── ui/
│       │       └── test_window.py
│       ├── voice/
│       │   ├── command_processor.py
│       │   ├── dictation.py
│       │   └── training_data.py
│       ├── config/
│       └── utils/
├── tests/                      # Representative test suite (current uploads include many test_*.py files)
├── GestureGuide/               # Static PNGs and dynamic MP4 assets used by the gesture guide
├── whisper.cpp/                # Optional local Whisper CLI build and models
└── assets/                     # Icons and other application assets
```

### Key folders

#### `src/hgr/app`
The application shell:
- app startup
- camera/session setup
- shared live runtime integration
- Qt UI windows
- overlays used by the main program

#### `src/hgr/core`
The recognition stack:
- gesture smoothing/arbitration
- static feature extraction
- static/dynamic classification
- backend pipeline and tracking

#### `src/hgr/debug`
The controller layer and developer tooling:
- Spotify, Chrome, desktop, mouse, volume, text input, and voice controllers
- the debug window
- overlays for volume, mouse, and voice status
- live dictation streaming helpers

#### `src/hgr/voice`
Voice-specific logic:
- command parsing and routing
- dictation text formatting/processing
- voice training-data utilities

#### `src/hgr/app/ui`
Primary UI windows:
- `main_window.py` for the app shell and settings pages
- `tutorial_window.py` for the guided six-part tutorial
- live view / mini viewer windows

#### `tests`
The project has a substantial test surface, including tests for:
- backend pipeline stability
- static features and gesture groups
- dynamic gestures
- mouse tracking logic
- volume gesture logic
- Chrome routing
- Spotify routing
- voice parsing and execution
- desktop/file-opening behavior

---

## Main runtime components

### 1. Main app
The main application launches through:

```bash
python run_app.py
```

This creates the Qt application, loads configuration, and opens `MainWindow`.

### 2. Debug / live inspection window
The debug window is a developer-facing live inspection tool that shows:
- camera feed
- gesture labels
- handedness
- finger states
- confidence and candidate scores
- dynamic gestures
- volume status
- voice status

Run it with:

```bash
python run_debug.py
```

### 3. Gesture test window
The separate gesture test UI is intended for focused validation of gesture behavior and voice/overlay interactions:

```bash
python run_test.py
```

---

## Gesture model and control design

The app distinguishes between:

### Static gestures
Examples in the current UI and test coverage include:
- fist
- one
- two
- three
- four
- mute
- volume pose
- wheel pose
- Chrome wheel pose
- left-hand voice and mode toggles

### Dynamic gestures
Examples include:
- swipe left
- swipe right
- repeat-circle / refresh-like motion

The system does not treat every detected pose as an immediate action. It uses:
- frame stabilization
- hold timing
- cooldowns
- mode gating
- context-aware routing

That design is why Spotify, Chrome, mouse mode, and tutorial navigation can share the same recognition runtime without every gesture firing all actions at once.

---

## Voice command capabilities

The current voice pipeline is designed to support commands such as:
- opening or searching in Chrome
- playing a track/playlist/album/artist on Spotify
- opening settings pages
- opening File Explorer or specific files
- opening Outlook folders
- launching installed applications through the desktop app catalog

The voice parser/controller tests show explicit support for:
- Spotify play requests
- Chrome open/search requests
- settings intents
- file explorer requests
- Outlook folder navigation
- generic app-open flows
- exporting voice training bundles for future improvement

---

## Gesture guide and tutorial

### Gesture Guide
The settings page includes a Gesture Guide panel that documents:
- gesture name
- action
- how to perform it

In the current project direction, this guide also supports:
- static gesture images (`.png`)
- dynamic gesture videos (`.mp4`)
- grouped sections for static and dynamic gestures

### Tutorial
The tutorial is a guided six-part onboarding flow using live runtime behavior:
1. Swipe practice
2. Spotify open/focus
3. Play/pause
4. Gesture wheel
5. Mouse mode
6. Voice command

The tutorial is intended to teach the real control vocabulary rather than a separate demo-only control scheme.

---

## Platform expectations

This project is strongly **Windows-focused** in its current form.

A large portion of the controller layer explicitly checks for Windows or uses Windows-specific mechanisms such as:
- window focusing
- keyboard shortcuts
- system volume APIs
- mouse injection
- Outlook / Settings / Explorer launch behavior
- Windows-oriented voice/text control workflows

If you want cross-platform support later, the clean approach will be to keep the gesture/voice recognition core portable and replace the controller layer per operating system.

---

## Whisper / speech setup

The project can be pointed at a local `whisper.cpp` build. A practical local setup is:

```text
whisper.cpp/
├── build/
│   └── bin/
│       └── Release/
│           └── whisper-cli.exe
└── models/
    ├── ggml-medium.en.bin
    └── ggml-silero-v5.1.2.bin   # optional VAD model
```

Typical environment variables:

```powershell
setx HGR_WHISPER_CPP "C:\HGR App v1.0.0\whisper.cpp\build\bin\Release\whisper-cli.exe"
setx HGR_WHISPER_CPP_MODEL "C:\HGR App v1.0.0\whisper.cpp\models\ggml-medium.en.bin"
setx HGR_WHISPER_CPP_VAD_MODEL "C:\HGR App v1.0.0\whisper.cpp\models\ggml-silero-v5.1.2.bin"
```

If you change those values, restart your terminal and the app so the new environment is picked up.

---

## Development workflow

A typical workflow for this repository is:

1. Create/activate a virtual environment
2. Install Python dependencies
3. Launch the main app, debug window, or gesture test window
4. Use the live view/debug tools to inspect recognition quality
5. Run the tests after changes
6. Iterate on controller logic, recognition thresholds, tutorial flow, or UI behavior

---

## Testing

The current uploaded test suite covers a broad range of behavior, including:
- static gesture classification
- dynamic gesture detection
- backend stabilization
- mouse mode tracking and clicks
- volume pose behavior
- Spotify controller and router behavior
- Chrome controller and router behavior
- desktop/file-opening behavior
- voice command parsing and execution
- voice training-data generation

Depending on your environment, you can run tests with either:

```bash
python -m pytest
```

or

```bash
python -m unittest discover
```

If one runner does not match your current environment setup, use the other.

---

## Current status

HGR App already has a strong project foundation:
- modern Qt desktop shell
- shared live runtime
- gesture guide and tutorial
- rich controller architecture
- significant automated test coverage
- ongoing work on dictation, overlay behavior, tutorial polish, and cross-app text insertion

The most stable parts of the project are the application shell, gesture routing structure, settings/tutorial flow, and the dedicated controller/test architecture. The most experimental area is still live cross-application dictation.

---

## Suggested next milestones

- Finalize dictation as a dedicated, robust subsystem
- Tighten gesture thresholds and recovery behavior
- Stabilize tutorial/runtime parity
- Improve packaging and installation docs
- Add clearer dependency/bootstrap automation
- Add screenshots/GIFs to this README
- Separate legacy prototype scripts from the modern `src/hgr` application code

---

## License / ownership

Add your preferred license here if you plan to distribute the project publicly.

If this repository is private or portfolio-only, you can replace this section with:
- author
- project intent
- resume/portfolio usage
- contact information

---

## Summary

HGR App is a Windows desktop gesture-and-voice control application that combines:
- real-time hand recognition
- context-aware gesture routing
- desktop automation controllers
- voice command parsing
- a polished Qt UI
- a guided tutorial and gesture guide
- a meaningful automated test suite

It is both a product-style application and a strong systems integration project spanning UI, computer vision, input control, automation, speech handling, and test-driven iteration.
