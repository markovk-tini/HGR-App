# Windows packaging plan for HGR App

This bundle updates the Windows packaging path so the installed app can ship with:
- `assets/`
- `GestureGuide/`
- `whisper.cpp/` including the CLI and models

## What to replace in your repo

Copy these files into your project:

- `hgr_app.spec` -> `builder/windows/hgr_app.spec`
- `hgr_app.iss` -> `installers/windows/hgr_app.iss`
- `build_windows.bat` -> `builder/windows/build_windows.bat`
- `voice_command_listener.py` -> `src/hgr/debug/voice_command_listener.py`
- `main_window.py` -> `src/hgr/app/ui/main_window.py`
- `voice_dictation.py` -> wherever you keep that module if you still use it

## Why these changes matter

1. `voice_command_listener.py` now looks for a bundled `whisper.cpp` first, so the installed app does not require a separate developer checkout under Documents.
2. `main_window.py` now resolves `GestureGuide` through the runtime resource helper, so media cards still work after freezing.
3. The PyInstaller spec now bundles `whisper.cpp` in addition to assets and the gesture guide.
4. The Inno Setup script now installs the current PyInstaller output and launches the correct app executable.

## Expected project layout before building

```text
HGR App v1.0.0/
├── assets/
├── GestureGuide/
├── whisper.cpp/
│   ├── build/ or build_stream/
│   │   └── .../whisper-cli.exe
│   └── models/
│       ├── ggml-medium.en.bin
│       └── ggml-silero-v5.1.2.bin
├── builder/windows/
│   ├── hgr_app.spec
│   └── build_windows.bat
└── installers/windows/
    └── hgr_app.iss
```

## Build steps on your Windows machine

1. Activate your project virtual environment.
2. Make sure `whisper.cpp` is already built.
3. Put the medium English model in `whisper.cpp/models/`.
4. Install Inno Setup 6.
5. Run:

```powershell
.\builder\windows\build_windows.bat
```

## Output

- PyInstaller bundle: `dist\HGR App\`
- Installer: `release\HGR_App_Installer.exe`

## Remaining non-installer dependency to decide on

Spotify control still depends on credentials/tokens. If you want Spotify to work for end users immediately after install, you still need a deliberate plan for shipping or provisioning those credentials.
