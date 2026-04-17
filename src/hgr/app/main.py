from __future__ import annotations

import sys
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..config.app_config import APP_NAME, load_config, save_config
from ..utils.runtime_paths import resource_path
from .ui.main_window import MainWindow
from .ui.touchless_splash import TouchlessSplash


def _resolve_app_icon():
    candidates = (
        resource_path('assets', 'icons', 'hgr_icon.ico'),
        resource_path('assets', 'icons', 'hgr_icon.png'),
    )
    return next((path for path in candidates if path.exists()), None)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationName(APP_NAME)

    icon_path = _resolve_app_icon()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    config = load_config()
    save_config(config)

    def _build_window() -> MainWindow:
        w = MainWindow(config)
        if icon_path is not None:
            w.setWindowIcon(QIcon(str(icon_path)))
        return w

    TouchlessSplash.run_with(_build_window, config.accent_color, app)
    return app.exec()
