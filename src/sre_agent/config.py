"""Configuration management for the SRE Agent system."""

from __future__ import annotations

import importlib.resources
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

USER_CONFIG_DIR: Path = Path.home() / ".config" / "sre-agent"
USER_CONFIG_PATH: Path = USER_CONFIG_DIR / "settings.yaml"


def _bundled_config_path(filename: str) -> Path:
    """Resolve the path to a config file bundled inside the package."""
    return Path(importlib.resources.files("sre_agent.defaults").joinpath(filename))


class AnthropicConfig(BaseModel):
    base_url: str = "https://api.anthropic.com"
    api_key: str = ""
    model_id: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096


class AgentTokenLimits(BaseModel):
    """Per-agent max_tokens overrides. Falls back to anthropic.max_tokens if 0."""

    orchestrator: int = 8192
    data_collector: int = 4096
    data_collector_max_tool_calls: int = 6
    ssh: int = 2048
    rca: int = 8192
    solution: int = 4096


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


class IntakeConfig(BaseModel):
    dedup_window_minutes: int = 5
    group_window_seconds: int = 60
    severity_routing: dict[str, str] = Field(default_factory=lambda: {
        "critical": "full_analysis",
        "warning": "lightweight",
        "info": "log_only",
        "resolved": "summary_only",
    })


class DeliveryConfig(BaseModel):
    teams_webhook_url: str = ""
    # Public base URL of the pipeline server (e.g. https://sre-agent.example.com).
    # Used to render the approval / detail links in Teams cards. When empty,
    # links are still emitted to logs but no Teams card buttons are added.
    public_base_url: str = ""


class MCPTransportConfig(BaseModel):
    transport: str = "stdio"


class MCPServersConfig(BaseModel):
    prometheus: MCPTransportConfig = Field(default_factory=MCPTransportConfig)
    elasticsearch: MCPTransportConfig = Field(default_factory=MCPTransportConfig)
    ssh: MCPTransportConfig = Field(default_factory=MCPTransportConfig)
    servicenow_cmdb: MCPTransportConfig = Field(default_factory=MCPTransportConfig)


class Settings(BaseSettings):
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    agent_tokens: AgentTokenLimits = Field(default_factory=AgentTokenLimits)
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)
    elasticsearch: ElasticsearchConfig = Field(default_factory=ElasticsearchConfig)
    ssh: SSHConfig = Field(default_factory=SSHConfig)
    servicenow: ServiceNowConfig = Field(default_factory=ServiceNowConfig)
    intake: IntakeConfig = Field(default_factory=IntakeConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    mcp_servers: MCPServersConfig = Field(default_factory=MCPServersConfig)

    model_config = {"env_prefix": "SRE_AGENT_", "env_nested_delimiter": "__"}


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from YAML config file, overlaid with environment variables."""
    data: dict[str, Any] = {}

    if config_path is None:
        env_config = os.environ.get("SRE_AGENT_CONFIG", "")
        candidates: list[Path] = []
        if env_config:
            candidates.append(Path(env_config))
        candidates.extend([
            Path("configs/settings.yaml"),
            Path("configs/settings.yml"),
            Path.home() / ".config" / "sre-agent" / "settings.yaml",
            _bundled_config_path("settings.yaml"),
        ])
        for candidate in candidates:
            try:
                if candidate.is_file():
                    config_path = candidate
                    break
            except (OSError, TypeError):
                continue

    if config_path and Path(config_path).is_file():
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
