"""RCA (Root Cause Analysis) specialist agent - pure reasoning, no tools."""

from __future__ import annotations

from typing import Any, Callable

from strands import Agent

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.rca import SYSTEM_PROMPT


def create_rca_agent(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create an RCA agent that performs pure reasoning on collected data.

    This agent has no tools - it only analyzes data passed to it
    and produces structured root cause analysis output.
    """
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.rca)

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[],
        callback_handler=callback_handler,
    )
