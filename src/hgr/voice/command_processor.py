from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..config.app_config import CONFIG_DIR
from ..debug.chrome_controller import ChromeController
from ..debug.desktop_controller import DesktopAppEntry, DesktopController
from ..debug.spotify_controller import SpotifyController


COMMON_FILLERS = (
    "please",
    "for me",
    "right now",
    "thanks",
    "thank you",
    "can you",
    "could you",
    "would you",
    "will you",
    "hey hgr",
    "hey app",
    "just",
    "uh",
    "um",
    "and",
)

COMMAND_CORRECTIONS = (
    ("google chrome", "chrome"),
    ("chrome browser", "chrome"),
    ("windows explorer", "file explorer"),
    ("file explore", "file explorer"),
    ("files explorer", "file explorer"),
    ("blu tooth", "bluetooth"),
    ("wi fi", "wifi"),
    ("e mail", "email"),
    ("chat gpt", "chatgpt"),
    ("visual stdios", "visual studio"),
    ("visual studios", "visual studio"),
    ("searchup", "search up"),
    ("a c dc", "ac/dc"),
    ("ac dc", "ac/dc"),
    ("v s code", "vs code"),
    ("vs code", "visual studio code"),
    ("discord app", "discord"),
    ("steam app", "steam"),
    ("you tube", "youtube"),
    ("key card", "kicad"),
    ("key cards", "kicad"),
    ("key cad", "kicad"),
    ("ki cad", "kicad"),
    ("k i cad", "kicad"),
    ("google browser", "chrome"),
    ("chrome browser", "chrome"),
    ("near by", "nearby"),
    ("clothes", "close"),
    ("cloths", "close"),
    ("flows", "close"),
    ("close d ", "close "),
    ("and close", "close"),
)

APP_ALIASES: dict[str, tuple[str, ...]] = {
    "spotify": ("spotify",),
    "chrome": ("google chrome", "chrome browser", "chrome", "browser"),
    "settings": ("settings", "device settings", "windows settings", "system settings"),
    "file_explorer": ("file explorer", "explorer"),
    "outlook": ("outlook", "mail app"),
}

SPOTIFY_PLAY_PHRASES = ("play", "put on", "listen to", "queue", "queue up", "start playing")
CHROME_SEARCH_PHRASES = ("search up", "search for", "search", "look up", "look for", "find", "go to", "navigate to", "take me to")
GENERIC_OPEN_PHRASES = ("open", "launch", "start", "bring up", "show")
APP_FOCUS_PHRASES = GENERIC_OPEN_PHRASES + ("switch to", "focus", "focus on", "pull up", "open up", "show me")
APP_LAUNCH_PHRASES = APP_FOCUS_PHRASES + (
    "run",
    "start up",
    "boot up",
    "fire up",
    "load",
    "use",
    "go into",
    "enter",
    "i need",
    "need",
)
DEDICATED_APP_LAUNCH_PHRASES = tuple(phrase for phrase in APP_LAUNCH_PHRASES if phrase != "focus")
CLOSE_WINDOW_PHRASES = ("close", "close the", "exit", "quit", "dismiss")
APP_OBJECT_HINTS = (
    "app called",
    "application called",
    "program called",
    "app named",
    "application named",
    "program named",
    "app",
    "application",
    "program",
    "window",
    "called",
    "named",
    "titled",
)
SPOTIFY_CONTEXT_PHRASES = ("on spotify", "in spotify", "from spotify", "using spotify")
CHROME_CONTEXT_PHRASES = ("on chrome", "in chrome", "using chrome", "in the browser", "on the browser")
WEB_FALLBACK_PREFIXES = (
    ("search", "search up"),
    ("search", "search for"),
    ("search", "search"),
    ("search", "look up"),
    ("search", "look for"),
    ("search", "find"),
    ("open", "go to"),
    ("open", "navigate to"),
    ("open", "take me to"),
    ("open", "open"),
)
WEB_TARGET_HINTS = {"youtube", "github", "gmail", "chatgpt", "reddit", "wikipedia", "maps", "docs", "drive", "calendar"}
MUSIC_NOUNS = ("music", "song", "songs", "track", "tracks", "playlist", "playlists", "album", "albums", "artist")
EDGE_NOISE_WORDS = (
    "a",
    "an",
    "app",
    "application",
    "called",
    "for",
    "from",
    "in",
    "inside",
    "my",
    "me",
    "named",
    "on",
    "program",
    "the",
    "to",
    "titled",
    "up",
    "window",
    "with",
)

SETTINGS_TOPICS = {
    "apps": ("apps", "applications", "installed apps", "programs"),
    "bluetooth": ("bluetooth",),
    "camera": ("camera", "webcam"),
    "display": ("display", "screen", "monitor"),
    "email": ("email", "mail", "accounts"),
    "network": ("network",),
    "privacy": ("privacy", "permissions"),
    "sound": ("sound", "audio"),
    "storage": ("storage", "disk", "drive"),
    "update": ("update", "updates", "windows update"),
    "volume": ("volume",),
    "wifi": ("wifi", "wi-fi", "wireless"),
}

KNOWN_FOLDERS = {
    "desktop": ("desktop",),
    "documents": ("documents", "document"),
    "downloads": ("downloads", "download"),
    "music": ("music",),
    "pictures": ("pictures", "photos"),
    "videos": ("videos", "video"),
}

FILE_REQUEST_HINTS = {
    "assignment",
    "budget",
    "contract",
    "csv",
    "doc",
    "docx",
    "document",
    "draft",
    "essay",
    "excel",
    "homework",
    "invoice",
    "json",
    "note",
    "notes",
    "paper",
    "pdf",
    "powerpoint",
    "presentation",
    "project",
    "proposal",
    "report",
    "resume",
    "sheet",
    "slides",
    "spreadsheet",
    "summary",
    "text",
    "thesis",
    "txt",
    "word",
    "xlsx",
}


@dataclass
class VoiceCommandContext:
    preferred_app: str | None = None


@dataclass
class ParsedVoiceCommand:
    raw_text: str
    normalized_text: str
    app_name: str
    action: str
    confidence: float
    query: str | None = None
    matched_alias: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class VoiceExecutionResult:
    success: bool
    target: str
    heard_text: str
    control_text: str
    info_text: str
    intent: ParsedVoiceCommand | None = None
    display_text: str | None = None


