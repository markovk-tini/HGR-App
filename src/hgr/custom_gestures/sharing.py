"""Portable bundle format for sharing custom gestures.

A `.tlg` file is a zip archive that holds:
  - `gestures.json` — top-level dict { "schema_version": int,
        "exported_at": iso8601, "gestures": [<gesture-dict>...] }
    where each gesture-dict is the same shape `CustomGesture.to_dict()`
    produces, so the registry's `from_dict` reads it back unchanged.
  - `thumbnails/<image_filename>` — optional PNG thumbnails, only
    written when the source gesture had one. Bundled by relative
    name so on import we can drop them into the receiver's
    `gesture_thumbnails/` directory unchanged.

The receiver's `import_bundle()` decides per-gesture whether to
overwrite (caller-supplied resolver), and writes thumbnails into
`registry.thumbnails_dir()` so cards display the same image the
sharer saw. Schema-versioned so we can evolve the format without
breaking the v1 readers users already have installed.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .registry import CustomGesture, GestureRegistry


_BUNDLE_SCHEMA_VERSION = 1
_BUNDLE_MANIFEST = "gestures.json"
_BUNDLE_THUMBNAIL_DIR = "thumbnails"


class BundleError(Exception):
    """Raised on malformed / unreadable bundle. Surfaced to the UI as
    a clean error message instead of a stack trace."""


def export_bundle(
    registry: GestureRegistry,
    names: List[str],
    dest_path: Path,
) -> int:
    """Write a `.tlg` bundle at `dest_path` containing the named
    gestures from `registry`. Returns the number of gestures written.

    Skips names that don't exist in the registry rather than
    raising — the UI can show "exported N of M" so a partial
    selection still ships something useful. Skips thumbnails that
    can't be read (file deleted while we were preparing the export)
    silently because the gesture still works without them.
    """
    if not names:
        raise BundleError("no gestures to export")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    gestures_payload: list[dict] = []
    thumbnails: list[Tuple[str, bytes]] = []

    for name in names:
        gesture = registry.get(name)
        if gesture is None:
            continue
        gestures_payload.append(gesture.to_dict())
        if gesture.image_filename:
            thumb_path = registry.thumbnail_path(gesture)
            if thumb_path is not None:
                try:
                    thumbnails.append(
                        (gesture.image_filename, thumb_path.read_bytes())
                    )
                except OSError:
                    pass

    if not gestures_payload:
        raise BundleError("none of the named gestures exist in the registry")

    manifest = {
        "schema_version": _BUNDLE_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc)
            .replace(microsecond=0).isoformat(),
        "gestures": gestures_payload,
    }
    # Build the zip in memory first so a write failure doesn't
    # leave a half-written file at dest_path.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_BUNDLE_MANIFEST, json.dumps(manifest, indent=2))
        for fname, data in thumbnails:
            zf.writestr(f"{_BUNDLE_THUMBNAIL_DIR}/{fname}", data)
    dest_path.write_bytes(buf.getvalue())
    return len(gestures_payload)


def read_bundle(source_path: Path) -> Tuple[List[CustomGesture], dict]:
    """Parse a `.tlg` file. Returns (gestures, thumbnails_by_filename).

    Raises `BundleError` on any structural problem so callers can
    surface a single clean error to the user. Does NOT touch the
    receiver's registry — caller decides what to do with each
    gesture (overwrite / skip / rename) before persisting."""
    source_path = Path(source_path)
    if not source_path.is_file():
        raise BundleError(f"file not found: {source_path}")
    try:
        with zipfile.ZipFile(source_path, "r") as zf:
            try:
                manifest_raw = zf.read(_BUNDLE_MANIFEST).decode("utf-8")
            except KeyError:
                raise BundleError(
                    f"bundle is missing {_BUNDLE_MANIFEST} — not a Touchless gesture pack"
                )
            try:
                manifest = json.loads(manifest_raw)
            except ValueError as exc:
                raise BundleError(f"manifest is not valid JSON: {exc}")
            gestures: list[CustomGesture] = []
            for entry in manifest.get("gestures", []):
                try:
                    gestures.append(CustomGesture.from_dict(entry))
                except Exception as exc:
                    raise BundleError(
                        f"could not parse gesture {entry.get('name', '?')!r}: {exc}"
                    )
            thumbnails: dict[str, bytes] = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not info.filename.startswith(f"{_BUNDLE_THUMBNAIL_DIR}/"):
                    continue
                rel = info.filename[len(_BUNDLE_THUMBNAIL_DIR) + 1:]
                if not rel:
                    continue
                try:
                    thumbnails[rel] = zf.read(info.filename)
                except Exception:
                    continue
    except zipfile.BadZipFile:
        raise BundleError("file isn't a valid .tlg / zip archive")
    if not gestures:
        raise BundleError("bundle contains no gestures")
    return gestures, thumbnails


# Resolver decision values. The UI maps a "what should happen for
# every conflicting gesture" radio choice to one of these.
RESOLVE_OVERWRITE = "overwrite"
RESOLVE_SKIP = "skip"


def import_bundle(
    registry: GestureRegistry,
    bundle_path: Path,
    *,
    on_conflict: Callable[[CustomGesture], str] = lambda _g: RESOLVE_SKIP,
) -> Tuple[int, int]:
    """Read a bundle and merge it into `registry`. Returns
    (imported_count, skipped_count).

    `on_conflict` is consulted for each gesture whose name already
    exists in the registry; it must return RESOLVE_OVERWRITE or
    RESOLVE_SKIP. Defaults to skip-on-conflict so a missing
    resolver can never silently destroy existing user gestures.

    Thumbnails are written to `registry.thumbnails_dir()` only for
    gestures that are actually imported (no orphan files left
    behind for skipped entries).
    """
    if not registry._loaded:  # type: ignore[attr-defined]
        registry.load()

    gestures, thumbnails = read_bundle(bundle_path)
    imported = 0
    skipped = 0
    thumb_dir = registry.thumbnails_dir()
    for gesture in gestures:
        existing = registry.get(gesture.name)
        if existing is not None:
            decision = on_conflict(gesture)
            if decision == RESOLVE_SKIP:
                skipped += 1
                continue
            # Overwrite path: drop the old entry first so add()
            # below doesn't trip the duplicate-name check.
            registry.remove(gesture.name)

        # Persist the gesture via the public add() helper so the
        # registry's locking + validation runs.
        registry.add(
            name=gesture.name,
            samples=list(gesture.samples),
            action=gesture.action,
            description=gesture.description,
            handedness=gesture.handedness,
            image_filename=gesture.image_filename,
            overwrite=True,
        )
        # Thumbnail (if any). Write only after the gesture record
        # is in so a crash mid-import doesn't leave an orphan PNG
        # referenced by no gesture.
        if gesture.image_filename and gesture.image_filename in thumbnails:
            try:
                (thumb_dir / gesture.image_filename).write_bytes(
                    thumbnails[gesture.image_filename]
                )
            except OSError:
                pass
        imported += 1

    if imported > 0:
        registry.save()
    return imported, skipped


def gestures_in_bundle(bundle_path: Path) -> List[CustomGesture]:
    """Convenience for the import dialog: peek at a bundle's contents
    without writing anything. Returns the gesture list (may be empty
    on malformed input — caller checks)."""
    try:
        gestures, _ = read_bundle(bundle_path)
        return gestures
    except BundleError:
        return []
