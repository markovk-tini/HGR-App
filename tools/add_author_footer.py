"""One-shot tool: append `Author: Konstantin Markov` as a comment to the
end of every project source file that supports comments.

Idempotent — re-running on already-stamped files is a no-op.
Skips third-party trees (whisper.cpp, llama.cpp, vendored pipelines) and
binary / data files that have no comment syntax.

Usage:
    python tools/add_author_footer.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Comment syntax per extension. Each entry is (prefix, suffix). For most
# languages suffix is empty; HTML/Markdown/CSS-style block comments use
# both.
EXTENSION_COMMENT = {
    ".py":   ("# Author: Konstantin Markov",                 ""),
    ".pyw":  ("# Author: Konstantin Markov",                 ""),
    ".spec": ("# Author: Konstantin Markov",                 ""),
    ".sh":   ("# Author: Konstantin Markov",                 ""),
    ".cfg":  ("# Author: Konstantin Markov",                 ""),
    ".toml": ("# Author: Konstantin Markov",                 ""),
    ".yaml": ("# Author: Konstantin Markov",                 ""),
    ".yml":  ("# Author: Konstantin Markov",                 ""),
    ".bat":  ("REM Author: Konstantin Markov",               ""),
    ".cmd":  ("REM Author: Konstantin Markov",               ""),
    ".ps1":  ("# Author: Konstantin Markov",                 ""),
    ".iss":  ("; Author: Konstantin Markov",                 ""),
    ".ini":  ("; Author: Konstantin Markov",                 ""),
    ".md":   ("<!-- Author: Konstantin Markov -->",          ""),
    ".html": ("<!-- Author: Konstantin Markov -->",          ""),
    ".css":  ("/* Author: Konstantin Markov */",             ""),
    ".js":   ("// Author: Konstantin Markov",                ""),
    ".ts":   ("// Author: Konstantin Markov",                ""),
}

# Skip directories anywhere along the path. Anything inside these is
# either third-party, generated, or environment-specific — none of it
# should carry an author claim.
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules",
    "__pycache__", "build", "dist", "release",
    "whisper.cpp", "whisper_bundle", "llama.cpp",
    "mp_modules", ".agents", ".claude", ".idea", ".vscode",
    "tools/onnx_conversion",  # path fragment, see is_skipped_path
    "models",                  # was the empty placeholder
    "assets/models",
    "checkpoints", "exported", "metadata",
    "tests/output",
}

# Specific filenames that should be left untouched even if their
# extension is in EXTENSION_COMMENT.
SKIP_FILENAMES = {
    "LICENSE", "LICENSE.md", "NOTICE", "NOTICE.md",
    "package-lock.json", "yarn.lock",
}


def is_skipped_path(rel: Path) -> bool:
    parts = set(rel.parts)
    if parts & SKIP_DIRS:
        return True
    posix = rel.as_posix()
    for fragment in SKIP_DIRS:
        if "/" in fragment and fragment in posix:
            return True
    return False


def already_stamped(text: str) -> bool:
    """Cheap check: does the file already contain the author line?
    Avoids re-appending on subsequent runs."""
    return "Author: Konstantin Markov" in text


def append_footer(path: Path, prefix: str, suffix: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Binary or non-UTF8 file — skip.
        return False
    except Exception:
        return False
    if already_stamped(text):
        return False
    line = prefix + suffix
    # Preserve trailing newline if present; otherwise add one before
    # the comment so we don't run into the previous content.
    if text and not text.endswith("\n"):
        text += "\n"
    if not text.endswith("\n\n"):
        text += "\n"
    text += line + "\n"
    try:
        path.write_text(text, encoding="utf-8", newline="\n")
        return True
    except Exception:
        return False


def main() -> int:
    touched = 0
    skipped_known = 0
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if is_skipped_path(rel):
            continue
        if path.name in SKIP_FILENAMES:
            skipped_known += 1
            continue
        ext = path.suffix.lower()
        if ext not in EXTENSION_COMMENT:
            continue
        prefix, suffix = EXTENSION_COMMENT[ext]
        if append_footer(path, prefix, suffix):
            touched += 1
    print(f"Stamped {touched} files (skipped {skipped_known} explicitly excluded names).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
