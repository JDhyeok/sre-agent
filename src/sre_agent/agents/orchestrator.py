"""Orchestrator agent that coordinates specialist agents for incident analysis."""

from __future__ import annotations

from strands import Agent

from sre_agent.agents.elasticsearch import create_elasticsearch_agent
from sre_agent.agents.prometheus import create_prometheus_agent
from sre_agent.agents.rca import create_rca_agent
from sre_agent.agents.solution import create_solution_agent
from sre_agent.agents.ssh import create_ssh_agent
from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.orchestrator import build_system_prompt


def create_orchestrator(settings: Settings) -> Agent:
    """Create the orchestrator agent with all specialist agents as tools.

    The system prompt is dynamically built with environment context (configured
    hosts, URLs, etc.) so the orchestrator knows what resources are available
    and can ask the user targeted questions about missing information.
    """
    model = create_model(settings.anthropic)

    system_prompt = build_system_prompt(
        prometheus_url=settings.prometheus.url,
        alertmanager_url=settings.prometheus.alertmanager_url,
        baseline_hours=settings.prometheus.baseline_window_hours,
        elasticsearch_url=settings.elasticsearch.url,
        elasticsearch_index=settings.elasticsearch.default_index,
        ssh_hosts=[h.model_dump() for h in settings.ssh.hosts],
    )

    prometheus_agent = create_prometheus_agent(settings)
    elasticsearch_agent = create_elasticsearch_agent(settings)
    ssh_agent = create_ssh_agent(settings)
    rca_agent = create_rca_agent(settings)
    solution_agent = create_solution_agent(settings)

    tools = [
        prometheus_agent.as_tool(
            name="prometheus_agent",
            description=(
                "Queries Prometheus metrics and Alertmanager alerts. "
                "Use for error rates, latency percentiles, resource usage (CPU/memory/disk), "
                "active firing alerts, and scrape target health. "
                "Pass the incident context describing what to investigate."
            ),
        ),
        elasticsearch_agent.as_tool(
            name="elasticsearch_agent",
            description=(
                "Searches and analyzes application and infrastructure logs in Elasticsearch. "
                "Use for error log patterns, log frequency timelines, affected service identification, "
                "and field-level aggregations. "
                "Pass the incident context describing what to investigate."
            ),
        ),
        ssh_agent.as_tool(
            name="ssh_agent",
            description=(
                "Executes read-only diagnostic commands on target servers via SSH. "
                "Use for process inspection (ps, top), network state (ss, netstat), "
                "disk/memory checks (df, free), and service status (systemctl). "
                "Pass the incident context and specify which hosts to check."
            ),
        ),
        rca_agent.as_tool(
            name="rca_agent",
            description=(
                "Performs Root Cause Analysis on collected observability data. "
                "Has NO tools - performs pure reasoning only. "
                "MUST be called AFTER data collection agents. "
                "Pass ALL collected data from prometheus_agent, elasticsearch_agent, "
                "and ssh_agent as input."
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
    )
