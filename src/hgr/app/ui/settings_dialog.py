from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...config.app_config import AppConfig


class ColorButton(QPushButton):
    color_changed = Signal(str)

    def __init__(self, label: str, color: str):
        super().__init__(label)
        self._color = color
        self.clicked.connect(self._pick_color)
        self._refresh_style()

    @property
    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        self._color = color
        self._refresh_style()

    def _pick_color(self) -> None:
        color = QColorDialog.getColor()
        if color.isValid():
            self._color = color.name()
            self._refresh_style()
            self.color_changed.emit(self._color)

    def _refresh_style(self) -> None:
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {self._color};
                color: white;
                border: 1px solid rgba(255,255,255,0.25);
                border-radius: 12px;
                padding: 10px 12px;
                font-weight: 700;
            }}
            """
        )


class SettingsDialog(QDialog):
    settings_applied = Signal(object)

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HGR Settings")
        self.setModal(False)
        self.setMinimumWidth(420)
        self.config = AppConfig(**config.__dict__)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        title = QLabel("HGR Settings")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #E5F6FF;")
        root.addWidget(title)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(14)

        self.primary_button = ColorButton("Primary", self.config.primary_color)
        self.primary_button.color_changed.connect(lambda c: setattr(self.config, "primary_color", c))
        form.addRow("Main color", self.primary_button)

        self.accent_button = ColorButton("Accent", self.config.accent_color)
        self.accent_button.color_changed.connect(lambda c: setattr(self.config, "accent_color", c))
        form.addRow("Accent color", self.accent_button)

        self.surface_button = ColorButton("Surface", self.config.surface_color)
        self.surface_button.color_changed.connect(lambda c: setattr(self.config, "surface_color", c))
        form.addRow("Surface color", self.surface_button)

        self.text_button = ColorButton("Text", self.config.text_color)
        self.text_button.color_changed.connect(lambda c: setattr(self.config, "text_color", c))
        form.addRow("Text color", self.text_button)

        self.font_slider = QSlider(Qt.Horizontal)
        self.font_slider.setMinimum(42)
        self.font_slider.setMaximum(140)
        self.font_slider.setValue(self.config.hello_font_size)
        self.font_value = QLabel(str(self.config.hello_font_size))
        self.font_value.setStyleSheet("font-weight: 700; color: #E5F6FF;")
        self.font_slider.valueChanged.connect(self._font_size_changed)
        font_row = QWidget()
        font_layout = QHBoxLayout(font_row)
        font_layout.setContentsMargins(0, 0, 0, 0)
        font_layout.addWidget(self.font_slider)
        font_layout.addWidget(self.font_value)
        form.addRow("HELLO size", font_row)

        root.addWidget(form_widget)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self._apply)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(apply_button)
        button_row.addWidget(close_button)
        root.addLayout(button_row)

        self.setStyleSheet(
            """
            QDialog {
                background-color: #0F172A;
                color: #E5F6FF;
                border: 1px solid rgba(29, 233, 182, 0.35);
            }
            QLabel {
                color: #E5F6FF;
                font-size: 14px;
            }
            QPushButton {
                background-color: #0B3D91;
                color: #E5F6FF;
                border: 1px solid rgba(29,233,182,0.35);
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 700;
            }
            QPushButton:hover {
                border: 1px solid #1DE9B6;
            }
            """
        )

    def _font_size_changed(self, value: int) -> None:
        self.config.hello_font_size = value
        self.font_value.setText(str(value))

    def _apply(self) -> None:
        self.settings_applied.emit(self.config)
