"""Analyzer module — Two-phase pipeline: Phase A (auto) + Phase B (on-demand)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
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
    status: str = "completed"  # completed | failed | skipped
    error: str = ""
    collected_data: str = ""  # Raw data collection output for Phase B reuse
    runbook_match: dict[str, Any] = field(default_factory=dict)  # Structured match data from report_match tool


class PipelineAnalyzer:
    """Two-phase analyzer: Phase A (data + runbook) runs automatically,
    Phase B (RCA + solution) runs on demand."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._phase_a = None
        self._phase_b = None
        self._match_result: dict[str, Any] = {}  # Side-channel for runbook match data
        from sre_agent.callbacks import LoggingProgressTracker
        self._tracker = LoggingProgressTracker(logger)

    def _get_phase_a(self):
        """Lazy-init Phase A orchestrator (data collector + runbook matcher)."""
        if self._phase_a is None:
            from sre_agent.agents.phase_a_orchestrator import create_phase_a_orchestrator
            self._phase_a, self._match_result = create_phase_a_orchestrator(
                self._settings,
                callback_handler=self._tracker.get_orchestrator_handler(),
                tool_callback_handler=self._tracker.get_tool_handler(),
            )
        return self._phase_a

    def _get_phase_b(self):
        """Lazy-init Phase B orchestrator (RCA + solution). Only created on demand."""
        if self._phase_b is None:
            from sre_agent.agents.phase_b_orchestrator import create_phase_b_orchestrator
            self._phase_b = create_phase_b_orchestrator(
                self._settings,
                callback_handler=self._tracker.get_orchestrator_handler(),
                tool_callback_handler=self._tracker.get_tool_handler(),
            )
        return self._phase_b

    # -- Phase A: automatic data collection + runbook matching -----------------

    def analyze_phase_a(self, request: IncidentRequest) -> AnalysisResult:
        """Phase A: collect observability data and match runbooks.

        This runs automatically for every incident. Does NOT perform RCA.
        """
        logger.info(
            "Phase A: analyzing %s (level=%s, alerts=%d)",
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
            orchestrator = self._get_phase_a()
            prompt = self._build_phase_a_prompt(context)
            result = orchestrator(prompt)
        except Exception as e:
            logger.exception("Phase A failed for %s", request.incident_id)
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

        # Read structured runbook match data from the side-channel
        # (populated by the report_match tool inside runbook_matcher_agent).
        runbook_match = dict(self._match_result)  # snapshot
        # Reset for next invocation (the container is reused across calls).
        self._match_result.update(
            {"matched": False, "name": "", "risk": "", "script": "", "target_host_label": ""}
        )
        logger.info(
            "Phase A completed for %s in %.1fs (runbook_matched=%s)",
            request.incident_id, elapsed, runbook_match.get("matched", False),
        )

        return AnalysisResult(
            incident_id=request.incident_id,
            report=report,
            analysis_level=request.analysis_level,
            elapsed_seconds=elapsed,
            collected_data=report,  # Preserve for Phase B
            runbook_match=runbook_match,
        )

    # -- Phase B: on-demand RCA + solution ------------------------------------

    def analyze_phase_b(self, incident_id: str, collected_data: str) -> AnalysisResult:
        """Phase B: run RCA + solution on previously collected data.

        Only called when user clicks "RCA 진행".
        """
        logger.info("Phase B: RCA for %s", incident_id)
        start = time.time()
        self._tracker.set_prefix(f"[{incident_id}] ")

        try:
            orchestrator = self._get_phase_b()
            prompt = self._build_phase_b_prompt(collected_data)
            result = orchestrator(prompt)
        except Exception as e:
            logger.exception("Phase B failed for %s", incident_id)
            return AnalysisResult(
                incident_id=incident_id,
                report="",
                analysis_level=AnalysisLevel.FULL_ANALYSIS,
                elapsed_seconds=time.time() - start,
                status="failed",
                error=str(e),
            )

        elapsed = time.time() - start
        report = str(result)

        logger.info("Phase B (RCA) completed for %s in %.1fs", incident_id, elapsed)
        return AnalysisResult(
            incident_id=incident_id,
            report=report,
            analysis_level=AnalysisLevel.FULL_ANALYSIS,
            elapsed_seconds=elapsed,
        )

    # -- Skip handlers (unchanged from v0.1) -----------------------------------

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

    # -- Prompt builders -------------------------------------------------------

    @staticmethod
    def _build_phase_a_prompt(context: str) -> str:
        return (
            "다음 인시던트에 대해 데이터를 수집하고 런북을 매칭하세요.\n"
            "data_collector_agent로 데이터를 수집한 후, "
            "runbook_matcher_agent로 적합한 런북을 찾으세요.\n\n"
            f"## 현재 인시던트\n{context}"
        )

    @staticmethod
    def _build_phase_b_prompt(collected_data: str) -> str:
        return (
            "아래는 Phase A에서 수집된 관측성 데이터입니다. "
            "이 데이터를 기반으로 근본 원인을 분석하고 조치 방안을 제안하세요.\n"
            "rca_agent를 먼저 호출한 후, solution_agent를 호출하세요.\n\n"
            f"## 수집된 데이터\n{collected_data}"
        )
