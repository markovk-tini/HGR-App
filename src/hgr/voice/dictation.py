from __future__ import annotations

import re
from dataclasses import dataclass


_PLACEHOLDER_PARAGRAPH = "__HGR_NEW_PARAGRAPH__"
_PLACEHOLDER_LINE = "__HGR_NEW_LINE__"

_SPOKEN_REPLACEMENTS = (
    ("new paragraph", _PLACEHOLDER_PARAGRAPH),
    ("new line", _PLACEHOLDER_LINE),
    ("question mark", "?"),
    ("exclamation point", "!"),
    ("exclamation mark", "!"),
    ("semicolon", ";"),
    ("colon", ":"),
    ("comma", ","),
    ("period", "."),
    ("full stop", "."),
    ("open quote", '"'),
    ("close quote", '"'),
    ("quote", '"'),
    ("open parenthesis", "("),
    ("close parenthesis", ")"),
    ("left parenthesis", "("),
    ("right parenthesis", ")"),
    ("dash", "-"),
    ("hyphen", "-"),
)

_TITLE_CASE_REPLACEMENTS = (
    (r"\bchat\s*gpt\b", "ChatGPT"),
    (r"\bkicad\b", "KiCad"),
    (r"\bvs\s*code\b", "VS Code"),
    (r"\bvscode\b", "VS Code"),
    (r"\bvisual studio code\b", "Visual Studio Code"),
    (r"\bspotify\b", "Spotify"),
    (r"\boutlook\b", "Outlook"),
    (r"\bgoogle chrome\b", "Google Chrome"),
    (r"\bchrome\b", "Chrome"),
)


@dataclass(frozen=True)
class DictationUpdate:
    raw_text: str
    text_to_insert: str
    full_text: str
    display_text: str


class DictationProcessor:
    def __init__(self) -> None:
        self._full_text = ""

    @property
    def full_text(self) -> str:
        return self._full_text

    def reset(self) -> None:
        self._full_text = ""

    def preview(self, spoken_text: str) -> str:
        raw_text = " ".join(str(spoken_text or "").split()).strip()
        if not raw_text:
            return ""

        rendered = self._render_spoken_text(raw_text)
        rendered = self._apply_leading_spacing(rendered)
        rendered = self._apply_sentence_casing(rendered)
        rendered = self._apply_title_case(rendered)
        rendered = self._normalize_spacing(rendered)
        return rendered

    def ingest(self, spoken_text: str) -> DictationUpdate:
        raw_text = " ".join(str(spoken_text or "").split()).strip()
        if not raw_text:
            return DictationUpdate(raw_text="", text_to_insert="", full_text=self._full_text, display_text=self._full_text or "-")

        rendered = self.preview(raw_text)
        if not rendered:
            return DictationUpdate(raw_text=raw_text, text_to_insert="", full_text=self._full_text, display_text=self._full_text or "-")

        self._full_text += rendered
        return DictationUpdate(
            raw_text=raw_text,
            text_to_insert=rendered,
            full_text=self._full_text,
            display_text=self._full_text or "-",
        )

    def _render_spoken_text(self, spoken_text: str) -> str:
        working = f" {spoken_text.strip()} "
        for phrase, replacement in sorted(_SPOKEN_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True):
            working = re.sub(rf"\b{re.escape(phrase)}\b", f" {replacement} ", working, flags=re.IGNORECASE)
        working = re.sub(r"\s+", " ", working).strip()
        working = working.replace(_PLACEHOLDER_PARAGRAPH, "\n\n")
        working = working.replace(_PLACEHOLDER_LINE, "\n")
        working = re.sub(r"[ ]+\n", "\n", working)
        working = re.sub(r"\n[ ]+", "\n", working)
        return working.strip()

    def _apply_leading_spacing(self, text: str) -> str:
        if not text:
            return ""
        if not self._full_text:
            return text
        if text.startswith(("\n", ".", ",", "!", "?", ";", ":", ")", "]", '"', "-")):
            return text
        if self._full_text.endswith((" ", "\n", "\t", "(", "[", '"', "-")):
            return text
        return " " + text

    def _apply_sentence_casing(self, text: str) -> str:
        if not text:
            return ""
        result: list[str] = []
        cap_next = not self._full_text.strip() or self._full_text.rstrip().endswith((".", "!", "?", "\n"))
        for char in text:
            if cap_next and char.isalpha():
                result.append(char.upper())
                cap_next = False
            else:
                result.append(char)
                if char.isalpha():
                    cap_next = False
            if char in ".!?":
                cap_next = True
            elif char == "\n":
                cap_next = True
        rendered = "".join(result)
        rendered = re.sub(r"\bi\b", "I", rendered)
        rendered = re.sub(r"\bim\b", "I'm", rendered, flags=re.IGNORECASE)
        rendered = re.sub(r"\bive\b", "I've", rendered, flags=re.IGNORECASE)
        rendered = re.sub(r"\bill\b", "I'll", rendered, flags=re.IGNORECASE)
        return rendered

    def _apply_title_case(self, text: str) -> str:
        rendered = text
        for pattern, replacement in _TITLE_CASE_REPLACEMENTS:
            rendered = re.sub(pattern, replacement, rendered, flags=re.IGNORECASE)
        return rendered

    def _normalize_spacing(self, text: str) -> str:
        rendered = re.sub(r"[ ]+([,.;:!?])", r"\1", text)
        rendered = re.sub(r"([(\[])\s+", r"\1", rendered)
        rendered = re.sub(r"\s+([)\]])", r"\1", rendered)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        rendered = re.sub(r"[ \t]+", " ", rendered)
        rendered = re.sub(r" *\n *", "\n", rendered)
        return rendered
