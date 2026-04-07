"""Data Collector agent — unified metrics, logs, and topology investigator.

Replaces the separate Prometheus and Elasticsearch agents with a single agent
that has access to Prometheus MCP, Elasticsearch MCP, and ServiceNow CMDB MCP.
The agent decides which tools to call based on the incident context and
follows a top-down, layer-by-layer investigation strategy.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.data_collector import SYSTEM_PROMPT

_MCP_DIR = Path(__file__).resolve().parent.parent / "mcp_servers"
_PROMETHEUS_SCRIPT = str(_MCP_DIR / "prometheus_server.py")
_ELASTICSEARCH_SCRIPT = str(_MCP_DIR / "elasticsearch_server.py")
_CMDB_SCRIPT = str(_MCP_DIR / "servicenow_cmdb_server.py")


def create_data_collector_agent(settings: Settings) -> Agent:
    """Create a Data Collector agent backed by Prometheus, ES, and CMDB MCP servers."""
    model = create_model(settings.anthropic)

    prometheus_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_PROMETHEUS_SCRIPT],
            env={
                "PROMETHEUS_URL": settings.prometheus.url,
                "ALERTMANAGER_URL": settings.prometheus.alertmanager_url,
                "PROMETHEUS_BASELINE_HOURS": str(settings.prometheus.baseline_window_hours),
            },
        )
    ))

    elasticsearch_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_ELASTICSEARCH_SCRIPT],
            env={
                "ELASTICSEARCH_URL": settings.elasticsearch.url,
                "ELASTICSEARCH_DEFAULT_INDEX": settings.elasticsearch.default_index,
                "ELASTICSEARCH_MAX_RESULTS": str(settings.elasticsearch.max_results),
            },
        )
    ))

    cmdb_env: dict[str, str] = {}
    if settings.servicenow.instance_url:
        cmdb_env["SERVICENOW_INSTANCE_URL"] = settings.servicenow.instance_url

    cmdb_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_CMDB_SCRIPT],
            env=cmdb_env,
        )
    ))

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[prometheus_mcp, elasticsearch_mcp, cmdb_mcp],
    )
