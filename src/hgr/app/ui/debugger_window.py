from __future__ import annotations

from .live_view_window import LiveViewWindow


class DebuggerWindow(LiveViewWindow):
    def show_expanded(self) -> None:
        self.show_window()

    def show_compact(self) -> None:
        self.hide()
