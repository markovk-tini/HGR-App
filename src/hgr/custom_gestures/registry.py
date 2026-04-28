from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Storage location. Overridable via env var so tests and alternate profiles
# can point elsewhere without editing code.
_ENV_REGISTRY_PATH = "HGR_CUSTOM_GESTURES_PATH"
_DEFAULT_REGISTRY_PATH = Path.home() / ".hgr_app" / "custom_gestures.json"

# Feature vector layout (total 81):
#   [0:63]   — 21 landmarks * 3 coords, wrist-centered, scaled by |L9|
#   [63:66]  — 3 adjacent fingertip-pair distances (grouping signal):
#              |L8-L12|, |L12-L16|, |L16-L20|
#   [66:71]  — 5 wrist-to-fingertip distances (extension signal):
#              |L0-L4|, |L0-L8|, |L0-L12|, |L0-L16|, |L0-L20|
#   [71:81]  — 10 joint-bend angles (curl signal, in radians):
#              2 angles per finger × 5 fingers; angle = bend at the joint
#              (0 = bones colinear/extended, π/2 = bent 90°, π = folded).
#              Order: thumb (L2, L3), index (L6, L7), middle (L10, L11),
#                     ring (L14, L15), pinky (L18, L19).
#
# The trailing 18 structural features are rotation-invariant (distances
# and angles between landmarks don't change under rigid rotation), and
# they are multiplied by a weight in the classifier so their contribution
# dominates the landmark portion — raw landmark Euclidean smears small
# per-finger differences across 63 dims and loses the signal.
#
# Joint angles add a strong "how curled is each finger" signal that the
# extension distances only approximate. Two poses with similar wrist-to-
# tip distances but different curl patterns (e.g., a fist with thumb-up
# vs a fist with thumb tucked) score very differently on joint angles.
_FEATURE_VECTOR_LEN = 81
_LANDMARK_FEATURE_LEN = 63
_SPACING_FEATURE_LEN = 3
_EXTENSION_FEATURE_LEN = 5
_JOINT_ANGLE_FEATURE_LEN = 10


def registry_path() -> Path:
    env = os.getenv(_ENV_REGISTRY_PATH, "").strip()
    if env:
        return Path(env)
    return _DEFAULT_REGISTRY_PATH


@dataclass(frozen=True)
class Action:
    """A declarative action to execute when a gesture is matched.

    `kind` picks the executor. `payload` carries executor-specific params.
    Executors live in action.py and are pure dispatchers on `kind`.
    """
    kind: str  # keystroke | hotkey | text | open_url | run_command | noop
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "payload": dict(self.payload)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Action":
        return cls(
            kind=str(data.get("kind", "noop")),
            payload=dict(data.get("payload") or {}),
        )


