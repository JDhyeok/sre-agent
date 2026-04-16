"""Shared fixtures for sre-agent tests."""

import sys
from pathlib import Path

import pytest

# Ensure src/ is on the path so we can import sre_agent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture
def sample_alertmanager_payload():
    """A realistic Alertmanager webhook payload."""
    return {
        "version": "4",
        "groupKey": "{}:{alertname=\"HighMemoryUsage\"}",
        "status": "firing",
        "receiver": "sre-agent",
        "groupLabels": {"alertname": "HighMemoryUsage"},
        "commonLabels": {
            "alertname": "HighMemoryUsage",
            "severity": "critical",
            "instance": "10.0.1.10:9100",
            "job": "node-exporter",
        },
        "commonAnnotations": {
            "summary": "Memory usage is above 90%",
            "description": "Host 10.0.1.10 memory usage is 92%",
        },
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighMemoryUsage",
                    "severity": "critical",
                    "instance": "10.0.1.10:9100",
                    "job": "node-exporter",
                    "service": "web-app",
                },
                "annotations": {
                    "summary": "Memory usage is above 90%",
                    "description": "Host 10.0.1.10 memory usage is 92%",
                },
                "startsAt": "2026-04-16T03:00:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph?g0.expr=...",
                "fingerprint": "abc123",
            }
        ],
    }


@pytest.fixture
def sample_runbook_markdown():
    """A sample runbook with YAML frontmatter."""
    return """---
name: memory-leak-restart
trigger: "High memory usage, memory leak detected, container memory pressure"
risk: medium
script: scripts/restart-app.sh
target_host_label: "service=web-app"
---

# When to use

- **Alert**: HighMemoryUsage or ContainerMemoryPressure is firing
- **Target**: web-app service containers
- **Context**: Memory has been gradually increasing over hours

# What it does

1. Gracefully stops the application
2. Clears in-memory caches
3. Restarts the application
4. Verifies health check passes

# Rollback

Stop the application and restore from last known good state.
"""


@pytest.fixture
def sample_match_found_report():
    """A report containing a MATCH_FOUND runbook match (Korean keys)."""
    return """## 인시던트 데이터 수집 리포트

### 현재 상황
메모리 사용률이 92%로 위험 수준입니다.

### 수집 데이터 요약
메모리 사용률이 지속적으로 상승 중입니다.

### 런북 매칭 결과
**상태**: MATCH_FOUND
**런북**: memory-leak-restart
**스크립트**: scripts/restart-app.sh
**위험도**: medium
**대상 호스트**: service=web-app

### 매칭 이유
메모리 사용률이 90%를 초과하고 있으며, HighMemoryUsage 알림이 발생 중입니다.

### 수행 작업
애플리케이션을 재시작하고 메모리 캐시를 정리합니다.
"""


@pytest.fixture
def sample_no_match_report():
    """A report containing a NO_MATCH runbook result."""
    return """## 인시던트 데이터 수집 리포트

### 현재 상황
CPU 사용률이 95%입니다.

### 수집 데이터 요약
CPU 집약적 프로세스가 실행 중입니다.

### 런북 매칭 결과
**상태**: NO_MATCH
**사유**: CPU 과부하에 대한 자동화된 런북이 등록되어 있지 않습니다.

### 수동 대안
1. CPU 집약적 프로세스를 식별하여 수동으로 종료
2. 서버 스케일 업 검토
"""
