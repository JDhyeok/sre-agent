"""Tests for the analyzer module — skip handlers and prompt building."""

import pytest

from sre_agent.pipeline.analyzer import AnalysisResult, PipelineAnalyzer
from sre_agent.pipeline.intake import AlertItem, AnalysisLevel, IncidentRequest


def _make_request(level: AnalysisLevel, alertname: str = "TestAlert") -> IncidentRequest:
    alert = AlertItem(
        alertname=alertname,
        severity="critical",
        status="firing",
        labels={"service": "test"},
        annotations={},
    )
    return IncidentRequest(
        incident_id="INC-TEST-001",
        alerts=[alert],
        analysis_level=level,
        group_key="grp-test",
    )


class TestAnalysisResult:
    def test_default_status(self):
        r = AnalysisResult(
            incident_id="INC-001",
            report="test",
            analysis_level=AnalysisLevel.FULL_ANALYSIS,
            elapsed_seconds=1.0,
        )
        assert r.status == "completed"
        assert r.collected_data == ""

    def test_collected_data_field(self):
        r = AnalysisResult(
            incident_id="INC-001",
            report="test",
            analysis_level=AnalysisLevel.FULL_ANALYSIS,
            elapsed_seconds=1.0,
            collected_data="some collected data",
        )
        assert r.collected_data == "some collected data"


class TestAnalyzerSkipHandlers:
    """Test the skip handlers that don't require LLM calls."""

    def _make_analyzer(self):
        from unittest.mock import MagicMock
        from sre_agent.callbacks import LoggingProgressTracker
        import logging
        analyzer = PipelineAnalyzer.__new__(PipelineAnalyzer)
        analyzer._settings = MagicMock()
        analyzer._phase_a = None
        analyzer._phase_b = None
        analyzer._tracker = LoggingProgressTracker(logging.getLogger("test"))
        return analyzer

    def test_handle_log_only(self):
        analyzer = self._make_analyzer()
        req = _make_request(AnalysisLevel.LOG_ONLY, "InfoAlert")
        result = analyzer.analyze_phase_a(req)
        assert result.status == "skipped"
        assert "Log Only" in result.report
        assert "InfoAlert" in result.report

    def test_handle_summary_only(self):
        analyzer = self._make_analyzer()
        alert = AlertItem(
            alertname="ResolvedAlert",
            severity="info",
            status="resolved",
            labels={},
            annotations={},
            starts_at="2026-04-16T01:00:00Z",
            ends_at="2026-04-16T02:00:00Z",
        )
        req = IncidentRequest(
            incident_id="INC-002",
            alerts=[alert],
            analysis_level=AnalysisLevel.SUMMARY_ONLY,
            group_key="grp-test",
        )
        result = analyzer.analyze_phase_a(req)
        assert result.status == "skipped"
        assert "Resolved" in result.report


class TestPromptBuilders:
    def test_phase_a_prompt(self):
        prompt = PipelineAnalyzer._build_phase_a_prompt("Alert: HighCPU on host1")
        assert "HighCPU" in prompt
        assert "data_collector_agent" in prompt
        assert "runbook_matcher_agent" in prompt

    def test_phase_b_prompt(self):
        prompt = PipelineAnalyzer._build_phase_b_prompt("Collected: CPU=95%, Memory=60%")
        assert "CPU=95%" in prompt
        assert "rca_agent" in prompt
        assert "solution_agent" in prompt
