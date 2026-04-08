"""AWX/Tower MCP Server — provides Ansible automation tools via FastMCP.

Exposes AWX REST API for job template discovery, job launching, and status
tracking. Used by the Operator Agent to match remediation actions to playbooks.
"""

from __future__ import annotations

import json
import os
import time

import httpx
from fastmcp import FastMCP

AWX_URL = os.environ.get("AWX_URL", "http://localhost:8052")
AWX_TOKEN = os.environ.get("AWX_TOKEN", "")

mcp = FastMCP("AWX Automation Server")
_client = httpx.Client(timeout=30.0)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {AWX_TOKEN}",
        "Content-Type": "application/json",
    }


def _awx_get(path: str, params: dict | None = None) -> dict:
    resp = _client.get(f"{AWX_URL}/api/v2{path}", headers=_headers(), params=params or {})
    resp.raise_for_status()
    return resp.json()


def _awx_post(path: str, body: dict | None = None) -> dict:
    resp = _client.post(f"{AWX_URL}/api/v2{path}", headers=_headers(), json=body or {})
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def list_job_templates(search: str = "", category: str = "") -> str:
    """List available AWX Job Templates with optional search and category filter.

    Args:
        search: Search term to filter templates by name or description
        category: Filter by template category/label (if AWX labels are used)

    Returns:
        JSON with list of job templates including id, name, description,
        and required survey variables.
    """
    try:
        params: dict[str, str] = {"page_size": "50"}
        if search:
            params["search"] = search
        if category:
            params["labels__name"] = category

        data = _awx_get("/job_templates/", params)
        templates = []
        for t in data.get("results", []):
            templates.append({
                "id": t["id"],
                "name": t["name"],
                "description": t.get("description", ""),
                "survey_enabled": t.get("survey_enabled", False),
                "ask_variables_on_launch": t.get("ask_variables_on_launch", False),
                "last_job_run": t.get("last_job_run"),
                "status": t.get("status", ""),
            })

        return json.dumps({
            "status": "success",
            "count": len(templates),
            "templates": templates,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_template_detail(template_id: int) -> str:
    """Get detailed information about a specific AWX Job Template.

    Includes survey specification (required variables and their types),
    which is needed to construct the extra_vars for launching.

    Args:
        template_id: AWX Job Template ID

    Returns:
        JSON with template details and survey variables specification.
    """
    try:
        template = _awx_get(f"/job_templates/{template_id}/")
        result = {
            "id": template["id"],
            "name": template["name"],
            "description": template.get("description", ""),
            "playbook": template.get("playbook", ""),
            "inventory": template.get("summary_fields", {}).get("inventory", {}).get("name", ""),
            "project": template.get("summary_fields", {}).get("project", {}).get("name", ""),
            "extra_vars": template.get("extra_vars", ""),
            "survey_enabled": template.get("survey_enabled", False),
            "survey_spec": {},
        }

        if template.get("survey_enabled"):
            try:
                survey = _awx_get(f"/job_templates/{template_id}/survey_spec/")
                result["survey_spec"] = {
                    "name": survey.get("name", ""),
                    "description": survey.get("description", ""),
                    "variables": [
                        {
                            "variable": v["variable"],
                            "question_name": v.get("question_name", ""),
                            "type": v.get("type", "text"),
                            "required": v.get("required", False),
                            "default": v.get("default", ""),
                            "choices": v.get("choices", ""),
                        }
                        for v in survey.get("spec", [])
                    ],
                }
            except httpx.HTTPError:
                pass

        return json.dumps({"status": "success", "template": result})
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def launch_job(template_id: int, extra_vars: str = "{}") -> str:
    """Launch an AWX Job from a Job Template with extra variables.

    IMPORTANT: This executes an actual Ansible playbook on target infrastructure.
    Only call this after receiving explicit approval.

    Args:
        template_id: AWX Job Template ID to launch
        extra_vars: JSON string of extra variables to pass to the playbook

    Returns:
        JSON with the launched job ID and initial status.
    """
    try:
        import json as _json
        vars_dict = _json.loads(extra_vars) if extra_vars else {}

        body: dict = {}
        if vars_dict:
            body["extra_vars"] = vars_dict

        data = _awx_post(f"/job_templates/{template_id}/launch/", body)
        return json.dumps({
            "status": "success",
            "job_id": data.get("id") or data.get("job"),
            "job_status": data.get("status", "pending"),
            "url": f"{AWX_URL}/#/jobs/{data.get('id', data.get('job', ''))}/output",
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_job_status(job_id: int) -> str:
    """Check the execution status of a running or completed AWX Job.

    Args:
        job_id: AWX Job ID returned by launch_job

    Returns:
        JSON with job status (pending, running, successful, failed, canceled),
        elapsed time, and completion info.
    """
    try:
        data = _awx_get(f"/jobs/{job_id}/")
        return json.dumps({
            "status": "success",
            "job_id": data["id"],
            "job_status": data.get("status", "unknown"),
            "failed": data.get("failed", False),
            "started": data.get("started"),
            "finished": data.get("finished"),
            "elapsed": data.get("elapsed"),
            "job_explanation": data.get("job_explanation", ""),
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_job_output(job_id: int, max_lines: int = 100) -> str:
    """Get the stdout output of an AWX Job execution.

    Args:
        job_id: AWX Job ID
        max_lines: Maximum number of output lines to return (default: 100)

    Returns:
        JSON with the job's stdout output (truncated to max_lines).
    """
    try:
        resp = _client.get(
            f"{AWX_URL}/api/v2/jobs/{job_id}/stdout/",
            headers=_headers(),
            params={"format": "txt"},
        )
        resp.raise_for_status()
        lines = resp.text.splitlines()
        truncated = len(lines) > max_lines
        output = "\n".join(lines[-max_lines:])

        return json.dumps({
            "status": "success",
            "job_id": job_id,
            "total_lines": len(lines),
            "truncated": truncated,
            "output": output,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


if __name__ == "__main__":
    mcp.run()
