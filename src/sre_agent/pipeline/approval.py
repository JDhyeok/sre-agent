"""Approval Gateway + Executor — handles human approval and remediation execution.

Provides FastAPI routes for the approval web UI and dispatches the matched
remediation via SSH-based runbook execution.

NOTE: This module intentionally does NOT use ``from __future__ import annotations``.
See ``sre_agent/pipeline/server.py`` for the full explanation — in short,
PEP 563 string annotations break FastAPI's body-type resolution for endpoints
defined inside ``register_approval_routes``.
"""

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from sre_agent.config import Settings
from sre_agent.tools.runbook import load_runbook_by_name

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_RUNBOOK_DIR = Path(__file__).resolve().parent.parent / "runbooks"
_APPROVAL_TIMEOUT_MINUTES = 10


def _extract_visualization_json(report: str) -> str:
    """Extract visualization_json code block from the report for chart/table rendering.

    Accepts both ``visualization_json`` (new) and ``metrics_json`` (legacy) blocks.
    Returns the raw JSON string, or empty string if not found/invalid.
    """
    import json as _json

    for tag in ("visualization_json", "metrics_json"):
        match = re.search(rf"```{tag}\s*\n(.*?)```", report, re.DOTALL)
        if match:
            raw = match.group(1).strip()
            try:
                parsed = _json.loads(raw)
                # Normalize legacy format (array) to new format (dict with charts key)
                if isinstance(parsed, list):
                    return _json.dumps({"charts": parsed})
                return raw
            except (_json.JSONDecodeError, ValueError):
                continue
    return ""


def _strip_visualization_block(report: str) -> str:
    """Remove visualization_json / metrics_json blocks from the report text.

    These blocks are consumed by the chart renderer and should not appear
    as raw text in the report display. Uses two strategies for robustness:
    1. Remove the entire "시각화 데이터" section (header through next heading)
    2. Remove any standalone code blocks as fallback
    """
    result = report
    # Strategy 1: Remove the entire section from header to next ### heading
    result = re.sub(
        r"###\s*시각화\s*데이터.*?(?=\n###\s|\Z)",
        "", result, flags=re.DOTALL,
    )
    # Strategy 2: Remove standalone code blocks (fallback if no section header)
    for tag in ("visualization_json", "metrics_json"):
        result = re.sub(rf"```\s*{tag}.*?```", "", result, flags=re.DOTALL)
    return result.strip()


