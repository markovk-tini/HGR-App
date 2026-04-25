"""Build the small app-only update package.

Reads the existing PyInstaller bundle in `dist/Touchless/` and zips
just the files that change between application releases — leaving
the heavy ML stack (whisper.cpp/, ~2.1 GB) and other Python deps
in place on the user's existing install.

What goes IN:
  - Touchless.exe                     — bundled Python app code (PYZ)
  - _internal/assets/                 — icons / small data files
  - _internal/GestureGuide/           — bundled tutorial assets

What stays OUT:
  - _internal/whisper.cpp/            — ML stack, rarely changes
  - _internal/PySide6, cv2, mediapipe, scipy, etc. — stable deps

Output: release/Touchless_App_Update_<version>.zip

Usage:
  .venv\\Scripts\\python.exe builder/windows/build_app_update_zip.py

Honors the version in src/hgr/__init__.py (single source of truth).

Why this is safe:
  PyInstaller embeds the user's Python source into Touchless.exe as
  a PYZ archive. Edits to src/hgr/ only touch that PYZ — every
  other file in _internal/ is the Python interpreter or a third-
  party library that stays binary-identical across builds. As long
  as the developer doesn't bump a dep version, shipping just
  Touchless.exe is sufficient. If a dep IS bumped, the developer
  uploads only the full installer for that release (no zip), and
  the updater falls back to the full path.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = ROOT / "dist" / "Touchless"
RELEASE_DIR = ROOT / "release"

# Only these paths (relative to the dist root) end up in the zip.
INCLUDED_PATHS = (
    "Touchless.exe",
    "_internal/assets",
    "_internal/GestureGuide",
)


def read_version() -> str:
    init_file = ROOT / "src" / "hgr" / "__init__.py"
    text = init_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        print(f"[ERROR] Could not find __version__ in {init_file}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def gather_files(included_paths: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for rel in included_paths:
        target = DIST_DIR / rel
        if not target.exists():
            print(f"[WARN] Skipping missing path: {target}")
            continue
        if target.is_file():
            files.append(target)
        else:
            for path in target.rglob("*"):
                if path.is_file():
                    files.append(path)
    return files


def main() -> int:
    if not DIST_DIR.exists():
        print(
            f"[ERROR] Bundle not found at {DIST_DIR}. Run PyInstaller first "
            f"(builder/windows/hgr_app.spec).",
            file=sys.stderr,
        )
        return 1

    version = read_version()
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RELEASE_DIR / f"Touchless_App_Update_{version}.zip"

    files = gather_files(INCLUDED_PATHS)
    if not files:
        print("[ERROR] No files matched the included paths.", file=sys.stderr)
        return 1

    total_bytes = sum(f.stat().st_size for f in files)
    print(f"[INFO] Packing {len(files)} files ({total_bytes / (1024*1024):.1f} MB raw) into:")
    print(f"       {output_path}")

    # ZIP_DEFLATED is the universally-supported format. Compression
    # ratio for code/asset files is solid (~50%+).
    if output_path.exists():
        output_path.unlink()
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arcname = path.relative_to(DIST_DIR).as_posix()
            zf.write(path, arcname=arcname)

    final_size = output_path.stat().st_size
    print(f"[OK] Done. Compressed size: {final_size / (1024*1024):.1f} MB")
    print(f"     Asset name to upload to GitHub release: {output_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
