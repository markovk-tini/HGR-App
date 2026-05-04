"""Experimental Live API computer-control agent for Touchless.

This is a prototype subsystem. It is intentionally isolated from the
existing gesture, voice, drawing, overlay, and tutorial pipelines so
nothing here can disturb production behaviour. Only the main window
imports `LiveApiManager` to wire up the "Test Live API" button.

The subsystem connects to the OpenAI Realtime API over a WebSocket,
streams microphone audio + periodic screen context, receives tool
calls, and dispatches them through the existing Touchless controllers
(text input, mouse, chrome, system actions, ...) via
`ToolExecutor`. See `live_api_manager.LiveApiManager` for the
orchestrator entry point.
"""

from .config import LiveApiConfig, load_config
from .live_api_manager import LiveApiManager, LiveApiState

__all__ = [
    "LiveApiConfig",
    "load_config",
    "LiveApiManager",
    "LiveApiState",
]

# Note: LocalBackend and RealtimeClient are intentionally NOT exported
# from the package root — they are internal swap-ins managed by
# LiveApiManager based on `LiveApiConfig.backend`. UI code should never
# instantiate them directly.

# Author: Konstantin Markov
