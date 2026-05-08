"""Survey-style wizard for creating a new custom gesture.

Collects: name, description, hold/cooldown timing, action kind + value.
On Start, launches the recorder dialog (caller wires that in). Conflict
detection on name happens here; pose-similarity conflicts are checked
later by the recorder once samples exist.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from hgr.custom_gestures.registry import Action, GestureRegistry

from .custom_gestures_chrome import apply_touchless_titlebar


# (label, kind, value-prompt, placeholder)
_ACTION_KINDS = (
    ("Press a single key", "keystroke", "Key name", "e.g. enter, f12, space"),
    ("Press a hotkey combo", "hotkey", "Keys (joined by +)", "e.g. ctrl+shift+t"),
    ("Type a text snippet", "text", "Text to type", "e.g. test@example.com"),
    ("Open a URL in browser", "open_url", "URL", "e.g. https://example.com"),
    ("Run a shell command", "run_command", "Command", "e.g. start spotify"),
    ("Open any file", "open_file", "Full file path", r"e.g. C:\Users\you\Documents\notes.docx"),
    ("Show a saved drawing as overlay", "show_overlay_drawing", "Drawing filename (in your drawings folder)", "e.g. Touchless_Drawing_001.png"),
)


@dataclass(frozen=True)
class WizardResult:
    name: str
    description: str
    hold_seconds: float
    cooldown_seconds: float
    action: Action


# Mock keyboard layout. (display_label, action_key_name, width_units).
# 1.0 width-unit ≈ 28 px. Width-units roughly match a real US QWERTY
# keyboard so the visual reads as familiar. Right-side modifier
# duplicates (R-Shift / R-Ctrl etc.) collapse to the same key name on
# selection — clicking 'Shift' twice on either side toggles the same
# entry, which matches what the underlying SendInput layer does.
_VK_LAYOUT_ROWS: tuple[tuple[tuple[str, str, float], ...], ...] = (
    (
        ("Esc", "esc", 1.5),
        ("F1", "f1", 1.0), ("F2", "f2", 1.0), ("F3", "f3", 1.0), ("F4", "f4", 1.0),
        ("F5", "f5", 1.0), ("F6", "f6", 1.0), ("F7", "f7", 1.0), ("F8", "f8", 1.0),
        ("F9", "f9", 1.0), ("F10", "f10", 1.0), ("F11", "f11", 1.0), ("F12", "f12", 1.0),
    ),
    (
        ("`", "`", 1.0),
        ("1", "1", 1.0), ("2", "2", 1.0), ("3", "3", 1.0), ("4", "4", 1.0),
        ("5", "5", 1.0), ("6", "6", 1.0), ("7", "7", 1.0), ("8", "8", 1.0),
        ("9", "9", 1.0), ("0", "0", 1.0),
        ("-", "-", 1.0), ("=", "=", 1.0),
        ("Backspace", "backspace", 2.0),
    ),
    (
        ("Tab", "tab", 1.5),
        ("Q", "q", 1.0), ("W", "w", 1.0), ("E", "e", 1.0), ("R", "r", 1.0), ("T", "t", 1.0),
        ("Y", "y", 1.0), ("U", "u", 1.0), ("I", "i", 1.0), ("O", "o", 1.0), ("P", "p", 1.0),
        ("[", "[", 1.0), ("]", "]", 1.0), ("\\", "\\", 1.5),
    ),
    (
        ("Caps", "caps", 1.75),
        ("A", "a", 1.0), ("S", "s", 1.0), ("D", "d", 1.0), ("F", "f", 1.0), ("G", "g", 1.0),
        ("H", "h", 1.0), ("J", "j", 1.0), ("K", "k", 1.0), ("L", "l", 1.0),
        (";", ";", 1.0), ("'", "'", 1.0),
        ("Enter", "enter", 2.25),
    ),
    (
        ("Shift", "shift", 2.25),
        ("Z", "z", 1.0), ("X", "x", 1.0), ("C", "c", 1.0), ("V", "v", 1.0), ("B", "b", 1.0),
        ("N", "n", 1.0), ("M", "m", 1.0),
        (",", ",", 1.0), (".", ".", 1.0), ("/", "/", 1.0),
        ("Shift", "shift", 2.75),
    ),
    (
        ("Ctrl", "ctrl", 1.5),
        ("Win", "win", 1.25),
        ("Alt", "alt", 1.25),
        ("Space", "space", 6.25),
        ("Alt", "alt", 1.25),
        ("Win", "win", 1.25),
        ("Ctrl", "ctrl", 1.5),
    ),
)

_VK_UNIT_WIDTH = 22
_VK_KEY_HEIGHT = 22
_VK_KEY_GAP = 2


class _VirtualKeyboard(QWidget):
    """Compact clickable keyboard for the gesture wizard. Two modes:
    'single' (only one key may be selected) and 'combo' (multi-key
    chord). Emits keys_changed with the formatted string ('a',
    'enter', 'ctrl+shift+t', etc.) so the wizard's QLineEdit can
    follow along.

    Future: detect the user's actual keyboard layout via Win32
    GetKeyboardLayoutName and swap rows. For now we ship US QWERTY,
    which covers the vast majority of bindable shortcuts the user is
    likely to want."""

    keys_changed = Signal(str)

    def __init__(self, accent_color: str, parent=None):
        super().__init__(parent)
        self._accent_color = accent_color
        self._mode = "single"
        self._selected: list[str] = []
        # Each action_key_name may be on multiple buttons (left + right
        # Shift / Ctrl / Win / Alt). Keep them all so we can highlight
        # both sides when one is clicked.
        self._buttons_by_key: dict[str, list[QPushButton]] = {}
        self._build()

    def set_mode(self, mode: str) -> None:
        """'single' = one key only (replaces on click); 'combo' = chord
        (toggles each key on click). Clears the current selection on
        mode change so a stale combo doesn't leak into a freshly-
        selected single-key action."""
        if mode == self._mode:
            return
        self._mode = mode
        self._selected = []
        self._refresh_visual()
        self.keys_changed.emit("")

    def set_value(self, value: str) -> None:
        """Sync from an external string so manual typing in the wizard's
        QLineEdit reflects on the keyboard's highlighted keys."""
        text = (value or "").strip().lower()
        if not text:
            self._selected = []
        elif self._mode == "single":
            self._selected = [text]
        else:
            self._selected = [p.strip() for p in text.split("+") if p.strip()]
        self._refresh_visual()

    def selection(self) -> str:
        return self._format()

    # --- internal -----------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(_VK_KEY_GAP)
        # Stretches on BOTH sides of each row so the row's content
        # sits centred horizontally inside the keyboard widget,
        # which itself can grow to fill whatever width the parent
        # layout gave it. The previous version only had a trailing
        # stretch and left-aligned the keys.
        for row_cells in _VK_LAYOUT_ROWS:
            row = QHBoxLayout()
            row.setSpacing(_VK_KEY_GAP)
            row.setContentsMargins(0, 0, 0, 0)
            row.addStretch(1)
            for label, key, width_units in row_cells:
                btn = self._make_key(label, key, width_units)
                row.addWidget(btn)
            row.addStretch(1)
            outer.addLayout(row)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def _make_key(self, label: str, key: str, width_units: float) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName("vkKey")
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setAutoDefault(False)
        btn.setDefault(False)
        # Stash the target pixel size on the button so
        # _apply_button_style can bake it into the stylesheet.
        # setFixedSize alone wasn't enforcing the size — the parent
        # wizard's QPushButton rule (padding 8 18) was cascading to
        # these buttons and Qt was honouring that padding's implied
        # min content size, so each key rendered ~100 px wide
        # regardless of what we asked for. Putting min-width and
        # max-width inside the per-button CSS forces the size.
        pixel_w = int(width_units * _VK_UNIT_WIDTH + max(0, width_units - 1) * _VK_KEY_GAP)
        pixel_h = _VK_KEY_HEIGHT
        btn._vk_w = pixel_w  # type: ignore[attr-defined]
        btn._vk_h = pixel_h  # type: ignore[attr-defined]
        btn.setFixedSize(pixel_w, pixel_h)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _checked=False, k=key: self._on_clicked(k))
        self._buttons_by_key.setdefault(key, []).append(btn)
        self._apply_button_style(btn, selected=False)
        return btn

    def _on_clicked(self, key: str) -> None:
        if self._mode == "single":
            # Toggle: clicking the already-selected key clears it,
            # clicking any other replaces.
            self._selected = [] if self._selected == [key] else [key]
        else:
            if key in self._selected:
                self._selected.remove(key)
            else:
                self._selected.append(key)
        self._refresh_visual()
        self.keys_changed.emit(self._format())

    def _format(self) -> str:
        if not self._selected:
            return ""
        if self._mode == "single":
            return self._selected[0]
        return "+".join(self._selected)

    def _refresh_visual(self) -> None:
        for key, buttons in self._buttons_by_key.items():
            sel = key in self._selected
            for btn in buttons:
                self._apply_button_style(btn, sel)

    def _apply_button_style(self, btn: QPushButton, selected: bool) -> None:
        # Bake explicit min/max width + height into the stylesheet
        # so the size sticks even with the wizard's general
        # QPushButton rule (which has padding 8 18) cascaded down.
        # padding: 0 also has to be set explicitly here so the parent
        # rule doesn't reintroduce horizontal slack.
        w = int(getattr(btn, "_vk_w", _VK_UNIT_WIDTH))
        h = int(getattr(btn, "_vk_h", _VK_KEY_HEIGHT))
        size_css = (
            f"  min-width: {w}px; max-width: {w}px;"
            f"  min-height: {h}px; max-height: {h}px;"
            f"  padding: 0;"
        )
        if selected:
            btn.setStyleSheet(
                f"QPushButton#vkKey {{"
                f"  background: {self._accent_color};"
                f"  color: #0B1620;"
                f"  border: 1px solid {self._accent_color};"
                f"  border-radius: 3px;"
                f"  font-weight: 700;"
                f"  font-size: 10px;"
                f"{size_css}"
                f"}}"
                f"QPushButton#vkKey:hover {{ filter: brightness(1.05); }}"
            )
        else:
            btn.setStyleSheet(
                "QPushButton#vkKey {"
                "  background: rgba(255,255,255,0.05);"
                "  color: #DCE9F2;"
                "  border: 1px solid rgba(255,255,255,0.15);"
                "  border-radius: 3px;"
                "  font-weight: 600;"
                "  font-size: 10px;"
                f"{size_css}"
                "}"
                "QPushButton#vkKey:hover {"
                "  background: rgba(255,255,255,0.12);"
                "  border-color: rgba(255,255,255,0.30);"
                "}"
            )


