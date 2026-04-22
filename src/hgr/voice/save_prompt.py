from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..debug.desktop_controller import DesktopController


_DEFAULT_HINTS = ("auto", "default", "same place", "same folder", "normal place", "usual place", "ignore")
_DISCARD_HINTS = ("cancel", "delete", "discard", "nevermind", "never mind", "don't save", "do not save")
_SAVE_PREFIXES = (
    r"^\s*(?:save|store|put|keep)\s+(?:it|this|that|the file)?\s*(?:to|in|under|inside)?\s*",
    r"^\s*(?:to|in|under|inside)\s+",
)
_SAVE_SUFFIXES = (
    r"\s+please\s*$",
    r"\s+folder\s*$",
    r"\s+directory\s*$",
)

_WORD_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10",
}


@dataclass(frozen=True)
class SavePromptDecision:
    action: str
    folder: Path | None = None
    custom_name: str | None = None
    heard_text: str = ""
    reason: str = ""


class SavePromptProcessor:
    def __init__(self, desktop_controller: DesktopController | None = None) -> None:
        self.desktop_controller = desktop_controller or DesktopController(outlook_paths=())

    def parse(self, heard_text: str, *, success: bool = True) -> SavePromptDecision:
        raw_text = str(heard_text or "").strip()
        cleaned = self._normalize(raw_text)
        if not success or not cleaned:
            return SavePromptDecision(action="default", heard_text=raw_text, reason="silence")
        if any(token in cleaned for token in _DISCARD_HINTS):
            return SavePromptDecision(action="discard", heard_text=raw_text, reason="discard")
        if cleaned in _DEFAULT_HINTS or any(token in cleaned for token in (" default", " auto", "default ", "auto ", " ignore", "ignore ")):
            return SavePromptDecision(action="default", heard_text=raw_text, reason="default")

        # Check for "location as name" syntax: "Documents folder as testing recording one"
        folder_part, name_part = self._split_location_as_name(cleaned)
        if name_part:
            folder_query = self._strip_save_language(folder_part)
            if not folder_query:
                return SavePromptDecision(action="default", heard_text=raw_text, reason="blank")
            explicit_path = self._explicit_path(folder_query)
            if explicit_path is not None and explicit_path.exists() and explicit_path.is_dir():
                return SavePromptDecision(action="move_rename", folder=explicit_path, custom_name=name_part, heard_text=raw_text, reason="path+name")
            resolved, ambiguous = self.desktop_controller.resolve_named_folder(folder_query)
            if resolved is not None and not ambiguous:
                return SavePromptDecision(action="move_rename", folder=resolved, custom_name=name_part, heard_text=raw_text, reason="resolved+name")
            return SavePromptDecision(action="default", heard_text=raw_text, reason="unresolved+name")

        folder_query = self._strip_save_language(cleaned)
        if not folder_query:
            return SavePromptDecision(action="default", heard_text=raw_text, reason="blank")

        explicit_path = self._explicit_path(folder_query)
        if explicit_path is not None and explicit_path.exists() and explicit_path.is_dir():
            return SavePromptDecision(action="move", folder=explicit_path, heard_text=raw_text, reason="path")

        resolved, ambiguous = self.desktop_controller.resolve_named_folder(folder_query)
        if ambiguous:
            return SavePromptDecision(action="default", heard_text=raw_text, reason="ambiguous")
        if resolved is not None:
            return SavePromptDecision(action="move", folder=resolved, heard_text=raw_text, reason="resolved")
        return SavePromptDecision(action="default", heard_text=raw_text, reason="unresolved")

    def _split_location_as_name(self, text: str) -> tuple[str, str]:
        """Split 'X as Y' into (X, normalized_Y). Returns ('', '') if no 'as' separator found."""
        match = re.search(r"\s+as\s+(.+)$", text, flags=re.IGNORECASE)
        if not match:
            return "", ""
        name_raw = match.group(1).strip()
        folder_part = text[:match.start()].strip()
        if not folder_part or not name_raw:
            return "", ""
        normalized_name = self._normalize_custom_name(name_raw)
        if not normalized_name:
            return "", ""
        return folder_part, normalized_name

    def _normalize_custom_name(self, text: str) -> str:
        """Convert spoken name to a safe filename: words→digits, spaces→underscores, title-case."""
        words = str(text or "").strip().lower().split()
        converted = [_WORD_TO_DIGIT.get(w, w) for w in words]
        name = "_".join(converted)
        name = re.sub(r"[^\w\-]", "_", name)
        name = re.sub(r"_+", "_", name).strip("_")
        return name

    def _normalize(self, text: str) -> str:
        value = " ".join(str(text or "").replace("\n", " ").split()).strip().lower()
        value = value.replace("my pc's", "my pc")
        value = value.replace("one drive", "onedrive")
        return value

    def _strip_save_language(self, text: str) -> str:
        value = str(text or "")
        for pattern in _SAVE_PREFIXES:
            value = re.sub(pattern, "", value, flags=re.IGNORECASE)
        for pattern in _SAVE_SUFFIXES:
            value = re.sub(pattern, "", value, flags=re.IGNORECASE)
        value = re.sub(r"^\s*(?:my)\s+", "", value, flags=re.IGNORECASE)
        return " ".join(value.split()).strip(" .!?")

    def _explicit_path(self, text: str) -> Path | None:
        value = str(text or "").strip().strip('"')
        if not value:
            return None
        if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
            return Path(value).expanduser()
        return None
