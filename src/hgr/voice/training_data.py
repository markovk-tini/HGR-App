from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..debug.desktop_controller import DesktopAppEntry, DesktopController
from .command_processor import VoiceProfileStore


POLITE_PREFIXES = ("", "please ", "can you ", "could you ", "would you ", "hey hgr ", "hey app ")
POLITE_SUFFIXES = ("", " please", " for me", " right now", " when you can")
GENERIC_APP_OPEN_TEMPLATES = (
    "open {app}",
    "launch {app}",
    "start {app}",
    "bring up {app}",
    "show me {app}",
    "open up {app}",
    "switch to {app}",
    "focus on {app}",
    "pull up {app}",
    "run {app}",
    "start up {app}",
    "boot up {app}",
    "fire up {app}",
    "load {app}",
    "use {app}",
    "open a {app} window",
    "open my {app}",
    "open the app called {app}",
    "open the app named {app}",
    "launch the application {app}",
    "start the program {app}",
    "i need {app}",
    "i need {app} open",
)
GENERIC_APP_NOISE = (
    ("visual studio", "visual studios"),
    ("visual studio", "visual stdios"),
    ("chatgpt", "chat gpt"),
    ("file explorer", "file explore"),
    ("wifi", "wi fi"),
)
CHROME_TARGETS = ("chatgpt", "youtube", "gmail", "indeed.com", "github.com", "calendar.google.com")
CHROME_SEARCHES = ("latest seattle weather", "best pizza nearby", "python dataclasses tutorial", "hgr app docs")
SPOTIFY_TRACKS = (
    "back in black by ac/dc",
    "blinding lights by the weeknd",
    "hotel california by eagles",
    "daft punk random access memories",
)
SPOTIFY_PLAYLISTS = ("discover weekly", "liked songs", "workout mix", "focus playlist")
SPOTIFY_ACTIONS = ("pause", "resume", "next", "previous", "shuffle", "repeat")
SETTINGS_TOPICS = ("apps", "bluetooth", "camera", "display", "network", "privacy", "sound", "storage", "update", "wifi")
FILE_FOLDERS = ("desktop", "documents", "downloads", "music", "pictures", "videos")
FILE_SEARCHES = ("resume", "invoice", "budget", "presentation")
OUTLOOK_FOLDERS = ("inbox", "sent items", "drafts", "deleted items", "calendar")
DEFAULT_EXPORT_DIR = Path.home() / "Documents" / "HGR Voice Training"


@dataclass(frozen=True)
class VoiceTrainingExample:
    utterance: str
    app_name: str
    app_display_name: str
    app_category: str
    intent: str
    slots: dict[str, Any]
    source: str


@dataclass(frozen=True)
class VoiceTrainingBundle:
    training_examples: list[VoiceTrainingExample]
    evaluation_examples: list[VoiceTrainingExample]
    correction_examples: list[VoiceTrainingExample]


