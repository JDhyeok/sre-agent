"""Tests for Prometheus MCP server internal functions."""

import json
from unittest.mock import patch, MagicMock

import pytest

from sre_agent.mcp_servers.prometheus_server import (
    _classify_severity,
    _interpret_deviation,
    _do_instant_query,
    _do_range_query,
)


# ---------------------------------------------------------------------------
# _classify_severity
# ---------------------------------------------------------------------------

class TestClassifySeverity:
    def test_critical_above_200(self):
        assert _classify_severity(250.0) == "critical"

    def test_critical_negative_above_200(self):
        assert _classify_severity(-210.0) == "critical"

    def test_warning_above_100(self):
        assert _classify_severity(150.0) == "warning"

    def test_warning_negative_above_100(self):
        assert _classify_severity(-120.0) == "warning"

    def test_info_above_50(self):
        assert _classify_severity(75.0) == "info"

    def test_normal_below_50(self):
        assert _classify_severity(30.0) == "normal"

    def test_zero_deviation(self):
        assert _classify_severity(0.0) == "normal"

    def test_boundary_200(self):
        assert _classify_severity(200.0) == "warning"

    def test_boundary_201(self):
        assert _classify_severity(200.1) == "critical"

    def test_boundary_100(self):
        assert _classify_severity(100.0) == "info"

    def test_boundary_50(self):
        assert _classify_severity(50.0) == "normal"


# ---------------------------------------------------------------------------
# _interpret_deviation
# ---------------------------------------------------------------------------

class TestInterpretDeviation:
    def test_critical_increase(self):
        result = _interpret_deviation("cpu_usage", 250.0, "critical", 3.5, 1.0)
        assert "CRITICAL" in result
        assert "increased" in result

    def test_warning_decrease(self):
        result = _interpret_deviation("requests", -120.0, "warning", 0.5, 1.0)
        assert "WARNING" in result
        assert "decreased" in result

    def test_info_deviation(self):
        result = _interpret_deviation("latency", 75.0, "info", 1.75, 1.0)
        assert "INFO" in result

    def test_normal_deviation(self):
        result = _interpret_deviation("cpu", 10.0, "normal", 1.1, 1.0)
        assert "NORMAL" in result


# ---------------------------------------------------------------------------
# _do_instant_query (with mocked HTTP)
# ---------------------------------------------------------------------------

class TestDoInstantQuery:
    @patch("sre_agent.mcp_servers.prometheus_server._prom_query")
    def test_successful_query(self, mock_prom):
        mock_prom.return_value = {
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "up", "instance": "host1:9100"},
                        "value": [1776300000, "1"],
                    }
                ]
            }
        }
        result = json.loads(_do_instant_query("up"))
        assert result["status"] == "success"
        assert result["result_count"] == 1
        assert result["results"][0]["value"] == "1"

    @patch("sre_agent.mcp_servers.prometheus_server._prom_query")
    def test_empty_result(self, mock_prom):
        mock_prom.return_value = {"data": {"result": []}}
        result = json.loads(_do_instant_query("nonexistent_metric"))
        assert result["status"] == "success"
        assert result["result_count"] == 0

    @patch("sre_agent.mcp_servers.prometheus_server._prom_query")
    def test_http_error(self, mock_prom):
        import httpx
        mock_prom.side_effect = httpx.HTTPError("Connection refused")
        result = json.loads(_do_instant_query("up"))
        assert result["status"] == "error"
        assert "Connection refused" in result["error"]


# ---------------------------------------------------------------------------
# _do_range_query (with mocked HTTP)
# ---------------------------------------------------------------------------

class TestDoRangeQuery:
    @patch("sre_agent.mcp_servers.prometheus_server._prom_query")
    def test_range_query_with_baseline(self, mock_prom):
        current_response = {
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "cpu_usage"},
                        "values": [[1776300000, "50"], [1776300060, "55"]],
                    }
                ]
            }
        }
        baseline_response = {
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "cpu_usage"},
                        "values": [[1776200000, "45"], [1776200060, "48"]],
                    }
                ]
            }
        }
        mock_prom.side_effect = [current_response, baseline_response]

        result = json.loads(_do_range_query("cpu_usage", duration_minutes=60))
        assert result["status"] == "success"
        assert len(result["results"]) == 1
        assert "current_average" in result["results"][0]
        assert "baseline_median" in result["results"][0]
        assert "deviation_percent" in result["results"][0]
        assert "severity" in result["results"][0]
