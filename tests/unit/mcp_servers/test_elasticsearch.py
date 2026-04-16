"""Tests for Elasticsearch MCP server internal functions."""

from sre_agent.mcp_servers.elasticsearch_server import (
    _templatize_message,
    _summarize_patterns,
)


class TestTemplatizeMessage:
    def test_uuid_replacement(self):
        msg = "Request 550e8400-e29b-41d4-a716-446655440000 failed"
        result = _templatize_message(msg)
        assert "<UUID>" in result
        assert "550e8400" not in result

    def test_ip_replacement(self):
        msg = "Connection to 192.168.1.100 refused"
        result = _templatize_message(msg)
        assert "<IP>" in result
        assert "192.168.1.100" not in result

    def test_timestamp_replacement(self):
        msg = "Error at 2026-04-16T03:00:00Z in module"
        result = _templatize_message(msg)
        assert "<TIMESTAMP>" in result

    def test_epoch_replacement(self):
        msg = "Event 1776300000000 processed"
        result = _templatize_message(msg)
        assert "<EPOCH>" in result

    def test_hex_replacement(self):
        msg = "Memory address 0xDEADBEEF corrupted"
        result = _templatize_message(msg)
        assert "<HEX>" in result

    def test_number_replacement(self):
        msg = "Timeout after timeout= 30000 ms"
        result = _templatize_message(msg)
        assert "<NUM>" in result

    def test_preserves_normal_text(self):
        msg = "Connection refused by server"
        result = _templatize_message(msg)
        assert result == "Connection refused by server"

    def test_multiple_replacements(self):
        msg = "Host 10.0.1.5 request 550e8400-e29b-41d4-a716-446655440000 timeout= 5000"
        result = _templatize_message(msg)
        assert "<IP>" in result
        assert "<UUID>" in result
        assert "<NUM>" in result


class TestSummarizePatterns:
    def test_no_patterns(self):
        result = _summarize_patterns([], 0)
        assert "No error patterns" in result

    def test_with_patterns(self):
        patterns = [
            {"template": "Connection refused by <IP>", "percentage": 65.0, "count": 13},
            {"template": "Timeout after <NUM>", "percentage": 35.0, "count": 7},
        ]
        result = _summarize_patterns(patterns, 20)
        assert "2 unique error patterns" in result
        assert "65.0%" in result
        assert "Connection refused" in result
