"""Callback handlers for displaying agent execution progress in the CLI and pipeline server."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from rich.console import Console

_AGENT_LABELS: dict[str, str] = {
    "data_collector_agent": "Data Collector",
    "ssh_agent": "SSH Diagnostics",
    "rca_agent": "Root Cause Analysis",
    "solution_agent": "Solution",
    "runbook_matcher_agent": "Runbook Matcher",
}


class AgentProgressTracker:
    """Tracks and displays agent/tool execution progress to the console.

    Displays start → tool calls → completion for each sub-agent:

        → Data Collector
          ↳ query_instant
          ↳ get_active_alerts
        ✓ Data Collector (12.3s)
        → Root Cause Analysis
        ✓ Root Cause Analysis (8.1s)
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._seen: set[str] = set()
        self._global_start: float = 0.0
        self._current_agent: str = ""
        self._agent_start: float = 0.0

    def reset(self) -> None:
        self._seen.clear()
        self._global_start = time.time()
        self._current_agent = ""
        self._agent_start = 0.0

    def _close_current_agent(self) -> None:
        """Print completion marker for the currently running agent."""
        if not self._current_agent:
            return
        elapsed = time.time() - self._agent_start
        label = _AGENT_LABELS.get(self._current_agent, self._current_agent)
        self.console.print(
            f"  [bold green]✓[/bold green] [bold]{label}[/bold] [dim]({elapsed:.1f}s)[/dim]"
        )
        self._current_agent = ""

    def finish(self) -> None:
        """Close the last agent's progress line. Call after orchestrator returns."""
        self._close_current_agent()

    # -- orchestrator-level: agent tool calls ----------------------------------

    def orchestrator_callback(self, **kwargs: Any) -> None:
        if "current_tool_use" not in kwargs:
            return
        tool = kwargs["current_tool_use"]
        name = tool.get("name", "")
        tid = tool.get("toolUseId", "")
        if name and tid and tid not in self._seen:
            self._seen.add(tid)
            self._close_current_agent()
            self._current_agent = name
            self._agent_start = time.time()
            label = _AGENT_LABELS.get(name, name)
            self.console.print(
                f"  [bold cyan]→[/bold cyan] [bold]{label}[/bold]"
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


# ---------------------------------------------------------------------------
# Logger-based progress tracker for the pipeline server (no Rich console)
# ---------------------------------------------------------------------------


class LoggingProgressTracker:
    """Same protocol as ``AgentProgressTracker`` but emits to a Python logger.

    Used by the pipeline server so the runbook_matcher_agent / data_collector
    invocations are visible in server logs without depending on Rich.
    """

    def __init__(self, logger: logging.Logger, prefix: str = "") -> None:
        self._logger = logger
        self._prefix = prefix
        self._seen: set[str] = set()

    def set_prefix(self, prefix: str) -> None:
        self._prefix = prefix
        self._seen.clear()

    def _orchestrator_callback(self, **kwargs: Any) -> None:
        if "current_tool_use" not in kwargs:
            return
        tool = kwargs["current_tool_use"]
        name = tool.get("name", "")
        tid = tool.get("toolUseId", "")
        if name and tid and tid not in self._seen:
            self._seen.add(tid)
            label = _AGENT_LABELS.get(name, name)
            self._logger.info("%s→ %s", self._prefix, label)

    def _tool_callback(self, **kwargs: Any) -> None:
        if "current_tool_use" not in kwargs:
            return
        tool = kwargs["current_tool_use"]
        name = tool.get("name", "")
        tid = tool.get("toolUseId", "")
        if name and tid and tid not in self._seen:
            self._seen.add(tid)
            self._logger.info("%s    ↳ %s", self._prefix, name)

    def get_orchestrator_handler(self) -> Callable[..., None]:
        return self._orchestrator_callback

    def get_tool_handler(self) -> Callable[..., None]:
        return self._tool_callback
