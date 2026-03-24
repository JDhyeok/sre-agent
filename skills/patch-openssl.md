---
id: patch-openssl
name: OpenSSL 보안 패치
trigger: cve_detected AND package == "openssl"
scope:
  os: [ubuntu, rhel, centos]
  package: openssl
risk: high
approval: required
tags: [security, patch, openssl]
requires: []
chains: [cert-renewal]
---

## preconditions
- package("openssl").installed == true
- service.any(uses_lib("libssl"))

## steps
1. drain_connections:
   description: "서비스 연결 드레인"
   command: |
     systemctl stop ${SERVICE_NAME} || true
   timeout: 60s
   rollback_on_fail: false

2. create_snapshot:
   description: "서버 스냅샷 생성"
   command: |
     if command -v aws &>/dev/null; then
       INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
       aws ec2 create-snapshot --volume-id $(aws ec2 describe-instances --instance-ids $INSTANCE_ID --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' --output text) --description "pre-patch-${HOSTNAME}"
     fi
   timeout: 300s
   rollback_on_fail: true

3. validate_config:
   description: "서비스 설정 검증 (INC-2024-0903 교훈: nginx -t 필수)"
   command: |
     if command -v nginx &>/dev/null; then
       nginx -t 2>&1 || exit 1
     fi
   timeout: 10s
   rollback_on_fail: true

4. upgrade_package:
   description: "openssl 패키지 업그레이드"
   command: |
     if command -v apt-get &>/dev/null; then
       apt-get update && apt-get install -y openssl
     elif command -v yum &>/dev/null; then
       yum update -y openssl
     fi
   timeout: 120s
   rollback_on_fail: true

5. restart_services:
   description: "openssl 사용 서비스 재시작"
   command: |
     for svc in ${AFFECTED_SERVICES}; do
       systemctl restart $svc
       sleep 5
       systemctl is-active $svc || exit 1
     done
   timeout: 120s
   rollback_on_fail: true

6. verify:
   description: "TLS 핸드셰이크 및 버전 검증"
   command: |
     openssl version
     for port in ${LISTEN_PORTS}; do
       timeout 5 openssl s_client -connect localhost:$port < /dev/null 2>&1 | grep -q "Verify return code: 0" || echo "WARN: port $port TLS verify failed"
     done
   timeout: 30s
   rollback_on_fail: false

## rollback
1. revert_snapshot:
   description: "스냅샷 복구"
   command: |
     echo "Manual snapshot revert required"
   timeout: 300s

2. restore_services:
   description: "서비스 복구"
   command: |
     for svc in ${AFFECTED_SERVICES}; do
       systemctl start $svc
     done
   timeout: 60s

## history
- 2024-11-15: web-042 외 12대 성공 (MTTR 23min)
- 2024-09-03: api-010 실패 (nginx conf 문법오류, INC-2024-0903)
  교훈: step 3 "validate_config" 추가
