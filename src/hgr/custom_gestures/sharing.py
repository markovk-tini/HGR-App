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
  - `drawings/<filename>` — optional drawing PNGs for gestures
    whose action kind is `show_overlay_drawing`. Bundled so the
    receiver can fire the imported gesture and see the same
    drawing the sender associated with it, even though the sender's
    drawings folder doesn't exist on the receiver's machine. The
    payload's `filename` field is rewritten on export to a bare
    basename so the receiver's `resolve_drawing_path` can find the
    extracted file under their own `drawings_save_dir`. Optional
    bundle entry; older readers that don't know about drawings/
    silently skip it (gestures still import, action just won't
    find its drawing on the receiver).

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
_BUNDLE_DRAWING_DIR = "drawings"


def _resolve_drawing_source(filename: str, drawings_dir: Optional[Path]) -> Optional[Path]:
    """Resolve a `show_overlay_drawing` payload filename against a
    drawings folder, mirroring resolve_drawing_path in
    drawing_overlay_window. Inlined here to keep `sharing` from
    importing UI code (sharing has to be safely usable by headless
    callers). Accepts: bare filename, relative path, or absolute path.
    Returns the existing source path or None."""
    if not filename:
        return None
    candidate = Path(filename).expanduser()
    if not candidate.is_absolute():
        if drawings_dir is None:
            return None
        try:
            candidate = Path(drawings_dir).expanduser() / candidate
        except Exception:
            return None
    try:
        if candidate.is_file():
            return candidate
    except OSError:
        return None
    return None


class BundleError(Exception):
    """Raised on malformed / unreadable bundle. Surfaced to the UI as
    a clean error message instead of a stack trace."""


def export_bundle(
    registry: GestureRegistry,
    names: List[str],
    dest_path: Path,
    *,
    drawings_dir: Optional[Path] = None,
) -> int:
    """Write a `.tlg` bundle at `dest_path` containing the named
    gestures from `registry`. Returns the number of gestures written.

    Skips names that don't exist in the registry rather than
    raising — the UI can show "exported N of M" so a partial
    selection still ships something useful. Skips thumbnails that
    can't be read (file deleted while we were preparing the export)
    silently because the gesture still works without them.

    `drawings_dir` is the user's drawings save directory, used to
    resolve `show_overlay_drawing` payload filenames so the actual
    PNG can be bundled into the .tlg. Pass None to skip drawing
    bundling (gestures still export, but the receiver won't get the
    drawings — same behavior as bundles produced by older versions).
    """
    if not names:
        raise BundleError("no gestures to export")
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    gestures_payload: list[dict] = []
    thumbnails: list[Tuple[str, bytes]] = []
    # Use a dict keyed by basename so the same drawing referenced by
    # multiple gestures only gets bundled once.
    drawings_to_bundle: dict[str, bytes] = {}

    for name in names:
        gesture = registry.get(name)
        if gesture is None:
            continue
        gesture_dict = gesture.to_dict()

        # Drawing-overlay action: try to bundle the referenced PNG
        # and rewrite the payload filename to a bare basename so the
        # receiver's resolve_drawing_path finds it under their
        # drawings_save_dir after extraction. Done on the dict copy
        # we're about to serialize so the in-memory registry is left
        # untouched (the sender's payload may still hold an absolute
        # path that's correct for them).
        if (
            (gesture.action.kind or "").lower() == "show_overlay_drawing"
            and drawings_dir is not None
        ):
            payload = gesture.action.payload or {}
            fname = str(payload.get("filename", "")).strip()
            source = _resolve_drawing_source(fname, drawings_dir)
            if source is not None:
                try:
                    drawings_to_bundle[source.name] = source.read_bytes()
                    # Update the dict-copy's payload to point to the
                    # bare name. Action.to_dict() returned a fresh
                    # nested payload dict, so this doesn't leak back
                    # into the registry.
                    action_dict = gesture_dict.get("action") or {}
                    payload_dict = dict(action_dict.get("payload") or {})
                    payload_dict["filename"] = source.name
                    action_dict["payload"] = payload_dict
                    gesture_dict["action"] = action_dict
                except OSError:
                    # Source disappeared / unreadable. Ship the gesture
                    # without the drawing rather than failing the whole
                    # export — the receiver will see "drawing missing"
                    # at fire time, same as if no bundling had happened.
                    pass

        gestures_payload.append(gesture_dict)
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
        for fname, data in drawings_to_bundle.items():
            zf.writestr(f"{_BUNDLE_DRAWING_DIR}/{fname}", data)
    dest_path.write_bytes(buf.getvalue())
    return len(gestures_payload)


