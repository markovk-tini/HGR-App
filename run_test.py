from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hgr.config.app_config import APP_NAME, load_config, save_config
from hgr.gesture.ui.test_window import GestureTestWindow
from hgr.utils.runtime_paths import resource_path


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationDisplayName(f"{APP_NAME} Gesture Test")
    app.setApplicationName(f"{APP_NAME} Gesture Test")

    icon_path = resource_path("assets", "icons", "hgr_icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    config = load_config()
    save_config(config)

    window = GestureTestWindow(config)
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