class VoiceCommandDatasetBuilder:
    def __init__(
        self,
        *,
        desktop_controller: DesktopController | None = None,
        profile_store: VoiceProfileStore | None = None,
    ) -> None:
        self.desktop_controller = desktop_controller or DesktopController()
        self.profile_store = profile_store or VoiceProfileStore()

    def build_bundle(self, *, max_generic_apps: int = 64) -> VoiceTrainingBundle:
        catalog = self._select_generic_apps(max_generic_apps=max_generic_apps)
        training_examples = self._dedupe_examples(
            self._build_builtin_training_examples() + self._build_generic_app_examples(catalog)
        )
        evaluation_examples = self._dedupe_examples(self._build_fixed_evaluation_examples(catalog))
        correction_examples = self._dedupe_examples(self._build_correction_examples())
        return VoiceTrainingBundle(
            training_examples=training_examples,
            evaluation_examples=evaluation_examples,
            correction_examples=correction_examples,
        )

    def export_bundle(self, output_dir: Path | None = None, *, max_generic_apps: int = 64) -> dict[str, Path]:
        bundle = self.build_bundle(max_generic_apps=max_generic_apps)
        destination = output_dir or DEFAULT_EXPORT_DIR
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except Exception:
            destination = DEFAULT_EXPORT_DIR
            destination.mkdir(parents=True, exist_ok=True)
        try:
            train_path, eval_path, corrections_path, summary_path = self._write_bundle_files(destination, bundle)
        except Exception:
            destination = DEFAULT_EXPORT_DIR
            destination.mkdir(parents=True, exist_ok=True)
            train_path, eval_path, corrections_path, summary_path = self._write_bundle_files(destination, bundle)
        return {"train": train_path, "eval": eval_path, "corrections": corrections_path, "summary": summary_path}

    def _select_generic_apps(self, *, max_generic_apps: int) -> list[DesktopAppEntry]:
        apps = [
            entry
            for entry in self.desktop_controller.application_catalog_snapshot()
            if entry.normalized_name not in {"google chrome", "spotify", "outlook"}
        ]
        apps.sort(key=lambda entry: (entry.category != "generic", entry.normalized_name))
        return apps[: max(1, int(max_generic_apps))]

    def _build_builtin_training_examples(self) -> list[VoiceTrainingExample]:
        examples: list[VoiceTrainingExample] = []
        for target in CHROME_TARGETS:
            for utterance in self._template_variants(
                "chrome",
                (
                    f"open {target} on chrome",
                    f"search up {target} on chrome",
                    f"go to {target} in chrome",
                    f"can you open {target} in chrome",
                ),
            ):
                examples.append(self._example(utterance, app_name="chrome", app_display_name="Google Chrome", app_category="browser", intent="search", slots={"query": target}, source="synthetic"))
        for query in CHROME_SEARCHES:
            for utterance in self._template_variants("chrome", (f"search {query} on chrome", f"google {query}", f"look up {query} in chrome")):
                examples.append(self._example(utterance, app_name="chrome", app_display_name="Google Chrome", app_category="browser", intent="search", slots={"query": query}, source="synthetic"))
        for utterance, intent in {
            "open chrome": "open",
            "new tab in chrome": "new_tab",
            "open an incognito tab in chrome": "incognito",
            "refresh chrome": "refresh",
            "go back in chrome": "back",
            "go forward in chrome": "forward",
        }.items():
            examples.append(self._example(utterance, app_name="chrome", app_display_name="Google Chrome", app_category="browser", intent=intent, slots={}, source="synthetic"))

        for track in SPOTIFY_TRACKS:
            for utterance in self._template_variants(
                "spotify",
                (
                    f"play {track} on spotify",
                    f"put on {track} on spotify",
                    f"listen to {track} on spotify",
                    f"queue {track} on spotify",
                ),
            ):
                examples.append(self._example(utterance, app_name="spotify", app_display_name="Spotify", app_category="music", intent="play", slots={"query": track}, source="synthetic"))
        for playlist in SPOTIFY_PLAYLISTS:
            for utterance in self._template_variants("spotify", (f"play {playlist} playlist on spotify", f"open {playlist} on spotify")):
                examples.append(self._example(utterance, app_name="spotify", app_display_name="Spotify", app_category="music", intent="play", slots={"query": playlist}, source="synthetic"))
        for action in SPOTIFY_ACTIONS:
            for utterance in self._template_variants("spotify", (f"{action} spotify", f"{action} the music on spotify")):
                examples.append(self._example(utterance, app_name="spotify", app_display_name="Spotify", app_category="music", intent=action, slots={}, source="synthetic"))
        examples.append(self._example("open spotify", app_name="spotify", app_display_name="Spotify", app_category="music", intent="open", slots={}, source="synthetic"))

        for topic in SETTINGS_TOPICS:
            for utterance in self._template_variants("settings", (f"open {topic} settings", f"show my pc {topic} settings", f"can you open {topic} settings")):
                examples.append(self._example(utterance, app_name="settings", app_display_name="Windows Settings", app_category="system", intent="open", slots={"query": topic}, source="synthetic"))

        for folder in FILE_FOLDERS:
            for utterance in self._template_variants("file_explorer", (f"open {folder} folder", f"open {folder} in file explorer", f"show my {folder} folder")):
                examples.append(self._example(utterance, app_name="file_explorer", app_display_name="File Explorer", app_category="files", intent="open", slots={"query": folder}, source="synthetic"))
        for query in FILE_SEARCHES:
            for utterance in self._template_variants(
                "file_explorer",
                (
                    f"search files for {query}",
                    f"find file {query} in file explorer",
                    f"look for {query} in explorer",
                    f"find {query} in file explorer",
                ),
            ):
                examples.append(self._example(utterance, app_name="file_explorer", app_display_name="File Explorer", app_category="files", intent="search", slots={"query": query}, source="synthetic"))

        for folder in OUTLOOK_FOLDERS:
            for utterance in self._template_variants("outlook", (f"open {folder} from outlook", f"show my {folder} in outlook", f"can you open {folder} in outlook")):
                examples.append(self._example(utterance, app_name="outlook", app_display_name="Outlook", app_category="email", intent="open_folder", slots={"query": folder}, source="synthetic"))
        examples.append(self._example("open outlook", app_name="outlook", app_display_name="Outlook", app_category="email", intent="open", slots={}, source="synthetic"))
        examples.append(self._example("compose an email in outlook", app_name="outlook", app_display_name="Outlook", app_category="email", intent="compose", slots={}, source="synthetic"))
        return examples

    def _build_generic_app_examples(self, apps: list[DesktopAppEntry]) -> list[VoiceTrainingExample]:
        examples: list[VoiceTrainingExample] = []
        for entry in apps:
            app_names = tuple(dict.fromkeys((entry.display_name, *entry.aliases)))
            for app_name in app_names:
                if not app_name:
                    continue
                for template in GENERIC_APP_OPEN_TEMPLATES:
                    for utterance in self._template_variants(entry.normalized_name, (template.format(app=app_name),)):
                        examples.append(
                            self._example(
                                utterance,
                                app_name="system",
                                app_display_name=entry.display_name,
                                app_category=entry.category,
                                intent="open_app",
                                slots={"query": entry.display_name},
                                source="synthetic",
                            )
                        )
        return examples

    def _build_fixed_evaluation_examples(self, apps: list[DesktopAppEntry]) -> list[VoiceTrainingExample]:
        examples = [
            self._example("search up indeedn.com on google chrome", app_name="chrome", app_display_name="Google Chrome", app_category="browser", intent="search", slots={"query": "indeed.com"}, source="eval"),
            self._example("can you open chat gpt on chrome", app_name="chrome", app_display_name="Google Chrome", app_category="browser", intent="search", slots={"query": "chatgpt"}, source="eval"),
            self._example("can you open my pcs display settings", app_name="settings", app_display_name="Windows Settings", app_category="system", intent="open", slots={"query": "display"}, source="eval"),
            self._example("open my sent items from outlook please", app_name="outlook", app_display_name="Outlook", app_category="email", intent="open_folder", slots={"query": "sent items"}, source="eval"),
            self._example("open a visual stdios window please", app_name="system", app_display_name="visual studio code", app_category="editor", intent="open_app", slots={"query": "visual studio code"}, source="eval"),
            self._example("open steam", app_name="system", app_display_name="steam", app_category="gaming", intent="open_app", slots={"query": "steam"}, source="eval"),
            self._example("i need discord open", app_name="system", app_display_name="discord", app_category="chat", intent="open_app", slots={"query": "discord"}, source="eval"),
            self._example("run genshin impact", app_name="system", app_display_name="genshin impact", app_category="gaming", intent="open_app", slots={"query": "genshin impact"}, source="eval"),
            self._example("find resume in file explorer", app_name="file_explorer", app_display_name="File Explorer", app_category="files", intent="search", slots={"query": "resume"}, source="eval"),
        ]
        for entry in apps[: min(10, len(apps))]:
            examples.append(
                self._example(
                    f"could you switch to {entry.display_name} for me",
                    app_name="system",
                    app_display_name=entry.display_name,
                    app_category=entry.category,
                    intent="open_app",
                    slots={"query": entry.display_name},
                    source="eval",
                )
            )
        return examples

    def _build_correction_examples(self) -> list[VoiceTrainingExample]:
        examples: list[VoiceTrainingExample] = []
        for entry in self.profile_store.history_entries():
            utterance = str(entry.get("utterance", "") or "").strip()
            app_name = str(entry.get("app_name", "") or "").strip()
            action = str(entry.get("action", "") or "").strip()
            query = entry.get("query")
            if not utterance or not app_name or not action:
                continue
            examples.append(
                self._example(
                    utterance,
                    app_name=app_name,
                    app_display_name=app_name.replace("_", " ").title(),
                    app_category="learned",
                    intent=action,
                    slots={"query": query} if query else {},
                    source="correction" if entry.get("corrected") else "history",
                )
            )
        return examples

    def _template_variants(self, key: str, utterances: tuple[str, ...]) -> list[str]:
        variants: list[str] = []
        for utterance in utterances:
            for prefix in POLITE_PREFIXES:
                for suffix in POLITE_SUFFIXES:
                    text = f"{prefix}{utterance}{suffix}".strip()
                    variants.append(" ".join(text.split()))
        for source, target in GENERIC_APP_NOISE:
            if source not in key:
                continue
            variants.extend(" ".join(item.replace(source, target).split()) for item in tuple(variants))
        return variants

    def _dedupe_examples(self, examples: list[VoiceTrainingExample]) -> list[VoiceTrainingExample]:
        seen: dict[tuple[str, str, str, str], VoiceTrainingExample] = {}
        for example in examples:
            query = str(example.slots.get("query", "") or "")
            key = (example.utterance.lower(), example.app_name, example.intent, query.lower())
            seen.setdefault(key, example)
        return list(seen.values())

    def _write_jsonl(self, path: Path, examples: list[VoiceTrainingExample]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for example in examples:
                handle.write(json.dumps(asdict(example), ensure_ascii=True) + "\n")

    def _write_bundle_files(self, destination: Path, bundle: VoiceTrainingBundle) -> tuple[Path, Path, Path, Path]:
        train_path = destination / "train.jsonl"
        eval_path = destination / "eval.jsonl"
        corrections_path = destination / "corrections.jsonl"
        summary_path = destination / "summary.json"
        self._write_jsonl(train_path, bundle.training_examples)
        self._write_jsonl(eval_path, bundle.evaluation_examples)
        self._write_jsonl(corrections_path, bundle.correction_examples)
        summary_path.write_text(
            json.dumps(
                {
                    "training_examples": len(bundle.training_examples),
                    "evaluation_examples": len(bundle.evaluation_examples),
                    "correction_examples": len(bundle.correction_examples),
                    "generic_apps": sorted({example.app_display_name for example in bundle.training_examples if example.app_name == "system"})[:24],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return train_path, eval_path, corrections_path, summary_path

    def _example(
        self,
        utterance: str,
        *,
        app_name: str,
        app_display_name: str,
        app_category: str,
        intent: str,
        slots: dict[str, Any],
        source: str,
    ) -> VoiceTrainingExample:
        return VoiceTrainingExample(
            utterance=" ".join(utterance.split()),
            app_name=app_name,
            app_display_name=app_display_name,
            app_category=app_category,
            intent=intent,
            slots=dict(slots),
            source=source,
        )