@dataclass(frozen=True)
class GestureSample:
    """One captured hand pose as a normalized feature vector."""
    features: List[float]

    def __post_init__(self) -> None:
        if len(self.features) != _FEATURE_VECTOR_LEN:
            raise ValueError(
                f"GestureSample.features must have {_FEATURE_VECTOR_LEN} "
                f"elements, got {len(self.features)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {"features": list(self.features)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GestureSample":
        feats = [float(x) for x in data.get("features", [])]

        def _dist(lm: List[float], a: int, b: int) -> float:
            ax, ay, az = lm[a * 3], lm[a * 3 + 1], lm[a * 3 + 2]
            bx, by, bz = lm[b * 3], lm[b * 3 + 1], lm[b * 3 + 2]
            dx, dy, dz = ax - bx, ay - by, az - bz
            return (dx * dx + dy * dy + dz * dz) ** 0.5

        def _angle(lm: List[float], a: int, b: int, c: int) -> float:
            """Bend angle at landmark b between segments (a→b) and (b→c).
            0 = colinear (extended); π = folded back."""
            ax, ay, az = lm[a * 3], lm[a * 3 + 1], lm[a * 3 + 2]
            bx, by, bz = lm[b * 3], lm[b * 3 + 1], lm[b * 3 + 2]
            cx, cy, cz = lm[c * 3], lm[c * 3 + 1], lm[c * 3 + 2]
            v1x, v1y, v1z = bx - ax, by - ay, bz - az
            v2x, v2y, v2z = cx - bx, cy - by, cz - bz
            n1 = (v1x * v1x + v1y * v1y + v1z * v1z) ** 0.5
            n2 = (v2x * v2x + v2y * v2y + v2z * v2z) ** 0.5
            if n1 < 1e-6 or n2 < 1e-6:
                return 0.0
            cos = (v1x * v2x + v1y * v2y + v1z * v2z) / (n1 * n2)
            cos = max(-1.0, min(1.0, cos))
            import math
            return float(math.acos(cos))

        def _derive_spacing(lm: List[float]) -> List[float]:
            return [_dist(lm, 8, 12), _dist(lm, 12, 16), _dist(lm, 16, 20)]

        def _derive_extension(lm: List[float]) -> List[float]:
            return [_dist(lm, 0, t) for t in (4, 8, 12, 16, 20)]

        def _derive_joint_angles(lm: List[float]) -> List[float]:
            chains = [(1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12),
                      (13, 14, 15, 16), (17, 18, 19, 20)]
            out: List[float] = []
            for a, b, c, d in chains:
                out.append(_angle(lm, a, b, c))
                out.append(_angle(lm, b, c, d))
            return out

        if len(feats) == _LANDMARK_FEATURE_LEN:
            # Legacy schema 1: landmarks only (63 floats).
            lm = feats
            feats = (list(feats)
                     + _derive_spacing(lm)
                     + _derive_extension(lm)
                     + _derive_joint_angles(lm))
        elif len(feats) == _LANDMARK_FEATURE_LEN + _SPACING_FEATURE_LEN:
            # Legacy schema 2: landmarks + spacing (66 floats).
            lm = feats[:_LANDMARK_FEATURE_LEN]
            feats = list(feats) + _derive_extension(lm) + _derive_joint_angles(lm)
        elif len(feats) == _LANDMARK_FEATURE_LEN + _SPACING_FEATURE_LEN + _EXTENSION_FEATURE_LEN:
            # Legacy schema 3: landmarks + spacing + extension (71 floats).
            lm = feats[:_LANDMARK_FEATURE_LEN]
            feats = list(feats) + _derive_joint_angles(lm)
        return cls(features=feats)


@dataclass(frozen=True)
class CustomGesture:
    name: str
    samples: List[GestureSample]
    action: Action
    created_at: str  # ISO-8601 UTC timestamp
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "action": self.action.to_dict(),
            "samples": [s.to_dict() for s in self.samples],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CustomGesture":
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", "")),
            action=Action.from_dict(data.get("action") or {}),
            samples=[GestureSample.from_dict(s) for s in data.get("samples", [])],
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class GestureRegistry:
    """JSON-backed store of user-defined gestures. Thread-safe for load/save
    but callers should coordinate writes to avoid lost updates.
    """

    _SCHEMA_VERSION = 1

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else registry_path()
        self._lock = threading.Lock()
        self._gestures: Dict[str, CustomGesture] = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        with self._lock:
            self._gestures = {}
            self._loaded = True
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                # Corrupt file — start fresh rather than crashing the caller.
                # A future version can back up the broken file here.
                return
            for entry in raw.get("gestures", []):
                try:
                    gesture = CustomGesture.from_dict(entry)
                except Exception:
                    continue
                self._gestures[gesture.name] = gesture

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": self._SCHEMA_VERSION,
                "gestures": [g.to_dict() for g in self._gestures.values()],
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)

    def add(
        self,
        name: str,
        samples: List[GestureSample],
        action: Action,
        *,
        description: str = "",
        overwrite: bool = False,
    ) -> CustomGesture:
        if not self._loaded:
            self.load()
        name = name.strip()
        if not name:
            raise ValueError("gesture name must be non-empty")
        if not samples:
            raise ValueError("gesture must have at least one sample")
        with self._lock:
            if name in self._gestures and not overwrite:
                raise ValueError(
                    f"gesture {name!r} already exists (pass overwrite=True to replace)"
                )
            gesture = CustomGesture(
                name=name,
                samples=list(samples),
                action=action,
                created_at=_utc_now_iso(),
                description=description,
            )
            self._gestures[name] = gesture
        return gesture

    def remove(self, name: str) -> bool:
        if not self._loaded:
            self.load()
        with self._lock:
            return self._gestures.pop(name, None) is not None

    def get(self, name: str) -> Optional[CustomGesture]:
        if not self._loaded:
            self.load()
        with self._lock:
            return self._gestures.get(name)

    def list(self) -> List[CustomGesture]:
        if not self._loaded:
            self.load()
        with self._lock:
            return list(self._gestures.values())
