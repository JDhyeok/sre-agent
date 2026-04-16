"""Runbook Matcher Agent — matches remediation recommendations to runbooks."""

from __future__ import annotations

from typing import Any, Callable

from strands import Agent

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.operator import RUNBOOK_MATCHER_PROMPT
from sre_agent.tools.runbook import create_match_reporter, get_runbook, list_runbooks


def create_runbook_matcher_agent(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
) -> tuple[Agent, dict[str, Any]]:
    """Create the Runbook Matcher agent backed by in-process runbook tools.

    Returns:
        (agent, match_result) — *match_result* is a mutable dict populated by
        the ``report_match`` tool when the agent runs.  The caller reads this
        dict after the agent finishes to get structured match data without
        parsing LLM text output.
    """
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.solution)
    report_match_tool, match_result = create_match_reporter()

    agent = Agent(
        model=model,
        system_prompt=RUNBOOK_MATCHER_PROMPT,
        tools=[list_runbooks, get_runbook, report_match_tool],
        callback_handler=callback_handler,
    )
    return agent, match_result
