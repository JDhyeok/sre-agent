"""Orchestrator agent that coordinates specialist agents for incident analysis."""

from __future__ import annotations

from typing import Any, Callable

from strands import Agent

from sre_agent.agents.data_collector import create_data_collector_agent
from sre_agent.agents.rca import create_rca_agent
from sre_agent.agents.solution import create_solution_agent
from sre_agent.agents.ssh import create_ssh_agent
from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.orchestrator import build_system_prompt


def create_orchestrator(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
    tool_callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create the orchestrator agent with all specialist agents as tools.

    Args:
        settings: Application settings.
        callback_handler: Callback for the orchestrator itself (shows agent-level tool calls).
        tool_callback_handler: Callback forwarded to sub-agents (shows MCP-level tool calls).
    """
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.orchestrator)

    system_prompt = build_system_prompt(
        prometheus_url=settings.prometheus.url,
        alertmanager_url=settings.prometheus.alertmanager_url,
        baseline_hours=settings.prometheus.baseline_window_hours,
        elasticsearch_url=settings.elasticsearch.url,
        elasticsearch_index=settings.elasticsearch.default_index,
        servicenow_url=settings.servicenow.instance_url,
        ssh_hosts=[h.model_dump() for h in settings.ssh.hosts],
    )

    data_collector_agent = create_data_collector_agent(
        settings, callback_handler=tool_callback_handler,
    )
    ssh_agent = create_ssh_agent(settings, callback_handler=tool_callback_handler)
    rca_agent = create_rca_agent(settings, callback_handler=tool_callback_handler)
    solution_agent = create_solution_agent(settings, callback_handler=tool_callback_handler)

    tools = [
        data_collector_agent.as_tool(
            name="data_collector_agent",
            description=(
                "Unified observability data investigator with access to Prometheus "
                "(metrics, alerts), Elasticsearch (logs), and ServiceNow CMDB "
                "(topology, dependencies). Performs top-down layer-by-layer "
                "investigation: L1 symptom → L2 service → L3 application → "
                "L4 dependency → L5 infrastructure → L6 platform. "
                "Pass the full incident context and it autonomously decides "
                "which data sources and layers to investigate."
            ),
        ),
        ssh_agent.as_tool(
            name="ssh_agent",
            description=(
                "Executes read-only diagnostic commands on target servers via SSH. "
                "Use for L5/L6 deep-dive: process inspection (ps, top), network "
                "state (ss, netstat), disk/memory checks (df, free), and service "
                "status (systemctl). Only call when data_collector_agent findings "
                "suggest a need for live server diagnostics. "
                "Pass the incident context and specify which hosts to check."
            ),
        ),
        rca_agent.as_tool(
            name="rca_agent",
            description=(
                "Performs Root Cause Analysis using a 5-Phase Framework: "
                "Triage → Timeline → Correlation → Root Cause (5 Whys) → Verification. "
                "Has NO tools — performs pure reasoning only. "
                "MUST be called AFTER data collection agents. "
                "Pass ALL collected data from data_collector_agent and ssh_agent."
            ),
        ),
        solution_agent.as_tool(
            name="solution_agent",
            description=(
                "Suggests remediation actions based on RCA results. "
                "MUST be called AFTER rca_agent. "
                "Pass the complete RCA report as input. "
                "Returns immediate actions, short-term fixes, and long-term recommendations."
            ),
        ),
    ]

    return Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        callback_handler=callback_handler,
    )
