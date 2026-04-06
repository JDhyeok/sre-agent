"""Elasticsearch specialist agent for log search and analysis."""

from __future__ import annotations

import sys
from pathlib import Path

from mcp import stdio_client, StdioServerParameters
from strands import Agent
from strands.tools.mcp import MCPClient

from sre_agent.config import Settings
from sre_agent.model import create_model
from sre_agent.prompts.elasticsearch import SYSTEM_PROMPT

_SERVER_SCRIPT = str(Path(__file__).resolve().parent.parent / "mcp_servers" / "elasticsearch_server.py")


def create_elasticsearch_agent(settings: Settings) -> Agent:
    """Create an Elasticsearch agent backed by the Elasticsearch MCP server."""
    model = create_model(settings.anthropic)

    mcp_client = MCPClient(lambda: stdio_client(
        StdioServerParameters(
            command=sys.executable,
            args=[_SERVER_SCRIPT],
            env={
                "ELASTICSEARCH_URL": settings.elasticsearch.url,
                "ELASTICSEARCH_DEFAULT_INDEX": settings.elasticsearch.default_index,
                "ELASTICSEARCH_MAX_RESULTS": str(settings.elasticsearch.max_results),
            },
        )
    ))

    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[mcp_client],
    )
