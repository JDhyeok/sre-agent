"""Operator Agent — matches remediation recommendations to AWX Job Templates."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.operator import SYSTEM_PROMPT

_MCP_DIR = Path(__file__).resolve().parent.parent / "mcp_servers"
_AWX_SCRIPT = str(_MCP_DIR / "awx_server.py")

_FASTMCP_QUIET: dict[str, str] = {
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "FASTMCP_LOG_ENABLED": "false",
}


def create_operator_agent(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create an Operator agent backed by the AWX MCP server.

    The Operator only uses list_job_templates and get_template_detail
    for matching — it never launches jobs directly.
    """
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.solution)

    awx_env: dict[str, str] = {**_FASTMCP_QUIET}
    if settings.awx.url:
        awx_env["AWX_URL"] = settings.awx.url
    if settings.awx.token:
        awx_env["AWX_TOKEN"] = settings.awx.token

    awx_mcp = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_AWX_SCRIPT],
            env=awx_env,
        )
    ))

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[awx_mcp],
        callback_handler=callback_handler,
    )
