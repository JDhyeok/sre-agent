"""Elasticsearch MCP Server - provides log search and analysis tools via FastMCP.

Exposes Elasticsearch log data to agents with built-in pattern extraction
and timeline aggregation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter

import httpx
from fastmcp import FastMCP

_log = logging.getLogger("sre_agent.mcp.elasticsearch")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

ELASTICSEARCH_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
DEFAULT_INDEX = os.environ.get("ELASTICSEARCH_DEFAULT_INDEX", "app-logs-*")
MAX_RESULTS = int(os.environ.get("ELASTICSEARCH_MAX_RESULTS", "500"))

mcp = FastMCP("Elasticsearch Log Analysis Server")
_client = httpx.Client(timeout=30.0)


def _es_request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{ELASTICSEARCH_URL}/{path}"
    if method == "GET":
        resp = _client.get(url, params={"format": "json"} if not body else None)
    else:
        resp = _client.post(url, json=body, headers={"Content-Type": "application/json"})
    resp.raise_for_status()
    return resp.json()


def _templatize_message(message: str) -> str:
    """Replace variable parts of a log message with placeholders for grouping."""
    result = message
    result = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<UUID>", result)
    result = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<IP>", result)
    result = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*", "<TIMESTAMP>", result)
    result = re.sub(r"\b\d{10,13}\b", "<EPOCH>", result)
    result = re.sub(r"\b0x[0-9a-fA-F]+\b", "<HEX>", result)
    result = re.sub(r'(?<=[=:])\s*\d+(?:\.\d+)?(?=[\s,})\]]|$)', " <NUM>", result)
    return result.strip()


def _do_search_logs(
    query: str = "",
    index: str = "",
    time_range_minutes: int = 60,
    log_level: str = "",
    service: str = "",
    max_results: int = 100,
) -> str:
    """Internal: search logs and return JSON string."""
    target_index = index or DEFAULT_INDEX
    max_results = min(max_results, MAX_RESULTS)

    must_clauses: list[dict] = []
    must_clauses.append({"range": {"@timestamp": {"gte": f"now-{time_range_minutes}m", "lte": "now"}}})

    if query:
        must_clauses.append({"query_string": {"query": query}})
    if log_level:
        must_clauses.append({"term": {"level": log_level.lower()}})
    if service:
        must_clauses.append({"term": {"service": service}})

    body = {
        "query": {"bool": {"must": must_clauses}},
        "sort": [{"@timestamp": "desc"}],
        "size": max_results,
    }

    try:
        data = _es_request("POST", f"{target_index}/_search", body)
        hits = data.get("hits", {})
        total = hits.get("total", {}).get("value", 0)

        logs = []
        for hit in hits.get("hits", []):
            src = hit.get("_source", {})
            logs.append({
                "timestamp": src.get("@timestamp", ""),
                "level": src.get("level", ""),
                "service": src.get("service", ""),
                "host": src.get("host", {}).get("name", src.get("hostname", "")),
                "message": src.get("message", ""),
            })

        return json.dumps({
            "status": "success",
            "index": target_index,
            "total_hits": total,
            "returned": len(logs),
            "logs": logs,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def search_logs(
    query: str,
    index: str = "",
    time_range_minutes: int = 60,
    log_level: str = "",
    service: str = "",
    max_results: int = 100,
) -> str:
    """Search Elasticsearch logs with filters.

    Args:
        query: Search query string (Lucene query syntax)
        index: Elasticsearch index pattern (default: configured default)
        time_range_minutes: How far back to search in minutes (default: 60)
        log_level: Filter by log level (e.g. 'error', 'warn', 'info')
        service: Filter by service name
        max_results: Maximum number of log entries to return (default: 100, max: 500)

    Returns:
        JSON with matching log entries and hit count.
    """
    _log.info("search_logs: query=%r index=%s range=%dm level=%s service=%s", query, index or DEFAULT_INDEX, time_range_minutes, log_level, service)
    return _do_search_logs(query, index, time_range_minutes, log_level, service, max_results)


def _do_error_patterns(
    index: str = "",
    time_range_minutes: int = 60,
    service: str = "",
    top_n: int = 10,
) -> str:
    """Internal: extract error patterns and return JSON string."""
    target_index = index or DEFAULT_INDEX

    must_clauses: list[dict] = [
        {"range": {"@timestamp": {"gte": f"now-{time_range_minutes}m", "lte": "now"}}},
        {"terms": {"level": ["error", "fatal", "critical"]}},
    ]
    if service:
        must_clauses.append({"term": {"service": service}})

    body = {
        "query": {"bool": {"must": must_clauses}},
        "size": MAX_RESULTS,
        "_source": ["message", "@timestamp", "service"],
    }

    try:
        data = _es_request("POST", f"{target_index}/_search", body)
        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)

        template_counter: Counter = Counter()
        template_samples: dict[str, list[str]] = {}

        for hit in hits:
            msg = hit.get("_source", {}).get("message", "")
            if not msg:
                continue
            template = _templatize_message(msg)
            template_counter[template] += 1
            if template not in template_samples:
                template_samples[template] = []
            if len(template_samples[template]) < 3:
                template_samples[template].append(msg[:300])

        patterns = []
        for template, count in template_counter.most_common(top_n):
            patterns.append({
                "template": template,
                "count": count,
                "percentage": round((count / len(hits)) * 100, 1) if hits else 0,
                "sample_messages": template_samples.get(template, []),
            })

        return json.dumps({
            "status": "success",
            "total_errors_analyzed": total,
            "fetched_for_analysis": len(hits),
            "unique_patterns": len(template_counter),
            "top_patterns": patterns,
            "analysis_insight": _summarize_patterns(patterns, total),
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


def _summarize_patterns(patterns: list[dict], total: int) -> str:
    if not patterns:
        return "No error patterns found in the specified time range."
    top = patterns[0]
    return (
        f"Found {len(patterns)} unique error patterns from {total} total errors. "
        f"The dominant pattern ({top['percentage']}% of errors) is: \"{top['template'][:100]}...\""
    )


@mcp.tool()
def get_error_patterns(
    index: str = "",
    time_range_minutes: int = 60,
    service: str = "",
    top_n: int = 10,
) -> str:
    """Extract and group error log patterns by frequency.

    Args:
        index: Elasticsearch index pattern (default: configured default)
        time_range_minutes: How far back to search in minutes (default: 60)
        service: Filter by service name
        top_n: Number of top patterns to return (default: 10)

    Returns:
        JSON with error patterns sorted by frequency, including sample messages.
    """
    _log.info("get_error_patterns: index=%s range=%dm service=%s", index or DEFAULT_INDEX, time_range_minutes, service)
    return _do_error_patterns(index, time_range_minutes, service, top_n)


@mcp.tool()
def get_log_timeline(
    index: str = "",
    time_range_minutes: int = 60,
    interval: str = "1m",
    log_level: str = "",
    service: str = "",
) -> str:
    """Get a time-series view of log counts for trend analysis.

    Args:
        index: Elasticsearch index pattern (default: configured default)
        time_range_minutes: How far back to aggregate (default: 60)
        interval: Bucket interval (e.g. '1m', '5m', '1h')
        log_level: Filter by log level (e.g. 'error')
        service: Filter by service name

    Returns:
        JSON with time-bucketed log counts showing trends.
    """
    target_index = index or DEFAULT_INDEX
    _log.info("get_log_timeline: index=%s range=%dm interval=%s level=%s service=%s", target_index, time_range_minutes, interval, log_level, service)

    must_clauses: list[dict] = [
        {"range": {"@timestamp": {"gte": f"now-{time_range_minutes}m", "lte": "now"}}},
    ]
    if log_level:
        must_clauses.append({"term": {"level": log_level.lower()}})
    if service:
        must_clauses.append({"term": {"service": service}})

    body = {
        "query": {"bool": {"must": must_clauses}},
        "size": 0,
        "aggs": {
            "log_timeline": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": interval,
                },
            },
        },
    }

    try:
        data = _es_request("POST", f"{target_index}/_search", body)
        buckets = data.get("aggregations", {}).get("log_timeline", {}).get("buckets", [])

        timeline = []
        counts = []
        for bucket in buckets:
            count = bucket.get("doc_count", 0)
            timeline.append({
                "timestamp": bucket.get("key_as_string", ""),
                "count": count,
            })
            counts.append(count)

        trend = "stable"
        if len(counts) >= 4:
            first_half = sum(counts[: len(counts) // 2])
            second_half = sum(counts[len(counts) // 2 :])
            if second_half > first_half * 1.5:
                trend = "increasing"
            elif second_half < first_half * 0.5:
                trend = "decreasing"

        return json.dumps({
            "status": "success",
            "interval": interval,
            "buckets": len(timeline),
            "total_count": sum(counts),
            "trend": trend,
            "timeline": timeline,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def get_field_aggregation(
    field: str,
    index: str = "",
    time_range_minutes: int = 60,
    log_level: str = "",
    top_n: int = 20,
) -> str:
    """Aggregate logs by a specific field (e.g. service, host, status_code).

    Args:
        field: Field name to aggregate on (e.g. 'service', 'host.name', 'status_code')
        index: Elasticsearch index pattern (default: configured default)
        time_range_minutes: How far back to aggregate (default: 60)
        log_level: Filter by log level
        top_n: Number of top values to return (default: 20)

    Returns:
        JSON with top values for the specified field and their counts.
    """
    target_index = index or DEFAULT_INDEX
    _log.info("get_field_aggregation: field=%s index=%s range=%dm level=%s", field, target_index, time_range_minutes, log_level)

    must_clauses: list[dict] = [
        {"range": {"@timestamp": {"gte": f"now-{time_range_minutes}m", "lte": "now"}}},
    ]
    if log_level:
        must_clauses.append({"term": {"level": log_level.lower()}})

    keyword_field = field if field.endswith(".keyword") else f"{field}.keyword"

    body = {
        "query": {"bool": {"must": must_clauses}},
        "size": 0,
        "aggs": {
            "field_values": {
                "terms": {
                    "field": keyword_field,
                    "size": top_n,
                },
            },
        },
    }

    try:
        data = _es_request("POST", f"{target_index}/_search", body)
        buckets = data.get("aggregations", {}).get("field_values", {}).get("buckets", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)

        values = []
        for bucket in buckets:
            values.append({
                "value": bucket.get("key", ""),
                "count": bucket.get("doc_count", 0),
                "percentage": round((bucket.get("doc_count", 0) / total) * 100, 1) if total else 0,
            })

        return json.dumps({
            "status": "success",
            "field": field,
            "total_docs": total,
            "unique_values": len(values),
            "top_values": values,
        })
    except httpx.HTTPError as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def batch_search(queries: str) -> str:
    """Execute multiple log searches in a single call.

    Far more efficient than calling search_logs or get_error_patterns multiple
    times. Use this tool when you need to query 2 or more log searches at once.

    Args:
        queries: A JSON array of search objects. Each object accepts:
            - type (str): "search" (default) or "error_patterns"
            - query (str): Lucene query string (for type "search")
            - index (str): Index pattern (default: configured default)
            - time_range_minutes (int): default 60
            - log_level (str): Filter by level
            - service (str): Filter by service
            - max_results (int): For "search" type (default: 50, max: 500)
            - top_n (int): For "error_patterns" type (default: 10)

        Example:
            [
              {"type": "search", "query": "error", "service": "payment-api", "max_results": 20},
              {"type": "error_patterns", "service": "payment-api", "time_range_minutes": 30},
              {"type": "search", "query": "timeout", "log_level": "error"}
            ]

    Returns:
        JSON with an array of results, one per input query, in the same order.
    """
    _log.info("batch_search: %s", queries)
    try:
        query_list = json.loads(queries)
    except (json.JSONDecodeError, TypeError) as e:
        return json.dumps({"status": "error", "error": f"Invalid JSON input: {e}"})

    if not isinstance(query_list, list):
        return json.dumps({"status": "error", "error": "Input must be a JSON array of search objects."})

    results = []
    for q in query_list:
        qtype = q.get("type", "search")

        if qtype == "error_patterns":
            result = json.loads(_do_error_patterns(
                index=q.get("index", ""),
                time_range_minutes=q.get("time_range_minutes", 60),
                service=q.get("service", ""),
                top_n=q.get("top_n", 10),
            ))
        else:
            result = json.loads(_do_search_logs(
                query=q.get("query", "*"),
                index=q.get("index", ""),
                time_range_minutes=q.get("time_range_minutes", 60),
                log_level=q.get("log_level", ""),
                service=q.get("service", ""),
                max_results=q.get("max_results", 50),
            ))
        results.append(result)

    return json.dumps({
        "status": "success",
        "query_count": len(results),
        "results": results,
    })


if __name__ == "__main__":
    mcp.run()
