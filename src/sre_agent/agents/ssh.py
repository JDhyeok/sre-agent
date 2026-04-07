"""SSH specialist agent for read-only remote system diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from mcp import stdio_client, StdioServerParameters
from strands import Agent
from strands.tools.mcp import MCPClient

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.ssh import SYSTEM_PROMPT

_SERVER_SCRIPT = str(Path(__file__).resolve().parent.parent / "mcp_servers" / "ssh_server.py")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_ALLOWLIST = str(_PROJECT_ROOT / "configs" / "ssh_allowlist.yaml")

_FASTMCP_QUIET: dict[str, str] = {
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "FASTMCP_LOG_ENABLED": "false",
}


def create_ssh_agent(
    settings: Settings,
    *,
    callback_handler: Callable[..., Any] | None = None,
) -> Agent:
    """Create an SSH agent backed by the SSH MCP server."""
    model = create_model(settings.anthropic, max_tokens=settings.agent_tokens.ssh)

    hosts_json = json.dumps([h.model_dump() for h in settings.ssh.hosts])

    mcp_client = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_SERVER_SCRIPT],
            env={
                **_FASTMCP_QUIET,
                "SSH_CONFIG_JSON": hosts_json,
                "SSH_TIMEOUT": str(settings.ssh.timeout_seconds),
                "SSH_ALLOWLIST_PATH": _DEFAULT_ALLOWLIST,
            },
        )
    ))

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[mcp_client],
        callback_handler=callback_handler,
    )
