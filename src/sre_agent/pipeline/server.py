"""Pipeline server — FastAPI application for the automated SRE pipeline.

Endpoints:
    POST /webhook/alertmanager  — Alertmanager webhook receiver
    POST /webhook/generic       — Generic webhook (custom JSON)
    GET  /health                — Health check
    GET  /incidents             — Recent incidents list
    GET  /incidents/{id}        — Incident detail

NOTE: This module intentionally does NOT use ``from __future__ import annotations``.
FastAPI's route registration calls ``typing.get_type_hints()`` on endpoint
functions, and PEP 563 string-ified annotations combined with endpoints defined
inside ``create_pipeline_app`` (closure over ``intake``, ``_lock``, etc.) make
FastAPI unable to resolve request body types — it falls back to treating them as
query params, producing ``422 {"loc": ["query", "payload"], ...}``.
"""

import logging
import threading
import time
from typing import Any

from pydantic import BaseModel

from sre_agent.config import Settings

logger = logging.getLogger(__name__)


# -- Pydantic models for request validation -----------------------------------
#
# These MUST live at module scope. FastAPI resolves endpoint type hints via
# ``typing.get_type_hints()``, and with ``from __future__ import annotations``
# all hints become strings — a class defined inside ``create_pipeline_app`` is
# invisible to that lookup, so FastAPI would fall back to treating the payload
# as a query parameter and every request would 422 with
# ``{"loc": ["query", "payload"], "msg": "Field required"}``.

class AlertmanagerPayload(BaseModel):
    version: str = ""
    groupKey: str = ""
    status: str = ""
    receiver: str = ""
    groupLabels: dict = {}
    commonLabels: dict = {}
    commonAnnotations: dict = {}
    externalURL: str = ""
    alerts: list[dict] = []


class GenericPayload(BaseModel):
    alertname: str = ""
    title: str = ""
    severity: str = "warning"
    status: str = "firing"
    message: str = ""
    labels: dict = {}
    annotations: dict = {}


def create_pipeline_app(settings: Settings):
    """Create and return a FastAPI application wired to the full pipeline."""
    try:
        from fastapi import BackgroundTasks, FastAPI
    except ImportError:
        raise RuntimeError(
            "Pipeline server requires fastapi. Install with: pip install sre-agent[webhook]"
        )

    from sre_agent.pipeline.analyzer import AnalysisResult, PipelineAnalyzer
    from sre_agent.pipeline.intake import IntakeProcessor

    app = FastAPI(
        title="SRE Agent Pipeline",
        description="Automated SRE incident analysis pipeline",
        version="0.2.0",
    )

    intake = IntakeProcessor(settings.intake)
    analyzer = PipelineAnalyzer(settings)

    _incidents: dict[str, dict[str, Any]] = {}
    _lock = threading.Lock()

    # -- Background analysis runner -------------------------------------------

    _teams_url = settings.delivery.teams_webhook_url
    _public_base_url = settings.delivery.public_base_url

    def _run_analysis(incident_request) -> None:
        from sre_agent.pipeline.intake import IncidentRequest

        req: IncidentRequest = incident_request

        with _lock:
            _incidents[req.incident_id]["status"] = "analyzing"

        try:
            from sre_agent.pipeline.delivery import send_alert_received
            alert_summary = f"{req.primary_alertname} ({req.primary_severity})"
            # send_alert_received logs to console when teams_webhook_url is "".
            send_alert_received(_teams_url, req.incident_id, alert_summary)
        except Exception:
            logger.exception("Failed to deliver start notification for %s", req.incident_id)

        result: AnalysisResult = analyzer.analyze(req)

        has_action = "MATCH_FOUND" in result.report
        report_sent_at = time.time()

        with _lock:
            _incidents[req.incident_id].update({
                "status": result.status,
                "report": result.report,
                "elapsed_seconds": result.elapsed_seconds,
                "analysis_level": result.analysis_level.value,
                "has_action": has_action,
                "error": result.error,
                "report_sent_at": report_sent_at,
            })

        if result.status in ("completed", "skipped"):
            try:
                from sre_agent.pipeline.delivery import send_report
                send_report(
                    webhook_url=_teams_url,
                    incident_id=req.incident_id,
                    report=result.report,
                    elapsed=result.elapsed_seconds,
                    has_action=has_action,
                    server_base_url=_public_base_url,
                )
            except Exception:
                logger.exception("Failed to deliver report for %s", req.incident_id)

    # -- Endpoints ------------------------------------------------------------

    @app.post("/webhook/alertmanager")
    async def receive_alertmanager(payload: AlertmanagerPayload, background_tasks: BackgroundTasks) -> dict:
        logger.info(
            "Alertmanager webhook: status=%s, alerts=%d, group=%s",
            payload.status, len(payload.alerts), payload.groupKey,
        )

        requests = intake.process_alertmanager_payload(payload.model_dump())

        if not requests:
            return {"status": "skipped", "reason": "all alerts deduplicated"}

        for req in requests:
            with _lock:
                _incidents[req.incident_id] = {
                    "incident_id": req.incident_id,
                    "status": "queued",
                    "alerts": [
                        {"alertname": a.alertname, "severity": a.severity, "status": a.status}
                        for a in req.alerts
                    ],
                    "analysis_level": req.analysis_level.value,
                    "received_at": req.received_at,
                }
            background_tasks.add_task(_run_analysis, req)

        return {
            "status": "accepted",
            "incidents": [
                {"incident_id": r.incident_id, "analysis_level": r.analysis_level.value}
                for r in requests
            ],
        }

    @app.post("/webhook/generic")
    async def receive_generic(payload: GenericPayload, background_tasks: BackgroundTasks) -> dict:
        logger.info("Generic webhook: %s (severity=%s)", payload.alertname or payload.title, payload.severity)

        requests = intake.process_generic_payload(payload.model_dump())

        if not requests:
            return {"status": "skipped", "reason": "deduplicated"}

        for req in requests:
            with _lock:
                _incidents[req.incident_id] = {
                    "incident_id": req.incident_id,
                    "status": "queued",
                    "alerts": [
                        {"alertname": a.alertname, "severity": a.severity, "status": a.status}
                        for a in req.alerts
                    ],
                    "analysis_level": req.analysis_level.value,
                    "received_at": req.received_at,
                }
            background_tasks.add_task(_run_analysis, req)

        return {
            "status": "accepted",
            "incidents": [
                {"incident_id": r.incident_id, "analysis_level": r.analysis_level.value}
                for r in requests
            ],
        }

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy", "pipeline": "active"}

    @app.get("/incidents")
    async def list_incidents(limit: int = 20) -> dict:
        with _lock:
            items = sorted(
                _incidents.values(),
                key=lambda x: x.get("received_at", 0),
                reverse=True,
            )[:limit]
        return {"count": len(items), "incidents": items}

    @app.get("/incidents/{incident_id}")
    async def get_incident(incident_id: str) -> dict:
        with _lock:
            if incident_id not in _incidents:
                return {"status": "not_found"}
            return _incidents[incident_id]

    from sre_agent.pipeline.approval import register_approval_routes
    register_approval_routes(app, _incidents, settings, _lock)

    return app
