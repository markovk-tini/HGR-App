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

from PySide6.QtCore import Qt
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
    ("Show a saved drawing as overlay", "show_overlay_drawing", "Drawing filename (in your drawings folder)", "e.g. Touchless_Drawing_001.png"),
)


@dataclass(frozen=True)
class WizardResult:
    name: str
    description: str
    hold_seconds: float
    cooldown_seconds: float
    action: Action


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
        self.setMinimumWidth(520)
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
        root.addWidget(self.action_value_edit)

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

    def _refresh_action_value(self) -> None:
        kind = self.action_combo.currentData()
        if kind is None:
            # Placeholder ("Choose an action") selected — keep value row
            # hidden and the Start button disabled.
            self.action_value_label.hide()
            self.action_value_edit.hide()
            self.action_value_edit.setText("")
            self._start_button.setEnabled(False)
            return
        # Look up the matching prompt + placeholder for the chosen kind.
        for _label, k, prompt, placeholder in _ACTION_KINDS:
            if k == kind:
                self.action_value_label.setText(prompt)
                self.action_value_edit.setPlaceholderText(placeholder)
                self.action_value_edit.setText("")
                self.action_value_label.show()
                self.action_value_edit.show()
                break
        self._start_button.setEnabled(True)

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
