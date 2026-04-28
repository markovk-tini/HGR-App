"""JSON-schema definitions for tools exposed to the Realtime model.

Each schema follows the OpenAI tool-format used by the Realtime
`session.update.tools` field:
    {"type": "function", "name": ..., "description": ..., "parameters": {...}}

`ToolRegistry.openai_tools()` returns the full list. `validate_args`
enforces required fields and basic types before any executor code
runs — the model is *not* trusted to follow the schema perfectly.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_screen_context",
        "description": (
            "Return the current active window title, process name, and "
            "screenshot metadata. Optionally include a fresh screenshot. "
            "Use this whenever you need to see what is on the user's screen "
            "right now."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "include_image": {
                    "type": "boolean",
                    "description": "If true, include a fresh JPEG screenshot.",
                    "default": True,
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "click_screen",
        "description": (
            "Move the mouse to a screen position and click. Coordinates are "
            "either absolute screen pixels (coordinate_space='screen') or "
            "normalized 0..1 of the primary virtual screen "
            "(coordinate_space='normalized')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "coordinate_space": {
                    "type": "string",
                    "enum": ["screen", "normalized"],
                    "default": "normalized",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                },
                "double_click": {"type": "boolean", "default": False},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "type_text",
        "description": (
            "Type text into the currently focused window. Prefer "
            "method='clipboard_paste' for long text/code; use "
            "'keyboard_type' only for short text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": ["clipboard_paste", "keyboard_type"],
                    "default": "clipboard_paste",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "press_hotkey",
        "description": (
            "Press a keyboard shortcut. `keys` is an ordered list of "
            "modifier+key names: ['ctrl','s'], ['alt','f4'], "
            "['ctrl','shift','t']."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
            },
            "required": ["keys"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "open_in_editor",
        "description": (
            "Open a folder (and optionally a specific file) in a code "
            "editor. PREFER THIS over open_app when the user wants to "
            "see / edit code — it ensures the editor opens WITH the "
            "folder loaded in the sidebar AND optionally with a "
            "specific file already open in the editor pane. "
            "Always pass the absolute folder_path you got from "
            "create_folder; passing relative paths or omitting it gives "
            "the user an empty editor window which is never what they "
            "want."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "editor": {
                    "type": "string",
                    "enum": ["code", "notepad"],
                    "default": "code",
                    "description": "Which editor to launch. 'code' = VS Code.",
                },
                "folder_path": {
                    "type": "string",
                    "description": (
                        "Absolute path to the folder to open in the "
                        "editor's sidebar."
                    ),
                },
                "file_to_open": {
                    "type": "string",
                    "description": (
                        "Optional. Absolute path to a specific file to "
                        "open in the editor pane (must be inside "
                        "folder_path). Pass this so the user immediately "
                        "sees the file you just created."
                    ),
                },
            },
            "required": ["folder_path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "open_app",
        "description": (
            "Open a desktop application by name (e.g. 'chrome', 'spotify', "
            "'notepad', 'code'). Uses the OS's registered handler. "
            "Optionally pass `arguments` to forward command-line args — "
            "e.g. open_app(app_name='code', arguments=['C:\\\\path\\\\to\\\\folder']) "
            "opens VS Code with that folder already loaded in the sidebar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string"},
                "arguments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional command-line arguments to pass to the app.",
                },
            },
            "required": ["app_name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "open_url",
        "description": (
            "Open a URL or web search query in the user's default or "
            "specified browser."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url_or_query": {"type": "string"},
                "browser": {"type": "string"},
            },
            "required": ["url_or_query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_folder",
        "description": (
            "Create a new folder. If `base_dir` is omitted, uses the safe "
            "workspace dir. Returns the absolute path created."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base_dir": {"type": "string"},
                "folder_name": {"type": "string"},
            },
            "required": ["folder_name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_file",
        "description": (
            "Create a new file with optional content. Refuses to overwrite "
            "unless `overwrite=true`."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base_dir": {"type": "string"},
                "relative_path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["relative_path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "write_file",
        "description": (
            "Write/replace the entire contents of a file. If the file "
            "exists and `overwrite=false`, returns an error requiring "
            "confirmation. A `.bak` backup is created before overwriting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "base_dir": {"type": "string"},
                "relative_path": {"type": "string"},
                "content": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["relative_path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "append_file",
        "description": "Append text to an existing file (created if missing).",
        "parameters": {
            "type": "object",
            "properties": {
                "base_dir": {"type": "string"},
                "relative_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["relative_path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "move_file",
        "description": (
            "Move or rename a file or folder. Use this when the user "
            "asks to relocate something you already created (e.g. \"move "
            "it to Documents\"), instead of trying to fake it with "
            "delete + create. Both paths must be absolute. Refuses to "
            "move into system directories (Windows, Program Files, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Absolute path of the file or folder to move.",
                },
                "destination_path": {
                    "type": "string",
                    "description": (
                        "Absolute path of the new location. If the "
                        "destination's parent doesn't exist, the call "
                        "fails — use create_folder first if needed."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "If true, replace any existing file at the "
                        "destination. If false (default) and the "
                        "destination exists, returns needs_confirmation."
                    ),
                },
            },
            "required": ["source_path", "destination_path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "rename_file",
        "description": (
            "Rename a file or folder in place. Pass the absolute current "
            "path and the NEW NAME (just the name, not a path)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path of the file or folder to rename.",
                },
                "new_name": {
                    "type": "string",
                    "description": "New name (basename only, e.g. 'final.py'). No slashes.",
                },
            },
            "required": ["path", "new_name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "delete_file",
        "description": (
            "Delete a file or folder. ALWAYS ask the user for explicit "
            "confirmation in plain text BEFORE calling this tool — never "
            "delete without asking. Refuses to touch system directories. "
            "Folders are deleted recursively."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path of the file or folder to delete.",
                },
                "confirmed": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Must be true to actually delete. The model is "
                        "responsible for asking the user for explicit "
                        "permission BEFORE setting this true."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_recent_paths",
        "description": (
            "Return the absolute paths the agent has created or written "
            "in this session, most-recent first. Use this when you've "
            "lost track of where you put something the user is now "
            "asking about."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "run_existing_touchless_action",
        "description": (
            "Bridge to the existing Touchless action router. Use this for "
            "spotify/chrome/youtube/system actions already implemented in "
            "the app."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_name": {"type": "string"},
                "parameters": {"type": "object"},
            },
            "required": ["action_name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "run_python_script",
        "description": (
            "Run a Python script and capture its output. Use this to "
            "EXECUTE a script you just created — never try to focus VS "
            "Code's terminal and type 'python main.py' manually. This "
            "tool spawns python directly and returns stdout/stderr. The "
            "script is launched DETACHED so GUI scripts (tkinter, "
            "pygame, etc.) can show their windows without blocking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script_path": {
                    "type": "string",
                    "description": "Absolute path to the .py file to run.",
                },
                "wait_for_exit": {
                    "type": "boolean",
                    "description": (
                        "If true, block until the script finishes and "
                        "return its stdout/stderr. Use for short "
                        "terminal scripts. If false (default), launch "
                        "and return immediately — required for GUI "
                        "scripts like tkinter/pygame that show a window."
                    ),
                    "default": False,
                },
                "timeout_sec": {
                    "type": "number",
                    "description": "Max wait when wait_for_exit=true (default 15).",
                    "default": 15,
                },
            },
            "required": ["script_path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "skip_youtube_ad",
        "description": (
            "Try to click YouTube's user-visible Skip Ad button using the "
            "existing template-matching pipeline. Does NOT bypass non-"
            "skippable ads or block ads in any way."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "ask_user_confirmation",
        "description": (
            "Show a confirmation dialog before a risky action. Returns "
            "{'approved': bool}. risk_level affects the dialog styling."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "default": "medium",
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
    },
]


def all_tool_schemas() -> List[Dict[str, Any]]:
    """Return a fresh copy of the full tool schema list."""
    # Shallow copy is fine — callers should not mutate the inner dicts.
    return [dict(s) for s in _TOOL_SCHEMAS]


_TYPE_MAP = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_args(tool_name: str, args: Any) -> Tuple[bool, str, Dict[str, Any]]:
    """Light JSON-schema validation. Returns (ok, error_message, normalised_args)."""
    schema = next(
        (s for s in _TOOL_SCHEMAS if s["name"] == tool_name),
        None,
    )
    if schema is None:
        return False, f"unknown tool: {tool_name}", {}
    if not isinstance(args, dict):
        return False, "tool arguments must be a JSON object", {}

    params = schema["parameters"]
    properties = params.get("properties", {})
    required = params.get("required", [])

    for key in required:
        if key not in args:
            return False, f"missing required argument: {key}", {}

    normalised: Dict[str, Any] = {}
    for key, value in args.items():
        if key not in properties:
            # tolerate extras to keep the model loop unblocked, but log them
            normalised[key] = value
            continue
        prop = properties[key]
        expected = _TYPE_MAP.get(str(prop.get("type", "")))
        if expected is not None and not isinstance(value, expected):
            # accept ints where numbers are expected
            if expected == (int, float) and isinstance(value, bool):
                return False, f"{key} expected number, got bool", {}
            if not (expected == (int, float) and isinstance(value, (int, float))):
                if expected != bool and isinstance(value, bool):
                    return False, f"{key} expected {prop.get('type')}, got bool", {}
                if not isinstance(value, expected):
                    return False, f"{key} expected {prop.get('type')}", {}
        if "enum" in prop and value not in prop["enum"]:
            return False, f"{key} must be one of {prop['enum']}", {}
        normalised[key] = value

    # apply declared defaults for missing optional fields
    for key, prop in properties.items():
        if key not in normalised and "default" in prop:
            normalised[key] = prop["default"]

    return True, "", normalised
