"""
SQLite 데이터베이스 초기화 + 연결 관리.

3개 DB 파일:
  - ontology.db: 그래프 (nodes + edges)
  - events.db: Tetragon 이벤트 큐
  - app.db: 사용자, 승인, 감사 로그
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.config import settings

_DATA_DIR = Path(settings.data_directory)


def _connect(db_name: str) -> sqlite3.Connection:
    """WAL 모드 SQLite 연결을 생성한다."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = _DATA_DIR / db_name
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_ontology_db() -> sqlite3.Connection:
    return _connect("ontology.db")


def get_events_db() -> sqlite3.Connection:
    return _connect("events.db")


def get_app_db() -> sqlite3.Connection:
    return _connect("app.db")


def init_all_databases() -> None:
    """모든 DB의 스키마를 초기화한다. 앱 시작 시 호출."""

    # ─── ontology.db ──────────────────────
    conn = get_ontology_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            properties  TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);

        CREATE TABLE IF NOT EXISTS edges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   TEXT NOT NULL REFERENCES nodes(id),
            target_id   TEXT NOT NULL REFERENCES nodes(id),
            type        TEXT NOT NULL,
            properties  TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(source_id, target_id, type)
        );
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
        CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
    """)
    conn.close()

    # ─── events.db ────────────────────────
    conn = get_events_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS event_queue (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type   TEXT NOT NULL,
            payload      TEXT NOT NULL,
            status       TEXT DEFAULT 'pending',
            created_at   TEXT DEFAULT (datetime('now')),
            processed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_status ON event_queue(status);
    """)
    conn.close()

    # ─── app.db ───────────────────────────
    conn = get_app_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS approval_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id      TEXT NOT NULL,
            skill_id     TEXT NOT NULL,
            targets      TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            requested_by TEXT,
            approved_by  TEXT,
            created_at   TEXT DEFAULT (datetime('now')),
            resolved_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            action      TEXT NOT NULL,
            actor       TEXT,
            target      TEXT,
            detail      TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.close()
