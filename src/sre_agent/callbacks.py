"""Callback handlers for displaying agent execution progress in the CLI and pipeline server."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from rich.console import Console

def _format_tool_detail(tool_name: str, tool_input: dict) -> str:
    """Format a compact summary of tool arguments for logging."""
    if not tool_input:
        return ""

    # Prometheus tools
    if tool_name in ("query_instant", "query_range"):
        q = tool_input.get("query", "")
        return f"  query={q}" if q else ""
    if tool_name == "batch_query":
        q = tool_input.get("queries", "")
        return f"  queries={q[:200]}" if q else ""

    # Elasticsearch tools
    if tool_name == "search_logs":
        parts = []
        if tool_input.get("query"):
            parts.append(f"query={tool_input['query']}")
        if tool_input.get("service"):
            parts.append(f"service={tool_input['service']}")
        if tool_input.get("log_level"):
            parts.append(f"level={tool_input['log_level']}")
        return f"  {' '.join(parts)}" if parts else ""
    if tool_name == "get_error_patterns":
        svc = tool_input.get("service", "")
        return f"  service={svc}" if svc else ""
    if tool_name == "batch_search":
        q = tool_input.get("queries", "")
        return f"  queries={q[:200]}" if q else ""

    # SSH diagnostic tools
    if tool_name in ("get_processes", "get_top_cpu_processes", "get_top_memory_processes",
                      "get_network_connections", "get_listening_ports", "get_memory_info",
                      "get_disk_usage", "get_system_load", "get_vmstat", "get_dmesg"):
        h = tool_input.get("hostname", "")
        return f"  host={h}" if h else ""
    if tool_name in ("get_service_status", "get_service_logs"):
        h = tool_input.get("hostname", "")
        s = tool_input.get("service", "")
        return f"  host={h} service={s}"

    # APM tools
    if tool_name == "get_apm_objects":
        return ""
    if tool_name in ("get_active_services", "get_xlog_data", "get_thread_dump"):
        obj = tool_input.get("object_id", "")
        return f"  object_id={obj}" if obj else ""
    if tool_name == "batch_apm_query":
        q = tool_input.get("queries", "")
        return f"  queries={q[:200]}" if q else ""

    # SSH exec (ssh_agent)
    if tool_name == "exec_command":
        h = tool_input.get("hostname", "")
        c = tool_input.get("command", "")
        return f"  host={h} cmd={c}"

    return ""


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
            detail = _format_tool_detail(name, tool.get("input", {}))
            self.console.print(f"    [dim]↳ {name}[/dim]{detail}")

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
            detail = _format_tool_detail(name, tool.get("input", {}))
            self._logger.info("%s    ↳ %s%s", self._prefix, name, detail)

    def get_orchestrator_handler(self) -> Callable[..., None]:
        return self._orchestrator_callback

    def get_tool_handler(self) -> Callable[..., None]:
        return self._tool_callback
