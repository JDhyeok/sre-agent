"""LLM model configuration for the SRE Agent system."""

from __future__ import annotations

from strands.models.anthropic import AnthropicModel

from sre_agent.config import AnthropicConfig


def create_model(config: AnthropicConfig) -> AnthropicModel:
    """Create an AnthropicModel instance with custom base_url support."""
    client_args: dict = {}

    if config.api_key:
        client_args["api_key"] = config.api_key
    if config.base_url:
        client_args["base_url"] = config.base_url

    return AnthropicModel(
        client_args=client_args,
        model_id=config.model_id,
        max_tokens=config.max_tokens,
    )
