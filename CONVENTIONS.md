# Coding Conventions

## Python

- Python 3.12+
- 패키지 관리: `uv` (pyproject.toml)
- 포매터: `ruff format`, 린터: `ruff check`
- 타입 힌트 필수 (모든 함수 시그니처)
- async/await 기본 (FastAPI 비동기)
- Pydantic v2 모델 사용 (데이터 검증, 직렬화)
- 테스트: pytest + pytest-asyncio

## 네이밍

- 파일/폴더: snake_case
- 클래스: PascalCase
- 함수/변수: snake_case
- 상수: UPPER_SNAKE_CASE
- 온톨로지 노드 타입: PascalCase (`Server`, `Service`)
- 온톨로지 관계: UPPER_SNAKE_CASE (`CALLS`, `HOSTED_ON`)
- SKILL.md id: kebab-case (`patch-openssl`)

## 에러 처리

- 비즈니스 로직 에러: 커스텀 Exception 클래스 정의 (`src/exceptions.py`)
- 외부 서비스 에러 (Claude API): retry with exponential backoff
- API 에러 응답: RFC 7807 Problem Details 형식

## 로깅

- structlog 사용 (구조화된 JSON 로그)
- 레벨: DEBUG (개발), INFO (운영), WARNING (주의), ERROR (장애)
- 모든 온톨로지 변경은 INFO 레벨로 기록
- 모든 AI 에이전트 동작은 INFO 레벨로 기록 (승인/거부 포함)

## 데이터베이스 (SQLite)

- 3개 DB 파일: ontology.db, events.db, app.db
- 항상 WAL 모드 사용 (동시 읽기 성능)
- 파라미터 바인딩 필수 (SQL injection 방지)
- json_patch로 JSON 속성 병합 (UPSERT 시)
- 그래프 쿼리: SQLite 단순 조회 + NetworkX 그래프 순회

## SKILL.md

- YAML frontmatter + Markdown body
- frontmatter 필수 필드: id, name, trigger, scope, risk, approval
- steps의 각 command는 idempotent해야 함 (2번 실행해도 안전)
- rollback 섹션 필수

## Git

- 커밋 메시지: Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`)
- SKILL.md 변경은 별도 PR + 리뷰 필수
- main 브랜치 직접 푸시 금지

## 보안

- 서버 접속 정보는 환경변수 또는 Vault
- 승인 없는 서버 변경 명령 실행 절대 금지
- 모든 승인/실행 로그는 SQLite audit_log에 불변 기록
