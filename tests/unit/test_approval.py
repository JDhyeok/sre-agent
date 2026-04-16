"""Tests for approval parsing functions: runbook extraction, section parsing."""

import pytest

from sre_agent.pipeline.approval import (
    _extract_visualization_json,
    _strip_visualization_block,
    _extract_runbook_name,
    _extract_section,
    _extract_what,
    _extract_why,
    _parse_no_match,
    _build_runbook_view,
)


# ---------------------------------------------------------------------------
# _extract_runbook_name
# ---------------------------------------------------------------------------

class TestExtractRunbookName:
    def test_english_key(self):
        report = "**Runbook**: memory-leak-restart\n**Script**: scripts/restart.sh"
        assert _extract_runbook_name(report) == "memory-leak-restart"

    def test_korean_key(self):
        report = "**런북**: memory-leak-restart\n**스크립트**: scripts/restart.sh"
        assert _extract_runbook_name(report) == "memory-leak-restart"

    def test_korean_spaced_key(self):
        report = "**런 북**: memory-leak-restart"
        assert _extract_runbook_name(report) == "memory-leak-restart"

    def test_name_with_dots_and_dashes(self):
        report = "**런북**: redis.restart-v2"
        assert _extract_runbook_name(report) == "redis.restart-v2"

    def test_no_runbook_returns_none(self):
        report = "No runbook matched for this incident."
        assert _extract_runbook_name(report) is None

    def test_match_found_report(self, sample_match_found_report):
        name = _extract_runbook_name(sample_match_found_report)
        assert name == "memory-leak-restart"


# ---------------------------------------------------------------------------
# _extract_section
# ---------------------------------------------------------------------------

class TestExtractSection:
    def test_extract_existing_section(self):
        report = "### My Section\nContent line 1\nContent line 2\n\n### Next Section\n"
        result = _extract_section(report, "My Section")
        assert "Content line 1" in result
        assert "Content line 2" in result

    def test_extract_nonexistent_section(self):
        report = "### Other Section\nSome content"
        result = _extract_section(report, "Missing Section")
        assert result == ""

    def test_section_at_end_of_report(self):
        report = "### Last Section\nFinal content here."
        result = _extract_section(report, "Last Section")
        assert "Final content" in result


# ---------------------------------------------------------------------------
# _extract_why / _extract_what
# ---------------------------------------------------------------------------

class TestExtractWhyWhat:
    def test_extract_why_english(self):
        report = "### Why this matches\nBecause memory is high.\n\n### What it will do\n"
        result = _extract_why(report)
        assert "memory is high" in result

    def test_extract_why_korean(self):
        report = "### 매칭 이유\n메모리가 높기 때문입니다.\n\n### 수행 작업\n"
        result = _extract_why(report)
        assert "메모리가 높기 때문" in result

    def test_extract_what_english(self):
        report = "### What it will do\nRestart the application.\n"
        result = _extract_what(report)
        assert "Restart" in result

    def test_extract_what_korean(self):
        report = "### 수행 작업\n애플리케이션을 재시작합니다.\n"
        result = _extract_what(report)
        assert "재시작" in result


# ---------------------------------------------------------------------------
# _parse_no_match
# ---------------------------------------------------------------------------

class TestParseNoMatch:
    def test_parse_no_match_with_reason(self):
        report = "**상태**: NO_MATCH\n**사유**: 적합한 런북이 없습니다.\n\n### 수동 대안\n1. 수동으로 재시작\n2. 스케일 업 검토"
        result = _parse_no_match(report)
        assert result["status"] == "no_match"
        assert "런북" in result["reason"]
        assert len(result["alternatives"]) == 2

    def test_parse_no_match_english_key(self):
        report = "**Reason**: No matching runbook.\n\n### Manual Alternatives\n1. Restart manually\n2. Check logs"
        result = _parse_no_match(report)
        assert result["status"] == "no_match"
        assert "runbook" in result["reason"].lower()
        assert len(result["alternatives"]) == 2

    def test_parse_no_match_max_three_alternatives(self):
        report = "**사유**: None.\n\n### 수동 대안\n1. Alt 1\n2. Alt 2\n3. Alt 3\n4. Alt 4"
        result = _parse_no_match(report)
        assert len(result["alternatives"]) <= 3

    def test_parse_no_match_no_alternatives(self):
        report = "**사유**: 이유 없음."
        result = _parse_no_match(report)
        assert result["alternatives"] == []


