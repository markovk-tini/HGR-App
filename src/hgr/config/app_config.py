from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

APP_NAME = "HGR App"
CONFIG_DIR = Path.home() / ".hgr_app"
CONFIG_PATH = CONFIG_DIR / "settings.json"

ORIGINAL_PRIMARY_COLOR = "#0B3D91"
ORIGINAL_ACCENT_COLOR = "#1DE9B6"
ORIGINAL_SURFACE_COLOR = "#0F172A"
ORIGINAL_TEXT_COLOR = "#E5F6FF"
ORIGINAL_HELLO_FONT_SIZE = 72
CURRENT_TUTORIAL_PROMPT_VERSION = 1

SAVE_LOCATION_OUTPUT_ORDER = (
    "drawings",
    "screenshots",
    "screen_recordings",
    "clips",
)

SAVE_LOCATION_LABELS = {
    "drawings": "Drawings",
    "screenshots": "Screenshots",
    "screen_recordings": "Screen Recordings",
    "clips": "Clips",
}

SAVE_LOCATION_CONFIG_FIELDS = {
    "drawings": "drawings_save_dir",
    "screenshots": "screenshots_save_dir",
    "screen_recordings": "screen_recordings_save_dir",
    "clips": "clips_save_dir",
}

SAVE_NAME_CONFIG_FIELDS = {
    "drawings": "drawings_save_name",
    "screenshots": "screenshots_save_name",
    "screen_recordings": "screen_recordings_save_name",
    "clips": "clips_save_name",
}

SAVE_NAME_DEFAULTS = {
    "drawings": "HGR_Drawing",
    "screenshots": "HGR_Screenshot",
    "screen_recordings": "HGR_Recording",
    "clips": "HGR_Clip",
}


def save_name_config_field(output_kind: str) -> str:
    normalized = str(output_kind or "").strip().lower()
    return SAVE_NAME_CONFIG_FIELDS.get(normalized, "")


def configured_save_name(config: "AppConfig", output_kind: str) -> str:
    field_name = save_name_config_field(output_kind)
    default_name = SAVE_NAME_DEFAULTS.get(str(output_kind or "").strip().lower(), "HGR_File")
    if not field_name:
        return default_name
    value = str(getattr(config, field_name, "") or "").strip()
    return value if value else default_name


def _fallback_user_dir(name: str) -> Path:
    candidate = Path.home() / str(name)
    if candidate.exists():
        return candidate
    return Path.home()


def default_save_directory(output_kind: str) -> Path:
    normalized = str(output_kind or "").strip().lower()
    if normalized in {"drawings", "screenshots"}:
        return _fallback_user_dir("Pictures")
    if normalized in {"screen_recordings", "clips"}:
        return _fallback_user_dir("Videos")
    return Path.home()


def save_location_config_field(output_kind: str) -> str:
    normalized = str(output_kind or "").strip().lower()
    return SAVE_LOCATION_CONFIG_FIELDS.get(normalized, "")


def configured_save_directory(config: "AppConfig", output_kind: str) -> Path:
    field_name = save_location_config_field(output_kind)
    default_dir = default_save_directory(output_kind)
    raw_value = str(getattr(config, field_name, "") or "").strip() if field_name else ""
    target = Path(raw_value).expanduser() if raw_value else default_dir
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception:
        try:
            default_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return Path.home()
        return default_dir


@dataclass
class AppConfig:
    primary_color: str = ORIGINAL_PRIMARY_COLOR
    accent_color: str = ORIGINAL_ACCENT_COLOR
    surface_color: str = ORIGINAL_SURFACE_COLOR
    text_color: str = ORIGINAL_TEXT_COLOR
    hello_font_size: int = ORIGINAL_HELLO_FONT_SIZE
    gesture_cooldown_seconds: float = 2.0
    stable_frames_required: int = 6
    camera_scan_limit: int = 8
    show_start_instructions_prompt: bool = True
    preferred_camera_index: Optional[int] = None
    preferred_microphone_name: Optional[str] = None
    tutorial_prompt_version: int = CURRENT_TUTORIAL_PROMPT_VERSION
    mouse_control_box_center_x: float = 0.50
    mouse_control_box_center_y: float = 0.55
    mouse_control_box_area: float = 0.36
    mouse_control_box_aspect_power: float = 0.40
    drawings_save_dir: str = field(default_factory=lambda: str(default_save_directory("drawings")))
    screenshots_save_dir: str = field(default_factory=lambda: str(default_save_directory("screenshots")))
    screen_recordings_save_dir: str = field(default_factory=lambda: str(default_save_directory("screen_recordings")))
    clips_save_dir: str = field(default_factory=lambda: str(default_save_directory("clips")))
    drawings_save_name: str = "HGR_Drawing"
    screenshots_save_name: str = "HGR_Screenshot"
    screen_recordings_save_name: str = "HGR_Recording"
    clips_save_name: str = "HGR_Clip"


DEFAULT_CONFIG = AppConfig()


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        values = {field: data.get(field, getattr(DEFAULT_CONFIG, field)) for field in asdict(DEFAULT_CONFIG)}

        # One-time migration: old installs had the instructions prompt setting,
        # but not the newer tutorial prompt version. Re-show the prompt once.
        if "tutorial_prompt_version" not in data:
            values["show_start_instructions_prompt"] = True
            values["tutorial_prompt_version"] = CURRENT_TUTORIAL_PROMPT_VERSION

        # Migrate the mouse control box only when the user still has the old defaults.
        if abs(float(values.get("mouse_control_box_center_x", 0.0)) - 0.44) < 1e-6:
            values["mouse_control_box_center_x"] = DEFAULT_CONFIG.mouse_control_box_center_x
        if abs(float(values.get("mouse_control_box_center_y", 0.0)) - 0.56) < 1e-6:
            values["mouse_control_box_center_y"] = DEFAULT_CONFIG.mouse_control_box_center_y
        if abs(float(values.get("mouse_control_box_area", 0.0)) - 0.31) < 1e-6:
            values["mouse_control_box_area"] = DEFAULT_CONFIG.mouse_control_box_area

        return AppConfig(**values)
    except Exception:
        return AppConfig()


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
