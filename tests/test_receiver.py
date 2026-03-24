"""Correlator receiver 통합 테스트 — 이벤트 인입 → 큐 → 매칭 흐름."""

import json
import os
import tempfile

os.environ["DATA_DIRECTORY"] = tempfile.mkdtemp()

from src.correlator.receiver import enqueue_event, resolve_service_name, update_pid_cache
from src.database import get_events_db, init_all_databases


def setup_module():
    init_all_databases()


def test_enqueue_event():
    """이벤트가 SQLite 큐에 들어간다."""
    payload = {
        "server_hostname": "web-042",
        "server_ip": "10.0.3.42",
        "process": {"pid": 1842, "binary": "/usr/sbin/nginx"},
        "direction": "outbound",
        "protocol": "TCP",
        "src_ip": "10.0.3.42",
        "src_port": 54321,
        "dst_ip": "10.0.3.100",
        "dst_port": 8080,
    }

    event_id = enqueue_event("network_connection", payload)
    assert event_id > 0

    # 큐에서 확인
    db = get_events_db()
    row = db.execute("SELECT * FROM event_queue WHERE id = ?", (event_id,)).fetchone()
    assert row["status"] == "pending"
    assert json.loads(row["payload"])["server_hostname"] == "web-042"


def test_resolve_service_name_known():
    """알려진 바이너리는 서비스 이름으로 변환된다."""
    assert resolve_service_name("web-042", 1842, "/usr/sbin/nginx") == "nginx"
    assert resolve_service_name("db-001", 999, "/usr/bin/postgres") == "postgresql"


def test_resolve_service_name_unknown():
    """모르는 바이너리는 파일명 그대로."""
    assert resolve_service_name("web-042", 9999, "/opt/custom/myapp") == "myapp"


def test_pid_cache():
    """PID 캐시가 우선된다."""
    update_pid_cache("api-010", 2901, "springboot-api")
    assert resolve_service_name("api-010", 2901, "/usr/bin/java") == "springboot-api"
