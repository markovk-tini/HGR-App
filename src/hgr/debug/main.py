from __future__ import annotations

import sys
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ..config.app_config import APP_NAME, load_config, save_config
from ..utils.runtime_paths import resource_path
from .debug_window import DebugWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(f'{APP_NAME} Debug')
    app.setApplicationName(f'{APP_NAME} Debug')

    icon_path = resource_path('assets', 'icons', 'touchless_icon.png')
    if not icon_path.exists():
        icon_path = resource_path('assets', 'icons', 'hgr_icon.png')
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    config = load_config()
    save_config(config)
    window = DebugWindow(config)
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()
    return app.exec()
