"""LLM model configuration for the SRE Agent system."""

from __future__ import annotations

from strands.models.anthropic import AnthropicModel

from sre_agent.config import AnthropicConfig


def create_model(config: AnthropicConfig, *, max_tokens: int | None = None) -> AnthropicModel:
    """Create an AnthropicModel instance with custom base_url support.

    Args:
        config: Base Anthropic configuration (api_key, base_url, model_id).
        max_tokens: Override max_tokens for this specific agent.
                    Falls back to config.max_tokens if None or 0.
    """
    client_args: dict = {}

    if config.api_key:
        client_args["api_key"] = config.api_key
    if config.base_url:
        client_args["base_url"] = config.base_url

    effective_max_tokens = max_tokens if max_tokens else config.max_tokens

    return AnthropicModel(
        client_args=client_args,
        model_id=config.model_id,
        max_tokens=effective_max_tokens,
    )
