"""Configuration management for the SRE Agent system."""

from __future__ import annotations

import importlib.resources
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


def _bundled_config_path(filename: str) -> Path:
    """Resolve the path to a config file bundled inside the package."""
    return Path(importlib.resources.files("sre_agent.defaults").joinpath(filename))


class AnthropicConfig(BaseModel):
    base_url: str = "https://api.anthropic.com"
    api_key: str = ""
    model_id: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096


class PrometheusConfig(BaseModel):
    url: str = "http://localhost:9090"
    alertmanager_url: str = "http://localhost:9093"
    default_step: str = "60s"
    baseline_window_hours: int = 24


class ElasticsearchConfig(BaseModel):
    url: str = "http://localhost:9200"
    default_index: str = "app-logs-*"
    max_results: int = 500


class SSHHostConfig(BaseModel):
    name: str
    hostname: str
    port: int = 22
    username: str = "sre-readonly"
    key_path: str = "~/.ssh/sre_readonly_key"


class SSHConfig(BaseModel):
    timeout_seconds: int = 10
    hosts: list[SSHHostConfig] = Field(default_factory=list)


class ServiceNowConfig(BaseModel):
    instance_url: str = ""


class MCPTransportConfig(BaseModel):
    transport: str = "stdio"


class MCPServersConfig(BaseModel):
    prometheus: MCPTransportConfig = Field(default_factory=MCPTransportConfig)
    elasticsearch: MCPTransportConfig = Field(default_factory=MCPTransportConfig)
    ssh: MCPTransportConfig = Field(default_factory=MCPTransportConfig)
    servicenow_cmdb: MCPTransportConfig = Field(default_factory=MCPTransportConfig)


class Settings(BaseSettings):
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)
    elasticsearch: ElasticsearchConfig = Field(default_factory=ElasticsearchConfig)
    ssh: SSHConfig = Field(default_factory=SSHConfig)
    servicenow: ServiceNowConfig = Field(default_factory=ServiceNowConfig)
    mcp_servers: MCPServersConfig = Field(default_factory=MCPServersConfig)

    model_config = {"env_prefix": "SRE_AGENT_", "env_nested_delimiter": "__"}


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from YAML config file, overlaid with environment variables."""
    data: dict[str, Any] = {}

    if config_path is None:
        candidates = [
            Path(os.environ.get("SRE_AGENT_CONFIG", "")),
            Path("configs/settings.yaml"),
            Path("configs/settings.yml"),
            Path.home() / ".config" / "sre-agent" / "settings.yaml",
            _bundled_config_path("settings.yaml"),
        ]
        for candidate in candidates:
            try:
                if candidate.exists():
                    config_path = candidate
                    break
            except (OSError, TypeError):
                continue

    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    anthropic_data = data.setdefault("anthropic", {})

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        anthropic_data["api_key"] = api_key

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if base_url:
        anthropic_data["base_url"] = base_url

    model_id = os.environ.get("ANTHROPIC_MODEL_ID", "")
    if model_id:
        anthropic_data["model_id"] = model_id

    return Settings(**data)