def register_approval_routes(
    app, incidents: dict, settings: Settings, lock,
    rca_callback=None,
) -> None:
    """Register approval-related routes onto the FastAPI app.

    Args:
        app: FastAPI application instance
        incidents: Shared incidents dict from the pipeline server
        settings: Application settings
        lock: Threading lock for incidents dict
        rca_callback: Optional callback for Phase B RCA execution.
                      Signature: rca_callback(incident_id: str) -> None
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

    try:
        import markdown as _markdown
        _md = _markdown.Markdown(extensions=["extra", "sane_lists", "tables"])
    except ImportError:
        _md = None
        logger.warning("markdown not installed — approval web UI will show raw markdown")

    # response_model=None: the return type is a Response subclass union,
    # which is not a valid Pydantic field. Without this, FastAPI raises
    # ``Invalid args for response field`` at route registration.
    @app.get("/approve/{incident_id}", response_model=None)
    async def approval_page(incident_id: str):
        with lock:
            incident = incidents.get(incident_id)

        if not incident:
            return JSONResponse({"status": "not_found"}, status_code=404)

        clock_start = incident.get("report_sent_at") or incident.get("received_at", 0)
        elapsed_since = time.time() - clock_start
        expired = elapsed_since > (_APPROVAL_TIMEOUT_MINUTES * 60)

        report_md = incident.get("report", "")

        if _jinja_env is None:
            return JSONResponse({
                "incident_id": incident_id,
                "status": incident.get("status", "unknown"),
                "expired": expired,
                "report": report_md,
            })

        # Strip visualization blocks before rendering (they're consumed by the chart JS)
        report_display = _strip_visualization_block(report_md)

        # Render the report as HTML so the user does not see raw markdown.
        if _md is not None and report_display:
            _md.reset()
            report_html = _md.convert(report_display)
        else:
            report_html = ""

        # Stripped report body (for the case when markdown lib is missing —
        # at least drop the marker characters so the page is readable).
        report_plain = _strip_markdown_markers(report_display)

        # Build the "what will run" panel from structured match data.
        runbook_match = incident.get("runbook_match", {})
        runbook_view = _build_runbook_view(report_md, runbook_match)

        # Phase B (RCA) results
        phase = incident.get("status", "unknown")
        rca_report_md = incident.get("rca_report", "")
        rca_report_html = ""
        if rca_report_md:
            try:
                rca_report_html = _md.convert(rca_report_md)
                _md.reset()
            except Exception:
                rca_report_html = _strip_markdown_markers(rca_report_md)

        # Extract visualization data from report
        metrics_json = _extract_visualization_json(report_md)

        template = _jinja_env.get_template("approval.html")
        html = template.render(
            incident_id=incident_id,
            status=phase,
            analysis_level=incident.get("analysis_level", ""),
            elapsed_seconds=incident.get("elapsed_seconds", 0),
            report_html=report_html,
            report_plain=report_plain,
            expired=expired,
            runbook=runbook_view,
            phase=phase,
            rca_report_html=rca_report_html,
            rca_callback_available=(rca_callback is not None),
            metrics_json=metrics_json,
        )
        return HTMLResponse(html)

    @app.post("/approve/{incident_id}", response_model=None)
    async def handle_approval(incident_id: str, request: Request):
        body = await request.json()
        action = body.get("action", "")

        with lock:
            incident = incidents.get(incident_id)

        if not incident:
            return JSONResponse({"status": "not_found"}, status_code=404)

        clock_start = incident.get("report_sent_at") or incident.get("received_at", 0)
        elapsed_since = time.time() - clock_start
        if elapsed_since > (_APPROVAL_TIMEOUT_MINUTES * 60):
            return JSONResponse({"status": "expired", "error": "Approval timeout exceeded"})

        current_status = incident.get("status", "")
        if current_status in ("approved", "rejected", "manual_action"):
            return JSONResponse({"status": current_status, "error": "Already processed"})

        if action == "rca":
            if current_status == "rca_running":
                return JSONResponse({"status": "rca_running", "error": "RCA already in progress"})
            if current_status == "rca_completed":
                return JSONResponse({"status": "rca_completed", "error": "RCA already completed"})
            if rca_callback is None:
                return JSONResponse({"status": "error", "error": "RCA not available"}, status_code=500)

            with lock:
                incidents[incident_id]["status"] = "rca_running"

            import threading
            threading.Thread(
                target=rca_callback,
                args=(incident_id,),
                daemon=True,
            ).start()

            return JSONResponse({"status": "rca_started"})

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

        if action == "manual_action":
            with lock:
                incidents[incident_id]["status"] = "manual_action"

            if settings.delivery.teams_webhook_url:
                try:
                    from sre_agent.pipeline.delivery import send_action_result
                    send_action_result(
                        settings.delivery.teams_webhook_url,
                        incident_id,
                        success=True,
                        message="수동조치예정으로 처리되었습니다. 담당자가 직접 조치합니다.",
                    )
                except Exception:
                    logger.exception("Failed to send manual action notification")

            return JSONResponse({"status": "manual_action"})

        if action == "approve":
            with lock:
                incidents[incident_id]["status"] = "approved"

            result = _execute_action(incident_id, incident, settings)

            with lock:
                incidents[incident_id]["execution_result"] = result

            return JSONResponse({"status": "approved", "execution": result})

        return JSONResponse({"status": "error", "error": f"Unknown action: {action}"}, status_code=400)


def _execute_action(incident_id: str, incident: dict, settings: Settings) -> dict[str, Any]:
    """Execute the approved remediation via SSH-based runbook execution."""
    # Primary: structured match data from report_match tool
    match_data = incident.get("runbook_match", {})
    if match_data.get("matched") and match_data.get("name"):
        return _execute_runbook(incident_id, match_data["name"], settings)

    # Fallback: text parsing for legacy incidents
    report = incident.get("report", "")
    if "MATCH_FOUND" not in report:
        return {"status": "skipped", "reason": "No remediation matched"}

    runbook_name = _extract_runbook_name(report)
    if not runbook_name:
        return {"status": "skipped", "reason": "Could not parse runbook name from report"}

    return _execute_runbook(incident_id, runbook_name, settings)


# ---------------------------------------------------------------------------
# Approval-page view helpers
# ---------------------------------------------------------------------------


def _strip_markdown_markers(text: str) -> str:
    """Strip the most jarring markdown markers for the no-markdown-lib fallback.

    Not a real renderer — just removes ``#``/``*``/``|`` so the page is at
    least readable when the optional ``markdown`` package is not installed.
    """
    if not text:
        return ""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            line = stripped.lstrip("#").lstrip()
        line = line.replace("**", "").replace("__", "")
        out.append(line)
    return "\n".join(out)


def _build_runbook_view(report: str, match_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the data the approval page shows about the matched runbook.

    Uses **structured match data** from the ``report_match`` tool (the
    ``match_data`` dict) as the primary source of truth.  This is
    deterministic — no regex parsing of LLM text output.

    Falls back to text parsing only for legacy incidents that don't have
    structured data (e.g., created before this change).

    Returns a dict with one of three shapes (the template branches on
    ``status``):

        {"status": "match", "name": ..., "risk": ..., ...}
        {"status": "no_match", "reason": ..., "alternatives": [...]}
        {"status": "none"}
    """
    if not report and not match_data:
        return {"status": "none"}

    # --- Primary path: structured data from report_match tool ---------------
    if match_data and match_data.get("matched"):
        return _build_match_from_structured(match_data, report)

    if match_data and not match_data.get("matched") and match_data.get("name") == "":
        # report_match was called with matched=False → explicit NO_MATCH
        return _parse_no_match(report)

    # --- Fallback: text parsing (legacy / report_match not called) ----------
    return _build_match_from_text(report)


