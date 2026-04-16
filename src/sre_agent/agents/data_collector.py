"""Data Collector agent — unified metrics, logs, topology, and system diagnostics.

Replaces the separate Prometheus and Elasticsearch agents with a single agent
that has access to Prometheus MCP, Elasticsearch MCP, ServiceNow CMDB MCP,
and SSH Diagnostic MCP. The agent decides which tools to call based on the
incident context and follows a top-down, layer-by-layer investigation strategy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.data_collector import build_system_prompt

_MCP_DIR = Path(__file__).resolve().parent.parent / "mcp_servers"
_PROMETHEUS_SCRIPT = str(_MCP_DIR / "prometheus_server.py")
_ELASTICSEARCH_SCRIPT = str(_MCP_DIR / "elasticsearch_server.py")
_CMDB_SCRIPT = str(_MCP_DIR / "servicenow_cmdb_server.py")
_SSH_DIAGNOSTIC_SCRIPT = str(_MCP_DIR / "ssh_diagnostic_server.py")
_APM_SCRIPT = str(_MCP_DIR / "apm_server.py")

_FASTMCP_QUIET: dict[str, str] = {
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "FASTMCP_LOG_ENABLED": "false",
}


def create_data_collector_agent(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create a Data Collector agent backed by Prometheus, ES, CMDB, and SSH Diagnostic MCP servers."""
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.data_collector)
    max_tool_calls = settings.agent_tokens.data_collector_max_tool_calls
    has_ssh_hosts = bool(settings.ssh.hosts)
    has_apm = bool(settings.hmg_apm.url)
    system_prompt = build_system_prompt(
        max_tool_calls=max_tool_calls, ssh_enabled=has_ssh_hosts, apm_enabled=has_apm,
    )

    prometheus_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_PROMETHEUS_SCRIPT],
            env={
                **_FASTMCP_QUIET,
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
                **_FASTMCP_QUIET,
                "ELASTICSEARCH_URL": settings.elasticsearch.url,
                "ELASTICSEARCH_DEFAULT_INDEX": settings.elasticsearch.default_index,
                "ELASTICSEARCH_MAX_RESULTS": str(settings.elasticsearch.max_results),
            },
        )
    ))

    cmdb_env: dict[str, str] = {**_FASTMCP_QUIET}
    if settings.servicenow.instance_url:
        cmdb_env["SERVICENOW_INSTANCE_URL"] = settings.servicenow.instance_url

    cmdb_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_CMDB_SCRIPT],
            env=cmdb_env,
        )
    ))

    tools: list = [prometheus_mcp, elasticsearch_mcp, cmdb_mcp]

    if has_ssh_hosts:
        hosts_json = json.dumps([h.model_dump() for h in settings.ssh.hosts])
        ssh_diag_mcp = MCPClient(lambda: stdio_client(
            StdioServerParameters(
                command=sys.executable,
                args=[_SSH_DIAGNOSTIC_SCRIPT],
                env={
                    **_FASTMCP_QUIET,
                    "SSH_CONFIG_JSON": hosts_json,
                    "SSH_TIMEOUT": str(settings.ssh.timeout_seconds),
                },
            )
        ))
        tools.append(ssh_diag_mcp)

    if has_apm:
        apm_mcp = MCPClient(lambda: stdio_client(
            StdioServerParameters(
                command=sys.executable,
                args=[_APM_SCRIPT],
                env={
                    **_FASTMCP_QUIET,
                    "HMG_APM_URL": settings.hmg_apm.url,
                    "HMG_APM_API_KEY": settings.hmg_apm.api_key,
                    "HMG_APM_TIMEOUT": str(settings.hmg_apm.timeout_seconds),
                },
            )
        ))
        tools.append(apm_mcp)

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        callback_handler=callback_handler,
    )
