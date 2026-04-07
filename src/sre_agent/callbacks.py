"""Callback handlers for displaying agent execution progress in the CLI."""

from __future__ import annotations

import time
from typing import Any, Callable

from rich.console import Console

_AGENT_LABELS: dict[str, str] = {
    "data_collector_agent": "Data Collector",
    "ssh_agent": "SSH Diagnostics",
    "rca_agent": "Root Cause Analysis",
    "solution_agent": "Solution",
}


class AgentProgressTracker:
    """Tracks and displays agent/tool execution progress to the console.

    Creates two callback functions:
    - ``orchestrator_callback``: shows which agent tools the orchestrator invokes
    - ``tool_callback``: shows which MCP tools sub-agents invoke (Prometheus, ES, …)
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._seen: set[str] = set()
        self._start: float = 0.0
        self._current_agent: str = ""

    def reset(self) -> None:
        self._seen.clear()
        self._start = time.time()
        self._current_agent = ""

    def _elapsed_tag(self) -> str:
        if not self._start:
            return ""
        return f" [dim]({time.time() - self._start:.1f}s)[/dim]"

    # -- orchestrator-level: agent tool calls ----------------------------------

    def orchestrator_callback(self, **kwargs: Any) -> None:
        if "current_tool_use" not in kwargs:
            return
        tool = kwargs["current_tool_use"]
        name = tool.get("name", "")
        tid = tool.get("toolUseId", "")
        if name and tid and tid not in self._seen:
            self._seen.add(tid)
            self._current_agent = name
            label = _AGENT_LABELS.get(name, name)
            self.console.print(
                f"  [bold cyan]→[/bold cyan] [bold]{label}[/bold]{self._elapsed_tag()}"
            )

    # -- sub-agent-level: MCP tool calls ---------------------------------------

    def tool_callback(self, **kwargs: Any) -> None:
        if "current_tool_use" not in kwargs:
            return
        tool = kwargs["current_tool_use"]
        name = tool.get("name", "")
        tid = tool.get("toolUseId", "")
        if name and tid and tid not in self._seen:
            self._seen.add(tid)
            self.console.print(f"    [dim]↳ {name}[/dim]")

    # -- convenience: get callbacks for agent creation -------------------------

    def get_orchestrator_handler(self) -> Callable[..., None]:
        return self.orchestrator_callback

    def get_tool_handler(self) -> Callable[..., None]:
        return self.tool_callback