def _build_match_from_structured(match_data: dict[str, Any], report: str) -> dict[str, Any]:
    """Build runbook view from the structured ``report_match`` tool output."""
    name = match_data.get("name", "")
    if not name:
        return {"status": "none"}

    loaded = load_runbook_by_name(name)
    if not loaded:
        return {
            "status": "match",
            "name": name,
            "risk": match_data.get("risk", "unknown"),
            "script_path": match_data.get("script", ""),
            "script_body": "",
            "script_exists": False,
            "script_missing_reason": (
                f"Runbook '{name}' is referenced but not present in {_RUNBOOK_DIR}."
            ),
            "target_host_label": match_data.get("target_host_label", ""),
            "trigger": "",
            "why": _extract_why(report),
            "what": _extract_what(report),
        }

    meta, _body = loaded
    script_path = _resolve_script_path(meta.script)

    script_body = ""
    script_exists = False
    script_missing_reason = ""
    if script_path is not None:
        try:
            script_body = script_path.read_text(encoding="utf-8")
            script_exists = True
        except OSError as exc:
            script_missing_reason = f"Cannot read script {script_path}: {exc}"
    else:
        script_missing_reason = (
            f"Script file '{meta.script}' is not present on disk. This runbook "
            "is descriptive only — approving will mark a manual action."
        )

    return {
        "status": "match",
        "name": meta.name,
        "risk": meta.risk,
        "script_path": str(script_path) if script_path else meta.script,
        "script_body": script_body,
        "script_exists": script_exists,
        "script_missing_reason": script_missing_reason,
        "target_host_label": meta.target_host_label,
        "trigger": meta.trigger,
        "why": _extract_why(report),
        "what": _extract_what(report),
    }


def _build_match_from_text(report: str) -> dict[str, Any]:
    """Legacy fallback: parse runbook match from LLM text output.

    Used only for incidents that don't have structured match data
    (e.g., created before the report_match tool was added).
    """
    if not report:
        return {"status": "none"}

    has_match = bool(re.search(r"MATCH_FOUND", report))
    has_no_match = bool(re.search(r"NO_MATCH", report))
    if has_no_match and not has_match:
        return _parse_no_match(report)

    name = _extract_runbook_name(report)
    if not name:
        return {"status": "none"}

    return _build_match_from_structured({"matched": True, "name": name}, report)


