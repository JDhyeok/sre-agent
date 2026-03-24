"""
공유 데이터 모델 — Tetragon 이벤트, 온톨로지 노드/엣지, API 스키마.
외부 의존성 없음 (pydantic만 사용).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# ─── Enums ───────────────────────────────────────────────


class Direction(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ─── Tetragon 이벤트 ─────────────────────────────────────


class ProcessInfo(BaseModel):
    pid: int
    binary: str
    args: list[str] = []


class ConnectionEvent(BaseModel):
    """Tetragon이 감지한 단일 네트워크 연결 이벤트."""

    server_hostname: str
    server_ip: str
    process: ProcessInfo
    direction: Direction
    protocol: str = "TCP"
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ─── Correlator 출력 ─────────────────────────────────────


class ServiceEndpoint(BaseModel):
    server_hostname: str
    server_ip: str
    service_name: str | None = None
    pid: int
    binary: str


class CorrelatedConnection(BaseModel):
    source: ServiceEndpoint
    target: ServiceEndpoint
    protocol: str
    port: int
    confidence: Confidence
    first_seen: datetime
    last_seen: datetime
    connection_count: int = 1


# ─── SKILL.md 파싱 결과 ──────────────────────────────────


class SkillStep(BaseModel):
    order: int
    name: str
    description: str
    command: str
    timeout: str = "60s"
    rollback_on_fail: bool = True


class SkillMeta(BaseModel):
    id: str
    name: str
    trigger: str
    scope: dict
    risk: Risk
    approval: str
    tags: list[str] = []
    preconditions: list[str] = []
    steps: list[SkillStep] = []
    rollback_steps: list[SkillStep] = []
    requires: list[str] = []
    chains: list[str] = []


# ─── 실행 계획 ───────────────────────────────────────────


class ExecutionPlan(BaseModel):
    plan_id: str = ""
    skill_id: str
    target_servers: list[str]
    estimated_impact: str
    estimated_downtime: str | None = None
    past_incidents: list[str] = []
    caution_notes: list[str] = []
    recommended_time: str | None = None
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
