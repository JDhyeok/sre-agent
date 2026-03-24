"""
승인 워크플로우 — SQLite 기반 Human-in-the-Loop.

모든 변경 작업:
1. AI가 ExecutionPlan 생성 → approval_log에 pending으로 저장
2. 사용자에게 표시 → 승인/거부
3. 승인 → 실행, 거부 → 종료
4. 결과 → audit_log에 기록
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from src.database import get_app_db

logger = logging.getLogger(__name__)


def submit_plan(
    skill_id: str,
    target_servers: list[str],
    requested_by: str = "system",
) -> str:
    """실행 계획을 제출한다. plan_id를 반환."""
    plan_id = f"PLAN-{uuid.uuid4().hex[:8]}"
    db = get_app_db()
    db.execute(
        """INSERT INTO approval_log (plan_id, skill_id, targets, status, requested_by)
           VALUES (?, ?, ?, 'pending', ?)""",
        (plan_id, skill_id, json.dumps(target_servers), requested_by),
    )
    db.commit()

    logger.info("plan_submitted", extra={"plan_id": plan_id, "skill": skill_id})
    return plan_id


def approve_plan(plan_id: str, approved_by: str) -> bool:
    """계획을 승인한다."""
    db = get_app_db()
    cursor = db.execute(
        """UPDATE approval_log
           SET status = 'approved', approved_by = ?, resolved_at = ?
           WHERE plan_id = ? AND status = 'pending'""",
        (approved_by, datetime.now(UTC).isoformat(), plan_id),
    )
    db.commit()
    success = cursor.rowcount > 0

    if success:
        logger.info("plan_approved", extra={"plan_id": plan_id, "by": approved_by})
        _audit("plan_approved", approved_by, plan_id)
    return success


def reject_plan(plan_id: str, rejected_by: str) -> bool:
    """계획을 거부한다."""
    db = get_app_db()
    cursor = db.execute(
        """UPDATE approval_log
           SET status = 'rejected', approved_by = ?, resolved_at = ?
           WHERE plan_id = ? AND status = 'pending'""",
        (rejected_by, datetime.now(UTC).isoformat(), plan_id),
    )
    db.commit()
    success = cursor.rowcount > 0

    if success:
        logger.info("plan_rejected", extra={"plan_id": plan_id, "by": rejected_by})
        _audit("plan_rejected", rejected_by, plan_id)
    return success


def get_pending_plans() -> list[dict]:
    """승인 대기 중인 계획 목록."""
    db = get_app_db()
    rows = db.execute(
        "SELECT * FROM approval_log WHERE status = 'pending' ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_plan(plan_id: str) -> dict | None:
    db = get_app_db()
    row = db.execute("SELECT * FROM approval_log WHERE plan_id = ?", (plan_id,)).fetchone()
    return dict(row) if row else None


def _audit(action: str, actor: str, target: str, detail: str = "") -> None:
    """감사 로그 기록."""
    db = get_app_db()
    db.execute(
        "INSERT INTO audit_log (action, actor, target, detail) VALUES (?, ?, ?, ?)",
        (action, actor, target, detail),
    )
    db.commit()


def record_execution(
    plan_id: str, result: str, detail: str = "", executed_by: str = "system"
) -> None:
    """실행 결과를 감사 로그에 기록한다."""
    _audit(
        action=f"execution_{result}",
        actor=executed_by,
        target=plan_id,
        detail=detail,
    )
    logger.info("execution_recorded", extra={"plan_id": plan_id, "result": result})