def _parse_no_match(report: str) -> dict[str, Any]:
    """Parse the NO_MATCH branch of the runbook matcher's report."""
    reason_match = re.search(r"\*\*(?:Reason|이유|사유)\*\*:\s*(.+)", report)
    reason = reason_match.group(1).strip() if reason_match else ""

    # Pull lines under "### Manual Alternatives" (or its Korean translation)
    # until the next blank/section.
    alternatives: list[str] = []
    lines = report.splitlines()
    in_section = False
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("### Manual Alternatives") or \
           stripped_line.startswith("### 수동 대안") or \
           stripped_line.startswith("### 수동 조치"):
            in_section = True
            continue
        if in_section:
            stripped = stripped_line
            if not stripped:
                if alternatives:  # blank line after items = end of section
                    break
                continue
            if stripped.startswith("###") or stripped.startswith("```"):
                break
            # "1. text", "- text", "* text"
            cleaned = re.sub(r"^([0-9]+\.|[-*])\s*", "", stripped)
            if cleaned:
                alternatives.append(cleaned)

    return {
        "status": "no_match",
        "reason": reason,
        "alternatives": alternatives[:3],
    }


def _extract_section(report: str, header: str) -> str:
    """Extract text under a "### <header>" line until the next header or fence."""
    pattern = rf"###\s+{re.escape(header)}\s*\n(.*?)(?=\n###\s|\n```|\Z)"
    match = re.search(pattern, report, re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_why(report: str) -> str:
    """Extract the matcher's 'Why this matches' body in any supported language."""
    for header in ("Why this matches", "매칭 이유", "왜 매칭되는가"):
        section = _extract_section(report, header)
        if section:
            return section
    return ""


def _extract_what(report: str) -> str:
    """Extract the matcher's 'What it will do' body in any supported language."""
    for header in ("What it will do", "런북의 기능", "수행 작업", "런북이 하는 일"):
        section = _extract_section(report, header)
        if section:
            return section
    return ""


# ---------------------------------------------------------------------------
# Runbook execution branch
# ---------------------------------------------------------------------------


def _extract_runbook_name(report: str) -> str | None:
    """Extract the runbook name from the runbook_matcher_agent's report.

    The matcher prompt mandates `**Runbook**: <name>`, but LLMs occasionally
    translate the field key when the rest of the report is in Korean. We
    accept either form so the approval UI does not silently degrade.
    """
    # Strategy 1: Match any bold key containing "runbook" or "런북" followed by a name.
    # This handles all observed LLM variants: **Runbook**, **런북**, **런북명**,
    # **매칭된 런북**, **매칭된 Runbook**, etc.
    match = re.search(
        r"\*\*(?:[^*]*(?:Runbook|런\s*북)[^*]*)\*\*:\s*`?([A-Za-z0-9._-]+)`?",
        report, re.IGNORECASE,
    )
    if match:
        return match.group(1)

    # Strategy 2: If MATCH_FOUND is present, look for a runbook-like name near it.
    # Runbook names in this project follow the pattern: word-word(-word)*.
    if re.search(r"MATCH_FOUND", report):
        # Look for a name that looks like a runbook slug (e.g., "memory-leak-restart")
        # near context clues like "런북", "runbook", "스크립트" etc.
        name_match = re.search(
            r"(?:런\s*북|runbook|스크립트|script)[^`\n]*`([A-Za-z0-9._-]+)`",
            report, re.IGNORECASE,
        )
        if name_match:
            return name_match.group(1)

    return None


def _resolve_script_path(script_field: str) -> Path | None:
    """Resolve a runbook's `script:` field to an actual file on disk.

    Searches a few likely locations relative to the runbooks directory.
    Returns None if the script does not exist (the runbook may be a
    descriptive-only stub for now).
    """
    if not script_field:
        return None

    candidates = [
        _RUNBOOK_DIR / script_field,
        _RUNBOOK_DIR.parent / script_field,
        Path(script_field),
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _resolve_target_host(target_host_label: str, settings: Settings) -> dict | None:
    """Pick an SSH host that matches the runbook's `target_host_label`.

    Matching is intentionally simple for the PoC: case-insensitive substring
    against host name and hostname. If no label is given, fall back to the
    first configured host. Returns None if no hosts are configured at all.
    """
    hosts = [h.model_dump() for h in settings.ssh.hosts]
    if not hosts:
        return None

    if not target_host_label:
        return hosts[0]

    needle = target_host_label.lower()
    for host in hosts:
        name = str(host.get("name", "")).lower()
        hostname = str(host.get("hostname", "")).lower()
        if needle in name or needle in hostname:
            return host

    return hosts[0]


def _execute_runbook(incident_id: str, runbook_name: str, settings: Settings) -> dict[str, Any]:
    """Run a runbook's script on a target SSH host.

    Pipes the local script content to ``ssh host bash -s`` so the script does
    not need to be pre-deployed. Bypasses the SSH MCP allowlist intentionally
    — runbooks are mutating actions, and this path runs only after a human
    has approved via the web UI.
    """
    loaded = load_runbook_by_name(runbook_name)
    if not loaded:
        msg = f"Runbook '{runbook_name}' not found in {_RUNBOOK_DIR}"
        logger.error("[%s] %s", incident_id, msg)
        return {"status": "error", "runbook": runbook_name, "error": msg}

    meta, _body = loaded
    logger.info(
        "[%s] Executing runbook=%s risk=%s script=%s",
        incident_id, meta.name, meta.risk, meta.script,
    )

    script_path = _resolve_script_path(meta.script)
    if script_path is None:
        msg = (
            f"Runbook '{meta.name}' has no executable script at '{meta.script}'. "
            "This runbook is descriptive only — manual action required."
        )
        logger.warning("[%s] %s", incident_id, msg)
        return {
            "status": "skipped",
            "runbook": meta.name,
            "reason": msg,
            "script_field": meta.script,
        }

    host = _resolve_target_host(meta.target_host_label, settings)
    if host is None:
        msg = "No SSH hosts configured — cannot execute runbook script remotely."
        logger.warning("[%s] %s", incident_id, msg)
        return {
            "status": "skipped",
            "runbook": meta.name,
            "reason": msg,
            "script_path": str(script_path),
        }

    logger.info(
        "[%s] runbook=%s target_host=%s script_path=%s",
        incident_id, meta.name, host.get("name") or host.get("hostname"), script_path,
    )

    try:
        script_content = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Could not read script {script_path}: {exc}"
        logger.error("[%s] %s", incident_id, msg)
        return {"status": "error", "runbook": meta.name, "error": msg}

    ssh_args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
        "-p", str(host.get("port", 22)),
    ]
    key_path = host.get("key_path", "")
    if key_path:
        ssh_args.extend(["-i", os.path.expanduser(key_path)])
    user = host.get("username", "sre-readonly")
    ssh_args.append(f"{user}@{host['hostname']}")
    ssh_args.append("bash -s")

    try:
        proc = subprocess.run(
            ssh_args,
            input=script_content,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        msg = "Runbook script timed out after 300s"
        logger.error("[%s] %s", incident_id, msg)
        return {"status": "timeout", "runbook": meta.name, "error": msg}
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] Runbook execution failed", incident_id)
        return {"status": "error", "runbook": meta.name, "error": str(exc)}

    success = proc.returncode == 0
    result: dict[str, Any] = {
        "status": "completed",
        "runbook": meta.name,
        "script_path": str(script_path),
        "target_host": host.get("name") or host.get("hostname"),
        "exit_code": proc.returncode,
        "stdout": proc.stdout[:5000],
        "stderr": proc.stderr[:2000],
        "success": success,
    }

    logger.info(
        "[%s] runbook=%s exit_code=%s success=%s",
        incident_id, meta.name, proc.returncode, success,
    )
    if proc.stdout:
        logger.info("[%s] stdout:\n%s", incident_id, proc.stdout[:2000])
    if proc.stderr:
        logger.info("[%s] stderr:\n%s", incident_id, proc.stderr[:2000])

    if settings.delivery.teams_webhook_url:
        try:
            from sre_agent.pipeline.delivery import send_action_result
            message = (
                f"Runbook `{meta.name}` 실행 {'완료' if success else '실패'} "
                f"(exit={proc.returncode}, host={result['target_host']})"
            )
            send_action_result(
                settings.delivery.teams_webhook_url, incident_id, success, message,
            )
        except Exception:
            logger.exception("[%s] Failed to notify Teams of runbook result", incident_id)

    return result
