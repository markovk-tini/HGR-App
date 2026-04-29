# Touchless PyInstaller spec for Windows
# Place this file at builder/windows/hgr_app.spec and run from the repo root.

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path.cwd()
SRC = ROOT / "src"
ASSETS = ROOT / "assets"
GESTURE_GUIDE = ROOT / "GestureGuide"
WHISPER_BUNDLES = [ROOT / "whisper.cpp", ROOT / "whisper_bundle"]
LLAMA_ROOT = ROOT / "llama.cpp"
ICON = ASSETS / "icons" / "touchless_icon.ico"

datas = []
binaries = []
hiddenimports = []

for package_name in ("PySide6", "shiboken6", "mediapipe"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package_name)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

# onnxruntime-directml: ships native DLLs (DirectML.dll, the DML
# execution provider, the providers_shared shim, plus a few
# Microsoft.AI.MachineLearning runtime files). PyInstaller's
# automatic detection misses some of these because they're loaded
# via LoadLibrary from C++. collect_all picks them up reliably and
# also includes the pure-Python `onnxruntime.capi`,
# `onnxruntime.providers` etc. submodules the runtime touches when
# initialising a DML session. Skip silently if the package isn't
# installed (some dev machines build CPU-only); the runtime
# already falls back to MediaPipe CPU when DML isn't reachable, so
# a missing wheel here just means GPU Mode is a no-op on that build.
try:
    ort_datas, ort_binaries, ort_hidden = collect_all("onnxruntime")
    datas += ort_datas
    binaries += ort_binaries
    hiddenimports += ort_hidden
except Exception:
    pass

# The app uses several dynamic imports and optional Windows-only controllers.
hiddenimports += collect_submodules("hgr")
hiddenimports += [
    "cv2",
    "numpy",
    "PIL",
    "psutil",
    "keyboard",
    "sounddevice",
    "comtypes",
    "pycaw",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
]
hiddenimports = list(dict.fromkeys(hiddenimports))

for source_path, target_name in (
    (ASSETS, "assets"),
    (GESTURE_GUIDE, "GestureGuide"),
):
    if source_path.exists():
        datas.append((str(source_path), target_name))


def _collect_whisper_runtime(roots):
    """Bundle only the whisper runtime files the app actually uses.

    Looks under each provided root (whisper.cpp and whisper_bundle), accepts
    binaries at either `build/bin/Release/` (MSBuild layout) or `build/bin/`
    (flat layout), and always maps them into the canonical
    `whisper.cpp/<build>/bin/Release/` output path so the runtime finder works
    the same way in the packaged app regardless of dev-side layout.

    Dropping the CMake build scaffolding keeps the installer under the
    Windows MAX_PATH limit that Inno Setup enforces on every compressed path.
    """
    keep_ext = {".exe", ".dll", ".bin", ".pdb"}
    collected = []
    seen_models: set[str] = set()
    seen_binaries: set[tuple[str, str]] = set()

    for root in roots:
        if not root.exists():
            continue
        models_dir = root / "models"
        if models_dir.exists():
            for model_file in models_dir.glob("*.bin"):
                if model_file.name in seen_models:
                    continue
                seen_models.add(model_file.name)
                collected.append((str(model_file), "whisper.cpp/models"))
        for build_dir_name in ("build", "build_cuda", "build_vulkan", "build_stream"):
            build_bin = root / build_dir_name / "bin"
            if not build_bin.exists():
                continue
            source_dirs = [build_bin / "Release", build_bin]
            target = f"whisper.cpp/{build_dir_name}/bin/Release"
            for source_dir in source_dirs:
                if not source_dir.exists():
                    continue
                for entry in source_dir.iterdir():
                    if not entry.is_file():
                        continue
                    if entry.suffix.lower() not in keep_ext:
                        continue
                    key = (build_dir_name, entry.name)
                    if key in seen_binaries:
                        continue
                    seen_binaries.add(key)
                    collected.append((str(entry), target))
    return collected


datas += _collect_whisper_runtime(WHISPER_BUNDLES)


def _collect_llama_runtime(llama_root):
    """Bundle the llama.cpp runtime binaries used by the local Live API agent.

    The Live API local backend (`src/hgr/live_api/local_backend.py`) and the
    grammar corrector (`src/hgr/voice/llama_server.py`) both spawn
    `llama-server.exe` from `llama.cpp/build_<backend>/bin/`. We mirror the
    whisper bundling strategy: only ship the runtime files (.exe / .dll /
    .bin), not the CMake scaffolding, and preserve the canonical layout
    so the existing discovery code doesn't need a packaged-vs-source switch.

    GGUF model files are intentionally NOT bundled — they're 3-5 GB each
    which would balloon the installer past the Inno Setup MAX_PATH limits
    and double the download. Models live in
    `~/Documents/TouchlessVoiceModels/` and the user downloads them once.

    Future Phase 2 (vision) will use `llama-mtmd-cli.exe` /
    `llama-qwen2vl-cli.exe` from the same bin/ dir, which is why we copy
    every .exe rather than just llama-server.exe.
    """
    if not llama_root.exists():
        return []
    keep_ext = {".exe", ".dll"}
    collected = []
    seen: set[tuple[str, str]] = set()
    for build_dir_name in ("build_cuda", "build_vulkan", "build_cpu", "build"):
        bin_dir = llama_root / build_dir_name / "bin"
        if not bin_dir.exists():
            continue
        # MSBuild emits to bin/Release; CMake to bin directly. Accept both
        # and remap to the canonical bin/ layout the discovery code uses.
        for source_dir in (bin_dir / "Release", bin_dir):
            if not source_dir.exists():
                continue
            target = f"llama.cpp/{build_dir_name}/bin"
            for entry in source_dir.iterdir():
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in keep_ext:
                    continue
                key = (build_dir_name, entry.name)
                if key in seen:
                    continue
                seen.add(key)
                collected.append((str(entry), target))
    return collected


datas += _collect_llama_runtime(LLAMA_ROOT)

a = Analysis(
    [str(ROOT / "run_app.py")],
    pathex=[str(ROOT), str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Touchless",
    console=False,
    icon=str(ICON) if ICON.exists() else None,
    disable_windowed_traceback=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name="Touchless",
    upx=False,
)
