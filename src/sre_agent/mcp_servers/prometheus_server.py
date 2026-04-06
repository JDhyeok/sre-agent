"""Prometheus MCP Server - provides metrics querying tools via FastMCP.

Exposes Prometheus and Alertmanager data to agents with built-in baseline
comparison and anomaly severity classification.
"""

from __future__ import annotations

import json
import os
import statistics
import time

import httpx
from fastmcp import FastMCP

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL", "http://localhost:9093")
BASELINE_WINDOW_HOURS = int(os.environ.get("PROMETHEUS_BASELINE_HOURS", "24"))

mcp = FastMCP("Prometheus Observability Server")
_client = httpx.Client(timeout=30.0)


def _classify_severity(deviation_percent: float) -> str:
    abs_dev = abs(deviation_percent)
    if abs_dev > 200:
        return "critical"
    if abs_dev > 100:
        return "warning"
    if abs_dev > 50:
        return "info"
    return "normal"


def _prom_query(endpoint: str, params: dict) -> dict:
    resp = _client.get(f"{PROMETHEUS_URL}{endpoint}", params=params)
    resp.raise_for_status()
    return resp.json()


def _alertmanager_query(endpoint: str) -> dict:
    resp = _client.get(f"{ALERTMANAGER_URL}{endpoint}")
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def query_instant(query: str) -> str:
    """Execute a PromQL instant query and return current metric values.

    Args:
        query: A valid PromQL expression (e.g. 'up', 'rate(http_requests_total[5m])')

    Returns:
        JSON with query results including metric name, labels, and current value.
    """
    try:
        data = _prom_query("/api/v1/query", {"query": query, "time": time.time()})
        results = data.get("data", {}).get("result", [])

        formatted = []
        for r in results:
            formatted.append({
                "metric": r.get("metric", {}),
                "value": r.get("value", [None, None])[1],
                "timestamp": r.get("value", [None, None])[0],
            })

        return json.dumps({"status": "success", "query": query, "result_count": len(formatted), "results": formatted})
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "query": query, "error": str(e)})


@mcp.tool()
def query_range(query: str, duration_minutes: int = 60, step: str = "60s") -> str:
    """Execute a PromQL range query with automatic baseline comparison.

    Queries the metric for the specified duration, computes a baseline from the
    past 24 hours, and classifies anomaly severity.

    Args:
        query: A valid PromQL expression
        duration_minutes: How many minutes of recent data to query (default: 60)
        step: Query resolution step (default: '60s')

    Returns:
        JSON with current values, baseline comparison, deviation percentage,
        and severity classification (critical/warning/info/normal).
    """
    try:
        now = time.time()
        start = now - (duration_minutes * 60)

        current_data = _prom_query(
            "/api/v1/query_range",
            {"query": query, "start": start, "end": now, "step": step},
        )

        baseline_start = now - (BASELINE_WINDOW_HOURS * 3600)
        baseline_end = start
        baseline_data = _prom_query(
            "/api/v1/query_range",
            {"query": query, "start": baseline_start, "end": baseline_end, "step": "300s"},
        )

        results = []
        for series in current_data.get("data", {}).get("result", []):
            values = [float(v[1]) for v in series.get("values", []) if v[1] != "NaN"]
            current_avg = statistics.mean(values) if values else 0.0

            baseline_values = []
            for b_series in baseline_data.get("data", {}).get("result", []):
                if b_series.get("metric") == series.get("metric"):
                    baseline_values = [float(v[1]) for v in b_series.get("values", []) if v[1] != "NaN"]
                    break

            baseline_median = statistics.median(baseline_values) if baseline_values else 0.0
            if baseline_median > 0:
                deviation = ((current_avg - baseline_median) / baseline_median) * 100
            else:
                deviation = 0.0 if current_avg == 0 else 100.0

            severity = _classify_severity(deviation)

            results.append({
                "metric": series.get("metric", {}),
                "current_average": round(current_avg, 4),
                "baseline_median": round(baseline_median, 4),
                "deviation_percent": round(deviation, 2),
                "severity": severity,
                "is_anomaly": severity in ("critical", "warning"),
                "data_points_current": len(values),
                "data_points_baseline": len(baseline_values),
                "interpretation": _interpret_deviation(query, deviation, severity, current_avg, baseline_median),
            })

        return json.dumps({"status": "success", "query": query, "duration_minutes": duration_minutes, "results": results})
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "query": query, "error": str(e)})


def _interpret_deviation(query: str, deviation: float, severity: str, current: float, baseline: float) -> str:
    direction = "increased" if deviation > 0 else "decreased"
    if severity == "critical":
        return f"CRITICAL: '{query}' has {direction} by {abs(deviation):.1f}% from baseline ({baseline:.4f} -> {current:.4f}). Immediate investigation recommended."
    if severity == "warning":
        return f"WARNING: '{query}' has {direction} by {abs(deviation):.1f}% from baseline ({baseline:.4f} -> {current:.4f}). Monitor closely."
    if severity == "info":
        return f"INFO: '{query}' shows notable {direction} of {abs(deviation):.1f}% from baseline."
    return f"NORMAL: '{query}' is within expected range (deviation: {deviation:.1f}%)."


@mcp.tool()
def get_active_alerts() -> str:
    """Retrieve all currently firing alerts from Alertmanager.

    Returns:
        JSON with list of active alerts including name, severity, labels,
        annotations, and duration.
    """
    try:
        data = _alertmanager_query("/api/v2/alerts")

        alerts = []
        for alert in data:
            if alert.get("status", {}).get("state") != "active":
                continue
            labels = alert.get("labels", {})
            alerts.append({
                "alertname": labels.get("alertname", "unknown"),
                "severity": labels.get("severity", "unknown"),
                "labels": labels,
                "annotations": alert.get("annotations", {}),
                "starts_at": alert.get("startsAt", ""),
                "generator_url": alert.get("generatorURL", ""),
            })

        return json.dumps({"status": "success", "active_alert_count": len(alerts), "alerts": alerts})
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_targets_health() -> str:
    """Check the health status of all Prometheus scrape targets.

    Returns:
        JSON with target health information grouped by job, including
        any targets that are down.
    """
    try:
        data = _prom_query("/api/v1/targets", {})
        active = data.get("data", {}).get("activeTargets", [])

        healthy = []
        unhealthy = []
        for target in active:
            info = {
                "job": target.get("labels", {}).get("job", "unknown"),
                "instance": target.get("labels", {}).get("instance", "unknown"),
                "health": target.get("health", "unknown"),
                "last_scrape": target.get("lastScrape", ""),
                "last_error": target.get("lastError", ""),
            }
            if target.get("health") == "up":
                healthy.append(info)
            else:
                unhealthy.append(info)

        return json.dumps({
            "status": "success",
            "total_targets": len(active),
            "healthy_count": len(healthy),
            "unhealthy_count": len(unhealthy),
            "unhealthy_targets": unhealthy,
            "healthy_targets": healthy,
            "summary": f"{len(unhealthy)} of {len(active)} targets are unhealthy" if unhealthy else "All targets healthy",
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


if __name__ == "__main__":
    mcp.run()
