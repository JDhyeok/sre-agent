"""
Event Receiver + Background Correlator.

기존 NATS consumer를 대체한다.
- OTel Collector가 POST /ingest/events로 이벤트를 보낸다
- SQLite event_queue에 저장
- Background task가 폴링하며 Matcher로 처리
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath

from src.config import settings
from src.correlator.matcher import ConnectionMatcher
from src.database import get_events_db
from src.graph.store import GraphStore
from src.models.schemas import ConnectionEvent

logger = logging.getLogger(__name__)

_PID_CACHE_MAX = 10_000
_pid_cache: dict[str, str] = {}

_KNOWN_BINARIES = {
    "nginx": "nginx",
    "httpd": "apache",
    "java": "java-app",
    "python3": "python-app",
    "node": "node-app",
    "postgres": "postgresql",
    "mysqld": "mysql",
    "redis-server": "redis",
    "mongod": "mongodb",
}


def enqueue_event(event_type: str, payload: dict) -> int:
    """이벤트를 SQLite 큐에 넣는다. (HTTP handler에서 호출)"""
    db = get_events_db()
    cursor = db.execute(
        "INSERT INTO event_queue (event_type, payload) VALUES (?, ?)",
        (event_type, json.dumps(payload, default=str)),
    )
    db.commit()
    return cursor.lastrowid


def resolve_service_name(hostname: str, pid: int, binary: str) -> str:
    """PID → Service 이름. 캐시 → 알려진 바이너리 → 파일명 fallback."""
    cache_key = f"{hostname}:{pid}"

    if cache_key in _pid_cache:
        return _pid_cache[cache_key]

    filename = PurePosixPath(binary).name
    name = _KNOWN_BINARIES.get(filename, filename)

    if len(_pid_cache) >= _PID_CACHE_MAX:
        to_remove = list(_pid_cache.keys())[: _PID_CACHE_MAX // 4]
        for k in to_remove:
            del _pid_cache[k]

    _pid_cache[cache_key] = name
    return name


def update_pid_cache(hostname: str, pid: int, service_name: str) -> None:
    """Inventory Agent가 호출. PID → Service 매핑 갱신."""
    _pid_cache[f"{hostname}:{pid}"] = service_name


async def run_correlator_loop(graph: GraphStore) -> None:
    """Background task. event_queue에서 이벤트를 폴링하며 매칭한다."""
    matcher = ConnectionMatcher()
    poll_interval = settings.correlator_poll_interval

    logger.info("correlator_started")

    while True:
        try:
            db = get_events_db()

            # pending 이벤트 가져오기 (batch)
            rows = db.execute(
                """UPDATE event_queue
                   SET status = 'processing'
                   WHERE id IN (
                     SELECT id FROM event_queue
                     WHERE status = 'pending'
                     ORDER BY id
                     LIMIT 100
                   )
                   RETURNING id, event_type, payload""",
            ).fetchall()
            db.commit()

            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                    event = ConnectionEvent.model_validate(payload)

                    # 매칭 시도
                    correlated = matcher.process_event(event)

                    if correlated is not None:
                        # Service 이름 변환
                        correlated.source.service_name = resolve_service_name(
                            correlated.source.server_hostname,
                            correlated.source.pid,
                            correlated.source.binary,
                        )
                        correlated.target.service_name = resolve_service_name(
                            correlated.target.server_hostname,
                            correlated.target.pid,
                            correlated.target.binary,
                        )

                        src_svc = correlated.source
                        tgt_svc = correlated.target
                        src_svc_id = f"service:{src_svc.service_name}@{src_svc.server_hostname}"
                        tgt_svc_id = f"service:{tgt_svc.service_name}@{tgt_svc.server_hostname}"
                        src_srv_id = f"server:{correlated.source.server_hostname}"
                        tgt_srv_id = f"server:{correlated.target.server_hostname}"

                        # 서비스 노드 upsert
                        graph.upsert_node(
                            src_svc_id,
                            "Service",
                            {
                                "name": correlated.source.service_name,
                                "server": correlated.source.server_hostname,
                            },
                        )
                        graph.upsert_node(
                            tgt_svc_id,
                            "Service",
                            {
                                "name": correlated.target.service_name,
                                "server": correlated.target.server_hostname,
                            },
                        )

                        # HOSTED_ON edge
                        graph.upsert_edge(src_svc_id, src_srv_id, "HOSTED_ON", {})
                        graph.upsert_edge(tgt_svc_id, tgt_srv_id, "HOSTED_ON", {})

                        # CALLS edge
                        graph.upsert_edge(
                            src_svc_id,
                            tgt_svc_id,
                            "CALLS",
                            {
                                "protocol": correlated.protocol,
                                "port": correlated.port,
                                "confidence": correlated.confidence.value,
                                "last_seen": correlated.last_seen.isoformat(),
                                "status": "active",
                            },
                        )

                    # 처리 완료
                    db.execute(
                        "UPDATE event_queue SET status = 'done', processed_at = ? WHERE id = ?",
                        (datetime.now(UTC).isoformat(), row["id"]),
                    )

                except Exception as e:
                    logger.error("event_process_failed", extra={"id": row["id"], "error": str(e)})
                    db.execute(
                        "UPDATE event_queue SET status = 'failed' WHERE id = ?",
                        (row["id"],),
                    )

            db.commit()

            # 만료된 단방향 이벤트 → inferred 처리
            inferred = matcher.flush_expired()
            for conn in inferred:
                conn.source.service_name = resolve_service_name(
                    conn.source.server_hostname, conn.source.pid, conn.source.binary
                )
                src_id = f"service:{conn.source.service_name}@{conn.source.server_hostname}"
                tgt_id = f"service:unknown@{conn.target.server_ip}"
                graph.upsert_node(
                    tgt_id,
                    "Service",
                    {"name": "unknown", "ip": conn.target.server_ip},
                )
                graph.upsert_edge(
                    src_id,
                    tgt_id,
                    "CALLS",
                    {
                        "protocol": conn.protocol,
                        "port": conn.port,
                        "confidence": "inferred",
                        "last_seen": conn.last_seen.isoformat(),
                        "status": "active",
                    },
                )

        except Exception as e:
            logger.error("correlator_loop_error", extra={"error": str(e)})

        await asyncio.sleep(poll_interval)
