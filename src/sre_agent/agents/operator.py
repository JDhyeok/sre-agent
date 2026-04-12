"""Runbook Matcher Agent — matches remediation recommendations to runbooks."""

from __future__ import annotations

from typing import Any, Callable

from strands import Agent

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.operator import RUNBOOK_MATCHER_PROMPT
from sre_agent.tools.runbook import get_runbook, list_runbooks


def create_runbook_matcher_agent(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create the Runbook Matcher agent backed by in-process runbook tools.

    The agent
    only inspects runbooks (list/get) — actual script execution happens in
    the approval pipeline after a human approves.
    """
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.solution)

    return Agent(
        model=model,
        system_prompt=RUNBOOK_MATCHER_PROMPT,
        tools=[list_runbooks, get_runbook],
        callback_handler=callback_handler,
    )