def read_bundle(source_path: Path) -> Tuple[List[CustomGesture], dict, dict]:
    """Parse a `.tlg` file. Returns
    (gestures, thumbnails_by_filename, drawings_by_filename).

    Raises `BundleError` on any structural problem so callers can
    surface a single clean error to the user. Does NOT touch the
    receiver's registry — caller decides what to do with each
    gesture (overwrite / skip / rename) before persisting.

    The drawings dict is empty for older bundles that pre-date the
    drawings/ directory addition; callers should treat that as
    "no bundled drawings, fire-time will fall back to the receiver's
    own drawings folder."""
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
            drawings: dict[str, bytes] = {}
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.filename.startswith(f"{_BUNDLE_THUMBNAIL_DIR}/"):
                    rel = info.filename[len(_BUNDLE_THUMBNAIL_DIR) + 1:]
                    if not rel:
                        continue
                    try:
                        thumbnails[rel] = zf.read(info.filename)
                    except Exception:
                        continue
                elif info.filename.startswith(f"{_BUNDLE_DRAWING_DIR}/"):
                    rel = info.filename[len(_BUNDLE_DRAWING_DIR) + 1:]
                    if not rel:
                        continue
                    try:
                        drawings[rel] = zf.read(info.filename)
                    except Exception:
                        continue
    except zipfile.BadZipFile:
        raise BundleError("file isn't a valid .tlg / zip archive")
    if not gestures:
        raise BundleError("bundle contains no gestures")
    return gestures, thumbnails, drawings


# Resolver decision values. The UI maps a "what should happen for
# every conflicting gesture" radio choice to one of these.
RESOLVE_OVERWRITE = "overwrite"
RESOLVE_SKIP = "skip"


def import_bundle(
    registry: GestureRegistry,
    bundle_path: Path,
    *,
    on_conflict: Callable[[CustomGesture], str] = lambda _g: RESOLVE_SKIP,
    drawings_dir: Optional[Path] = None,
) -> Tuple[int, int]:
    """Read a bundle and merge it into `registry`. Returns
    (imported_count, skipped_count).

    `on_conflict` is consulted for each gesture whose name already
    exists in the registry; it must return RESOLVE_OVERWRITE or
    RESOLVE_SKIP. Defaults to skip-on-conflict so a missing
    resolver can never silently destroy existing user gestures.

    `drawings_dir` is the receiver's drawings save directory.
    `show_overlay_drawing` payload PNGs bundled in the .tlg get
    extracted there so the imported gesture finds its drawing on
    fire. Pass None to skip drawing extraction (the gestures still
    import; their show-drawing actions just won't find their PNG
    until the user manually copies one in).

    Thumbnails are written to `registry.thumbnails_dir()` only for
    gestures that are actually imported (no orphan files left
    behind for skipped entries).
    """
    if not registry._loaded:  # type: ignore[attr-defined]
        registry.load()

    gestures, thumbnails, drawings = read_bundle(bundle_path)
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

        # If this gesture's action references a bundled drawing,
        # extract it into the receiver's drawings folder and rewrite
        # the payload to a bare basename so resolve_drawing_path
        # finds the extracted file. Mutating the action's payload
        # dict in place is fine — Action is frozen but its payload
        # field is a regular mutable dict (verified in registry.py).
        action = gesture.action
        if (
            (action.kind or "").lower() == "show_overlay_drawing"
            and drawings_dir is not None
        ):
            payload = action.payload or {}
            requested = str(payload.get("filename", "")).strip()
            # Match by basename. Senders can ship absolute paths;
            # bundled drawings are always stored under their basename.
            bare = Path(requested).name if requested else ""
            if bare and bare in drawings:
                try:
                    drawings_dir = Path(drawings_dir)
                    drawings_dir.mkdir(parents=True, exist_ok=True)
                    target = drawings_dir / bare
                    target.write_bytes(drawings[bare])
                    payload["filename"] = bare
                except OSError:
                    # Disk full / permission issue — leave the gesture
                    # importable; user will see "drawing missing" at
                    # fire time and can manually fix.
                    pass

        # Persist the gesture via the public add() helper so the
        # registry's locking + validation runs.
        registry.add(
            name=gesture.name,
            samples=list(gesture.samples),
            action=action,
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
        gestures, _thumbnails, _drawings = read_bundle(bundle_path)
        return gestures
    except BundleError:
        return []
