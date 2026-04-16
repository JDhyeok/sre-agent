"""Phase B Orchestrator — on-demand RCA + solution.

Only runs when user clicks "RCA 진행". Receives collected data from Phase A.
"""

from __future__ import annotations

from typing import Any, Callable

from strands import Agent

from sre_agent.agents.rca import create_rca_agent
from sre_agent.agents.solution import create_solution_agent
from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.phase_b import SYSTEM_PROMPT


def create_phase_b_orchestrator(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
    tool_callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create the Phase B orchestrator with RCA + solution agents."""
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.orchestrator)

    rca_agent = create_rca_agent(settings, callback_handler=tool_callback_handler)
    solution_agent = create_solution_agent(settings, callback_handler=tool_callback_handler)

    tools = [
        rca_agent.as_tool(
            name="rca_agent",
            description=(
                "Performs Root Cause Analysis using a 5-Phase Framework: "
                "Triage → Timeline → Correlation → Root Cause (5 Whys) → Verification. "
                "Has NO tools — performs pure reasoning only. "
                "Pass ALL collected data from Phase A."
            ),
        ),
        solution_agent.as_tool(
            name="solution_agent",
            description=(
                "Suggests 1-3 remediation actions based on RCA results. "
                "MUST be called AFTER rca_agent. "
                "Pass the complete RCA report as input."
            ),
        ),
    ]

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        callback_handler=callback_handler,
    )
