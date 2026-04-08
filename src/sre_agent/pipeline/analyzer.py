"""Analyzer module — KB search + Orchestrator invocation with severity-based analysis levels.

Wraps the existing multi-agent orchestrator and adds:
- Pre-analysis KB search for similar past incidents
- Severity-based analysis depth control
- Post-analysis KB storage
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

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
    similar_incidents: list[dict[str, Any]]
    status: str = "completed"  # completed | failed | skipped
    error: str = ""


class PipelineAnalyzer:
    """Runs analysis on an IncidentRequest using the appropriate depth level."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._orchestrator = None
        self._orchestrator_lightweight = None

    def _get_orchestrator(self):
        """Lazy-init the full orchestrator (expensive — MCP subprocesses)."""
        if self._orchestrator is None:
            from sre_agent.agents.orchestrator import create_orchestrator
            self._orchestrator = create_orchestrator(self._settings)
        return self._orchestrator

    def _get_kb(self):
        from sre_agent.integrations.knowledge_base import IncidentKB
        return IncidentKB()

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

        kb = self._get_kb()
        similar = kb.search(context, top_k=3)
        kb_context = self._format_kb_context(similar)

        try:
            if request.analysis_level == AnalysisLevel.LIGHTWEIGHT:
                result = self._run_lightweight(context, kb_context)
            else:
                result = self._run_full(context, kb_context)
        except Exception as e:
            logger.exception("Analysis failed for %s", request.incident_id)
            return AnalysisResult(
                incident_id=request.incident_id,
                report="",
                analysis_level=request.analysis_level,
                elapsed_seconds=time.time() - start,
                similar_incidents=similar,
                status="failed",
                error=str(e),
            )

        elapsed = time.time() - start
        report = str(result)

        self._store_to_kb(kb, request, report)

        logger.info("Analysis completed for %s in %.1fs", request.incident_id, elapsed)
        return AnalysisResult(
            incident_id=request.incident_id,
            report=report,
            analysis_level=request.analysis_level,
            elapsed_seconds=elapsed,
            similar_incidents=similar,
        )

    def _run_full(self, context: str, kb_context: str):
        """Full analysis with all agents."""
        orchestrator = self._get_orchestrator()
        prompt = self._build_prompt(context, kb_context, full=True)
        return orchestrator(prompt)

    def _run_lightweight(self, context: str, kb_context: str):
        """Lightweight: quick data check. Escalates to full if anomalies found."""
        orchestrator = self._get_orchestrator()
        prompt = self._build_prompt(context, kb_context, full=False)
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
            similar_incidents=[],
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
            similar_incidents=[],
            status="skipped",
        )

    @staticmethod
    def _build_prompt(context: str, kb_context: str, *, full: bool) -> str:
        parts = []
        if full:
            parts.append(
                "Investigate the following incident and produce a complete RCA report. "
                "Use all available agents (Data Collector, SSH if needed, RCA, Solution)."
            )
        else:
            parts.append(
                "Perform a quick health check for the following alert. "
                "Use ONLY the Data Collector agent for a rapid assessment. "
                "If you detect anomalies that warrant deeper investigation, "
                "proceed with full RCA. Otherwise, provide a brief status summary."
            )

        if kb_context:
            parts.append(f"\n\n## Similar Past Incidents\n{kb_context}")

        parts.append(f"\n\n## Current Incident\n{context}")
        return "\n".join(parts)

    @staticmethod
    def _format_kb_context(similar: list[dict[str, Any]]) -> str:
        if not similar:
            return ""
        lines = []
        for inc in similar[:3]:
            inc_id = inc.get("id", "unknown")
            summary = inc.get("incident_summary", inc.get("summary", ""))
            root_cause = inc.get("primary_root_cause", "")
            lines.append(f"- **{inc_id}**: {summary}")
            if root_cause:
                lines.append(f"  Root cause: {root_cause}")
        return "\n".join(lines)

    @staticmethod
    def _store_to_kb(kb, request: IncidentRequest, report: str) -> None:
        try:
            kb.store({
                "incident_context": request.format_context(),
                "incident_summary": f"{request.primary_alertname} ({request.primary_severity})",
                "analysis_report": report,
            })
        except Exception:
            logger.exception("Failed to store incident %s to KB", request.incident_id)
