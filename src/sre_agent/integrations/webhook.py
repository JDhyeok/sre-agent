"""Alertmanager webhook handler for the SRE Agent system.

Provides a FastAPI endpoint that receives Alertmanager webhook payloads
and triggers automatic incident analysis.

Requires: pip install sre-agent[webhook]
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from sre_agent.config import load_settings

logger = logging.getLogger(__name__)


def _format_alert_context(payload: dict[str, Any]) -> str:
    """Convert Alertmanager webhook payload into a human-readable incident context."""
    alerts = payload.get("alerts", [])
    status = payload.get("status", "unknown")
    group_labels = payload.get("groupLabels", {})

    lines = [
        f"Alertmanager Notification (status: {status})",
        f"Group Labels: {json.dumps(group_labels)}",
        f"Alert Count: {len(alerts)}",
        "",
    ]

    for i, alert in enumerate(alerts, 1):
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        lines.append(f"--- Alert {i} ---")
        lines.append(f"  Name: {labels.get('alertname', 'unknown')}")
        lines.append(f"  Severity: {labels.get('severity', 'unknown')}")
        lines.append(f"  Status: {alert.get('status', 'unknown')}")
        lines.append(f"  Started: {alert.get('startsAt', 'unknown')}")
        lines.append(f"  Labels: {json.dumps(labels)}")
        if annotations.get("summary"):
            lines.append(f"  Summary: {annotations['summary']}")
        if annotations.get("description"):
            lines.append(f"  Description: {annotations['description']}")
        lines.append("")

    return "\n".join(lines)


def create_webhook_app():
    """Create a FastAPI application with the Alertmanager webhook endpoint."""
    try:
        from fastapi import FastAPI, BackgroundTasks
        from pydantic import BaseModel
    except ImportError:
        raise RuntimeError(
            "Webhook integration requires fastapi. Install with: pip install sre-agent[webhook]"
        )

    app = FastAPI(title="SRE Agent Webhook", description="Alertmanager webhook receiver")
    settings = load_settings()

    _results: dict[str, dict] = {}

    class WebhookPayload(BaseModel):
        version: str = ""
        groupKey: str = ""
        status: str = ""
        receiver: str = ""
        groupLabels: dict = {}
        commonLabels: dict = {}
        commonAnnotations: dict = {}
        externalURL: str = ""
        alerts: list[dict] = []

    def _run_analysis(payload_dict: dict, request_id: str) -> None:
        from sre_agent.agents.orchestrator import create_orchestrator

        incident_context = _format_alert_context(payload_dict)
        orchestrator = create_orchestrator(settings)

        start = time.time()
        try:
            response = orchestrator(
                f"Investigate the following incident and produce a complete RCA report:\n\n{incident_context}"
            )
            elapsed = time.time() - start
            _results[request_id] = {
                "status": "completed",
                "elapsed_seconds": round(elapsed, 1),
                "analysis": str(response),
            }
            logger.info("Analysis completed for %s in %.1fs", request_id, elapsed)
        except Exception as e:
            logger.exception("Analysis failed for %s", request_id)
            _results[request_id] = {"status": "failed", "error": str(e)}

    @app.post("/webhook/alertmanager")
    async def receive_alert(payload: WebhookPayload, background_tasks: BackgroundTasks) -> dict:
        request_id = f"{payload.groupKey}-{int(time.time())}"
        logger.info(
            "Received alert webhook: status=%s, alerts=%d, group=%s",
            payload.status,
            len(payload.alerts),
            payload.groupKey,
        )

        _results[request_id] = {"status": "analyzing"}
        background_tasks.add_task(_run_analysis, payload.model_dump(), request_id)

        return {
            "status": "accepted",
            "request_id": request_id,
            "message": f"Analysis started for {len(payload.alerts)} alert(s)",
        }

    @app.get("/webhook/status/{request_id}")
    async def get_status(request_id: str) -> dict:
        if request_id not in _results:
            return {"status": "not_found"}
        return _results[request_id]

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    return app
