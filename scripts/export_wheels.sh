#!/bin/bash
# export_wheels.sh — 외부 인터넷 가능 머신에서 실행
# 폐쇄망으로 가져갈 wheel 파일들을 다운로드한다.
#
# 사용법:
#   1. 인터넷 가능 머신에서: bash scripts/export_wheels.sh
#   2. wheels/ 폴더를 USB 등으로 폐쇄망 서버에 복사
#   3. 폐쇄망에서: make setup-offline

set -e

echo "=== SRE Agent — Wheel Export ==="

WHEEL_DIR="./wheels"
mkdir -p "$WHEEL_DIR"

# pip compile이 있으면 사용, 없으면 직접 목록 지정
if command -v pip-compile &>/dev/null; then
    pip-compile pyproject.toml -o /tmp/requirements.txt
    pip download -r /tmp/requirements.txt -d "$WHEEL_DIR"
else
    # 주요 패키지 직접 다운로드
    pip download \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "anthropic>=0.40" \
        "networkx>=3.4" \
        "pydantic>=2.10" \
        "pydantic-settings>=2.6" \
        "pyyaml>=6.0" \
        "structlog>=24.4" \
        "pytest>=8.3" \
        "pytest-asyncio>=0.24" \
        "ruff>=0.8" \
        "httpx>=0.27" \
        -d "$WHEEL_DIR"
fi

echo ""
echo "=== 다운로드 완료 ==="
echo "파일 수: $(ls -1 "$WHEEL_DIR" | wc -l)"
echo "총 크기: $(du -sh "$WHEEL_DIR" | cut -f1)"
echo ""
echo "다음 단계:"
echo "  1. wheels/ 폴더를 폐쇄망 서버에 복사"
echo "  2. 폐쇄망에서: make setup-offline"
