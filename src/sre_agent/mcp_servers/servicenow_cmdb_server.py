"""ServiceNow CMDB MCP Server - provides CI and dependency lookup via FastMCP.

Queries the ServiceNow CMDB (Configuration Management Database) to retrieve
service topology, CI details, and dependency relationships.  Authentication
and base URL are injected through environment variables so the server stays
decoupled from the agent configuration layer.
"""

from __future__ import annotations

import json
import os

import httpx
from fastmcp import FastMCP

SERVICENOW_INSTANCE_URL = os.environ.get("SERVICENOW_INSTANCE_URL", "").rstrip("/")
SERVICENOW_API_TOKEN = os.environ.get("SERVICENOW_API_TOKEN", "")
SERVICENOW_USERNAME = os.environ.get("SERVICENOW_USERNAME", "")
SERVICENOW_PASSWORD = os.environ.get("SERVICENOW_PASSWORD", "")

mcp = FastMCP("ServiceNow CMDB Server")


def _build_client() -> httpx.Client:
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    auth = None

    if SERVICENOW_API_TOKEN:
        headers["Authorization"] = f"Bearer {SERVICENOW_API_TOKEN}"
    elif SERVICENOW_USERNAME and SERVICENOW_PASSWORD:
        auth = (SERVICENOW_USERNAME, SERVICENOW_PASSWORD)

    return httpx.Client(
        base_url=SERVICENOW_INSTANCE_URL,
        headers=headers,
        auth=auth,
        timeout=30.0,
    )


_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def _table_api(table: str, params: dict) -> dict:
    """Call the ServiceNow Table API."""
    if not SERVICENOW_INSTANCE_URL:
        return {"error": "SERVICENOW_INSTANCE_URL is not configured"}
    resp = _get_client().get(f"/api/now/table/{table}", params=params)
    resp.raise_for_status()
    return resp.json()


