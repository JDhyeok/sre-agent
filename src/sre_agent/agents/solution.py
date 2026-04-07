"""Solution specialist agent for remediation recommendations."""

from __future__ import annotations

from typing import Any, Callable

from strands import Agent

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.solution import SYSTEM_PROMPT


def create_solution_agent(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create a Solution agent that suggests remediation actions.

    This agent has no tools - it reasons about RCA results to produce
    actionable remediation plans. Future versions may add KB search tools.
    """
    model = create_model(settings.anthropic)

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        callback_handler=callback_handler,
    )
