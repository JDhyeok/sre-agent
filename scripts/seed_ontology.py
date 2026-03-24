"""
시드 데이터 — SQLite + NetworkX 온톨로지에 샘플 데이터 투입.

실행: python scripts/seed_ontology.py
"""

from src.database import init_all_databases
from src.graph.store import GraphStore


def seed():
    init_all_databases()
    g = GraphStore()

    # ─── 서버 ─────────────────────────────────
    servers = [
        {
            "hostname": "web-prod-kr-042", "ip_addresses": ["10.0.3.42"],
            "os": "ubuntu-22.04", "kernel": "5.15.0-91", "environment": "production",
            "platform": "aws/ec2", "team_owner": "platform-sre", "owner_contact": "김SRE",
            "service_description": "결제 API 프론트 프록시", "business_impact": "critical",
            "caution_notes": ["nginx reload 시 30초 grace period 필요", "매월 1일 정산 배치 — PM 금지"],
            "maintenance_window": "화/목 02:00-06:00",
            "escalation": "1차 김SRE → 2차 박팀장 → 3차 CTO",
        },
        {
            "hostname": "api-prod-kr-010", "ip_addresses": ["10.0.3.100"],
            "os": "ubuntu-22.04", "kernel": "5.15.0-91", "environment": "production",
            "platform": "aws/ec2", "team_owner": "backend-sre", "owner_contact": "이SRE",
            "service_description": "결제 Spring Boot API", "business_impact": "critical",
            "caution_notes": ["JVM heap 8GB — 줄이지 말 것"],
            "maintenance_window": "화/목 02:00-06:00",
        },
        {
            "hostname": "db-prod-kr-001", "ip_addresses": ["10.0.3.200"],
            "os": "ubuntu-22.04", "environment": "production",
            "platform": "bare-metal", "team_owner": "dba-sre", "owner_contact": "박DBA",
            "service_description": "결제 PostgreSQL primary", "business_impact": "critical",
            "caution_notes": ["replication lag 확인 필수", "PM 시 replica promote 먼저"],
            "maintenance_window": "일 03:00-05:00",
        },
        {
            "hostname": "web-stg-kr-001", "ip_addresses": ["10.0.4.10"],
            "os": "ubuntu-22.04", "environment": "staging",
            "platform": "aws/ec2", "team_owner": "platform-sre", "owner_contact": "김SRE",
            "service_description": "스테이징 웹서버", "business_impact": "low",
            "maintenance_window": "언제든 가능",
        },
    ]

    for s in servers:
        g.upsert_node(f"server:{s['hostname']}", "Server", s)

    # ─── 서비스 + HOSTED_ON ────────────────────
    services = [
        ("nginx", "systemd", "web-prod-kr-042", [80, 443]),
        ("springboot-api", "systemd", "api-prod-kr-010", [8080]),
        ("postgresql", "systemd", "db-prod-kr-001", [5432]),
        ("nginx", "systemd", "web-stg-kr-001", [80, 443]),
    ]

    for name, stype, server, ports in services:
        svc_id = f"service:{name}@{server}"
        g.upsert_node(svc_id, "Service", {
            "name": name, "type": stype, "server": server, "listen_ports": ports,
        })
        g.upsert_edge(svc_id, f"server:{server}", "HOSTED_ON", {})

    # ─── 패키지 + INSTALLED_ON ────────────────
    packages = [
        ("openssl", "3.0.2", ["web-prod-kr-042", "api-prod-kr-010", "db-prod-kr-001"]),
        ("nginx", "1.24.0", ["web-prod-kr-042"]),
        ("openjdk-17", "17.0.9", ["api-prod-kr-010"]),
        ("postgresql-15", "15.4", ["db-prod-kr-001"]),
    ]

    for name, version, server_list in packages:
        pkg_id = f"package:{name}:{version}"
        g.upsert_node(pkg_id, "Package", {"name": name, "version": version, "manager": "apt"})
        for server in server_list:
            g.upsert_edge(pkg_id, f"server:{server}", "INSTALLED_ON", {})

    # ─── CALLS 관계 ──────────────────────────
    g.upsert_edge(
        "service:nginx@web-prod-kr-042",
        "service:springboot-api@api-prod-kr-010",
        "CALLS",
        {"protocol": "HTTP", "port": 8080, "confidence": "confirmed", "status": "active"},
    )
    g.upsert_edge(
        "service:springboot-api@api-prod-kr-010",
        "service:postgresql@db-prod-kr-001",
        "CALLS",
        {"protocol": "TCP", "port": 5432, "confidence": "confirmed", "status": "active"},
    )

    # ─── 장애 기록 ───────────────────────────
    g.upsert_node("incident:INC-2024-0903", "Incident", {
        "severity": "P1",
        "started_at": "2024-09-03T14:32:00Z",
        "resolved_at": "2024-09-03T15:15:00Z",
        "mttr_minutes": 43,
        "root_cause": "openssl 패치 후 nginx -t 미실행으로 config 오류 미감지",
        "lesson_learned": "패치 후 서비스 재시작 전 config validation 필수",
    })
    g.upsert_edge("incident:INC-2024-0903", "server:api-prod-kr-010", "AFFECTED", {})

    # ─── 스킬 노드 ───────────────────────────
    g.upsert_node("skill:patch-openssl", "Skill", {
        "name": "OpenSSL 보안 패치", "trigger": "cve_detected AND package == openssl", "risk": "high",
    })
    g.upsert_edge("incident:INC-2024-0903", "skill:patch-openssl", "RESOLVED_BY", {})

    print(f"Seed complete: {g.nx.number_of_nodes()} nodes, {g.nx.number_of_edges()} edges")


if __name__ == "__main__":
    seed()
