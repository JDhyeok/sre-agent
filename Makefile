.PHONY: setup seed api api-ipv6 api-prod test lint clean

# ─── 초기 설정 ─────────────────────────────────
setup:
	cp .env.example .env
	pip install uv
	uv pip install -e ".[dev]"
	mkdir -p data

# ─── 폐쇄망 설치 (wheel에서) ──────────────────
setup-offline:
	pip install --no-index --find-links=./wheels/ -e ".[dev]"
	mkdir -p data

# ─── 시드 데이터 ──────────────────────────────
seed:
	python -m scripts.seed_ontology

# ─── 앱 실행 ──────────────────────────────────
# 대시보드: http://127.0.0.1:8000/ (macOS에서 localhost 가 IPv6만 쓰면 연결 실패할 수 있음)
api:
	uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# IPv6 localhost(::1)에서도 붙이려면 (환경에 따라 동작이 다를 수 있음)
api-ipv6:
	uvicorn src.api.main:app --host '::' --port 8000 --reload

api-prod:
	uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 1

# ─── 테스트 ───────────────────────────────────
test:
	pytest tests/ -v

# ─── 린트 ─────────────────────────────────────
lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

fmt:
	ruff format src/ tests/

# ─── Wheel 다운로드 (외부 머신에서) ────────────
download-wheels:
	pip download -r <(pip compile pyproject.toml) -d ./wheels/

# ─── 정리 ─────────────────────────────────────
clean:
	rm -rf data/*.db
	find . -type d -name __pycache__ -exec rm -rf {} +
