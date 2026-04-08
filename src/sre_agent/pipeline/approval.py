"""Approval Gateway + Executor — handles human approval and AWX job execution.

Provides FastAPI routes for the approval web UI and handles the full execution
lifecycle: approval -> snapshot -> AWX launch -> health check -> notify.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from sre_agent.config import Settings

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_APPROVAL_TIMEOUT_MINUTES = 30


def register_approval_routes(app, incidents: dict, settings: Settings, lock) -> None:
    """Register approval-related routes onto the FastAPI app.

    Args:
        app: FastAPI application instance
        incidents: Shared incidents dict from the pipeline server
        settings: Application settings
        lock: Threading lock for incidents dict
    """
    try:
        from fastapi import Request
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError:
        raise RuntimeError("Approval routes require fastapi.")

    try:
        import jinja2
        _jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=True,
        )
    except ImportError:
        _jinja_env = None
        logger.warning("jinja2 not installed — approval web UI will return JSON only")

    @app.get("/approve/{incident_id}")
    async def approval_page(incident_id: str) -> HTMLResponse | JSONResponse:
        with lock:
            incident = incidents.get(incident_id)

        if not incident:
            return JSONResponse({"status": "not_found"}, status_code=404)

        elapsed_since = time.time() - incident.get("received_at", 0)
        expired = elapsed_since > (_APPROVAL_TIMEOUT_MINUTES * 60)

        if _jinja_env is None:
            return JSONResponse({
                "incident_id": incident_id,
                "status": incident.get("status", "unknown"),
                "expired": expired,
                "report": incident.get("report", ""),
            })

        template = _jinja_env.get_template("approval.html")
        html = template.render(
            incident_id=incident_id,
            status=incident.get("status", "unknown"),
            analysis_level=incident.get("analysis_level", ""),
            elapsed_seconds=incident.get("elapsed_seconds", 0),
            report=incident.get("report", ""),
            expired=expired,
        )
        return HTMLResponse(html)

    @app.post("/approve/{incident_id}")
    async def handle_approval(incident_id: str, request: Request) -> JSONResponse:
        body = await request.json()
        action = body.get("action", "")

        with lock:
            incident = incidents.get(incident_id)

        if not incident:
            return JSONResponse({"status": "not_found"}, status_code=404)

        elapsed_since = time.time() - incident.get("received_at", 0)
        if elapsed_since > (_APPROVAL_TIMEOUT_MINUTES * 60):
            return JSONResponse({"status": "expired", "error": "Approval timeout exceeded"})

        current_status = incident.get("status", "")
        if current_status in ("approved", "rejected"):
            return JSONResponse({"status": current_status, "error": "Already processed"})

        if action == "reject":
            with lock:
                incidents[incident_id]["status"] = "rejected"

            if settings.delivery.teams_webhook_url:
                try:
                    from sre_agent.pipeline.delivery import send_action_result
                    send_action_result(
                        settings.delivery.teams_webhook_url,
                        incident_id,
                        success=False,
                        message="조치가 거부되었습니다. 수동 조치가 필요합니다.",
                    )
                except Exception:
                    logger.exception("Failed to send rejection notification")

            return JSONResponse({"status": "rejected"})

        if action == "approve":
            with lock:
                incidents[incident_id]["status"] = "approved"

            result = _execute_action(incident_id, incident, settings)

            with lock:
                incidents[incident_id]["execution_result"] = result

            return JSONResponse({"status": "approved", "execution": result})

        return JSONResponse({"status": "error", "error": f"Unknown action: {action}"}, status_code=400)


def _execute_action(incident_id: str, incident: dict, settings: Settings) -> dict[str, Any]:
    """Execute the approved AWX action and perform health check."""
    report = incident.get("report", "")
    if "MATCH_FOUND" not in report:
        return {"status": "skipped", "reason": "No AWX template matched"}

    template_id = _extract_template_id(report)
    if not template_id:
        return {"status": "skipped", "reason": "Could not parse template ID from report"}

    if not settings.awx.url or not settings.awx.token:
        return {"status": "skipped", "reason": "AWX not configured"}

    logger.info("Executing AWX template %s for incident %s", template_id, incident_id)

    try:
        headers = {
            "Authorization": f"Bearer {settings.awx.token}",
            "Content-Type": "application/json",
        }
        client = httpx.Client(timeout=30.0)

        extra_vars = _extract_extra_vars(report)
        body: dict[str, Any] = {}
        if extra_vars:
            body["extra_vars"] = extra_vars

        resp = client.post(
            f"{settings.awx.url}/api/v2/job_templates/{template_id}/launch/",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        launch_data = resp.json()
        job_id = launch_data.get("id") or launch_data.get("job")

        if not job_id:
            return {"status": "error", "error": "No job ID returned from AWX"}

        job_result = _poll_job(client, headers, settings.awx.url, job_id)

        if settings.delivery.teams_webhook_url:
            _notify_execution_result(settings.delivery.teams_webhook_url, incident_id, job_result)

        _store_audit_log(incident_id, template_id, extra_vars, job_result)

        return job_result

    except httpx.HTTPError as e:
        error_result = {"status": "error", "error": str(e)}
        if settings.delivery.teams_webhook_url:
            _notify_execution_result(settings.delivery.teams_webhook_url, incident_id, error_result)
        return error_result


def _poll_job(client: httpx.Client, headers: dict, awx_url: str, job_id: int) -> dict[str, Any]:
    """Poll AWX job until completion (max 10 minutes)."""
    max_wait = 600
    interval = 5
    elapsed = 0

    while elapsed < max_wait:
        try:
            resp = client.get(f"{awx_url}/api/v2/jobs/{job_id}/", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "")

            if status in ("successful", "failed", "error", "canceled"):
                return {
                    "status": "completed",
                    "job_id": job_id,
                    "job_status": status,
                    "failed": data.get("failed", False),
                    "elapsed": data.get("elapsed"),
                }
        except httpx.HTTPError:
            pass

        time.sleep(interval)
        elapsed += interval

    return {"status": "timeout", "job_id": job_id, "error": "Job did not complete within 10 minutes"}


def _notify_execution_result(webhook_url: str, incident_id: str, result: dict) -> None:
    try:
        from sre_agent.pipeline.delivery import send_action_result

        job_status = result.get("job_status", result.get("status", "unknown"))
        success = job_status == "successful"

        if success:
            message = f"AWX Job #{result.get('job_id', '?')} 실행 완료 ({result.get('elapsed', '?')}s)"
        elif result.get("status") == "timeout":
            message = f"AWX Job #{result.get('job_id', '?')} 실행 시간 초과 (10분). 수동 확인 필요."
        else:
            message = f"AWX Job 실행 실패: {result.get('error', job_status)}"

        send_action_result(webhook_url, incident_id, success, message)
    except Exception:
        logger.exception("Failed to send execution result notification")


def _store_audit_log(incident_id: str, template_id: str, extra_vars: dict, result: dict) -> None:
    try:
        from sre_agent.integrations.knowledge_base import IncidentKB
        kb = IncidentKB()
        kb.store({
            "type": "execution_audit",
            "incident_id": incident_id,
            "awx_template_id": template_id,
            "extra_vars": extra_vars,
            "result": result,
            "incident_summary": f"AWX execution for {incident_id}",
        })
    except Exception:
        logger.exception("Failed to store audit log for %s", incident_id)


def _extract_template_id(report: str) -> str | None:
    """Extract AWX template ID from the operator's report text."""
    import re
    match = re.search(r"ID:\s*(\d+)", report)
    return match.group(1) if match else None


def _extract_extra_vars(report: str) -> dict:
    """Extract extra_vars from the operator's parameter table in the report."""
    import re
    variables: dict[str, str] = {}
    for match in re.finditer(r"\|\s*(\w+)\s*\|\s*([^|]+?)\s*\|", report):
        var_name = match.group(1).strip()
        var_value = match.group(2).strip()
        if var_name.lower() not in ("variable", "---", ""):
            variables[var_name] = var_value
    return variables
