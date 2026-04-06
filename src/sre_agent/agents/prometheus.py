"""Prometheus specialist agent for metrics collection and analysis."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp import stdio_client, StdioServerParameters
from strands import Agent
from strands.tools.mcp import MCPClient

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.prometheus import SYSTEM_PROMPT

_SERVER_SCRIPT = str(Path(__file__).resolve().parent.parent / "mcp_servers" / "prometheus_server.py")


def create_prometheus_agent(settings: Settings) -> Agent:
    """Create a Prometheus agent backed by the Prometheus MCP server."""
    model = create_model(settings.anthropic)

    mcp_client = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_SERVER_SCRIPT],
            env={
                "PROMETHEUS_URL": settings.prometheus.url,
                "ALERTMANAGER_URL": settings.prometheus.alertmanager_url,
                "PROMETHEUS_BASELINE_HOURS": str(settings.prometheus.baseline_window_hours),
            },
        )
    ))

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[mcp_client],
    )
