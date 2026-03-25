"""
Tetragon 이벤트 시뮬레이터 — 로컬 테스트용.

두 가지 모드:
  1. --direct: FastAPI의 POST /ingest/events로 직접 전송 (OTel 없이)
  2. --otel:   OTel Collector의 OTLP HTTP receiver로 전송

실행:
  python -m scripts.simulate_tetragon --direct
  python -m scripts.simulate_tetragon --otel
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

SERVERS = {
    "web-prod-kr-042": {"ip": "10.0.3.42", "services": [("nginx", "/usr/sbin/nginx", 1842)]},
    "api-prod-kr-010": {"ip": "10.0.3.100", "services": [("java", "/usr/bin/java", 2901)]},
    "db-prod-kr-001": {"ip": "10.0.3.200", "services": [("postgres", "/usr/bin/postgres", 3100)]},
}

CONNECTIONS = [
    {
        "src_host": "web-prod-kr-042",
        "dst_host": "api-prod-kr-010",
        "dst_port": 8080,
        "protocol": "TCP",
        "label": "nginx → springboot-api (HTTP)",
    },
    {
        "src_host": "api-prod-kr-010",
        "dst_host": "db-prod-kr-001",
        "dst_port": 5432,
        "protocol": "TCP",
        "label": "springboot-api → postgresql (TCP)",
    },
]


def _make_connection_event(
    hostname: str,
    ip: str,
    pid: int,
    binary: str,
    direction: str,
    src_ip: str,
    src_port: int,
    dst_ip: str,
    dst_port: int,
) -> dict:
    return {
        "server_hostname": hostname,
        "server_ip": ip,
        "process": {"pid": pid, "binary": binary},
        "direction": direction,
        "protocol": "TCP",
        "src_ip": src_ip,
        "src_port": src_port,
        "dst_ip": dst_ip,
        "dst_port": dst_port,
    }


def _make_otlp_log(hostname: str, event_json: str) -> dict:
    """OTLP ExportLogsServiceRequest 포맷으로 감싼다."""
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "host.name", "value": {"stringValue": hostname}},
                        {"key": "service.name", "value": {"stringValue": "tetragon"}},
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "tetragon"},
                        "logRecords": [
                            {
                                "body": {"stringValue": event_json},
                                "attributes": [
                                    {
                                        "key": "event.type",
                                        "value": {"stringValue": "network_connection"},
                                    },
                                ],
                                "timeUnixNano": str(int(time.time() * 1e9)),
                                "severityNumber": 9,
                                "severityText": "INFO",
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _post_json(url: str, data: dict) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def send_direct(api_url: str) -> None:
    """FastAPI /ingest/events로 직접 전송."""
    print(f"\n=== Direct mode → {api_url}/ingest/events ===\n")

    src_port = 50000
    for conn in CONNECTIONS:
        src = SERVERS[conn["src_host"]]
        dst = SERVERS[conn["dst_host"]]
        src_svc = src["services"][0]
        dst_svc = dst["services"][0]

        src_port += 1

        # Outbound event (source 서버)
        outbound = _make_connection_event(
            conn["src_host"],
            src["ip"],
            src_svc[2],
            src_svc[1],
            "outbound",
            src["ip"],
            src_port,
            dst["ip"],
            conn["dst_port"],
        )
        print(f"  [OUT] {conn['label']}")
        result = _post_json(
            f"{api_url}/ingest/events",
            {
                "event_type": "network_connection",
                "payload": outbound,
            },
        )
        print(f"        → {result}")

        time.sleep(0.2)

        # Inbound event (target 서버)
        inbound = _make_connection_event(
            conn["dst_host"],
            dst["ip"],
            dst_svc[2],
            dst_svc[1],
            "inbound",
            src["ip"],
            src_port,
            dst["ip"],
            conn["dst_port"],
        )
        print(f"  [IN]  {conn['label']}")
        result = _post_json(
            f"{api_url}/ingest/events",
            {
                "event_type": "network_connection",
                "payload": inbound,
            },
        )
        print(f"        → {result}")

        time.sleep(0.2)

    print(f"\n=== {len(CONNECTIONS) * 2}개 이벤트 전송 완료 ===")
    print("Correlator가 매칭할 때까지 잠시 대기...")
    time.sleep(4)

    print("\n=== 토폴로지 확인 ===")
    topo = _post_json(f"{api_url}/api/topology", {}) if False else None
    req = urllib.request.Request(f"{api_url}/api/topology")
    with urllib.request.urlopen(req, timeout=5) as resp:
        topo = json.loads(resp.read().decode())
    print(json.dumps(topo, indent=2, ensure_ascii=False))

    print("\n=== Health ===")
    req = urllib.request.Request(f"{api_url}/health")
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(json.dumps(json.loads(resp.read().decode()), indent=2))


def send_via_otel(otel_url: str) -> None:
    """OTel Collector OTLP HTTP receiver로 전송."""
    print(f"\n=== OTel mode → {otel_url}/v1/logs ===\n")

    src_port = 50100
    for conn in CONNECTIONS:
        src = SERVERS[conn["src_host"]]
        dst = SERVERS[conn["dst_host"]]
        src_svc = src["services"][0]
        dst_svc = dst["services"][0]

        src_port += 1

        # Outbound
        outbound = _make_connection_event(
            conn["src_host"],
            src["ip"],
            src_svc[2],
            src_svc[1],
            "outbound",
            src["ip"],
            src_port,
            dst["ip"],
            conn["dst_port"],
        )
        otlp_out = _make_otlp_log(conn["src_host"], json.dumps(outbound))
        print(f"  [OUT] {conn['label']}")
        result = _post_json(f"{otel_url}/v1/logs", otlp_out)
        print(f"        → {result}")

        time.sleep(0.2)

        # Inbound
        inbound = _make_connection_event(
            conn["dst_host"],
            dst["ip"],
            dst_svc[2],
            dst_svc[1],
            "inbound",
            src["ip"],
            src_port,
            dst["ip"],
            conn["dst_port"],
        )
        otlp_in = _make_otlp_log(conn["dst_host"], json.dumps(inbound))
        print(f"  [IN]  {conn['label']}")
        result = _post_json(f"{otel_url}/v1/logs", otlp_in)
        print(f"        → {result}")

        time.sleep(0.2)

    print(f"\n=== {len(CONNECTIONS) * 2}개 이벤트를 OTel Collector로 전송 완료 ===")
    print("OTel batch (2s) + Correlator 매칭 대기...")
    time.sleep(6)

    print("\n=== 토폴로지 확인 (FastAPI) ===")
    req = urllib.request.Request("http://localhost:8000/api/topology")
    with urllib.request.urlopen(req, timeout=5) as resp:
        topo = json.loads(resp.read().decode())
    print(json.dumps(topo, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Tetragon 이벤트 시뮬레이터")
    parser.add_argument(
        "--mode",
        choices=["direct", "otel"],
        default="direct",
        help="direct: FastAPI 직접 전송, otel: OTel Collector 경유",
    )
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--otel-url", default="http://localhost:4318")
    args = parser.parse_args()

    if args.mode == "direct":
        send_direct(args.api_url)
    else:
        send_via_otel(args.otel_url)


if __name__ == "__main__":
    main()
