"""Correlator Matcher 테스트 — in-memory dict 기반, 외부 의존성 없음."""

from datetime import UTC, datetime

from src.correlator.matcher import ConnectionMatcher
from src.models.schemas import Confidence, ConnectionEvent, Direction, ProcessInfo


def _make_event(
    hostname: str,
    ip: str,
    pid: int,
    binary: str,
    direction: Direction,
    src_ip: str,
    src_port: int,
    dst_ip: str,
    dst_port: int,
) -> ConnectionEvent:
    return ConnectionEvent(
        server_hostname=hostname,
        server_ip=ip,
        process=ProcessInfo(pid=pid, binary=binary),
        direction=direction,
        protocol="TCP",
        src_ip=src_ip,
        src_port=src_port,
        dst_ip=dst_ip,
        dst_port=dst_port,
        timestamp=datetime.now(UTC),
    )


def test_bidirectional_match():
    """양쪽 이벤트가 도착하면 confirmed 매칭."""
    matcher = ConnectionMatcher()

    outbound = _make_event(
        "web-042",
        "10.0.3.42",
        1842,
        "/usr/sbin/nginx",
        Direction.OUTBOUND,
        "10.0.3.42",
        54321,
        "10.0.3.100",
        8080,
    )
    inbound = _make_event(
        "api-010",
        "10.0.3.100",
        2901,
        "/usr/bin/java",
        Direction.INBOUND,
        "10.0.3.42",
        54321,
        "10.0.3.100",
        8080,
    )

    assert matcher.process_event(outbound) is None
    result = matcher.process_event(inbound)

    assert result is not None
    assert result.confidence == Confidence.CONFIRMED
    assert result.source.server_hostname == "web-042"
    assert result.target.server_hostname == "api-010"
    assert result.port == 8080
    assert matcher.pending_count == 0


def test_inbound_first():
    """Inbound가 먼저 와도 매칭된다."""
    matcher = ConnectionMatcher()

    inbound = _make_event(
        "api-010",
        "10.0.3.100",
        2901,
        "/usr/bin/java",
        Direction.INBOUND,
        "10.0.3.42",
        54321,
        "10.0.3.100",
        8080,
    )
    outbound = _make_event(
        "web-042",
        "10.0.3.42",
        1842,
        "/usr/sbin/nginx",
        Direction.OUTBOUND,
        "10.0.3.42",
        54321,
        "10.0.3.100",
        8080,
    )

    assert matcher.process_event(inbound) is None
    result = matcher.process_event(outbound)
    assert result is not None
    assert result.confidence == Confidence.CONFIRMED


def test_no_false_match():
    """다른 dst_port는 매칭되지 않는다."""
    matcher = ConnectionMatcher()

    outbound = _make_event(
        "web-042",
        "10.0.3.42",
        1842,
        "/usr/sbin/nginx",
        Direction.OUTBOUND,
        "10.0.3.42",
        54321,
        "10.0.3.100",
        8080,
    )
    wrong_inbound = _make_event(
        "api-010",
        "10.0.3.100",
        2901,
        "/usr/bin/java",
        Direction.INBOUND,
        "10.0.3.42",
        54322,
        "10.0.3.100",
        5432,
    )

    matcher.process_event(outbound)
    result = matcher.process_event(wrong_inbound)
    assert result is None
    assert matcher.pending_count == 2  # 둘 다 매칭 대기 중


def test_pending_count():
    """매칭 전후 pending 카운트 확인."""
    matcher = ConnectionMatcher()

    ev = _make_event(
        "web-042",
        "10.0.3.42",
        1842,
        "/usr/sbin/nginx",
        Direction.OUTBOUND,
        "10.0.3.42",
        54321,
        "10.0.3.100",
        8080,
    )
    matcher.process_event(ev)
    assert matcher.pending_count == 1
