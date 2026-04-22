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

## Grammar correction (llama.cpp)

Dictation mode runs a local LLM alongside whisper-stream to clean up homophones, punctuation, and capitalization every ~20 seconds. Backend detection mirrors whisper: CUDA → Vulkan → CPU. The app searches the same paths as whisper and skips LLM correction silently if no build is present, so it is optional but recommended.

### Expected layout

```text
HGR App v1.0.0/
├── llama.cpp/
│   ├── build_cuda/bin/Release/llama-server.exe      (NVIDIA)
│   ├── build_vulkan/bin/Release/llama-server.exe    (AMD/Intel/Arc)
│   └── build_cpu/bin/Release/llama-server.exe       (fallback)
```

Env overrides: `HGR_LLAMA_CPP_ROOT`, `HGR_LLAMA_BACKEND` (`cuda`|`vulkan`|`cpu`), `HGR_LLAMA_MODEL_ROOT`.

### Build commands (CMake + Visual Studio 2022)

```powershell
cd llama.cpp
# CUDA (requires CUDA Toolkit 12.x)
cmake -B build_cuda -DGGML_CUDA=ON -DLLAMA_BUILD_SERVER=ON
cmake --build build_cuda --config Release --target llama-server -j

# Vulkan (requires LunarG Vulkan SDK)
cmake -B build_vulkan -DGGML_VULKAN=ON -DLLAMA_BUILD_SERVER=ON
cmake --build build_vulkan --config Release --target llama-server -j

# CPU fallback
cmake -B build_cpu -DLLAMA_BUILD_SERVER=ON
cmake --build build_cpu --config Release --target llama-server -j
```

### Model

Drop a GGUF correction model in `%USERPROFILE%\Documents\HGRVoiceModels\` or `llama.cpp/models/`. The app probes these names first, then any `*.gguf` in the folder:

- `Qwen2.5-3B-Instruct-Q4_K_M.gguf` (recommended, ~2 GB)
- `Qwen2.5-7B-Instruct-Q4_K_M.gguf`
- `Llama-3.2-3B-Instruct-Q4_K_M.gguf`

Download from Hugging Face (e.g. `Qwen/Qwen2.5-3B-Instruct-GGUF`). Q4_K_M is the target quant — adequate quality, fits in 4 GB VRAM.

### Runtime

`LlamaServer` launches `llama-server.exe` on a free localhost port with `--no-webui`, `-c 4096`, `-ngl 999` on GPU backends. `GrammarCorrector` picks the longest sentence-bounded chunk from the buffer every 20 s, POSTs to `/v1/chat/completions`, and replaces the typed text via backspace+insert if the corrected output differs. The dictation overlay shows `Using {whisper}/{grammar}` under the mic icon so the active backend combo is always visible.
