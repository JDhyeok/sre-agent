"""Tests for callback utility functions."""

from sre_agent.callbacks import _format_tool_detail


class TestFormatToolDetail:
    def test_query_instant(self):
        result = _format_tool_detail("query_instant", {"query": "up"})
        assert "query=up" in result

    def test_query_range(self):
        result = _format_tool_detail("query_range", {"query": "rate(http_total[5m])"})
        assert "query=rate(http_total[5m])" in result

    def test_batch_query(self):
        result = _format_tool_detail("batch_query", {"queries": '[{"query":"up"}]'})
        assert "queries=" in result

    def test_search_logs(self):
        result = _format_tool_detail("search_logs", {
            "query": "error", "service": "web-app", "log_level": "error"
        })
        assert "query=error" in result
        assert "service=web-app" in result
        assert "level=error" in result

    def test_get_error_patterns(self):
        result = _format_tool_detail("get_error_patterns", {"service": "api"})
        assert "service=api" in result

    def test_ssh_diagnostic_tool(self):
        result = _format_tool_detail("get_memory_info", {"hostname": "app-server-1"})
        assert "host=app-server-1" in result

    def test_service_status(self):
        result = _format_tool_detail("get_service_status", {
            "hostname": "host1", "service": "nginx"
        })
        assert "host=host1" in result
        assert "service=nginx" in result

    def test_exec_command(self):
        result = _format_tool_detail("exec_command", {
            "hostname": "host1", "command": "ps -ef"
        })
        assert "host=host1" in result
        assert "cmd=ps -ef" in result

    def test_apm_objects(self):
        result = _format_tool_detail("get_apm_objects", {})
        assert result == ""

    def test_apm_xlog(self):
        result = _format_tool_detail("get_xlog_data", {"object_id": "abc123"})
        assert "object_id=abc123" in result

    def test_batch_apm(self):
        result = _format_tool_detail("batch_apm_query", {"queries": "[{}]"})
        assert "queries=" in result

    def test_unknown_tool(self):
        result = _format_tool_detail("unknown_tool", {"foo": "bar"})
        assert result == ""

    def test_empty_input(self):
        result = _format_tool_detail("query_instant", {})
        assert result == ""

    def test_none_input(self):
        result = _format_tool_detail("query_instant", None)
        assert result == ""