class CreateGestureWizard(QDialog):
    """Modal-ish dialog. Caller calls exec(); on accept, .result_payload
    is a WizardResult; on reject, None."""

    def __init__(
        self,
        accent_color: str,
        parent: Optional[QWidget] = None,
        *,
        edit_mode: bool = False,
        initial_name: str = "",
        initial_description: str = "",
        initial_hold: float = 1.0,
        initial_cooldown: float = 2.0,
        initial_action_kind: Optional[str] = None,
        initial_action_value: str = "",
        original_name: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._edit_mode = bool(edit_mode)
        self.setWindowTitle("Edit Custom Gesture" if self._edit_mode else "Create Custom Gesture")
        self.setModal(True)
        # Keyboard's natural width with 22-px unit keys is ~334 px,
        # so the original 520-wide dialog has plenty of room. Kept
        # the modest bump to 540 for a small safety margin without
        # making the dialog feel oversized for users with the other
        # action kinds (text / URL / command / file / overlay).
        self.setMinimumWidth(540)
        self.setMinimumHeight(540)
        self._accent_color = accent_color
        self._original_name = original_name if self._edit_mode else None
        self._initial_name = initial_name
        self._initial_description = initial_description
        self._initial_hold = float(initial_hold)
        self._initial_cooldown = float(initial_cooldown)
        self._initial_action_kind = initial_action_kind
        self._initial_action_value = initial_action_value
        self.result_payload: Optional[WizardResult] = None
        self._build()
        self._populate_initial_values()

    def showEvent(self, event):  # noqa: N802 (Qt API name)
        super().showEvent(event)
        try:
            apply_touchless_titlebar(self)
        except Exception:
            pass

    # --- UI -------------------------------------------------------------

    def _build(self) -> None:
        self.setStyleSheet(
            f"""
            QDialog {{ background: #0E1822; }}
            QLabel {{ color: #DCE9F2; font-size: 13px; }}
            QLabel#sectionTitle {{ color: #E5F6FF; font-weight: 700; font-size: 16px; }}
            QLineEdit, QDoubleSpinBox, QComboBox {{
                background: rgba(255,255,255,0.05);
                color: #E5F6FF;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 6px;
                padding: 6px 8px;
                font-size: 13px;
            }}
            QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{
                border: 1px solid {self._accent_color};
            }}
            /* The dropdown POPUP — Qt renders it as a separate native widget,
               so its colors must be styled explicitly or it inherits the OS
               theme (white text on white background on some Win 11 setups). */
            QComboBox QAbstractItemView {{
                background: #0E1822;
                color: #E5F6FF;
                selection-background-color: {self._accent_color};
                selection-color: #0B1620;
                border: 1px solid rgba(255,255,255,0.18);
                outline: none;
                padding: 4px 0;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 12px;
                color: #E5F6FF;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #DCE9F2;
                margin-right: 8px;
            }}
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: #E5F6FF;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
                padding: 8px 18px;
                font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,0.12); }}
            QPushButton#startBtn {{
                background: {self._accent_color};
                color: #0B1620;
                font-weight: 800;
            }}
            QPushButton#startBtn:hover {{ background: #FFFFFF; }}
            QPushButton#startBtn:disabled {{
                background: rgba(255,255,255,0.06);
                color: #5C6F7E;
            }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = QLabel("Create Custom Gesture")
        title.setObjectName("sectionTitle")
        outer.addWidget(title)

        # Form lives inside a scroll area so smaller windows still let
        # the user reach every field.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # CSS-only viewport selectors don't always beat the OS default
        # on Win 11 — set the viewport background explicitly.
        scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(scroll, 1)

        form_widget = QWidget()
        form_widget.setStyleSheet("background: transparent;")
        scroll.setWidget(form_widget)
        root = QVBoxLayout(form_widget)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(14)

        # Name
        root.addWidget(QLabel("Name *"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Open Inbox")
        root.addWidget(self.name_edit)

        # Description
        root.addWidget(QLabel("Description (optional)"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("What the gesture does, in your own words")
        root.addWidget(self.desc_edit)

        # Timing
        timing_row = QHBoxLayout()
        timing_row.setSpacing(14)
        timing_box1 = QVBoxLayout()
        timing_box1.addWidget(QLabel("Hold to activate (seconds)"))
        self.hold_spin = QDoubleSpinBox()
        self.hold_spin.setRange(0.2, 5.0)
        self.hold_spin.setSingleStep(0.1)
        self.hold_spin.setDecimals(1)
        self.hold_spin.setValue(1.0)
        timing_box1.addWidget(self.hold_spin)
        timing_row.addLayout(timing_box1)

        timing_box2 = QVBoxLayout()
        timing_box2.addWidget(QLabel("Cooldown after fire (seconds)"))
        self.cooldown_spin = QDoubleSpinBox()
        self.cooldown_spin.setRange(0.0, 30.0)
        self.cooldown_spin.setSingleStep(0.5)
        self.cooldown_spin.setDecimals(1)
        self.cooldown_spin.setValue(2.0)
        timing_box2.addWidget(self.cooldown_spin)
        timing_row.addLayout(timing_box2)

        root.addLayout(timing_row)

        # Action kind. Starts unselected so the user has to deliberately
        # pick — the value-input row below stays hidden until they do.
        root.addWidget(QLabel("Action *"))
        self.action_combo = QComboBox()
        self.action_combo.addItem("— Choose an action —", None)
        for label, kind, *_ in _ACTION_KINDS:
            self.action_combo.addItem(label, kind)
        self.action_combo.setCurrentIndex(0)
        self.action_combo.currentIndexChanged.connect(self._refresh_action_value)
        root.addWidget(self.action_combo)

        # Action value (label + line edit, prompt changes with combo).
        # Hidden until the user picks a non-placeholder action.
        self.action_value_label = QLabel("")
        self.action_value_label.hide()
        root.addWidget(self.action_value_label)
        self.action_value_edit = QLineEdit()
        self.action_value_edit.hide()
        # Manual typing in the line edit updates the keyboard's
        # highlighted keys so the two views stay in sync. Use
        # textEdited (not textChanged) — textEdited fires only on
        # actual user input, so the keyboard's own setText calls
        # don't loop back through here.
        self.action_value_edit.textEdited.connect(self._on_value_text_edited)
        root.addWidget(self.action_value_edit)

        # Mock keyboard, shown only for keystroke / hotkey actions.
        # Single-mode for keystroke (one key replaces another),
        # combo-mode for hotkey (chord). The user can either type
        # in the line edit above OR click keys here; both paths
        # stay in sync.
        self.action_value_keyboard = _VirtualKeyboard(self._accent_color, parent=self)
        self.action_value_keyboard.hide()
        self.action_value_keyboard.keys_changed.connect(self._on_keyboard_keys_changed)
        root.addWidget(self.action_value_keyboard)

        root.addStretch(1)

        # Buttons pinned outside the scroll area so they stay reachable.
        # autoDefault=False on every button so pressing Enter in any
        # text field doesn't accidentally activate Back/Save/Start. Enter
        # is handled explicitly in keyPressEvent below — it advances to
        # Start ONLY when Start is actually enabled.
        button_row = QHBoxLayout()
        back_button = QPushButton("Back")
        back_button.setAutoDefault(False)
        back_button.setDefault(False)
        back_button.clicked.connect(self.reject)
        button_row.addWidget(back_button)
        button_row.addStretch(1)
        self._start_button = QPushButton(
            "Save" if self._edit_mode else "Start"
        )
        self._start_button.setObjectName("startBtn")
        self._start_button.setAutoDefault(False)
        self._start_button.setDefault(False)
        self._start_button.clicked.connect(self._on_start)
        # Disabled until the user picks an action — keeps the "next part
        # only appears after action chosen" promise even via shortcut.
        self._start_button.setEnabled(False)
        button_row.addWidget(self._start_button)
        outer.addLayout(button_row)

    def keyPressEvent(self, event):  # noqa: N802 (Qt API name)
        """Swallow Enter/Return so it never closes the dialog or
        triggers Back. Only forward to Start when the form is fully
        valid (Start button enabled). Escape uses the QDialog default
        (reject) — handled by super(). Every other key passes through
        normally so text editing works."""
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._start_button.isEnabled():
                self._on_start()
            event.accept()
            return
        super().keyPressEvent(event)

    def _populate_initial_values(self) -> None:
        """For edit mode: pre-fill the form with the existing gesture's
        values so the user can tweak instead of typing everything fresh."""
        if self._initial_name:
            self.name_edit.setText(self._initial_name)
        if self._initial_description:
            self.desc_edit.setText(self._initial_description)
        try:
            self.hold_spin.setValue(self._initial_hold)
        except Exception:
            pass
        try:
            self.cooldown_spin.setValue(self._initial_cooldown)
        except Exception:
            pass
        if self._initial_action_kind:
            for i in range(self.action_combo.count()):
                if self.action_combo.itemData(i) == self._initial_action_kind:
                    self.action_combo.setCurrentIndex(i)
                    break
            if self._initial_action_value:
                self.action_value_edit.setText(self._initial_action_value)
                # Mirror to the mock keyboard for keystroke / hotkey
                # so editing an existing gesture shows the saved keys
                # already highlighted. The keyboard's set_mode call
                # in _refresh_action_value already ran via the
                # currentIndexChanged trigger above, so the mode is
                # set; we only need to push the value in.
                if self._initial_action_kind in ("keystroke", "hotkey"):
                    self.action_value_keyboard.set_value(self._initial_action_value)

    def _refresh_action_value(self) -> None:
        kind = self.action_combo.currentData()
        if kind is None:
            # Placeholder ("Choose an action") selected — keep value row
            # hidden and the Start button disabled.
            self.action_value_label.hide()
            self.action_value_edit.hide()
            self.action_value_keyboard.hide()
            self.action_value_edit.setText("")
            self._start_button.setEnabled(False)
            return
        # Look up the matching prompt + placeholder for the chosen kind.
        for _label, k, prompt, placeholder in _ACTION_KINDS:
            if k == kind:
                # Keystroke / hotkey actions get the mock keyboard
                # below the input plus a clearer prompt that mentions
                # both input methods. Other action kinds keep their
                # original "Key name" / "Keys (joined by +)" / "URL"
                # / etc. prompt.
                if kind in ("keystroke", "hotkey"):
                    self.action_value_label.setText("Type or select key(s) below")
                else:
                    self.action_value_label.setText(prompt)
                self.action_value_edit.setPlaceholderText(placeholder)
                self.action_value_edit.setText("")
                self.action_value_label.show()
                self.action_value_edit.show()
                if kind == "keystroke":
                    self.action_value_keyboard.set_mode("single")
                    self.action_value_keyboard.set_value("")
                    self.action_value_keyboard.show()
                elif kind == "hotkey":
                    self.action_value_keyboard.set_mode("combo")
                    self.action_value_keyboard.set_value("")
                    self.action_value_keyboard.show()
                else:
                    self.action_value_keyboard.hide()
                break
        self._start_button.setEnabled(True)

    def _on_keyboard_keys_changed(self, value: str) -> None:
        """User clicked / unclicked a key on the mock keyboard.
        Push the formatted string into the line edit. Setting via
        setText doesn't fire textEdited, so this won't loop back
        through _on_value_text_edited."""
        self.action_value_edit.setText(value)

    def _on_value_text_edited(self, text: str) -> None:
        """User typed in the line edit. Mirror to the keyboard's
        highlighted keys so clicks-vs-typing stay consistent. No-op
        when the keyboard isn't visible (non keystroke/hotkey
        action), so other action kinds aren't affected."""
        if not self.action_value_keyboard.isVisible():
            return
        self.action_value_keyboard.set_value(text)

    # --- validation + accept --------------------------------------------

    def _on_start(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self._error("Please enter a gesture name.")
            return
        # Spaces are now allowed (e.g., "open chrome", "my wave"); the
        # registry stores the literal name so display reflects what
        # the user typed. The thumbnail filename is sanitised
        # separately in custom_gestures_recorder._save_thumbnail_to_disk.

        # Name-conflict check against the registry. In edit mode, the
        # gesture's own existing name is fine (no warning needed) — only
        # warn if the user changed it to collide with a DIFFERENT
        # gesture.
        registry = GestureRegistry()
        registry.load()
        existing = registry.get(name)
        unchanged_in_edit = (
            self._edit_mode
            and self._original_name is not None
            and name == self._original_name
        )
        if existing is not None and not unchanged_in_edit:
            answer = QMessageBox.warning(
                self,
                "Gesture name already exists",
                f"A gesture named '{name}' already exists.\n\n"
                f"Continuing will overwrite it. Or click Cancel to pick a "
                f"different name.",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Ok:
                return

        action_kind = self.action_combo.currentData()
        if action_kind is None:
            self._error("Please choose an action from the dropdown.")
            return
        action_value = self.action_value_edit.text().strip()
        if action_kind != "noop" and not action_value:
            self._error("Please fill in the action value.")
            return

        try:
            action = self._build_action(action_kind, action_value)
        except ValueError as exc:
            self._error(str(exc))
            return

        self.result_payload = WizardResult(
            name=name,
            description=self.desc_edit.text().strip(),
            hold_seconds=float(self.hold_spin.value()),
            cooldown_seconds=float(self.cooldown_spin.value()),
            action=action,
        )
        self.accept()

    def _build_action(self, kind: str, value: str) -> Action:
        # Both cooldown AND hold-to-activate are stored in the payload so
        # the live runner reads per-gesture timing back at run-time.
        # action.cooldown_seconds() already reads cooldown_s; the runner
        # reads hold_s.
        timing_payload = {
            "cooldown_s": float(self.cooldown_spin.value()),
            "hold_s": float(self.hold_spin.value()),
        }
        if kind == "keystroke":
            return Action(kind=kind, payload={"key": value, **timing_payload})
        if kind == "hotkey":
            keys = [k.strip() for k in value.split("+") if k.strip()]
            if not keys:
                raise ValueError("Hotkey combo must include at least one key.")
            return Action(kind=kind, payload={"keys": keys, **timing_payload})
        if kind == "text":
            return Action(kind=kind, payload={"text": value, **timing_payload})
        if kind == "open_url":
            if "://" not in value and not value.startswith("/"):
                value = "https://" + value
            return Action(kind=kind, payload={"url": value, **timing_payload})
        if kind == "run_command":
            return Action(kind=kind, payload={"command": value, "shell": True, **timing_payload})
        if kind == "open_file":
            # Strip wrapping quotes — Explorer's "Copy as path"
            # context-menu entry surrounds paths with double-quotes,
            # and pasting that straight in shouldn't break the
            # action. The executor strips them too as a defence in
            # depth, but normalising here keeps the stored payload
            # clean for display in Recent Actions / edit-mode.
            cleaned = value.strip().strip('"').strip("'")
            return Action(kind=kind, payload={"path": cleaned, **timing_payload})
        if kind == "show_overlay_drawing":
            # Strip the user's input and append .png if they didn't.
            # Resolution against the configured drawings_save_dir
            # happens at fire-time in main_window — the wizard just
            # stores the literal filename so the user can later move
            # the underlying file or rename the directory without
            # editing the gesture binding.
            filename = value
            if filename and not filename.lower().endswith((".png", ".jpg", ".jpeg")):
                filename = filename + ".png"
            return Action(kind=kind, payload={"filename": filename, **timing_payload})
        return Action(kind="noop", payload=timing_payload)

    def _error(self, message: str) -> None:
        QMessageBox.warning(self, "Cannot create gesture", message)

# Author: Konstantin Markov