class VoiceProfileStore:
    _volatile_cache: dict[str, dict[str, Any]] = {}

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path or (CONFIG_DIR / "voice_profile.json")
        self._lock = threading.Lock()

    def best_match(self, utterance: str) -> dict[str, Any] | None:
        normalized = self._normalize(utterance)
        if not normalized:
            return None
        best_entry: dict[str, Any] | None = None
        best_score = 0.0
        for entry in self._read_history():
            previous = self._normalize(str(entry.get("utterance", "")))
            if not previous:
                continue
            ratio = SequenceMatcher(None, normalized, previous).ratio()
            overlap = self._token_overlap(normalized, previous)
            score = ratio + overlap * 0.20 + min(int(entry.get("count", 1)), 8) * 0.02
            if score > best_score:
                best_score = score
                best_entry = dict(entry)
                best_entry["score"] = score
        if best_entry is None or best_score < 0.92:
            return None
        return best_entry

    def record_success(self, intent: ParsedVoiceCommand) -> None:
        self._upsert_history(intent=intent, corrected=False)

    def record_correction(self, *, utterance: str, app_name: str, action: str, query: str | None = None) -> None:
        intent = ParsedVoiceCommand(
            raw_text=utterance,
            normalized_text=self._normalize(utterance),
            app_name=app_name,
            action=action,
            confidence=1.0,
            query=query,
        )
        self._upsert_history(intent=intent, corrected=True)

    def history_entries(self) -> list[dict[str, Any]]:
        return list(self._read_history())

    def _upsert_history(self, *, intent: ParsedVoiceCommand, corrected: bool) -> None:
        normalized = self._normalize(intent.raw_text)
        if not normalized:
            return
        with self._lock:
            data = self._load_data()
            history = data.setdefault("history", [])
            now = int(time.time())
            for entry in history:
                if (
                    self._normalize(str(entry.get("utterance", ""))) == normalized
                    and entry.get("app_name") == intent.app_name
                    and entry.get("action") == intent.action
                    and (entry.get("query") or None) == intent.query
                ):
                    entry["count"] = int(entry.get("count", 0)) + 1
                    entry["updated_at"] = now
                    entry["corrected"] = bool(entry.get("corrected")) or corrected
                    break
            else:
                history.append(
                    {
                        "utterance": normalized,
                        "app_name": intent.app_name,
                        "action": intent.action,
                        "query": intent.query,
                        "count": 1,
                        "corrected": corrected,
                        "updated_at": now,
                    }
                )
            history.sort(key=lambda item: (int(item.get("count", 0)), int(item.get("updated_at", 0))), reverse=True)
            data["history"] = history[:64]
            self._save_data(data)

    def _read_history(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load_data()
        history = data.get("history")
        return history if isinstance(history, list) else []

    def _load_data(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            cached = self._volatile_cache.get(str(self._path))
            return dict(cached) if isinstance(cached, dict) else {"history": []}

    def _save_data(self, data: dict[str, Any]) -> None:
        self._volatile_cache[str(self._path)] = json.loads(json.dumps(data))
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _normalize(self, text: str) -> str:
        return " ".join(str(text or "").lower().split()).strip()

    def _token_overlap(self, left: str, right: str) -> float:
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        shared = len(left_tokens & right_tokens)
        return shared / max(len(left_tokens), len(right_tokens))


class VoiceCommandProcessor:
    def __init__(
        self,
        *,
        chrome_controller: ChromeController | None = None,
        spotify_controller: SpotifyController | None = None,
        desktop_controller: DesktopController | None = None,
        profile_store: VoiceProfileStore | None = None,
    ) -> None:
        self.chrome_controller = chrome_controller or ChromeController()
        self.spotify_controller = spotify_controller or SpotifyController()
        self.desktop_controller = desktop_controller or DesktopController()
        self.profile_store = profile_store or VoiceProfileStore()
        self._pending_selection: dict[str, Any] | None = None

    def parse(self, spoken_text: str, *, context: VoiceCommandContext | None = None) -> ParsedVoiceCommand | None:
        normalized = self._normalize_text(spoken_text)
        if not normalized:
            return None

        candidates = [
            self._parse_spotify(normalized, raw_text=spoken_text, context=context),
            self._parse_chrome(normalized, raw_text=spoken_text, context=context),
            self._parse_settings(normalized, raw_text=spoken_text, context=context),
            self._parse_close_window(normalized, raw_text=spoken_text, context=context),
            self._parse_file_explorer(normalized, raw_text=spoken_text, context=context),
            self._parse_outlook(normalized, raw_text=spoken_text, context=context),
        ]
        intents = [intent for intent in candidates if intent is not None]
        early = self._best_intent(intents)
        if early is not None and self._can_skip_catalog_lookup(early):
            return early if early.confidence >= 0.56 else None

        catalog_matches = self.desktop_controller.rank_applications_in_text(normalized)
        candidates.extend(
            [
                self._parse_catalog_open(normalized, raw_text=spoken_text, catalog_matches=catalog_matches),
                self._parse_generic_open(normalized, raw_text=spoken_text),
            ]
        )
        intents = [intent for intent in candidates if intent is not None]
        learned = self.profile_store.best_match(normalized)
        if learned is not None:
            for intent in intents:
                if intent.app_name == learned.get("app_name"):
                    intent.confidence += 0.08
                if intent.action == learned.get("action"):
                    intent.confidence += 0.04
        if not intents:
            return None
        for intent in intents:
            self._apply_catalog_bias(intent, catalog_matches, context=context)
            intent.confidence += self._intent_template_bonus(normalized, intent)
        best = self._best_intent(intents)
        if best is None:
            return None
        return best if best.confidence >= 0.56 else None

    def execute(self, spoken_text: str, *, context: VoiceCommandContext | None = None) -> VoiceExecutionResult:
        pending_result = self._execute_pending_selection(spoken_text)
        if pending_result is not None:
            return pending_result

        intent = self.parse(spoken_text, context=context)
        if intent is None:
            return VoiceExecutionResult(
                success=False,
                target="voice",
                heard_text=spoken_text,
                control_text="voice command not understood",
                info_text="-",
                intent=None,
                display_text=self._normalize_text(spoken_text) or spoken_text,
            )

        result = self._execute_intent(intent)
        if result.success:
            self.profile_store.record_success(intent)
        if result.display_text is None:
            result.display_text = self._display_text_for_intent(intent)
        return result

    def _extract_selection_number(self, spoken_text: str) -> int | None:
        normalized = self._normalize_text(spoken_text)
        if not normalized:
            return None
        _CANCEL_EXACT = {
            "cancel", "nevermind", "never mind", "stop", "go back",
            "close list", "wrong files", "wrong file", "wrong folders", "wrong folder",
            "wrong apps", "wrong app", "not these", "none of these", "cancel search",
            "no", "nope", "abort", "exit", "quit", "close", "dismiss", "hide",
            "not that", "forget it", "scratch that", "skip", "leave it", "exit list",
        }
        if normalized in _CANCEL_EXACT:
            return -1
        _CANCEL_PATTERNS = (
            r"\bcancel\b", r"\bnevermind\b", r"\bnever mind\b", r"\bgo back\b",
            r"\bforget it\b", r"\bscratch that\b", r"\bnone of (?:these|those|them)\b",
            r"\bclose (?:the )?list\b", r"\bnot (?:this|that|these|those)\b",
        )
        for pat in _CANCEL_PATTERNS:
            if re.search(pat, normalized):
                return -1
        word_map = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
        prefix = (
            r"(?:(?:can\s+(?:you|i)\s+)?(?:open|show(?:\s+me)?|launch|pull\s+up|choose|select|see|"
            r"let(?:(?:'|\s)?s)\s+(?:see|open|look\s+at|check)|i(?:'|\s)?ll\s+take)"
            r")?(?:\s+(?:file|folder|item|number|option))?"
        )
        match = re.search(rf"\b{prefix}\s*(\d{{1,2}})\b", normalized)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None
        word_match = re.search(rf"\b{prefix}\s*(one|two|three|four|five|six|seven|eight)\b", normalized)
        if word_match:
            return word_map.get(word_match.group(1))
        return None

    def _create_pending_selection(self, *, kind: str, query: str, heard_text: str, options: list[dict[str, Any]]) -> str:
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for option in options:
            label = str(option.get("label", "") or "").strip()
            path_text = str(option.get("path", option.get("app_name", "")) or "").strip()
            key = f"{label.lower()}|{path_text.lower()}"
            if not label or key in seen:
                continue
            seen.add(key)
            cleaned.append(dict(option))
        self._pending_selection = {
            "kind": kind,
            "query": query,
            "heard_text": heard_text,
            "options": cleaned[:8],
        }
        title = "Which file/folder?" if kind == "file" else "Which app?"
        lines = [title]
        for index, option in enumerate(self._pending_selection["options"], start=1):
            if kind == "file":
                lines.append(f"{index}. {option['label']} — {option.get('path', '')}")
            else:
                lines.append(f"{index}. {option['label']}")
        lines.append("Say the corresponding number.")
        return "\n".join(lines)

    def _execute_pending_selection(self, spoken_text: str) -> VoiceExecutionResult | None:
        pending = self._pending_selection
        if pending is None:
            return None
        choice = self._extract_selection_number(spoken_text)
        if choice is None:
            prompt = self._create_pending_selection(
                kind=str(pending.get("kind", "file")),
                query=str(pending.get("query", "")),
                heard_text=str(pending.get("heard_text", "")),
                options=list(pending.get("options", [])),
            )
            return VoiceExecutionResult(
                success=False,
                target="voice_selection",
                heard_text=spoken_text,
                control_text="awaiting selection",
                info_text=prompt,
                display_text=prompt,
            )
        if choice == -1:
            self._pending_selection = None
            return VoiceExecutionResult(
                success=False,
                target="voice",
                heard_text=spoken_text,
                control_text="selection cancelled",
                info_text="-",
                display_text="selection cancelled",
            )
        options = list(pending.get("options", []))
        if choice < 1 or choice > len(options):
            prompt = self._create_pending_selection(
                kind=str(pending.get("kind", "file")),
                query=str(pending.get("query", "")),
                heard_text=str(pending.get("heard_text", "")),
                options=options,
            )
            return VoiceExecutionResult(
                success=False,
                target="voice_selection",
                heard_text=spoken_text,
                control_text="that number was not listed",
                info_text=prompt,
                display_text=prompt,
            )
        selected = options[choice - 1]
        self._pending_selection = None
        if pending.get("kind") == "app":
            success = self.desktop_controller.open_named_application(str(selected.get("app_name", selected.get("label", ""))))
            return VoiceExecutionResult(
                success=success,
                target="system",
                heard_text=spoken_text,
                control_text=self.desktop_controller.message,
                info_text=self.desktop_controller.message,
                display_text=str(selected.get("label", "")),
            )
        success = self.desktop_controller.open_resolved_path(Path(str(selected.get("path", ""))))
        return VoiceExecutionResult(
            success=success,
            target="file_explorer",
            heard_text=spoken_text,
            control_text=self.desktop_controller.message,
            info_text=self.desktop_controller.message,
            display_text=str(selected.get("label", "")),
        )

    def export_training_bundle(self, *, output_dir: Path | None = None) -> dict[str, Path]:
        from .training_data import VoiceCommandDatasetBuilder

        builder = VoiceCommandDatasetBuilder(
            desktop_controller=self.desktop_controller,
            profile_store=self.profile_store,
        )
        return builder.export_bundle(output_dir=output_dir)

    def _execute_intent(self, intent: ParsedVoiceCommand) -> VoiceExecutionResult:
        if intent.app_name == "spotify":
            result = self._execute_spotify(intent)
        elif intent.app_name == "chrome":
            result = self._execute_chrome(intent)
        elif intent.app_name == "settings":
            result = self._execute_settings(intent)
        elif intent.app_name == "file_explorer":
            result = self._execute_file_explorer(intent)
        elif intent.app_name == "outlook":
            result = self._execute_outlook(intent)
        else:
            result = self._execute_generic(intent)
        result.intent = intent
        return result

    def _best_intent(self, intents: list[ParsedVoiceCommand]) -> ParsedVoiceCommand | None:
        if not intents:
            return None
        return max(intents, key=lambda item: item.confidence)

    def _can_skip_catalog_lookup(self, intent: ParsedVoiceCommand) -> bool:
        if intent.app_name == "file_explorer" and bool(intent.query):
            return True
        if intent.app_name == "settings":
            return True
        if intent.app_name == "outlook" and intent.action in {"open_folder", "compose"}:
            return True
        if intent.app_name in {"chrome", "spotify"} and intent.matched_alias is not None:
            return True
        if intent.app_name == "chrome" and intent.confidence >= 0.88:
            return True
        if intent.app_name == "spotify" and intent.confidence >= 0.88:
            return True
        return intent.confidence >= 0.98 and intent.app_name != "system"

    def _display_text_for_intent(self, intent: ParsedVoiceCommand) -> str:
        query = str(intent.query or "").strip()
        if intent.app_name == "spotify":
            if intent.action == "play" and query:
                return f"play {query} on spotify"
            if intent.action == "open":
                return "open spotify"
            if intent.action == "next":
                return "next song on spotify"
            if intent.action == "previous":
                return "previous song on spotify"
            if intent.action == "shuffle":
                return "shuffle on spotify"
            if intent.action == "repeat":
                return "repeat on spotify"
            if intent.action in {"pause", "resume"}:
                return f"{intent.action} spotify"
        elif intent.app_name == "chrome":
            if intent.action in {"open", "search"} and query:
                return f"search {query} on chrome"
            if intent.action == "open":
                return "open chrome"
            if intent.action == "back":
                return "go back in chrome"
            if intent.action == "forward":
                return "go forward in chrome"
            if intent.action == "refresh":
                return "refresh chrome"
            if intent.action == "new_tab":
                return "new tab in chrome"
            if intent.action == "incognito":
                return "new incognito tab in chrome"
        elif intent.app_name == "system":
            resolved = str(intent.slots.get("resolved_app_name") or query or "").strip()
            if intent.action == "open_app" and resolved:
                return f"open {resolved}"
        elif intent.app_name == "file_explorer" and query:
            return f"open {query} in file explorer"
        elif intent.app_name == "settings" and query:
            return f"open {query} settings"
        elif intent.app_name == "outlook" and query:
            return f"open {query} in outlook"
        return intent.normalized_text or intent.raw_text

    def _execute_spotify(self, intent: ParsedVoiceCommand) -> VoiceExecutionResult:
        success = False
        info_text = "-"
        if intent.action == "open":
            if self._is_spotify_resume_phrase(intent.raw_text):
                focused = self.spotify_controller.focus_or_open_window()
                played = self.spotify_controller.play()
                success = focused and played
                details = self.spotify_controller.get_current_track_details() if success else None
                if details is not None:
                    info_text = details.summary()
            else:
                success = self.spotify_controller.focus_or_open_window()
        elif intent.action == "play":
            preferred_types = tuple(intent.slots.get("preferred_types", ()))
            success = self.spotify_controller.play_search_request(intent.query or "", preferred_types=preferred_types)
            details = self.spotify_controller.get_current_track_details() if success else None
            if details is not None:
                info_text = details.summary()
            elif intent.query:
                info_text = f"Spotify request: {intent.query}"
        elif intent.action == "pause":
            success = self.spotify_controller.pause()
        elif intent.action == "resume":
            focused = self.spotify_controller.focus_or_open_window()
            played = self.spotify_controller.play()
            success = focused and played
            details = self.spotify_controller.get_current_track_details() if success else None
            if details is not None:
                info_text = details.summary()
        elif intent.action == "next":
            success = self.spotify_controller.next_track()
        elif intent.action == "previous":
            success = self.spotify_controller.previous_track()
        elif intent.action == "shuffle":
            success = self.spotify_controller.toggle_shuffle()
        elif intent.action == "repeat":
            success = self.spotify_controller.toggle_repeat_track()
        if info_text == "-" and intent.query:
            info_text = f"Spotify request: {intent.query}"
        return VoiceExecutionResult(
            success=success,
            target="spotify",
            heard_text=intent.raw_text,
            control_text=self.spotify_controller.message,
            info_text=info_text,
        )

    def _execute_chrome(self, intent: ParsedVoiceCommand) -> VoiceExecutionResult:
        success = False
        if intent.action == "open" and not intent.query:
            success = self.chrome_controller.focus_or_open_window()
        elif intent.action in {"open", "search"}:
            success = self.chrome_controller.open_or_search(intent.query or "")
        elif intent.action == "back":
            success = self.chrome_controller.navigate_back()
        elif intent.action == "forward":
            success = self.chrome_controller.navigate_forward()
        elif intent.action == "refresh":
            success = self.chrome_controller.refresh_page()
        elif intent.action == "new_tab":
            success = self.chrome_controller.new_tab()
        elif intent.action == "incognito":
            success = self.chrome_controller.new_incognito_tab()
        return VoiceExecutionResult(
            success=success,
            target="chrome",
            heard_text=intent.raw_text,
            control_text=self.chrome_controller.message,
            info_text=f"Chrome target: {intent.query}" if intent.query else self.chrome_controller.message,
        )

    def _execute_settings(self, intent: ParsedVoiceCommand) -> VoiceExecutionResult:
        success = self.desktop_controller.open_settings(intent.query)
        return VoiceExecutionResult(
            success=success,
            target="settings",
            heard_text=intent.raw_text,
            control_text=self.desktop_controller.message,
            info_text=self.desktop_controller.message,
        )

    def _execute_file_explorer(self, intent: ParsedVoiceCommand) -> VoiceExecutionResult:
        if intent.action == "search":
            success = self.desktop_controller.search_file_explorer(intent.query or "")
            return VoiceExecutionResult(
                success=success,
                target="file_explorer",
                heard_text=intent.raw_text,
                control_text=self.desktop_controller.message,
                info_text=self.desktop_controller.message,
            )
        if intent.query:
            success = self.desktop_controller.open_named_file(
                intent.query,
                preferred_root=intent.slots.get("preferred_root"),
                folder_hint=intent.slots.get("folder_hint"),
            )
            message = self.desktop_controller.message
            if success:
                return VoiceExecutionResult(
                    success=True,
                    target="file_explorer",
                    heard_text=intent.raw_text,
                    control_text=message,
                    info_text=message,
                )
            if str(message).lower().startswith("multiple matching files found"):
                resolved, ambiguous = self.desktop_controller.resolve_named_file(
                    intent.query,
                    preferred_root=intent.slots.get("preferred_root"),
                    folder_hint=intent.slots.get("folder_hint"),
                )
                prompt = self._create_pending_selection(
                    kind="file",
                    query=intent.query,
                    heard_text=intent.raw_text,
                    options=[{"path": str(path), "label": path.name} for path in ([resolved] if resolved is not None else []) + list(ambiguous)],
                )
                return VoiceExecutionResult(
                    success=False,
                    target="voice_selection",
                    heard_text=intent.raw_text,
                    control_text="awaiting file selection",
                    info_text=prompt,
                    display_text=prompt,
                )
            return VoiceExecutionResult(
                success=False,
                target="file_explorer",
                heard_text=intent.raw_text,
                control_text=message or f"could not find file: {intent.query}",
                info_text=message,
                display_text=intent.query,
            )
        else:
            success = self.desktop_controller.open_file_explorer(intent.query)
        return VoiceExecutionResult(
            success=success,
            target="file_explorer",
            heard_text=intent.raw_text,
            control_text=self.desktop_controller.message,
            info_text=self.desktop_controller.message,
        )

    def _execute_outlook(self, intent: ParsedVoiceCommand) -> VoiceExecutionResult:
        if intent.action == "compose":
            success = self.desktop_controller.compose_email(
                recipient=intent.slots.get("recipient"),
                subject=intent.slots.get("subject"),
                body=intent.slots.get("body"),
            )
        elif intent.action == "open_folder":
            success = self.desktop_controller.open_outlook_folder(intent.query)
        else:
            success = self.desktop_controller.open_outlook()
        return VoiceExecutionResult(
            success=success,
            target="outlook",
            heard_text=intent.raw_text,
            control_text=self.desktop_controller.message,
            info_text=self.desktop_controller.message,
        )

    def _execute_generic(self, intent: ParsedVoiceCommand) -> VoiceExecutionResult:
        if intent.action == "close_window":
            if intent.query:
                parts = [p.strip() for p in re.split(r"[,;]\s*|\s+and\s+", intent.query) if p.strip()]
                results: list[bool] = []
                messages: list[str] = []
                for part in (parts if parts else [intent.query]):
                    ok = self.desktop_controller.close_named_window(part)
                    results.append(ok)
                    messages.append(self.desktop_controller.message)
                success = any(results)
                msg = "; ".join(m for m in messages if m)
            else:
                success = self.desktop_controller.close_active_window()
                msg = self.desktop_controller.message
            return VoiceExecutionResult(
                success=success,
                target="system",
                heard_text=intent.raw_text,
                control_text=msg,
                info_text=msg,
            )
        best_entry, ambiguous_entries = self.desktop_controller.resolve_named_application_options(intent.query or "")
        if ambiguous_entries:
            prompt = self._create_pending_selection(
                kind="app",
                query=intent.query or "",
                heard_text=intent.raw_text,
                options=[{"app_name": entry.display_name, "label": entry.display_name} for entry in ([best_entry] if best_entry is not None else []) + list(ambiguous_entries)],
            )
            return VoiceExecutionResult(
                success=False,
                target="voice_selection",
                heard_text=intent.raw_text,
                control_text="awaiting app selection",
                info_text=prompt,
                display_text=prompt,
            )
        if best_entry is not None:
            success = self.desktop_controller.open_desktop_entry(best_entry)
        else:
            success = self.desktop_controller.open_named_application(intent.query or "")
        return VoiceExecutionResult(
            success=success,
            target="system",
            heard_text=intent.raw_text,
            control_text=self.desktop_controller.message,
            info_text=self.desktop_controller.message,
        )

    def _parse_close_window(
        self,
        text: str,
        *,
        raw_text: str,
        context: VoiceCommandContext | None,
    ) -> ParsedVoiceCommand | None:
        trimmed = self._strip_common_prefix(text)
        if not self._contains_launch_request(trimmed, CLOSE_WINDOW_PHRASES):
            return None
        query = self._cleanup_query(
            trimmed,
            app_name=None,
            extra_phrases=CLOSE_WINDOW_PHRASES + APP_OBJECT_HINTS + ("window", "app", "application", "program"),
            trim_edge_noise=True,
        )
        query = self._cleanup_app_launch_query(query)
        if query in {"close", "window", "app", "application", "program"}:
            query = None
        confidence = 0.92 if query else 0.88
        return ParsedVoiceCommand(
            raw_text=raw_text,
            normalized_text=text,
            app_name="system",
            action="close_window",
            confidence=confidence,
            query=query,
        )

    def _parse_spotify(
        self,
        text: str,
        *,
        raw_text: str,
        context: VoiceCommandContext | None,
    ) -> ParsedVoiceCommand | None:
        matched_alias = self._matched_alias(text, "spotify")
        spotify_context = (
            matched_alias is not None
            or self._contains_any(text, SPOTIFY_CONTEXT_PHRASES)
            or (context is not None and context.preferred_app == "spotify")
        )
        music_hint = self._contains_any(text, MUSIC_NOUNS)
        has_play_phrase = self._contains_any(text, SPOTIFY_PLAY_PHRASES)

        action = None
        if spotify_context and self._contains_any(text, ("pause", "stop music")):
            action = "pause"
        elif spotify_context and self._contains_any(text, ("resume", "continue", "unpause")):
            action = "resume"
        elif spotify_context and self._contains_any(text, ("next song", "next track", "skip song", "skip track", "skip")):
            action = "next"
        elif spotify_context and self._contains_any(text, ("previous song", "previous track", "last song", "last track", "go back")):
            action = "previous"
        elif spotify_context and "shuffle" in text:
            action = "shuffle"
        elif spotify_context and "repeat" in text:
            action = "repeat"
        elif has_play_phrase and (spotify_context or music_hint):
            action = "play"
        elif matched_alias and self._contains_launch_request(text, DEDICATED_APP_LAUNCH_PHRASES):
            action = "open"

        if action is None:
            return None

        confidence = 0.76
        if matched_alias:
            confidence = 0.94
        elif spotify_context:
            confidence = 0.84
        elif music_hint:
            confidence = 0.68

        preferred_types = self._spotify_preferred_types(text)
        query = None
        if action == "play":
            query = self._cleanup_query(
                text,
                app_name="spotify",
                extra_phrases=SPOTIFY_PLAY_PHRASES + SPOTIFY_CONTEXT_PHRASES,
                trim_edge_noise=False,
            )
            query = self._strip_music_tail(query)
            if not query:
                if matched_alias:
                    action = "resume"
                    query = None
                else:
                    return None

        return ParsedVoiceCommand(
            raw_text=raw_text,
            normalized_text=text,
            app_name="spotify",
            action=action,
            confidence=confidence,
            query=query,
            matched_alias=matched_alias,
            slots={"preferred_types": preferred_types},
        )

    def _parse_chrome(
        self,
        text: str,
        *,
        raw_text: str,
        context: VoiceCommandContext | None,
    ) -> ParsedVoiceCommand | None:
        matched_alias = self._matched_alias(text, "chrome")
        chrome_context = (
            matched_alias is not None
            or self._contains_any(text, CHROME_CONTEXT_PHRASES)
            or (context is not None and context.preferred_app == "chrome")
        )

        action: str | None = None
        if chrome_context and self._contains_any(text, ("go back", "back page", "previous page")):
            action = "back"
        elif chrome_context and self._contains_any(text, ("go forward", "forward page", "next page")):
            action = "forward"
        elif chrome_context and self._contains_any(text, ("refresh", "reload")):
            action = "refresh"
        elif chrome_context and self._contains_any(text, ("incognito", "private tab")):
            action = "incognito"
        elif chrome_context and self._contains_any(text, ("new tab", "open tab")):
            action = "new_tab"
        elif chrome_context and self._contains_any(text, GENERIC_OPEN_PHRASES):
            action = "open"
        elif self._contains_any(text, CHROME_SEARCH_PHRASES) or (context is not None and context.preferred_app == "chrome"):
            action = "search"

        if action is None:
            return None

        confidence = 0.74
        if matched_alias:
            confidence = 0.92
        elif chrome_context:
            confidence = 0.84

        query = None
        if action in {"open", "search"}:
            browser_context_phrases = CHROME_SEARCH_PHRASES + CHROME_CONTEXT_PHRASES + (
                "on google",
                "in google",
                "using google",
                "with google",
                "google chrome",
                "chrome browser",
                "browser",
            )
            extra_phrases = browser_context_phrases
            if action == "open":
                extra_phrases = extra_phrases + GENERIC_OPEN_PHRASES
            query = self._cleanup_query(
                text,
                app_name="chrome",
                extra_phrases=extra_phrases,
                trim_edge_noise=True,
            )
            query = self._normalize_browser_query(query)
            if action == "open" and not query:
                query = None
            elif not query and matched_alias:
                action = "open"
            elif not query:
                return None
            if query:
                query = self._normalize_browser_query(self.chrome_controller.normalize_spoken_target(query))
            if matched_alias and action == "open" and not query:
                confidence = max(confidence, 1.08)

        return ParsedVoiceCommand(
            raw_text=raw_text,
            normalized_text=text,
            app_name="chrome",
            action=action,
            confidence=confidence,
            query=query,
            matched_alias=matched_alias,
        )

    def _parse_settings(
        self,
        text: str,
        *,
        raw_text: str,
        context: VoiceCommandContext | None,
    ) -> ParsedVoiceCommand | None:
        matched_alias = self._matched_alias(text, "settings")
        topic = self._matched_settings_topic(text)
        if matched_alias is None and topic is None:
            return None
        if not self._contains_any(text, APP_LAUNCH_PHRASES + ("settings",)):
            return None
        explicit_settings = "settings" in text or matched_alias is not None
        confidence = 1.02 if explicit_settings else 0.90
        if topic is not None and explicit_settings:
            confidence = max(confidence, 1.06)
        elif topic is not None:
            confidence = max(confidence, 0.92)
        if context is not None and context.preferred_app == "settings":
            confidence += 0.04
        return ParsedVoiceCommand(
            raw_text=raw_text,
            normalized_text=text,
            app_name="settings",
            action="open",
            confidence=confidence,
            query=topic,
            matched_alias=matched_alias,
        )

    def _parse_file_explorer(
        self,
        text: str,
        *,
        raw_text: str,
        context: VoiceCommandContext | None,
    ) -> ParsedVoiceCommand | None:
        matched_alias = self._matched_alias(text, "file_explorer")
        if self._contains_launch_request(text, CLOSE_WINDOW_PHRASES):
            return None
        folder = self._matched_folder(text)
        has_folder_language = self._contains_any(text, ("folder", "directory", "files"))
        has_launch_request = self._contains_launch_request(text, APP_LAUNCH_PHRASES)
        bare_file_request = False
        if matched_alias is None and folder is None and not has_folder_language:
            if has_launch_request:
                rough_query = self._cleanup_query(
                    text,
                    app_name=None,
                    extra_phrases=APP_LAUNCH_PHRASES,
                    trim_edge_noise=True,
                )
                rough_query = self._normalize_file_request_query(rough_query)
                bare_file_request = self._looks_like_file_request(text, rough_query, context=context)
            if not bare_file_request:
                return None
        if matched_alias is None and folder is not None and not has_folder_language and not has_launch_request and not bare_file_request:
            return None

        search_phrases = ("search files", "find file", "find files", "search for")
        alias_search_hint = matched_alias is not None and self._contains_any(text, ("search", "find", "look for", "look up"))
        if self._contains_any(text, search_phrases) or alias_search_hint:
            query = self._cleanup_query(
                text,
                app_name="file_explorer",
                extra_phrases=search_phrases + ("find", "look for", "look up", "in file explorer", "in explorer"),
                trim_edge_noise=True,
            )
            query = self._strip_file_search_tail(query)
            if not query:
                return None
            action = "search"
        else:
            action = "open"
            cleaned_query = self._cleanup_query(
                text,
                app_name="file_explorer",
                extra_phrases=APP_LAUNCH_PHRASES + ("folder", "directory", "files"),
                trim_edge_noise=True,
            )
            cleaned_query = self._normalize_file_request_query(cleaned_query)
            query = cleaned_query or folder
            if not matched_alias and not folder and not query:
                return None
            if matched_alias and not query:
                query = None

        confidence = 0.90 if matched_alias else 0.78
        if matched_alias is not None:
            confidence = 1.02
            if action == "open" and not query:
                confidence = 1.08
        if folder is not None:
            confidence += 0.08
        if bare_file_request:
            confidence += 0.12
        if context is not None and context.preferred_app == "file_explorer":
            confidence += 0.04
        slots: dict[str, Any] = {}
        if folder is not None and query is not None:
            normalized_query = self.desktop_controller._normalize_application_name(query)
            if normalized_query != folder:
                slots["preferred_root"] = folder
        return ParsedVoiceCommand(
            raw_text=raw_text,
            normalized_text=text,
            app_name="file_explorer",
            action=action,
            confidence=confidence,
            query=query,
            matched_alias=matched_alias,
            slots=slots,
        )

    def _parse_outlook(
        self,
        text: str,
        *,
        raw_text: str,
        context: VoiceCommandContext | None,
    ) -> ParsedVoiceCommand | None:
        matched_alias = self._matched_alias(text, "outlook")
        if "settings" in text and matched_alias in {"email", "mail"}:
            return None
        folder_name = self._matched_outlook_folder(text)
        mentions_outlook = matched_alias is not None or re.search(r"\boutlook\b", text) is not None
        email_hint = self._contains_any(text, ("email", "mail", "inbox")) or folder_name is not None
        if matched_alias is None and not email_hint:
            return None

        if folder_name is not None and self._contains_any(text, GENERIC_OPEN_PHRASES):
            confidence = 0.92 if mentions_outlook else 0.78
            if context is not None and context.preferred_app == "outlook":
                confidence += 0.04
            return ParsedVoiceCommand(
                raw_text=raw_text,
                normalized_text=text,
                app_name="outlook",
                action="open_folder",
                confidence=confidence,
                query=folder_name,
                matched_alias=matched_alias,
            )

        if self._contains_any(text, ("compose", "draft", "write", "send email", "email")):
            action = "compose"
            recipient = self._strip_outlook_tail(self._extract_slot(text, start_phrase="to ", stop_phrases=(" subject ", " body ", " message ")))
            subject = self._strip_outlook_tail(self._extract_slot(text, start_phrase="subject ", stop_phrases=(" body ", " message ")))
            body = self._strip_outlook_tail(self._extract_slot(text, start_phrase="body ", stop_phrases=()))
            confidence = 0.88 if matched_alias else 0.76
            if context is not None and context.preferred_app == "outlook":
                confidence += 0.04
            return ParsedVoiceCommand(
                raw_text=raw_text,
                normalized_text=text,
                app_name="outlook",
                action=action,
                confidence=confidence,
                query=recipient,
                matched_alias=matched_alias,
                slots={"recipient": recipient, "subject": subject, "body": body},
            )

        if self._contains_launch_request(text, DEDICATED_APP_LAUNCH_PHRASES + ("open outlook", "open inbox", "open calendar")):
            confidence = 0.90 if matched_alias else 0.76
            if matched_alias is not None:
                confidence = max(confidence, 1.02)
            if context is not None and context.preferred_app == "outlook":
                confidence += 0.04
            return ParsedVoiceCommand(
                raw_text=raw_text,
                normalized_text=text,
                app_name="outlook",
                action="open",
                confidence=confidence,
                matched_alias=matched_alias,
            )
        return None

    def _parse_catalog_open(
        self,
        text: str,
        *,
        raw_text: str,
        catalog_matches: list[tuple[DesktopAppEntry, float, str | None]],
    ) -> ParsedVoiceCommand | None:
        if not self._contains_any(text, APP_LAUNCH_PHRASES):
            return None
        if not catalog_matches:
            return None
        entry, match_score, matched_alias = catalog_matches[0]
        if match_score < 0.82:
            return None
        entry_name = self.desktop_controller._normalize_application_name(entry.display_name)
        alias_name = self.desktop_controller._normalize_application_name(matched_alias or "")
        if self._should_skip_catalog_open(text, entry_name=entry_name, matched_alias=alias_name):
            return None
        query = self._cleanup_query(
            text,
            app_name=None,
            extra_phrases=APP_LAUNCH_PHRASES + APP_OBJECT_HINTS,
            trim_edge_noise=True,
        )
        if self._looks_like_file_request(text, self._normalize_file_request_query(query), context=None):
            return None
        query = self._cleanup_app_launch_query(query)
        normalized_query = self.desktop_controller._normalize_application_name(query or "")
        app_alias = self.desktop_controller._normalize_application_name(matched_alias or entry.normalized_name)
        if query and not self.desktop_controller.can_resolve_application(query):
            if app_alias and app_alias not in normalized_query:
                return None
            resolved_query = entry.display_name
        else:
            resolved_query = query or entry.display_name
        return ParsedVoiceCommand(
            raw_text=raw_text,
            normalized_text=text,
            app_name="system",
            action="open_app",
            confidence=min(0.96, 0.68 + match_score * 0.22),
            query=resolved_query,
            matched_alias=matched_alias,
            slots={
                "resolved_app_name": entry.display_name,
                "app_category": entry.category,
                "catalog_match_score": match_score,
            },
        )

    def _parse_generic_open(self, text: str, *, raw_text: str) -> ParsedVoiceCommand | None:
        if not self._contains_any(text, APP_LAUNCH_PHRASES):
            return None
        query = self._cleanup_query(
            text,
            app_name=None,
            extra_phrases=APP_LAUNCH_PHRASES + APP_OBJECT_HINTS,
            trim_edge_noise=True,
        )
        if self._looks_like_file_request(text, self._normalize_file_request_query(query), context=None):
            return None
        query = self._cleanup_app_launch_query(query)
        if not query:
            return None
        normalized_query = self.desktop_controller._normalize_application_name(query)
        matched_alias: str | None = None
        slots: dict[str, Any] = {}
        confidence = 0.78
        if not self.desktop_controller.can_resolve_application(query):
            recovered = self._best_fuzzy_launch_match(query, text=text)
            if recovered is not None:
                entry, recovered_score, matched_alias = recovered
                query = entry.display_name
                confidence = max(confidence, min(1.04, 0.86 + recovered_score * 0.28))
                slots = {
                    "resolved_app_name": entry.display_name,
                    "app_category": entry.category,
                    "catalog_match_score": recovered_score,
                }
            elif normalized_query in {"calendar", "system calendar"}:
                query = "calendar"
            else:
                if query and len(query.split()) >= 2:
                    return ParsedVoiceCommand(
                        raw_text=raw_text,
                        normalized_text=text,
                        app_name="file_explorer",
                        action="open",
                        confidence=0.60,
                        query=query,
                    )
                return None
        return ParsedVoiceCommand(
            raw_text=raw_text,
            normalized_text=text,
            app_name="system",
            action="open_app",
            confidence=confidence,
            query=query,
            matched_alias=matched_alias,
            slots=slots,
        )

    def _best_fuzzy_launch_match(
        self,
        query: str | None,
        *,
        text: str,
    ) -> tuple[DesktopAppEntry, float, str | None] | None:
        normalized_query = self.desktop_controller._normalize_application_query(query or "")
        if not normalized_query:
            return None
        matches = self.desktop_controller.rank_applications_in_text(normalized_query)
        if not matches:
            return None
        entry, score, matched_alias = matches[0]
        token_count = len(normalized_query.split())
        has_object_hint = self._contains_any(text, APP_OBJECT_HINTS)
        if not has_object_hint and self._contains_any(
            text,
            CHROME_CONTEXT_PHRASES + SPOTIFY_CONTEXT_PHRASES + ("outlook", "file explorer", "settings"),
        ):
            return None
        threshold = 0.58
        if has_object_hint and token_count <= 1:
            threshold = 0.38
        elif has_object_hint and token_count <= 2:
            threshold = 0.46
        elif token_count <= 1:
            threshold = 0.50
        if entry.source == "known":
            threshold = max(0.34, threshold - 0.04)
        if score < threshold:
            return None
        return entry, score, matched_alias

    def _apply_catalog_bias(
        self,
        intent: ParsedVoiceCommand,
        catalog_matches: list[tuple[DesktopAppEntry, float, str | None]],
        *,
        context: VoiceCommandContext | None,
    ) -> None:
        best_score = 0.0
        catalog_keys = self._intent_catalog_keys(intent)
        for entry, score, matched_alias in catalog_matches:
            entry_keys = {entry.normalized_name, *(alias for alias in entry.aliases if alias)}
            if entry_keys & catalog_keys:
                best_score = max(best_score, score)
            elif matched_alias is not None and matched_alias in catalog_keys:
                best_score = max(best_score, score)
        if best_score > 0.0:
            intent.confidence += min(0.15, best_score * 0.12)
        elif intent.app_name == "system" and catalog_matches and intent.query:
            top_entry, top_score, _matched_alias = catalog_matches[0]
            if self.desktop_controller._normalize_application_name(intent.query) == top_entry.normalized_name:
                intent.confidence += min(0.12, top_score * 0.10)
        if context is not None and context.preferred_app and context.preferred_app == intent.app_name:
            intent.confidence += 0.04

    def _intent_catalog_keys(self, intent: ParsedVoiceCommand) -> set[str]:
        keys = {intent.app_name}
        if intent.app_name == "chrome":
            keys.update({"chrome", "google chrome"})
        elif intent.app_name == "spotify":
            keys.add("spotify")
        elif intent.app_name == "outlook":
            keys.add("outlook")
        elif intent.app_name == "system":
            resolved_name = str(intent.slots.get("resolved_app_name", "") or "").strip()
            if resolved_name:
                keys.add(self.desktop_controller._normalize_application_name(resolved_name))
            if intent.query:
                keys.add(self.desktop_controller._normalize_application_name(intent.query))
        return {key for key in keys if key}

    def _intent_template_bonus(self, text: str, intent: ParsedVoiceCommand) -> float:
        best_score = 0.0
        for phrase in self._intent_reference_phrases(intent):
            score = SequenceMatcher(None, text, phrase).ratio()
            score += self._token_overlap(text, phrase) * 0.22
            if score > best_score:
                best_score = score
        return max(0.0, min(0.12, (best_score - 0.55) * 0.30))

    def _intent_reference_phrases(self, intent: ParsedVoiceCommand) -> tuple[str, ...]:
        query = (intent.query or "").strip()
        if intent.app_name == "spotify":
            if intent.action == "play" and query:
                return (f"play {query} on spotify", f"spotify play {query}")
            if intent.action == "open":
                return ("open spotify", "launch spotify")
            return (f"spotify {intent.action}",)
        if intent.app_name == "chrome":
            if intent.action in {"open", "search"} and query:
                return (f"open {query} on chrome", f"search {query} in chrome")
            return (f"chrome {intent.action}",)
        if intent.app_name == "settings":
            topic = query or "settings"
            return (f"open {topic} settings", f"show {topic} settings")
        if intent.app_name == "file_explorer":
            if intent.action == "search" and query:
                return (f"search files for {query}", f"find files named {query}")
            if query:
                return (f"open {query} folder", f"open {query} in file explorer")
            return ("open file explorer",)
        if intent.app_name == "outlook":
            if intent.action == "open_folder" and query:
                return (f"open {query.lower()} from outlook", f"show {query.lower()} in outlook")
            if intent.action == "compose":
                return ("compose email in outlook", "write email in outlook")
            return ("open outlook",)
        if intent.app_name == "system" and query:
            return (f"open {query}", f"launch {query}", f"switch to {query}", f"run {query}")
        return ()

    def _token_overlap(self, left: str, right: str) -> float:
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        shared = len(left_tokens & right_tokens)
        return shared / max(len(left_tokens), len(right_tokens))

    def _normalize_text(self, text: str) -> str:
        normalized = str(text or "").replace("\n", " ").lower()
        normalized = re.sub(r"[?!,]+", " ", normalized)
        normalized = normalized.replace("'", "")
        normalized = re.sub(r"\bshowme\b", "show me", normalized)
        normalized = re.sub(r"\bpullup\b", "pull up", normalized)
        normalized = re.sub(r"\b(open|close|launch|start|boot|show)(?=[a-z0-9])", lambda m: m.group(1) + " ", normalized)
        normalized = f" {normalized} "
        for source, target in COMMAND_CORRECTIONS:
            normalized = normalized.replace(f" {source} ", f" {target} ")
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip(" .")

    def _matched_alias(self, text: str, app_name: str) -> str | None:
        for alias in sorted(APP_ALIASES.get(app_name, ()), key=len, reverse=True):
            if re.search(rf"\b{re.escape(alias)}\b", text):
                return alias
        if app_name == "outlook":
            for alias in ("outlook", "email", "mail", "inbox"):
                if re.search(rf"\b{re.escape(alias)}\b", text):
                    return alias
        return None

    def _matched_settings_topic(self, text: str) -> str | None:
        for topic, aliases in SETTINGS_TOPICS.items():
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", text):
                    return topic
        return None

    def _matched_folder(self, text: str) -> str | None:
        for folder, aliases in KNOWN_FOLDERS.items():
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", text):
                    return folder
        return None

    def _matched_outlook_folder(self, text: str) -> str | None:
        for canonical, aliases in self.desktop_controller.OUTLOOK_FOLDERS.items():
            for alias in aliases:
                if re.search(rf"\b{re.escape(alias)}\b", text):
                    return self.desktop_controller.outlook_folder_display_name(canonical)
        return None

    def _spotify_preferred_types(self, text: str) -> tuple[str, ...]:
        if "playlist" in text:
            return ("playlist", "track", "album", "artist")
        if "album" in text:
            return ("album", "track", "playlist", "artist")
        if "artist" in text or "songs by " in text:
            return ("artist", "track", "playlist", "album")
        return ("track", "playlist", "album", "artist")

    def _strip_music_tail(self, text: str | None) -> str | None:
        value = str(text or "").strip()
        if not value:
            return None
        preserved = {"liked songs"}
        if value.lower() in preserved:
            return value
        for tail in (" music", " song", " songs", " track", " tracks", " playlist", " playlists", " album", " albums"):
            if value.endswith(tail):
                candidate = value[: -len(tail)].strip()
                if candidate.lower() in preserved:
                    return candidate + tail
                value = candidate
                break
        return value or None

    def _strip_file_search_tail(self, text: str | None) -> str | None:
        value = str(text or "").strip()
        if not value:
            return None
        for tail in (" file", " files", " folder", " folders"):
            if value.endswith(tail):
                value = value[: -len(tail)].strip()
        return value or None

    def _normalize_file_request_query(self, text: str | None) -> str | None:
        value = self._strip_file_search_tail(text)
        if not value:
            return None
        value = re.sub(r"\s+(?:in|from)\s+(?:file explorer|explorer)$", "", value).strip()
        value = re.sub(r"^\s*(?:file|document|folder)\s+", "", value).strip()
        return value or None

    def _looks_like_file_request(
        self,
        text: str,
        query: str | None,
        *,
        context: VoiceCommandContext | None,
    ) -> bool:
        value = str(query or "").strip()
        if not value:
            return False
        normalized_query = self.desktop_controller._normalize_application_name(value)
        if not normalized_query:
            return False
        if self._matched_outlook_folder(text) is not None:
            return False
        if self._contains_any(text, APP_OBJECT_HINTS):
            return False
        tokens = [token for token in normalized_query.split() if token]
        if not tokens:
            return False
        if context is not None and context.preferred_app == "file_explorer":
            return True
        explicit_file_language = self._contains_any(
            text,
            ("file", "document", "pdf", "word", "excel", "powerpoint", "spreadsheet", "slides", "txt", "json", "csv"),
        )
        if explicit_file_language:
            return True
        if any(token in FILE_REQUEST_HINTS for token in tokens):
            return True
        if any(re.fullmatch(r"[a-z]{1,4}\d{2,4}", token) for token in tokens) and len(tokens) >= 2:
            return True
        if "." in value and re.search(r"\.[a-z0-9]{1,5}\b", value):
            return True
        if self._contains_any(text, CHROME_CONTEXT_PHRASES + SPOTIFY_CONTEXT_PHRASES):
            return False
        for size in range(min(4, len(tokens)), 0, -1):
            candidate = " ".join(tokens[:size])
            if self.desktop_controller.can_resolve_application(candidate):
                return False
        if self.desktop_controller.can_resolve_application(normalized_query) and len(tokens) <= 4:
            return False
        if self._contains_launch_request(text, APP_LAUNCH_PHRASES) and 1 <= len(tokens) <= 5:
            return True
        if self._looks_like_web_target(value):
            return False
        return False

    def _strip_outlook_tail(self, text: str | None) -> str | None:
        value = str(text or "").strip()
        if not value:
            return None
        value = re.sub(r"\s+(?:in|from)\s+outlook$", "", value).strip()
        return value or None

    def _parse_bare_browser_request(self, text: str) -> tuple[str, str] | None:
        trimmed = self._strip_common_prefix(text)
        for action, prefix in WEB_FALLBACK_PREFIXES:
            if re.search(rf"^\b{re.escape(prefix)}\b", trimmed):
                query = trimmed[len(prefix):].strip()
                query = self._trim_edge_noise(query)
                query = self._normalize_browser_query(query)
                if query and self._looks_like_web_target(query):
                    return action, query
        return None

    def _looks_like_web_target(self, query: str | None) -> bool:
        value = str(query or "").strip().lower()
        if not value:
            return False
        if "." in value or "/" in value or value.startswith("www "):
            return True
        if value in WEB_TARGET_HINTS:
            return True
        tokens = [token for token in value.split() if token]
        if len(tokens) >= 2:
            return True
        return False

    def _contains_launch_request(self, text: str, phrases: tuple[str, ...]) -> bool:
        trimmed = self._strip_common_prefix(text)
        return any(re.search(rf"^\b{re.escape(phrase)}\b", trimmed) for phrase in phrases)

    def _strip_common_prefix(self, text: str) -> str:
        value = f" {str(text or '').strip()} "
        changed = True
        while changed:
            changed = False
            for phrase in sorted(COMMON_FILLERS, key=len, reverse=True):
                updated = re.sub(rf"^\s*{re.escape(phrase)}\b\s*", " ", value)
                if updated != value:
                    value = updated
                    changed = True
        return re.sub(r"\s+", " ", value).strip()

    def _should_skip_catalog_open(self, text: str, *, entry_name: str, matched_alias: str) -> bool:
        folder = self._matched_folder(text)
        if "settings" in text and entry_name not in {"settings", "windows settings"}:
            return True
        if entry_name in {"google chrome", "chrome"} or matched_alias in {"chrome", "browser"}:
            if self._contains_launch_request(text, DEDICATED_APP_LAUNCH_PHRASES):
                return True
            if self._contains_any(text, ("new tab", "open tab", "incognito", "private tab", "go back", "back page", "previous page", "go forward", "forward page", "refresh", "reload")):
                return True
        if entry_name in {"file explorer", "explorer"} or matched_alias in {"file explorer", "explorer"}:
            if self._contains_launch_request(text, DEDICATED_APP_LAUNCH_PHRASES):
                return True
            if folder is not None or self._contains_any(text, ("search files", "find file", "find files", "search for", "look for", "look up")):
                return True
        if entry_name == "spotify" or matched_alias == "spotify":
            if self._contains_launch_request(text, DEDICATED_APP_LAUNCH_PHRASES):
                return True
            if self._contains_any(text, SPOTIFY_PLAY_PHRASES + ("pause", "stop music", "resume", "continue", "unpause", "next song", "next track", "skip song", "skip track", "skip", "previous song", "previous track", "last song", "last track", "go back", "shuffle", "repeat")):
                return True
        if entry_name == "outlook" or matched_alias in {"outlook", "mail", "email", "inbox"}:
            if self._contains_launch_request(text, DEDICATED_APP_LAUNCH_PHRASES):
                return True
            if self._contains_any(text, ("compose", "draft", "write", "send email", "email", "inbox", "calendar", "sent items", "drafts", "deleted items", "outbox")):
                return True
        return False

    def _is_spotify_resume_phrase(self, raw_text: str) -> bool:
        normalized = self._normalize_text(raw_text)
        if not normalized:
            return False
        if "spotify" not in normalized:
            return False
        play_only = self._cleanup_query(
            normalized,
            app_name="spotify",
            extra_phrases=SPOTIFY_PLAY_PHRASES + SPOTIFY_CONTEXT_PHRASES,
            trim_edge_noise=False,
        )
        play_only = self._strip_music_tail(play_only)
        return not play_only

    def _normalize_browser_query(self, text: str | None) -> str | None:
        if text is None:
            return None
        working = str(text or "").strip().lower()
        if not working:
            return None
        working = re.sub(
            r"\b(search up|search for|search on|search|look up|look for|look on|find|google|open up|open|go to|navigate to|take me to)\b",
            " ",
            working,
        )
        working = re.sub(
            r"\b(on|in|using|with)\s+(google chrome|chrome browser|chrome|browser)\b",
            " ",
            working,
        )
        working = re.sub(
            r"\b(on|in|using|with)\s+google\b",
            " ",
            working,
        )
        working = re.sub(r"\b(google chrome|chrome browser|chrome|browser)\b", " ", working)
        working = re.sub(r"\bgoogle\b$", " ", working)
        working = re.sub(r"\bplease\b$", " ", working)
        working = re.sub(r"\s+", " ", working).strip(" .")
        working = self._trim_edge_noise(working)
        return working or None

    def _cleanup_app_launch_query(self, query: str | None) -> str | None:
        if query is None:
            return None
        working = str(query or "").strip()
        if not working:
            return None
        normalized = self.desktop_controller._normalize_application_query(working)
        if not normalized:
            return None

        tokens = normalized.split()
        tail_words = {"library", "client", "launcher", "app", "application", "program", "window"}
        while len(tokens) > 1 and tokens[-1] in tail_words:
            tokens.pop()
        cleaned = " ".join(tokens).strip()
        return cleaned or None

    def _cleanup_query(
        self,
        text: str,
        *,
        app_name: str | None,
        extra_phrases: tuple[str, ...],
        trim_edge_noise: bool = True,
    ) -> str | None:
        working = f" {text} "
        phrases = list(dict.fromkeys(COMMON_FILLERS + extra_phrases))
        for phrase in sorted(phrases, key=len, reverse=True):
            working = re.sub(rf"\b{re.escape(phrase)}\b", " ", working)
        if app_name is not None:
            for alias in sorted(APP_ALIASES.get(app_name, ()), key=len, reverse=True):
                working = re.sub(rf"\b{re.escape(alias)}\b", " ", working)
        working = re.sub(r"\s+", " ", working).strip(" .")
        if trim_edge_noise:
            working = self._trim_edge_noise(working)
        return working or None

    def _contains_any(self, text: str, phrases: tuple[str, ...]) -> bool:
        return any(re.search(rf"\b{re.escape(phrase)}\b", text) for phrase in phrases)

    def _extract_slot(self, text: str, *, start_phrase: str, stop_phrases: tuple[str, ...]) -> str | None:
        start_index = text.find(start_phrase)
        if start_index < 0:
            return None
        value = text[start_index + len(start_phrase):]
        for stop_phrase in stop_phrases:
            stop_index = value.find(stop_phrase)
            if stop_index >= 0:
                value = value[:stop_index]
                break
        cleaned = " ".join(value.split()).strip(" .")
        return cleaned or None

    def _trim_edge_noise(self, text: str) -> str:
        tokens = [token for token in str(text or "").split() if token]
        while tokens and tokens[0] in EDGE_NOISE_WORDS:
            tokens.pop(0)
        while tokens and tokens[-1] in EDGE_NOISE_WORDS:
            tokens.pop()
        return " ".join(tokens).strip()
