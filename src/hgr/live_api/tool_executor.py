"""Tool dispatcher for the Live API session.

Validates arguments, routes calls to existing Touchless controllers
where possible, and returns structured results that are sent back to
the model as a `function_call_output` event.

Important boundaries:
  * NO arbitrary Python/PowerShell/CMD execution. If we need that
    later, it must be a separate locked-down tool with explicit user
    confirmation.
  * Risky filesystem ops (overwrite, delete) require explicit
    `overwrite=true` AND ideally a prior `ask_user_confirmation`. The
    executor returns `{"status": "needs_confirmation", ...}` rather
    than silently doing the action.
  * All paths are constrained to the safe workspace dir unless an
    absolute path was explicitly provided.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .config import LiveApiConfig
from .live_api_logger import LiveApiLogger
from .schemas import validate_args
from .screen_context import ScreenContext
from ..debug.foreground_window import get_foreground_window_info
from ..debug.mouse_controller import MouseController
from ..debug.text_input_controller import TextInputController
from ..utils.subprocess_utils import launch_external


# Map of human/model-friendly key names -> Win32 virtual-key codes.
# Only the modifiers and the most common keys — anything not here is
# treated as a literal character via SendInput unicode path (handled by
# TextInputController for printable text).
_VK_MAP: Dict[str, int] = {
    "ctrl": 0x11, "control": 0x11,
    "shift": 0x10,
    "alt": 0x12, "menu": 0x12,
    "win": 0x5B, "windows": 0x5B, "meta": 0x5B, "lwin": 0x5B,
    "tab": 0x09,
    "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08, "back": 0x08,
    "delete": 0x2E, "del": 0x2E,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}
for _i in range(26):
    _VK_MAP[chr(ord("a") + _i)] = 0x41 + _i
for _i in range(10):
    _VK_MAP[str(_i)] = 0x30 + _i


# A confirmation callback can be injected by the manager so risky tool
# calls show the existing Touchless overlay/dialog. Signature:
#   callback(message: str, risk_level: str) -> bool   # True = approved
ConfirmCallback = Callable[[str, str], bool]


class ToolExecutor:
    def __init__(
        self,
        *,
        config: LiveApiConfig,
        logger: LiveApiLogger,
        screen_context: ScreenContext,
        confirm_callback: Optional[ConfirmCallback] = None,
        external_action_router: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._config = config
        self._logger = logger
        self._screen = screen_context
        self._confirm = confirm_callback
        self._external_router = external_action_router

        # Lazy-init heavy controllers — only created when first used so
        # the main app's startup cost is unaffected when Live API is OFF.
        self._mouse: Optional[MouseController] = None
        self._text: Optional[TextInputController] = None
        self._youtube = None  # YouTubeController, lazy
        self._chrome = None   # ChromeController, lazy

        self._safe_workspace = Path(self._config.safe_workspace_dir)
        try:
            self._safe_workspace.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._logger.exception("safe_workspace_init_failed", exc, path=str(self._safe_workspace))
        # Bounded MRU of paths the agent has created or written this
        # session. Used by list_recent_paths so the model can recover
        # context when it loses track of "where did I just put that".
        self._recent_paths: list[str] = []

    # ---- public ----

    def execute(self, name: str, raw_args: Dict[str, Any]) -> Dict[str, Any]:
        started = time.time()
        ok, err, args = validate_args(name, raw_args or {})
        if not ok:
            self._logger.event(
                "tool_validation_failed", tool=name, error=err, raw_keys=list((raw_args or {}).keys())
            )
            return _result(status="error", error=err, code="invalid_arguments")

        self._logger.event(
            "tool_call",
            tool=name,
            args=_summarize_args(name, args, debug=self._config.debug_text_logging),
        )

        try:
            handler = self._handlers().get(name)
            if handler is None:
                result = _result(status="error", error=f"no handler for {name}", code="no_handler")
            else:
                result = handler(args)
        except Exception as exc:
            self._logger.exception("tool_exception", exc, tool=name)
            result = _result(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                code="exception",
            )

        self._logger.latency(f"tool:{name}", started, status=result.get("status"))
        return result

    # ---- handlers ----

    def _handlers(self) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
        return {
            "get_screen_context": self._t_get_screen_context,
            "click_screen": self._t_click_screen,
            "type_text": self._t_type_text,
            "press_hotkey": self._t_press_hotkey,
            "open_app": self._t_open_app,
            "open_in_editor": self._t_open_in_editor,
            "open_url": self._t_open_url,
            "create_folder": self._t_create_folder,
            "create_file": self._t_create_file,
            "write_file": self._t_write_file,
            "append_file": self._t_append_file,
            "move_file": self._t_move_file,
            "rename_file": self._t_rename_file,
            "delete_file": self._t_delete_file,
            "list_recent_paths": self._t_list_recent_paths,
            "run_existing_touchless_action": self._t_run_existing_action,
            "run_python_script": self._t_run_python_script,
            "skip_youtube_ad": self._t_skip_youtube_ad,
            "ask_user_confirmation": self._t_ask_user_confirmation,
        }

    def _t_get_screen_context(self, args: Dict[str, Any]) -> Dict[str, Any]:
        include_image = bool(args.get("include_image", True))
        info = get_foreground_window_info()
        out: Dict[str, Any] = {
            "status": "ok",
            "active_window_title": (info.title if info else ""),
            "active_window_process": (info.process_name if info else ""),
            "timestamp": time.time(),
        }
        if include_image:
            frame = self._screen.capture()
            if frame is None:
                out["screenshot"] = None
                out["screenshot_error"] = "capture_failed"
            else:
                out["screenshot"] = {
                    "width": frame.width,
                    "height": frame.height,
                    "jpeg_b64": frame.b64,
                    "active_window_title": frame.active_window_title,
                    "active_window_process": frame.active_window_process,
                }
        return out

    def _t_click_screen(self, args: Dict[str, Any]) -> Dict[str, Any]:
        mouse = self._ensure_mouse()
        if mouse is None or not mouse.available:
            return _result(status="error", error="mouse unavailable", code="no_mouse")
        x = float(args["x"])
        y = float(args["y"])
        space = str(args.get("coordinate_space", "normalized"))
        button = str(args.get("button", "left"))
        double = bool(args.get("double_click", False))
        if button != "left":
            # Only left+right are wired into MouseController; right used directly,
            # middle returns not-implemented to keep behaviour explicit.
            if button == "right":
                if not mouse.right_click():
                    return _result(status="error", error="right click failed", code="click_failed")
                return _result(status="ok", message="right_click", x=x, y=y, space=space)
            return _result(status="error", error="middle button not supported", code="unsupported")
        if space == "normalized":
            mouse.move_normalized(max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))
        else:
            bounds = mouse.virtual_bounds()
            if bounds is None:
                return _result(status="error", error="virtual bounds unknown", code="no_bounds")
            left, top, width, height = bounds
            nx = (float(x) - left) / max(width - 1, 1)
            ny = (float(y) - top) / max(height - 1, 1)
            mouse.move_normalized(max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))
        time.sleep(0.05)
        if not mouse.left_click():
            return _result(status="error", error="left click failed", code="click_failed")
        if double:
            time.sleep(0.06)
            mouse.left_click()
        return _result(status="ok", message="clicked", x=x, y=y, space=space, double=double)

    def _t_type_text(self, args: Dict[str, Any]) -> Dict[str, Any]:
        text = str(args.get("text", ""))
        method = str(args.get("method", "clipboard_paste"))
        controller = self._ensure_text()
        if controller is None or not controller.available:
            return _result(status="error", error=controller.message if controller else "text input unavailable", code="no_text")
        # TextInputController.insert_text needs a target window to focus.
        # The Live API agent never sets one explicitly, so auto-capture
        # whatever's currently in the foreground (the model is usually
        # being asked to type into VS Code / a browser / Notepad — all
        # of those will be in the foreground if the user just opened
        # them). Without this, every type_text call returns
        # "could not focus dictation target" and the model wastes turns
        # retrying.
        try:
            controller.capture_target_window()
        except Exception as exc:
            self._logger.exception("type_text_capture_failed", exc)
        ok = controller.insert_text(text, prefer_paste=(method == "clipboard_paste"))
        if not ok:
            return _result(status="error", error=controller.message, code="insert_failed")
        self._logger.text("tool_type_text_payload", text)
        return _result(status="ok", message="text inserted", chars=len(text), method=method)

    def _t_press_hotkey(self, args: Dict[str, Any]) -> Dict[str, Any]:
        keys = list(args.get("keys", []) or [])
        if not keys:
            return _result(status="error", error="empty hotkey", code="invalid_arguments")
        normalised = [str(k).strip().lower() for k in keys]
        # Soft-confirm destructive shortcuts.
        if normalised in ([["alt", "f4"]], [["ctrl", "shift", "q"]]) or normalised == ["alt", "f4"]:
            if self._confirm and not self._confirm(
                f"Press {'+'.join(normalised)} (this may close a window)?", "high"
            ):
                return _result(status="needs_confirmation", message="user declined hotkey", keys=normalised)
        vks: list[int] = []
        for k in normalised:
            vk = _VK_MAP.get(k)
            if vk is None:
                return _result(status="error", error=f"unknown key: {k}", code="invalid_key")
            vks.append(vk)
        controller = self._ensure_text()
        if controller is None or not controller.available:
            return _result(status="error", error="text input unavailable", code="no_text")
        ok = controller._send_shortcut(*vks)  # noqa: SLF001 — internal but stable in this codebase
        return _result(status="ok" if ok else "error", message="hotkey", keys=normalised)

    def _t_open_app(self, args: Dict[str, Any]) -> Dict[str, Any]:
        name = str(args.get("app_name", "")).strip()
        if not name:
            return _result(status="error", error="empty app_name", code="invalid_arguments")
        # Editor names route to open_in_editor instead — this prevents
        # the common failure mode where the model launches VS Code with
        # no folder and shows the user an empty window. Returning a
        # corrective error tells the model exactly which tool to call
        # next, so it self-corrects on the second hop.
        editor_aliases = {
            "code", "vscode", "vs code", "visual studio code", "vs",
            "notepad", "notepad.exe",
        }
        if name.strip().lower() in editor_aliases:
            return _result(
                status="error",
                error=(
                    f"Use open_in_editor for {name}, not open_app. "
                    f"open_in_editor takes a folder_path so the editor "
                    f"opens with the folder loaded; open_app gives an "
                    f"empty window which is never useful."
                ),
                code="wrong_tool_for_editor",
                correct_tool="open_in_editor",
            )
        # Optional CLI args to forward (e.g. `code "C:\path\to\folder"`).
        cli_args = args.get("arguments") or []
        if not isinstance(cli_args, list):
            cli_args = []
        cli_args = [str(a) for a in cli_args]
        # Models often pass friendly names ("Visual Studio Code") that
        # are NOT what Windows App Paths expects ("code"). Build a
        # candidate list of synonyms + likely install paths so a single
        # tool call resolves the common cases without forcing the model
        # to retry.
        candidates = _open_app_candidates(name)
        tried: list[str] = []
        for candidate in candidates:
            tried.append(candidate)
            if launch_external(candidate, args=cli_args or None):
                return _result(
                    status="ok",
                    message=f"launched via '{candidate}'",
                    app=name,
                    resolved=candidate,
                    args=cli_args,
                    tried=tried,
                )
        return _result(
            status="error",
            error=f"could not launch '{name}'",
            code="launch_failed",
            tried=tried,
        )

    def _t_open_in_editor(self, args: Dict[str, Any]) -> Dict[str, Any]:
        editor = str(args.get("editor", "code")).strip().lower()
        folder = str(args.get("folder_path", "")).strip()
        file_to_open = str(args.get("file_to_open", "") or "").strip()
        if not folder:
            return _result(status="error", error="empty folder_path", code="invalid_arguments")
        folder_path = Path(folder).expanduser().resolve()
        if not folder_path.exists():
            return _result(
                status="error",
                error=f"folder does not exist: {folder_path}",
                code="folder_not_found",
            )
        cli_args: list[str] = [str(folder_path)]
        if file_to_open:
            file_path = Path(file_to_open).expanduser().resolve()
            if file_path.exists():
                cli_args.append(str(file_path))
            else:
                # Don't fail — open the folder anyway, just without the
                # specific file. Logged so we know the model passed a
                # bad file path.
                self._logger.event(
                    "open_in_editor_file_missing",
                    file=str(file_path),
                    folder=str(folder_path),
                )
        # Reuse open_app's resolver so editor synonyms ("code", "vscode",
        # "notepad", etc.) all map to the right binary.
        candidates = _open_app_candidates(editor)
        tried: list[str] = []
        for candidate in candidates:
            tried.append(candidate)
            if launch_external(candidate, args=cli_args):
                return _result(
                    status="ok",
                    message=f"opened {editor} on {folder_path.name}",
                    folder=str(folder_path),
                    file=file_to_open or None,
                    resolved=candidate,
                    args=cli_args,
                )
        return _result(
            status="error",
            error=f"could not launch editor '{editor}'",
            code="editor_launch_failed",
            tried=tried,
        )

    def _t_open_url(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = str(args.get("url_or_query", "")).strip()
        if not target:
            return _result(status="error", error="empty url_or_query", code="invalid_arguments")
        browser = str(args.get("browser", "")).strip().lower()
        if "://" not in target and not target.startswith(("www.", "http")):
            url = f"https://www.google.com/search?q={target.replace(' ', '+')}"
        else:
            url = target if "://" in target else f"https://{target}"
        if browser in {"chrome", "google chrome"}:
            chrome = self._ensure_chrome()
            if chrome is not None and chrome.available:
                if chrome.open_url(url):
                    return _result(status="ok", message="opened in chrome", url=url)
        ok = launch_external(url)
        return _result(status="ok" if ok else "error", message="opened url", url=url)

    def _t_create_folder(self, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            base = self._resolve_base_dir(args.get("base_dir"))
        except ValueError as exc:
            return _result(
                status="error", error=str(exc), code="invalid_base_dir",
                next_action="retry_with_a_real_path_under_user_home",
            )
        folder_name = str(args.get("folder_name", "")).strip()
        if not folder_name:
            return _result(status="error", error="empty folder_name", code="invalid_arguments")
        target = (base / folder_name).resolve()
        if not _is_within(target, base) and not args.get("base_dir"):
            return _result(status="error", error="path escapes safe workspace", code="path_escape")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return _result(status="error", error=str(exc), code="mkdir_failed", path=str(target))
        self._remember_path(str(target))
        return _result(status="ok", message="folder created", path=str(target))

    def _t_create_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            base = self._resolve_base_dir(args.get("base_dir"))
        except ValueError as exc:
            return _result(
                status="error", error=str(exc), code="invalid_base_dir",
                next_action="retry_with_a_real_path_under_user_home",
            )
        rel = str(args.get("relative_path", "")).strip()
        if not rel:
            return _result(status="error", error="empty relative_path", code="invalid_arguments")
        target = (base / rel).resolve()
        if not _is_within(target, base) and not args.get("base_dir"):
            return _result(status="error", error="path escapes safe workspace", code="path_escape")
        if target.exists() and not bool(args.get("overwrite", False)):
            return _result(
                status="needs_confirmation",
                error=(
                    f"File already exists at {target}. To overwrite, "
                    f"either ask the user for permission first OR call "
                    f"this tool again with overwrite=true if they "
                    f"already approved."
                ),
                code="exists",
                path=str(target),
                next_action="ask_user_then_retry_with_overwrite_true",
            )
        if target.exists() and self._confirm and not self._confirm(
            f"Overwrite existing file {target}?", "medium"
        ):
            return _result(status="needs_confirmation", error="user declined overwrite", path=str(target))
        content = args.get("content")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            text = "" if content is None else str(content)
            target.write_text(text, encoding="utf-8")
        except Exception as exc:
            return _result(status="error", error=str(exc), code="write_failed", path=str(target))
        if content is not None:
            self._logger.text("tool_create_file_content", str(content), path=str(target))
        self._remember_path(str(target))
        return _result(status="ok", message="file created", path=str(target), bytes=target.stat().st_size)

    def _t_write_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            base = self._resolve_base_dir(args.get("base_dir"))
        except ValueError as exc:
            return _result(
                status="error", error=str(exc), code="invalid_base_dir",
                next_action="retry_with_a_real_path_under_user_home",
            )
        rel = str(args.get("relative_path", "")).strip()
        content = str(args.get("content", ""))
        overwrite = bool(args.get("overwrite", False))
        if not rel:
            return _result(status="error", error="empty relative_path", code="invalid_arguments")
        target = (base / rel).resolve()
        if not _is_within(target, base) and not args.get("base_dir"):
            return _result(status="error", error="path escapes safe workspace", code="path_escape")
        if target.exists() and not overwrite:
            return _result(status="needs_confirmation", error="file exists, set overwrite=true", code="exists", path=str(target))
        if target.exists():
            try:
                shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
            except Exception as exc:
                self._logger.exception("backup_failed", exc, path=str(target))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except Exception as exc:
            return _result(status="error", error=str(exc), code="write_failed", path=str(target))
        self._logger.text("tool_write_file_content", content, path=str(target))
        self._remember_path(str(target))
        return _result(status="ok", message="file written", path=str(target), bytes=target.stat().st_size)

    def _t_append_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            base = self._resolve_base_dir(args.get("base_dir"))
        except ValueError as exc:
            return _result(
                status="error", error=str(exc), code="invalid_base_dir",
                next_action="retry_with_a_real_path_under_user_home",
            )
        rel = str(args.get("relative_path", "")).strip()
        content = str(args.get("content", ""))
        if not rel:
            return _result(status="error", error="empty relative_path", code="invalid_arguments")
        target = (base / rel).resolve()
        if not _is_within(target, base) and not args.get("base_dir"):
            return _result(status="error", error="path escapes safe workspace", code="path_escape")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(content)
        except Exception as exc:
            return _result(status="error", error=str(exc), code="append_failed", path=str(target))
        self._logger.text("tool_append_file_content", content, path=str(target))
        self._remember_path(str(target))
        return _result(status="ok", message="file appended", path=str(target))

    def _t_move_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        src_str = str(args.get("source_path", "")).strip()
        dst_str = str(args.get("destination_path", "")).strip()
        overwrite = bool(args.get("overwrite", False))
        if not src_str or not dst_str:
            return _result(status="error", error="source_path and destination_path are required", code="invalid_arguments")
        try:
            src = Path(src_str).expanduser().resolve()
            dst = Path(dst_str).expanduser().resolve()
        except Exception as exc:
            return _result(status="error", error=f"path resolve failed: {exc}", code="path_resolve")
        if not src.exists():
            return _result(status="error", error=f"source does not exist: {src}", code="not_found", source=str(src))
        if _is_protected_path(src) or _is_protected_path(dst):
            return _result(status="error", error="refusing to move into/from a system directory", code="protected_path")
        if not dst.parent.exists():
            return _result(
                status="error",
                error=f"destination parent does not exist: {dst.parent}",
                code="invalid_destination",
                next_action="create_folder_first_then_retry",
            )
        if dst.exists() and not overwrite:
            return _result(
                status="needs_confirmation",
                error=f"destination already exists at {dst}; pass overwrite=true to replace",
                code="exists",
                destination=str(dst),
            )
        try:
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            shutil.move(str(src), str(dst))
        except Exception as exc:
            return _result(status="error", error=str(exc), code="move_failed", source=str(src), destination=str(dst))
        self._remember_path(str(dst))
        self._logger.event("tool_move_file_ok", source=str(src), destination=str(dst))
        return _result(status="ok", message=f"moved to {dst}", source=str(src), destination=str(dst))

    def _t_rename_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path_str = str(args.get("path", "")).strip()
        new_name = str(args.get("new_name", "")).strip()
        if not path_str or not new_name:
            return _result(status="error", error="path and new_name are required", code="invalid_arguments")
        if "/" in new_name or "\\" in new_name:
            return _result(status="error", error="new_name must be a basename, not a path", code="invalid_arguments")
        try:
            src = Path(path_str).expanduser().resolve()
        except Exception as exc:
            return _result(status="error", error=f"path resolve failed: {exc}", code="path_resolve")
        if not src.exists():
            return _result(status="error", error=f"path does not exist: {src}", code="not_found")
        if _is_protected_path(src):
            return _result(status="error", error="refusing to rename a system path", code="protected_path")
        dst = src.parent / new_name
        if dst.exists():
            return _result(
                status="needs_confirmation",
                error=f"a file named {new_name} already exists in {src.parent}",
                code="exists",
                destination=str(dst),
            )
        try:
            src.rename(dst)
        except Exception as exc:
            return _result(status="error", error=str(exc), code="rename_failed")
        self._remember_path(str(dst))
        self._logger.event("tool_rename_file_ok", source=str(src), destination=str(dst))
        return _result(status="ok", message=f"renamed to {dst}", source=str(src), destination=str(dst))

    def _t_delete_file(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path_str = str(args.get("path", "")).strip()
        confirmed = bool(args.get("confirmed", False))
        if not path_str:
            return _result(status="error", error="path is required", code="invalid_arguments")
        try:
            target = Path(path_str).expanduser().resolve()
        except Exception as exc:
            return _result(status="error", error=f"path resolve failed: {exc}", code="path_resolve")
        if not target.exists():
            return _result(status="ok", message=f"already absent: {target}", path=str(target))
        if _is_protected_path(target):
            return _result(status="error", error=f"refusing to delete protected path: {target}", code="protected_path")
        if not confirmed:
            return _result(
                status="needs_confirmation",
                error="ask the user for explicit permission, then call again with confirmed=true",
                code="needs_explicit_confirm",
                path=str(target),
                next_action="ask_user_then_retry_with_confirmed_true",
            )
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except Exception as exc:
            return _result(status="error", error=str(exc), code="delete_failed", path=str(target))
        self._logger.event("tool_delete_file_ok", path=str(target))
        return _result(status="ok", message=f"deleted {target}", path=str(target))

    def _t_list_recent_paths(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return _result(
            status="ok",
            paths=list(self._recent_paths),
            count=len(self._recent_paths),
            note=(
                "Paths the agent created or wrote in this session, "
                "most-recent first. If you've lost track of where a "
                "user-created file is, the latest entry is almost "
                "certainly it."
            ),
        )

    def _t_run_existing_action(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if self._external_router is None:
            return _result(
                status="not_implemented_yet",
                error="no external action router wired up",
                code="no_router",
            )
        action_name = str(args.get("action_name", ""))
        params = args.get("parameters") or {}
        try:
            return self._external_router(action_name, dict(params))
        except Exception as exc:
            self._logger.exception("external_router_failed", exc, action=action_name)
            return _result(status="error", error=str(exc), code="router_exception")

    def _t_run_python_script(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path_str = str(args.get("script_path", "")).strip()
        if not path_str:
            return _result(status="error", error="empty script_path", code="invalid_arguments")
        script_path = Path(path_str).expanduser().resolve()
        if not script_path.exists():
            return _result(
                status="error", error=f"script not found: {script_path}",
                code="not_found", path=str(script_path),
            )
        if script_path.suffix.lower() != ".py":
            return _result(
                status="error",
                error=f"only .py files supported, got {script_path.suffix}",
                code="unsupported_extension",
                path=str(script_path),
            )
        wait = bool(args.get("wait_for_exit", False))
        timeout = float(args.get("timeout_sec", 15.0) or 15.0)
        from ..utils.subprocess_utils import hidden_subprocess_kwargs
        python_exe = _resolve_python_executable()
        if not python_exe:
            return _result(
                status="error",
                error=(
                    "no python interpreter found. Install Python and "
                    "ensure 'python' is on PATH, or set the "
                    "TOUCHLESS_PYTHON env var to its absolute path."
                ),
                code="no_python",
                path=str(script_path),
            )
        cmd = [python_exe, str(script_path)]
        try:
            if wait:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(1.0, timeout),
                    cwd=str(script_path.parent),
                    **hidden_subprocess_kwargs(),
                )
                return _result(
                    status="ok" if proc.returncode == 0 else "script_error",
                    returncode=proc.returncode,
                    stdout=(proc.stdout or "")[:4000],
                    stderr=(proc.stderr or "")[:2000],
                    python=python_exe,
                    path=str(script_path),
                )
            else:
                # Detached — for GUI scripts (tkinter / pygame / pyside).
                popen = subprocess.Popen(
                    cmd,
                    cwd=str(script_path.parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **hidden_subprocess_kwargs(),
                )
                return _result(
                    status="ok",
                    message="script launched (detached)",
                    pid=popen.pid,
                    python=python_exe,
                    path=str(script_path),
                )
        except subprocess.TimeoutExpired:
            return _result(
                status="error",
                error=f"script timed out after {timeout}s",
                code="timeout",
                path=str(script_path),
            )
        except Exception as exc:
            return _result(
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                code="run_failed",
                path=str(script_path),
            )

    def _t_skip_youtube_ad(self, args: Dict[str, Any]) -> Dict[str, Any]:
        controller = self._ensure_youtube()
        if controller is None:
            return _result(status="not_implemented_yet", error="youtube controller unavailable")
        try:
            ok = controller.skip_ad()
        except Exception as exc:
            self._logger.exception("skip_ad_exception", exc)
            return _result(status="error", error=str(exc), code="exception")
        return _result(
            status="ok" if ok else "no_skippable_ad",
            message=getattr(controller, "_message", ""),
        )

    def _t_ask_user_confirmation(self, args: Dict[str, Any]) -> Dict[str, Any]:
        message = str(args.get("message", "Continue?"))
        risk = str(args.get("risk_level", "medium"))
        if self._confirm is None:
            return _result(status="needs_confirmation", approved=False, error="no confirm UI wired up")
        try:
            approved = bool(self._confirm(message, risk))
        except Exception as exc:
            self._logger.exception("confirm_callback_failed", exc)
            return _result(status="error", error=str(exc), code="confirm_exception")
        return _result(status="ok", approved=approved, risk_level=risk)

    # ---- helpers ----

    def _remember_path(self, path: str) -> None:
        """Push to the most-recent-first list, dropping duplicates and capping at 20."""
        s = str(path)
        try:
            self._recent_paths = [s] + [p for p in self._recent_paths if p != s]
            self._recent_paths = self._recent_paths[:20]
        except Exception:
            pass

    def _resolve_base_dir(self, base_dir: Optional[str]) -> Path:
        """Resolve a model-supplied base_dir to an absolute Path.

        Returns the safe workspace when nothing was provided. Otherwise
        validates that the path's PARENT exists so we don't silently
        create weird top-level dirs (e.g. the model passed
        "C:\\Documents\\Demo" — Documents at the C:\\ root doesn't
        normally exist, and parents=True would silently invent it).
        Raises ValueError if validation fails so the caller surfaces a
        corrective error to the model.
        """
        if not base_dir:
            return self._safe_workspace.resolve()

        p = Path(str(base_dir)).expanduser()
        try:
            resolved = p.resolve()
        except Exception as exc:
            raise ValueError(f"could not resolve path: {exc}")

        # If the directory already exists, we're done — no validation needed.
        if resolved.exists():
            return resolved

        # New directory: only allow creation if the PARENT already
        # exists. This catches cases like "C:\Documents\Demo" where
        # Documents isn't at the root — the model probably meant
        # "~/Documents/Demo" and we should reject so the model
        # corrects on the next hop.
        parent = resolved.parent
        if not parent.exists():
            raise ValueError(
                f"refusing to create '{resolved}' because the parent "
                f"'{parent}' does not exist. Did you mean a path under "
                f"~/Documents (which expands to "
                f"{Path.home() / 'Documents'})?"
            )
        try:
            resolved.mkdir(exist_ok=True)
        except Exception as exc:
            raise ValueError(f"mkdir failed: {exc}")
        return resolved

    def _ensure_mouse(self) -> Optional[MouseController]:
        if self._mouse is None:
            try:
                self._mouse = MouseController()
            except Exception as exc:
                self._logger.exception("mouse_init_failed", exc)
                self._mouse = None
        return self._mouse

    def _ensure_text(self) -> Optional[TextInputController]:
        if self._text is None:
            try:
                self._text = TextInputController()
            except Exception as exc:
                self._logger.exception("text_input_init_failed", exc)
                self._text = None
        return self._text

    def _ensure_youtube(self):
        if self._youtube is None:
            try:
                from ..debug.youtube_controller import YouTubeController
                self._youtube = YouTubeController()
            except Exception as exc:
                self._logger.exception("youtube_init_failed", exc)
                self._youtube = None
        return self._youtube

    def _ensure_chrome(self):
        if self._chrome is None:
            try:
                from ..debug.chrome_controller import ChromeController
                self._chrome = ChromeController()
            except Exception as exc:
                self._logger.exception("chrome_init_failed", exc)
                self._chrome = None
        return self._chrome


def _result(**fields: Any) -> Dict[str, Any]:
    return dict(fields)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_protected_path(path: Path) -> bool:
    """True if `path` falls under a system / install directory we should
    never touch via move/delete/rename tools.

    Errs on the side of caution: anything under C:\\Windows,
    Program Files, Program Files (x86), ProgramData, the system root,
    or directly at a drive root is protected.
    """
    try:
        s = str(path).lower()
    except Exception:
        return True
    protected_roots = []
    for env_var in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemRoot"):
        v = os.environ.get(env_var, "")
        if v:
            protected_roots.append(v.lower())
    # Drive roots themselves (e.g. "c:\\") — never let move/delete touch.
    try:
        if str(path) == str(path.anchor):
            return True
    except Exception:
        pass
    for root in protected_roots:
        if not root:
            continue
        # Match if path equals or sits under one of the protected roots.
        if s == root or s.startswith(root + os.sep) or s.startswith(root + "/"):
            return True
    return False


# Friendly-name → list of candidates to try in order. The first one that
# succeeds via ShellExecuteW wins. Order matters: registry names first
# (cheap), explicit install paths last (covers user-installs not in
# HKLM App Paths).
_APP_SYNONYMS: Dict[str, list[str]] = {
    "vs code": ["code", "code.cmd"],
    "vscode": ["code", "code.cmd"],
    "visual studio code": ["code", "code.cmd"],
    "vs": ["code", "code.cmd"],
    "google chrome": ["chrome"],
    "chrome": ["chrome"],
    "spotify": ["spotify"],
    "notepad": ["notepad"],
    "explorer": ["explorer"],
    "file explorer": ["explorer"],
    "files": ["explorer"],
    "calculator": ["calc"],
    "terminal": ["wt", "powershell", "cmd"],
    "powershell": ["powershell"],
    "command prompt": ["cmd"],
    "edge": ["msedge"],
    "microsoft edge": ["msedge"],
}


def _open_app_candidates(name: str) -> list[str]:
    """Build the ordered list of launch targets for a friendly app name."""
    seen: list[str] = []

    def _add(candidate: str) -> None:
        candidate = (candidate or "").strip()
        if candidate and candidate not in seen:
            seen.append(candidate)

    # Always try the literal name first — it might be a real registry
    # key or a path the model resolved correctly.
    _add(name)

    lookup_key = name.strip().lower()
    for synonym in _APP_SYNONYMS.get(lookup_key, ()):
        _add(synonym)

    # VS Code user-install fallback paths (per-user installer skips HKLM).
    if lookup_key in {"vs code", "vscode", "visual studio code", "vs", "code"}:
        local_app = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        if local_app:
            _add(str(Path(local_app) / "Programs" / "Microsoft VS Code" / "Code.exe"))
        _add(str(Path(program_files) / "Microsoft VS Code" / "Code.exe"))

    return seen


def _resolve_python_executable() -> Optional[str]:
    """Locate a Python interpreter we can use to run user scripts.

    Resolution order:
      1. TOUCHLESS_PYTHON env var (escape hatch for unusual setups).
      2. sys.executable — but ONLY when not running as a PyInstaller
         frozen binary, because in frozen mode sys.executable is
         Touchless.exe itself, which obviously can't run a .py file.
      3. shutil.which("python") / "python3" / "py" — first match.
      4. Common Windows install locations as a last-resort fallback.

    Returns the absolute path string, or None if nothing is found.
    """
    env_override = os.environ.get("TOUCHLESS_PYTHON", "").strip()
    if env_override and Path(env_override).exists():
        return env_override

    if not getattr(sys, "frozen", False):
        # Source mode — sys.executable is the user's actual python.
        # Prefer it because it's the same env they ran the app from
        # (so deps like tkinter, pygame, etc. are guaranteed there).
        if sys.executable and Path(sys.executable).exists():
            return sys.executable

    for candidate in ("python", "python3", "py"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    # Common Windows fallbacks for installed-mode users who don't have
    # python on PATH but do have it installed in the standard locations.
    local_app = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    fallback_globs = []
    if local_app:
        fallback_globs.append(Path(local_app) / "Programs" / "Python")
    fallback_globs.append(Path(program_files) / "Python313")
    fallback_globs.append(Path(program_files) / "Python312")
    fallback_globs.append(Path(program_files) / "Python311")
    for base in fallback_globs:
        if not base.exists():
            continue
        # Direct python.exe in the dir, OR nested Python3xx/python.exe.
        direct = base / "python.exe"
        if direct.exists():
            return str(direct)
        for child in base.glob("Python3*/python.exe"):
            return str(child)
    return None


def _summarize_args(name: str, args: Dict[str, Any], *, debug: bool) -> Dict[str, Any]:
    """Produce a privacy-friendly summary of args for the log."""
    if debug:
        return args
    out: Dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 64 and key in {"text", "content"}:
            out[key] = {"length": len(value), "preview": value[:64]}
        else:
            out[key] = value
    return out
