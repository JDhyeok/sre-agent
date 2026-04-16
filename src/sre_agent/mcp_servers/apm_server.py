"""HMG-APM MCP Server — Scouter-based APM data collection via REST API.

Provides application performance metrics including JVM instance status,
active service requests, transaction traces (XLog), and thread dumps.
"""

from __future__ import annotations

import json
import logging
import os

import httpx
from fastmcp import FastMCP

HMG_APM_URL = os.environ.get("HMG_APM_URL", "")
HMG_APM_API_KEY = os.environ.get("HMG_APM_API_KEY", "")
HMG_APM_TIMEOUT = int(os.environ.get("HMG_APM_TIMEOUT", "30"))

mcp = FastMCP("HMG-APM Observability Server")
_client = httpx.Client(timeout=HMG_APM_TIMEOUT)
_log = logging.getLogger("sre_agent.mcp.apm")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json"}
    if HMG_APM_API_KEY:
        h["Authorization"] = f"Bearer {HMG_APM_API_KEY}"
    return h


def _api_get(path: str, params: dict | None = None) -> dict | list:
    url = f"{HMG_APM_URL.rstrip('/')}{path}"
    resp = _client.get(url, headers=_headers(), params=params)
    resp.raise_for_status()
    return resp.json()


def _api_post(path: str, body: dict | None = None) -> dict | list:
    url = f"{HMG_APM_URL.rstrip('/')}{path}"
    resp = _client.post(url, headers=_headers(), json=body)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Internal functions (called by both individual tools and batch tool)
# ---------------------------------------------------------------------------

