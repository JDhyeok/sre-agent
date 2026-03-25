"""
OTLP → ConnectionEvent 변환 어댑터.

OTel Collector의 otlphttp exporter가 보내는 OTLP JSON 로그를
Tetragon 이벤트로 파싱해서 내부 이벤트 큐에 넣는다.

OTLP Log Export 형식:
{
  "resourceLogs": [{
    "resource": { "attributes": [{"key": "host.name", "value": {"stringValue": "..."}}] },
    "scopeLogs": [{
      "logRecords": [{
        "body": { "stringValue": "{...tetragon JSON...}" },
        "attributes": [...]
      }]
    }]
  }]
}
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.correlator.receiver import enqueue_event

logger = logging.getLogger(__name__)


def _extract_attr(attributes: list[dict], key: str) -> str | None:
    for attr in attributes:
        if attr.get("key") == key:
            val = attr.get("value", {})
            return val.get("stringValue") or val.get("intValue") or val.get("boolValue")
    return None


def _get_string_value(obj: dict) -> str | None:
    if "stringValue" in obj:
        return obj["stringValue"]
    if "kvlistValue" in obj:
        return json.dumps(obj["kvlistValue"])
    return None


def process_otlp_logs(data: dict[str, Any]) -> int:
    """OTLP ExportLogsServiceRequest JSON을 처리한다. 큐에 넣은 이벤트 수를 반환."""
    count = 0
    resource_logs = data.get("resourceLogs", [])

    for rl in resource_logs:
        resource_attrs = rl.get("resource", {}).get("attributes", [])
        hostname = _extract_attr(resource_attrs, "host.name") or "unknown"

        for sl in rl.get("scopeLogs", []):
            for record in sl.get("logRecords", []):
                try:
                    event = _parse_log_record(record, hostname)
                    if event:
                        enqueue_event(event["event_type"], event["payload"])
                        count += 1
                except Exception as e:
                    logger.warning("otlp_parse_failed", extra={"error": str(e)})

    return count


def _parse_log_record(record: dict, default_hostname: str) -> dict | None:
    """단일 OTLP LogRecord를 내부 이벤트로 변환한다."""
    body = record.get("body", {})
    body_str = _get_string_value(body)
    if not body_str:
        return None

    try:
        tetragon_event = json.loads(body_str)
    except (json.JSONDecodeError, TypeError):
        return None

    attrs = record.get("attributes", [])
    event_type = _extract_attr(attrs, "event.type") or "network_connection"

    if "process_kprobe" in tetragon_event:
        payload = _transform_kprobe(tetragon_event, default_hostname)
    elif "server_hostname" in tetragon_event:
        payload = tetragon_event
    else:
        payload = tetragon_event
        payload.setdefault("server_hostname", default_hostname)

    return {"event_type": event_type, "payload": payload}


def _transform_kprobe(raw: dict, default_hostname: str) -> dict:
    """Tetragon kprobe 이벤트를 ConnectionEvent 형식으로 변환한다."""
    kprobe = raw.get("process_kprobe", {})
    process = kprobe.get("process", {})
    args = kprobe.get("args", [])

    sock_arg = {}
    for arg in args:
        if "sock_arg" in arg:
            sock_arg = arg["sock_arg"]
            break

    func = kprobe.get("function_name", "")
    direction = "outbound" if "connect" in func else "inbound"

    return {
        "server_hostname": raw.get("node_name", default_hostname),
        "server_ip": sock_arg.get("saddr", "0.0.0.0"),
        "process": {
            "pid": process.get("pid", 0),
            "binary": process.get("binary", "unknown"),
            "args": process.get("arguments", "").split() if process.get("arguments") else [],
        },
        "direction": direction,
        "protocol": "TCP",
        "src_ip": sock_arg.get("saddr", "0.0.0.0"),
        "src_port": sock_arg.get("sport", 0),
        "dst_ip": sock_arg.get("daddr", "0.0.0.0"),
        "dst_port": sock_arg.get("dport", 0),
    }
