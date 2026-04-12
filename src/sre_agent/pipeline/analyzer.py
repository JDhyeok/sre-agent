"""Analyzer module — Orchestrator invocation with severity-based analysis levels."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sre_agent.config import Settings
from sre_agent.pipeline.intake import AnalysisLevel, IncidentRequest

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Output of the analyzer pipeline stage."""

    incident_id: str
    report: str
    analysis_level: AnalysisLevel
    elapsed_seconds: float
    status: str = "completed"  # completed | failed | skipped
    error: str = ""


class PipelineAnalyzer:
    """Runs analysis on an IncidentRequest using the appropriate depth level."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._orchestrator = None
        from sre_agent.callbacks import LoggingProgressTracker
        self._tracker = LoggingProgressTracker(logger)

    def _get_orchestrator(self):
        """Lazy-init the full orchestrator (expensive — MCP subprocesses)."""
        if self._orchestrator is None:
            from sre_agent.agents.orchestrator import create_orchestrator
            self._orchestrator = create_orchestrator(
                self._settings,
                callback_handler=self._tracker.get_orchestrator_handler(),
                tool_callback_handler=self._tracker.get_tool_handler(),
            )
        return self._orchestrator

    def analyze(self, request: IncidentRequest) -> AnalysisResult:
        """Run the appropriate analysis based on the incident's analysis level."""
        logger.info(
            "Analyzing %s (level=%s, alerts=%d)",
            request.incident_id, request.analysis_level.value, len(request.alerts),
        )

        if request.analysis_level == AnalysisLevel.LOG_ONLY:
            return self._handle_log_only(request)

        if request.analysis_level == AnalysisLevel.SUMMARY_ONLY:
            return self._handle_summary_only(request)

        start = time.time()
        context = request.format_context()

        self._tracker.set_prefix(f"[{request.incident_id}] ")

        try:
            if request.analysis_level == AnalysisLevel.LIGHTWEIGHT:
                result = self._run_lightweight(context)
            else:
                result = self._run_full(context)
        except Exception as e:
            logger.exception("Analysis failed for %s", request.incident_id)
            return AnalysisResult(
                incident_id=request.incident_id,
                report="",
                analysis_level=request.analysis_level,
                elapsed_seconds=time.time() - start,
                status="failed",
                error=str(e),
            )

        elapsed = time.time() - start
        report = str(result)

        logger.info("Analysis completed for %s in %.1fs", request.incident_id, elapsed)
        return AnalysisResult(
            incident_id=request.incident_id,
            report=report,
            analysis_level=request.analysis_level,
            elapsed_seconds=elapsed,
        )

    def _run_full(self, context: str):
        """Full analysis with all agents."""
        orchestrator = self._get_orchestrator()
        prompt = self._build_prompt(context, full=True)
        return orchestrator(prompt)

    def _run_lightweight(self, context: str):
        """Lightweight: quick data check. Escalates to full if anomalies found."""
        orchestrator = self._get_orchestrator()
        prompt = self._build_prompt(context, full=False)
        return orchestrator(prompt)

    def _handle_log_only(self, request: IncidentRequest) -> AnalysisResult:
        report = (
            f"## Log Only — {request.primary_alertname}\n\n"
            f"Severity: {request.primary_severity} (info)\n"
            f"Alerts: {len(request.alerts)}\n\n"
            "No analysis performed (info-level alert). Logged for reference."
        )
        logger.info("Log-only for %s: %s", request.incident_id, request.primary_alertname)
        return AnalysisResult(
            incident_id=request.incident_id,
            report=report,
            analysis_level=AnalysisLevel.LOG_ONLY,
            elapsed_seconds=0.0,
            status="skipped",
        )

    def _handle_summary_only(self, request: IncidentRequest) -> AnalysisResult:
        alert = request.alerts[0] if request.alerts else None
        duration = ""
        if alert and alert.starts_at and alert.ends_at:
            duration = f"{alert.starts_at} ~ {alert.ends_at}"

        report = (
            f"## Resolved — {request.primary_alertname}\n\n"
            f"Alert has been resolved. {f'Duration: {duration}' if duration else ''}\n"
            "No further analysis required."
        )
        logger.info("Summary-only for %s: %s (resolved)", request.incident_id, request.primary_alertname)
        return AnalysisResult(
            incident_id=request.incident_id,
            report=report,
            analysis_level=AnalysisLevel.SUMMARY_ONLY,
            elapsed_seconds=0.0,
            status="skipped",
        )

    @staticmethod
    def _build_prompt(context: str, *, full: bool) -> str:
        parts = []
        if full:
            parts.append(
                "Investigate the following incident and produce a complete RCA report. "
                "Follow the full investigation workflow: data_collector_agent → "
                "(ssh_agent if needed) → rca_agent → solution_agent → "
                "**runbook_matcher_agent**. "
                "You MUST call runbook_matcher_agent after solution_agent and "
                "include its verbatim output (the '## Runbook Match' block with "
                "either MATCH_FOUND or NO_MATCH) in your final report under the "
                "'### 자동 조치' section. Do NOT paraphrase the matcher's output."
            )
        else:
            parts.append(
                "Perform a quick health check for the following alert. "
                "Use ONLY the Data Collector agent for a rapid assessment. "
                "If you detect anomalies that warrant deeper investigation, "
                "proceed with full RCA AND call runbook_matcher_agent at the end."
            )

        parts.append(f"\n\n## Current Incident\n{context}")
        return "\n".join(parts)
