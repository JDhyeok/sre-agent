"""
Correlator Matcher — in-memory dict 기반 양방향 연결 매칭.

Redis 대신 Python dict + TTL 관리로 동작한다.
단일 프로세스이므로 thread-safe하게 동작.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.config import settings
from src.models.schemas import (
    Confidence,
    ConnectionEvent,
    CorrelatedConnection,
    Direction,
    ServiceEndpoint,
)

logger = logging.getLogger(__name__)


@dataclass
class _PendingEvent:
    event: ConnectionEvent
    expires_at: float


def _make_match_key(event: ConnectionEvent) -> str:
    return f"{event.protocol}:{event.dst_ip}:{event.dst_port}:{event.src_ip}"


class ConnectionMatcher:
    """In-memory 양방향 연결 매칭기. Redis 불필요."""

    def __init__(self) -> None:
        self._ttl = settings.match_window_seconds
        # key → {"outbound": _PendingEvent, "inbound": _PendingEvent}
        self._pending: dict[str, dict[str, _PendingEvent]] = {}

    def process_event(self, event: ConnectionEvent) -> CorrelatedConnection | None:
        """이벤트를 받아서 매칭을 시도한다."""
        key = _make_match_key(event)
        side = event.direction.value
        opposite = "inbound" if side == "outbound" else "outbound"
        now = time.time()

        # 현재 이벤트 저장
        if key not in self._pending:
            self._pending[key] = {}
        self._pending[key][side] = _PendingEvent(event=event, expires_at=now + self._ttl)

        # 반대쪽 확인
        opposite_pending = self._pending[key].get(opposite)
        if opposite_pending is None or opposite_pending.expires_at < now:
            return None

        # 양방향 매칭 성공
        opposite_event = opposite_pending.event
        if event.direction == Direction.OUTBOUND:
            outbound, inbound = event, opposite_event
        else:
            outbound, inbound = opposite_event, event

        result = CorrelatedConnection(
            source=ServiceEndpoint(
                server_hostname=outbound.server_hostname,
                server_ip=outbound.server_ip,
                service_name=None,
                pid=outbound.process.pid,
                binary=outbound.process.binary,
            ),
            target=ServiceEndpoint(
                server_hostname=inbound.server_hostname,
                server_ip=inbound.server_ip,
                service_name=None,
                pid=inbound.process.pid,
                binary=inbound.process.binary,
            ),
            protocol=outbound.protocol,
            port=outbound.dst_port,
            confidence=Confidence.CONFIRMED,
            first_seen=min(outbound.timestamp, inbound.timestamp),
            last_seen=max(outbound.timestamp, inbound.timestamp),
        )

        # 매칭 완료 → 삭제
        del self._pending[key]

        logger.info(
            "connection_matched",
            extra={
                "source": outbound.server_hostname,
                "target": inbound.server_hostname,
                "port": outbound.dst_port,
            },
        )

        return result

    def flush_expired(self) -> list[CorrelatedConnection]:
        """TTL 만료된 단방향 이벤트를 inferred 연결로 변환한다."""
        now = time.time()
        inferred: list[CorrelatedConnection] = []
        expired_keys: list[str] = []

        for key, sides in self._pending.items():
            all_expired = all(p.expires_at < now for p in sides.values())
            if not all_expired:
                continue

            expired_keys.append(key)

            # Outbound만 있는 경우 → inferred
            if "outbound" in sides and "inbound" not in sides:
                ev = sides["outbound"].event
                conn = CorrelatedConnection(
                    source=ServiceEndpoint(
                        server_hostname=ev.server_hostname,
                        server_ip=ev.server_ip,
                        pid=ev.process.pid,
                        binary=ev.process.binary,
                    ),
                    target=ServiceEndpoint(
                        server_hostname="unknown",
                        server_ip=ev.dst_ip,
                        pid=0,
                        binary="unknown",
                    ),
                    protocol=ev.protocol,
                    port=ev.dst_port,
                    confidence=Confidence.INFERRED,
                    first_seen=ev.timestamp,
                    last_seen=ev.timestamp,
                )
                inferred.append(conn)

        for key in expired_keys:
            del self._pending[key]

        return inferred

    @property
    def pending_count(self) -> int:
        return len(self._pending)
