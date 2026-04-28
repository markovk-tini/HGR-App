"""Mapping from tool name -> python callable for the Live API session.

This is intentionally a thin wrapper over `ToolExecutor` so the
schemas (`schemas.py`) and the implementation (`tool_executor.py`) can
evolve independently. The registry knows which tool names are
risky-by-default and helps build the system prompt that lists all
tools to the model.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

from .schemas import all_tool_schemas


# Tools that always require a confirmation overlay before executing.
# (`ask_user_confirmation` itself is the dialog; it can't gate itself.)
RISKY_TOOLS = {
    "press_hotkey",  # alt+f4, ctrl+w, etc. — shown but not auto-blocked
}

# Tools that are read-only and never need confirmation.
READ_ONLY_TOOLS = {
    "get_screen_context",
    "ask_user_confirmation",
}


class ToolRegistry:
    def __init__(self, executor: "Any") -> None:
        # `executor` is `tool_executor.ToolExecutor`. Typed as Any to avoid
        # a circular import — registry is imported by the executor too.
        self._executor = executor

    def openai_tools(self) -> List[Dict[str, Any]]:
        return all_tool_schemas()

    def names(self) -> List[str]:
        return [s["name"] for s in all_tool_schemas()]

    def call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._executor.execute(name, args)

    def callable_for(self, name: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
        return lambda args: self.call(name, args)
