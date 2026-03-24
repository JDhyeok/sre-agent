# SRE Agent — Project Specification (Air-Gapped Edition)

## 프로젝트 개요

SRE 운영 자동화 에이전트 시스템. 수십~수백 대 서버의 장애 대응, 보안 패치, EoS 관리, 라이선스 만료, PM 작업 등을 AI가 가이드하고 자동화한다.

## 폐쇄망 제약

- 외부 인터넷 접근 불가
- Docker / 컨테이너 런타임 없음
- Neo4j, NATS, Redis 등 별도 서버 소프트웨어 설치 불가
- **모든 의존성은 pip wheel로 사전 반입** (외부에서 pip download → USB → 내부 설치)
- Claude API는 내부 프록시 또는 self-hosted LLM으로 대체 가능

## 기술 스택

| 역할 | 기술 | 비고 |
|------|------|------|
| Web API | FastAPI + uvicorn | 순수 Python, wheel 반입 |
| 그래프 저장소 | **SQLite + NetworkX** | 파일 기반, 설치 무필요(표준라이브러리) + pip |
| 이벤트 큐 | **SQLite WAL queue** | events.db, 별도 서버 불필요 |
| 캐시 | **Python dict (in-memory)** | 프로세스 내 캐시 |
| 감사/사용자 | **SQLite** | app.db |
| AI Engine | Claude API (내부 프록시) 또는 self-hosted LLM | |
| Edge Agent | Tetragon (바이너리 배포) | 각 서버에 설치 |
| Data Pipeline | OTel Collector (바이너리 배포) | 각 서버에 설치 |

### 핵심: SQLite 3개 파일로 전체 영속성 해결

```
data/
├── ontology.db      ← 그래프 (nodes + edges 테이블), NetworkX로 메모리 로드
├── events.db        ← Tetragon 이벤트 큐 (pending → done)
└── app.db           ← 사용자, 세션, 감사 로그, 승인 기록
```

## 아키텍처

```
[각 서버]
  Tetragon (eBPF) ──→ OTel Collector (Agent)
  Inventory Agent ──→       ↓
                      (HTTP POST)
                            ↓
[중앙 서버 — 단일 Python 프로세스]
  ┌──────────────────────────────────────┐
  │  FastAPI                             │
  │  ├── POST /ingest/events             │  ← OTel Agent가 여기로 전송
  │  ├── Correlator (background task)    │  ← event_queue 폴링 + 매칭
  │  ├── AI Agent (Claude API proxy)     │
  │  ├── Skill Engine                    │
  │  └── WebSocket /ws/chat              │
  │                                      │
  │  SQLite: ontology.db, events.db      │
  │  NetworkX: in-memory DiGraph         │
  └──────────────────────────────────────┘
```

## SQLite 스키마

### ontology.db — 그래프 저장소

```sql
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
```

### events.db — 이벤트 큐

```sql
CREATE TABLE IF NOT EXISTS event_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    created_at  TEXT DEFAULT (datetime('now')),
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_status ON event_queue(status);
```

### app.db — 앱 데이터

```sql
CREATE TABLE IF NOT EXISTS approval_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     TEXT NOT NULL,
    skill_id    TEXT NOT NULL,
    targets     TEXT NOT NULL,
    status      TEXT NOT NULL,
    requested_by TEXT,
    approved_by  TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    actor       TEXT,
    target      TEXT,
    detail      TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);
```

## SKILL.md 포맷

(기존과 동일 — YAML frontmatter + Markdown body)
skills/ 디렉토리에 저장, Git 관리.

## 온톨로지 노드 ID 컨벤션

```
server:{hostname}              → "server:web-prod-kr-042"
service:{name}@{hostname}      → "service:nginx@web-prod-kr-042"
package:{name}:{version}       → "package:openssl:3.0.2"
cert:{subject_cn}@{hostname}   → "cert:api.company.io@web-prod-kr-042"
incident:{id}                  → "incident:INC-2024-0903"
skill:{id}                     → "skill:patch-openssl"
cve:{id}                       → "cve:CVE-2024-5535"
```

## AI 에이전트 승인 규칙

| 작업 유형 | 예시 | 승인 |
|-----------|------|------|
| 정보 조회 | "이 서버에 뭐 깔려있어?" | 불필요 |
| 분석 | "CVE 영향 범위 분석해줘" | 불필요 |
| 읽기 실행 | "health check 돌려줘" | 자동 승인 |
| 변경 실행 | "openssl 패치해줘" | **명시적 승인 필요** |
| 긴급 장애 | "서비스 다운됐어" | 분석은 즉시, 실행은 승인 필요 |

## 개발 순서

### Phase 1 — 기반 (3주)
- [ ] database.py: SQLite 초기화 + WAL 모드
- [ ] graph/store.py: SQLite ↔ NetworkX 동기화
- [ ] seed 스크립트
- [ ] FastAPI 서버 + 서버 조회 API
- [ ] SKILL.md 파서

### Phase 2 — Correlator (3주)
- [ ] POST /ingest/events endpoint
- [ ] Background task: event_queue 폴링
- [ ] In-memory matcher (dict 기반)
- [ ] 매칭 결과 → ontology.db + NetworkX

### Phase 3 — AI 에이전트 (3주)
- [ ] Claude API (프록시) 연동
- [ ] Tool use + 온톨로지 컨텍스트
- [ ] WebSocket Chat
- [ ] 승인 워크플로우

### Phase 4 — 장애 대응 + UI (4주)
- [ ] Incident 관리
- [ ] 실행 엔진 (SSH/Ansible)
- [ ] Dashboard + Chat
