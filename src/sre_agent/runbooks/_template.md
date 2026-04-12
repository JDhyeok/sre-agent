---
name: example-runbook
trigger: "이 런북이 매칭되어야 하는 상황을 자유 서술. LLM이 의미 매칭에 사용하므로 알림명, 증상, 전제 조건을 구체적으로 적는다. 예: 'ContainerMemoryPressure 알림, 단일 컨테이너의 워킹셋 메모리가 cgroup 한도 대비 80% 이상으로 1분 이상 지속, 호스트 노드 정상, 재기동으로 복구 가능한 알려진 누수 패턴.'"
risk: low|medium|high|critical
script: scripts/your-script.sh
target_host_label: "ssh host name이나 hostname substring. settings.yaml의 ssh.hosts에서 매칭됨."
---

# When to use

다음 조건이 모두 만족할 때:

- **알림**: (어떤 알림이 발생해야 하는지)
- **대상**: (어떤 서비스/호스트인지)
- **컨텍스트**: (사전 조건 — 알려진 문제인지, 변경 이력 없는지 등)
- **호스트 상태**: (호스트 노드 자체는 정상인지)
- **전제**: (스크립트 실행에 필요한 권한/CLI 도구)

다음에는 사용하지 말 것:

- (이 런북을 쓰면 안 되는 상황을 명시)
- (잘못 매칭되면 위험한 사례를 명시)

# What it does

1. (스크립트가 수행하는 첫 번째 동작)
2. (두 번째 동작)
3. (검증 — 성공/실패 확인 방법)

# Rollback

(조치 후 문제가 생겼을 때 되돌리는 방법. 되돌릴 수 없는 경우도 명시.)