def _do_get_objects() -> str:
    """List all monitored JVM/application instances with their status."""
    try:
        data = _api_get("/api/v1/objects")
        objects = []
        if isinstance(data, list):
            for obj in data:
                objects.append({
                    "object_id": obj.get("objHash") or obj.get("object_id", ""),
                    "name": obj.get("objName") or obj.get("name", ""),
                    "type": obj.get("objType") or obj.get("type", ""),
                    "host": obj.get("address") or obj.get("host", ""),
                    "alive": obj.get("alive", True),
                })
        elif isinstance(data, dict):
            for key, obj in data.items():
                objects.append({
                    "object_id": obj.get("objHash") or key,
                    "name": obj.get("objName") or obj.get("name", key),
                    "type": obj.get("objType") or obj.get("type", ""),
                    "host": obj.get("address") or obj.get("host", ""),
                    "alive": obj.get("alive", True),
                })

        alive_count = sum(1 for o in objects if o["alive"])
        return json.dumps({
            "status": "success",
            "total": len(objects),
            "alive": alive_count,
            "dead": len(objects) - alive_count,
            "objects": objects,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


def _do_get_active_services(object_id: str) -> str:
    """Get active service (request) list for a specific object."""
    try:
        data = _api_get(f"/api/v1/objects/{object_id}/active-services")
        services = []
        if isinstance(data, list):
            for svc in data:
                services.append({
                    "service": svc.get("serviceName") or svc.get("service", ""),
                    "elapsed_ms": svc.get("elapsed") or svc.get("elapsed_ms", 0),
                    "thread": svc.get("threadName") or svc.get("thread", ""),
                    "status": svc.get("status", "running"),
                    "sql": svc.get("sql", ""),
                    "subcall": svc.get("subcall", ""),
                })

        return json.dumps({
            "status": "success",
            "object_id": object_id,
            "active_count": len(services),
            "services": services,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "object_id": object_id, "error": str(e)})


def _do_get_xlog(object_id: str, duration_minutes: int = 10) -> str:
    """Get recent transaction traces (XLog) for a specific object."""
    try:
        data = _api_get(
            f"/api/v1/objects/{object_id}/xlog",
            params={"duration": duration_minutes},
        )
        transactions = []
        if isinstance(data, list):
            for tx in data[:100]:
                transactions.append({
                    "service": tx.get("serviceName") or tx.get("service", ""),
                    "elapsed_ms": tx.get("elapsed") or tx.get("elapsed_ms", 0),
                    "error": tx.get("error", False),
                    "timestamp": tx.get("endTime") or tx.get("timestamp", ""),
                    "cpu_time_ms": tx.get("cpu") or tx.get("cpu_time_ms", 0),
                    "sql_count": tx.get("sqlCount") or tx.get("sql_count", 0),
                    "sql_time_ms": tx.get("sqlTime") or tx.get("sql_time_ms", 0),
                    "apicall_count": tx.get("apicallCount") or tx.get("apicall_count", 0),
                    "apicall_time_ms": tx.get("apicallTime") or tx.get("apicall_time_ms", 0),
                })

        error_count = sum(1 for t in transactions if t["error"])
        avg_elapsed = (
            sum(t["elapsed_ms"] for t in transactions) / len(transactions)
            if transactions else 0
        )

        return json.dumps({
            "status": "success",
            "object_id": object_id,
            "duration_minutes": duration_minutes,
            "total_transactions": len(transactions),
            "error_count": error_count,
            "avg_elapsed_ms": round(avg_elapsed, 1),
            "transactions": transactions,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "object_id": object_id, "error": str(e)})


def _do_get_thread_dump(object_id: str) -> str:
    """Get thread dump for stuck-thread analysis."""
    try:
        data = _api_get(f"/api/v1/objects/{object_id}/thread-dump")
        threads = []
        if isinstance(data, list):
            for t in data:
                threads.append({
                    "name": t.get("name") or t.get("threadName", ""),
                    "state": t.get("state") or t.get("threadState", ""),
                    "cpu_time_ms": t.get("cpu") or t.get("cpuTime", 0),
                    "stack_trace": t.get("stackTrace", "")[:500],
                    "daemon": t.get("daemon", False),
                })

        state_counts: dict[str, int] = {}
        for t in threads:
            s = t["state"]
            state_counts[s] = state_counts.get(s, 0) + 1

        return json.dumps({
            "status": "success",
            "object_id": object_id,
            "total_threads": len(threads),
            "state_summary": state_counts,
            "threads": threads[:50],
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "object_id": object_id, "error": str(e)})


# ---------------------------------------------------------------------------
# MCP tool wrappers
# ---------------------------------------------------------------------------

@mcp.tool()
def get_apm_objects() -> str:
    """List all monitored JVM/application instances with their alive/dead status.

    Returns:
        JSON with object list including object_id, name, type, host, alive status.
    """
    _log.info("get_apm_objects")
    return _do_get_objects()


@mcp.tool()
def get_active_services(object_id: str) -> str:
    """Get active service requests currently running on a JVM instance.

    Useful for detecting stuck threads, long-running requests, or high concurrency.

    Args:
        object_id: The APM object ID (from get_apm_objects)

    Returns:
        JSON with active service list including service name, elapsed time, thread info.
    """
    _log.info("get_active_services: object_id=%s", object_id)
    return _do_get_active_services(object_id)


@mcp.tool()
def get_xlog_data(object_id: str, duration_minutes: int = 10) -> str:
    """Get recent transaction traces (XLog) for performance analysis.

    Shows response times, error rates, SQL/API call counts per transaction.

    Args:
        object_id: The APM object ID
        duration_minutes: How far back to fetch (default: 10)

    Returns:
        JSON with transaction traces including elapsed time, error status,
        SQL/API call stats, and summary statistics.
    """
    _log.info("get_xlog_data: object_id=%s duration=%dm", object_id, duration_minutes)
    return _do_get_xlog(object_id, duration_minutes)


@mcp.tool()
def get_thread_dump(object_id: str) -> str:
    """Get thread dump for stuck-thread and deadlock analysis.

    Args:
        object_id: The APM object ID

    Returns:
        JSON with thread list including name, state, CPU time, and stack traces.
    """
    _log.info("get_thread_dump: object_id=%s", object_id)
    return _do_get_thread_dump(object_id)


@mcp.tool()
def batch_apm_query(queries: str) -> str:
    """Execute multiple APM queries in a single call.

    Args:
        queries: A JSON array of query objects. Each object accepts:
            - type (str, required): "objects", "active_services", "xlog", or "thread_dump"
            - object_id (str): Required for all except "objects"
            - duration_minutes (int): For "xlog" type only (default: 10)

        Example:
            [
              {"type": "objects"},
              {"type": "xlog", "object_id": "abc123", "duration_minutes": 5},
              {"type": "active_services", "object_id": "abc123"}
            ]

    Returns:
        JSON with an array of results, one per input query, in the same order.
    """
    _log.info("batch_apm_query: %s", queries)
    try:
        query_list = json.loads(queries)
    except (json.JSONDecodeError, TypeError) as e:
        return json.dumps({"status": "error", "error": f"Invalid JSON input: {e}"})

    if not isinstance(query_list, list):
        return json.dumps({"status": "error", "error": "Input must be a JSON array."})

    results = []
    for q in query_list:
        qtype = q.get("type", "")
        obj_id = q.get("object_id", "")

        if qtype == "objects":
            result = json.loads(_do_get_objects())
        elif qtype == "active_services" and obj_id:
            result = json.loads(_do_get_active_services(obj_id))
        elif qtype == "xlog" and obj_id:
            result = json.loads(_do_get_xlog(obj_id, q.get("duration_minutes", 10)))
        elif qtype == "thread_dump" and obj_id:
            result = json.loads(_do_get_thread_dump(obj_id))
        else:
            result = {"status": "error", "error": f"Unknown type '{qtype}' or missing object_id"}
        results.append(result)

    return json.dumps({
        "status": "success",
        "query_count": len(results),
        "results": results,
    })


if __name__ == "__main__":
    mcp.run()
