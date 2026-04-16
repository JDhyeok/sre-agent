"""Tests for configuration loading and validation."""

import os
import pytest

from sre_agent.config import (
    AnthropicConfig,
    AgentTokenLimits,
    HmgApmConfig,
    Settings,
    load_settings,
)


class TestDefaultConfig:
    def test_anthropic_defaults(self):
        cfg = AnthropicConfig()
        assert cfg.base_url == "https://api.anthropic.com"
        assert cfg.max_tokens == 4096

    def test_agent_token_limits(self):
        limits = AgentTokenLimits()
        assert limits.orchestrator == 8192
        assert limits.data_collector == 4096
        assert limits.data_collector_max_tool_calls == 6
        assert limits.ssh == 2048
        assert limits.rca == 8192
        assert limits.solution == 4096

    def test_hmg_apm_defaults(self):
        cfg = HmgApmConfig()
        assert cfg.url == ""
        assert cfg.api_key == ""
        assert cfg.timeout_seconds == 30


class TestSettingsModel:
    def test_settings_has_all_sections(self):
        s = Settings()
        assert s.anthropic is not None
        assert s.prometheus is not None
        assert s.elasticsearch is not None
        assert s.ssh is not None
        assert s.servicenow is not None
        assert s.hmg_apm is not None
        assert s.intake is not None
        assert s.delivery is not None

    def test_hmg_apm_in_settings(self):
        s = Settings()
        assert s.hmg_apm.url == ""
        assert s.hmg_apm.timeout_seconds == 30


class TestLoadSettings:
    def test_load_bundled_defaults(self, tmp_path):
        # Load from bundled defaults when no config file exists
        s = load_settings(config_path=tmp_path / "nonexistent.yaml")
        assert s.anthropic.max_tokens == 4096

    def test_env_var_override_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        s = load_settings()
        assert s.anthropic.api_key == "test-key-123"

    def test_env_var_override_model_id(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_MODEL_ID", "claude-opus-4-6")
        s = load_settings()
        assert s.anthropic.model_id == "claude-opus-4-6"

    def test_yaml_config_loading(self, tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(
            "anthropic:\n  max_tokens: 8192\nhmg_apm:\n  url: http://apm:6180\n"
        )
        s = load_settings(config_path=config_file)
        assert s.anthropic.max_tokens == 8192
        assert s.hmg_apm.url == "http://apm:6180"

    def test_severity_routing_defaults(self):
        s = Settings()
        routing = s.intake.severity_routing
        assert routing["critical"] == "full_analysis"
        assert routing["warning"] == "lightweight"
        assert routing["info"] == "log_only"
        assert routing["resolved"] == "summary_only"