def _cmdb_api(endpoint: str, params: dict) -> dict:
    """Call a CMDB-specific REST endpoint."""
    if not SERVICENOW_INSTANCE_URL:
        return {"error": "SERVICENOW_INSTANCE_URL is not configured"}
    resp = _get_client().get(endpoint, params=params)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def get_ci_details(ci_name: str) -> str:
    """Look up a Configuration Item (CI) by name and return its details.

    Searches across cmdb_ci (servers, applications, services, etc.) for a CI
    whose name matches the provided value.

    Args:
        ci_name: The display name of the CI (server hostname, service name, etc.)

    Returns:
        JSON with CI attributes: sys_id, name, class, operational status,
        environment, IP address, assigned support group, and more.
    """
    try:
        data = _table_api("cmdb_ci", {
            "sysparm_query": f"nameLIKE{ci_name}",
            "sysparm_fields": (
                "sys_id,name,sys_class_name,operational_status,"
                "ip_address,os,environment,category,subcategory,"
                "support_group,assigned_to,location,company,"
                "short_description,install_status"
            ),
            "sysparm_limit": 10,
        })

        results = data.get("result", [])
        if not results:
            return json.dumps({"status": "not_found", "query": ci_name, "message": f"No CI found matching '{ci_name}'"})

        return json.dumps({"status": "success", "query": ci_name, "count": len(results), "cis": results})
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def search_ci(query: str, ci_type: str = "") -> str:
    """Search Configuration Items by keyword with optional type filter.

    Args:
        query: Free-text search keyword
        ci_type: Optional CI class filter (e.g. 'cmdb_ci_server', 'cmdb_ci_service',
                 'cmdb_ci_app_server', 'cmdb_ci_db_instance')

    Returns:
        JSON with matching CIs and basic attributes.
    """
    try:
        table = ci_type if ci_type else "cmdb_ci"
        sysparm_query = f"nameLIKE{query}^ORshort_descriptionLIKE{query}^ORip_addressLIKE{query}"

        data = _table_api(table, {
            "sysparm_query": sysparm_query,
            "sysparm_fields": "sys_id,name,sys_class_name,operational_status,ip_address,environment,short_description",
            "sysparm_limit": 20,
        })

        results = data.get("result", [])
        return json.dumps({"status": "success", "query": query, "ci_type": ci_type or "all", "count": len(results), "cis": results})
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_service_dependencies(service_name: str, direction: str = "both") -> str:
    """Retrieve upstream and/or downstream dependencies for a service.

    Uses the cmdb_rel_ci (CI Relationship) table to map out what a service
    depends on and what depends on it.

    Args:
        service_name: Name of the service CI to look up dependencies for
        direction: 'upstream' (things this service depends on),
                   'downstream' (things that depend on this service),
                   or 'both' (default)

    Returns:
        JSON with upstream and downstream dependency lists, each including
        CI name, type, and relationship description.
    """
    try:
        ci_data = _table_api("cmdb_ci", {
            "sysparm_query": f"name={service_name}",
            "sysparm_fields": "sys_id,name,sys_class_name",
            "sysparm_limit": 1,
        })
        ci_results = ci_data.get("result", [])
        if not ci_results:
            return json.dumps({"status": "not_found", "service": service_name, "message": f"Service '{service_name}' not found in CMDB"})

        ci_sys_id = ci_results[0]["sys_id"]
        result: dict = {"status": "success", "service": service_name, "sys_id": ci_sys_id}

        if direction in ("upstream", "both"):
            upstream_data = _table_api("cmdb_rel_ci", {
                "sysparm_query": f"child={ci_sys_id}",
                "sysparm_fields": "parent.name,parent.sys_class_name,type.name",
                "sysparm_limit": 50,
            })
            result["upstream"] = [
                {
                    "name": r.get("parent.name", ""),
                    "type": r.get("parent.sys_class_name", ""),
                    "relationship": r.get("type.name", ""),
                }
                for r in upstream_data.get("result", [])
            ]

        if direction in ("downstream", "both"):
            downstream_data = _table_api("cmdb_rel_ci", {
                "sysparm_query": f"parent={ci_sys_id}",
                "sysparm_fields": "child.name,child.sys_class_name,type.name",
                "sysparm_limit": 50,
            })
            result["downstream"] = [
                {
                    "name": r.get("child.name", ""),
                    "type": r.get("child.sys_class_name", ""),
                    "relationship": r.get("type.name", ""),
                }
                for r in downstream_data.get("result", [])
            ]

        return json.dumps(result)
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_ci_relationships(ci_name: str) -> str:
    """Retrieve all relationships for a CI (server, application, service, etc.).

    Returns every relationship where this CI appears as either parent or child,
    providing a full picture of how it connects to other infrastructure components.

    Args:
        ci_name: Name of the CI to look up relationships for

    Returns:
        JSON with all relationships grouped as 'depends_on' (upstream) and
        'used_by' (downstream).
    """
    try:
        ci_data = _table_api("cmdb_ci", {
            "sysparm_query": f"name={ci_name}",
            "sysparm_fields": "sys_id,name,sys_class_name,ip_address",
            "sysparm_limit": 1,
        })
        ci_results = ci_data.get("result", [])
        if not ci_results:
            return json.dumps({"status": "not_found", "ci_name": ci_name, "message": f"CI '{ci_name}' not found in CMDB"})

        ci = ci_results[0]
        ci_sys_id = ci["sys_id"]

        as_child = _table_api("cmdb_rel_ci", {
            "sysparm_query": f"child={ci_sys_id}",
            "sysparm_fields": "parent.name,parent.sys_class_name,parent.ip_address,type.name",
            "sysparm_limit": 50,
        })
        as_parent = _table_api("cmdb_rel_ci", {
            "sysparm_query": f"parent={ci_sys_id}",
            "sysparm_fields": "child.name,child.sys_class_name,child.ip_address,type.name",
            "sysparm_limit": 50,
        })

        depends_on = [
            {
                "name": r.get("parent.name", ""),
                "type": r.get("parent.sys_class_name", ""),
                "ip_address": r.get("parent.ip_address", ""),
                "relationship": r.get("type.name", ""),
            }
            for r in as_child.get("result", [])
        ]
        used_by = [
            {
                "name": r.get("child.name", ""),
                "type": r.get("child.sys_class_name", ""),
                "ip_address": r.get("child.ip_address", ""),
                "relationship": r.get("type.name", ""),
            }
            for r in as_parent.get("result", [])
        ]

        return json.dumps({
            "status": "success",
            "ci": ci,
            "depends_on": depends_on,
            "used_by": used_by,
            "total_relationships": len(depends_on) + len(used_by),
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


if __name__ == "__main__":
    mcp.run()
