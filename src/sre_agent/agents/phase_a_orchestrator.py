"""Phase A Orchestrator — data collection + runbook matching.

Runs automatically on every incident. Does NOT perform RCA or solution analysis.
"""

from __future__ import annotations

from typing import Any, Callable

from strands import Agent

from sre_agent.agents.data_collector import create_data_collector_agent
from sre_agent.agents.operator import create_runbook_matcher_agent
from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.phase_a import build_system_prompt


def create_phase_a_orchestrator(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
    tool_callback_handler: Callable[..., Any] | None = None,
) -> tuple[Agent, dict[str, Any]]:
    """Create the Phase A orchestrator with data collector + runbook matcher.

    Returns:
        (agent, match_result) — *match_result* is a mutable dict that the
        ``report_match`` tool inside runbook_matcher_agent writes to.
        Read it after the orchestrator finishes to get structured runbook
        match data (no regex parsing needed).
    """
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.orchestrator)

    system_prompt = build_system_prompt(
        prometheus_url=settings.prometheus.url,
        alertmanager_url=settings.prometheus.alertmanager_url,
        elasticsearch_url=settings.elasticsearch.url,
        elasticsearch_index=settings.elasticsearch.default_index,
        servicenow_url=settings.servicenow.instance_url,
        ssh_hosts=[h.model_dump() for h in settings.ssh.hosts],
    )

    data_collector_agent = create_data_collector_agent(
        settings, callback_handler=tool_callback_handler,
    )
    runbook_matcher_agent, match_result = create_runbook_matcher_agent(
        settings, callback_handler=tool_callback_handler,
    )

    tools = [
        data_collector_agent.as_tool(
            name="data_collector_agent",
            description=(
                "Unified observability data investigator with access to Prometheus "
                "(metrics, alerts), Elasticsearch (logs), ServiceNow CMDB "
                "(topology, dependencies), SSH diagnostics (hardcoded read-only "
                "commands), and HMG-APM (application performance). "
                "Pass the full incident context and it autonomously decides "
                "which data sources to query. Returns collected data summary."
            ),
        ),
        runbook_matcher_agent.as_tool(
            name="runbook_matcher_agent",
            description=(
                "Matches collected observability data and incident symptoms to "
                "a single Markdown runbook. Call AFTER data_collector_agent. "
                "Pass the complete data collection output. "
                "Returns MATCH_FOUND with runbook details, or NO_MATCH "
                "with manual alternatives."
            ),
        ),
    ]

    orchestrator = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        callback_handler=callback_handler,
    )
    return orchestrator, match_result