# ---------------------------------------------------------------------------
# _build_runbook_view
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _extract_metrics_json
# ---------------------------------------------------------------------------

class TestExtractVisualizationJson:
    def test_new_format_visualization_json(self):
        report = '```visualization_json\n{"charts": [{"label": "CPU", "data": [{"t": "2026-04-16T03:00:00Z", "v": 55.2}]}]}\n```\n'
        result = _extract_visualization_json(report)
        assert '"CPU"' in result
        assert "55.2" in result

    def test_legacy_metrics_json_normalized(self):
        report = '```metrics_json\n[{"label": "Memory", "data": [{"t": "2026-04-16T03:00:00Z", "v": 80.5}]}]\n```\n'
        result = _extract_visualization_json(report)
        # Legacy array format should be wrapped in {"charts": [...]}
        import json
        parsed = json.loads(result)
        assert "charts" in parsed
        assert parsed["charts"][0]["label"] == "Memory"

    def test_no_block(self):
        report = "Just a regular report without charts."
        assert _extract_visualization_json(report) == ""

    def test_invalid_json_returns_empty(self):
        report = '```visualization_json\n{not valid}\n```'
        assert _extract_visualization_json(report) == ""

    def test_full_visualization_data(self):
        report = '```visualization_json\n{"charts": [], "processes": [{"pid": 1, "name": "java", "cpu": 45.0, "mem": 30.0, "command": "java"}], "logs": {"total_errors": 10, "patterns": []}}\n```'
        result = _extract_visualization_json(report)
        import json
        parsed = json.loads(result)
        assert parsed["processes"][0]["name"] == "java"
        assert parsed["logs"]["total_errors"] == 10


class TestStripVisualizationBlock:
    def test_strips_section_with_json(self):
        report = "### 현재 상황\n내용\n\n### 시각화 데이터\n\n```visualization_json\n{\"charts\":[]}\n```\n\n### 런북 매칭 결과"
        result = _strip_visualization_block(report)
        assert "visualization_json" not in result
        assert "시각화 데이터" not in result
        assert "현재 상황" in result
        assert "런북 매칭 결과" in result

    def test_strips_multiline_json(self):
        report = (
            "### 수집 데이터 요약\n메트릭 내용\n\n"
            "### 시각화 데이터\n\n"
            "```visualization_json\n"
            "{\n"
            '  "charts": [\n'
            '    {"label": "CPU", "data": [{"t": "2026-04-16T03:00:00Z", "v": 55}]}\n'
            "  ]\n"
            "}\n"
            "```\n\n"
            "### 런북 매칭 결과\n매칭 내용"
        )
        result = _strip_visualization_block(report)
        assert "charts" not in result
        assert "visualization_json" not in result
        assert "수집 데이터 요약" in result
        assert "런북 매칭 결과" in result

    def test_strips_legacy_metrics_json(self):
        report = "내용\n```metrics_json\n[]\n```\n뒤 내용"
        result = _strip_visualization_block(report)
        assert "metrics_json" not in result
        assert "뒤 내용" in result

    def test_strips_with_spaces_around_tag(self):
        report = "앞\n``` visualization_json\n{}\n```\n뒤"
        result = _strip_visualization_block(report)
        assert "visualization_json" not in result

    def test_no_block_returns_unchanged(self):
        report = "### 현재 상황\n평범한 리포트"
        result = _strip_visualization_block(report)
        assert result == report

    def test_section_at_end_of_report(self):
        report = "### 현재 상황\n내용\n\n### 시각화 데이터\n```visualization_json\n{}\n```"
        result = _strip_visualization_block(report)
        assert "visualization_json" not in result
        assert "현재 상황" in result


# ---------------------------------------------------------------------------
# _build_runbook_view
# ---------------------------------------------------------------------------

class TestBuildRunbookView:
    def test_match_found(self, sample_match_found_report):
        view = _build_runbook_view(sample_match_found_report)
        assert view["status"] == "match"
        assert view["name"] == "memory-leak-restart"

    def test_no_match(self, sample_no_match_report):
        view = _build_runbook_view(sample_no_match_report)
        assert view["status"] == "no_match"

    def test_empty_report(self):
        view = _build_runbook_view("")
        assert view["status"] == "none"

    def test_report_without_runbook_section(self):
        view = _build_runbook_view("## Just a regular report\nNo runbook section.")
        assert view["status"] == "none"
