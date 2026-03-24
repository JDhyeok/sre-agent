---
id: cert-renewal
name: TLS 인증서 갱신
trigger: cert_expiry_days <= 30
scope:
  package: openssl
risk: medium
approval: required
tags: [certificate, tls, renewal]
requires: []
chains: []
---

## preconditions
- certificate.days_until_expiry <= 30
- certificate.issuer CONTAINS "Let's Encrypt" OR certificate.issuer CONTAINS "ZeroSSL"

## steps
1. check_current_cert:
   description: "현재 인증서 상태 확인"
   command: |
     openssl x509 -in ${CERT_PATH} -noout -dates -subject
   timeout: 10s
   rollback_on_fail: false

2. renew_cert:
   description: "certbot으로 인증서 갱신"
   command: |
     certbot renew --cert-name ${DOMAIN} --non-interactive
   timeout: 120s
   rollback_on_fail: true

3. verify_new_cert:
   description: "새 인증서 검증"
   command: |
     openssl x509 -in ${CERT_PATH} -noout -dates | grep "notAfter"
     openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt ${CERT_PATH}
   timeout: 10s
   rollback_on_fail: true

4. reload_service:
   description: "서비스 설정 리로드 (재시작 아님)"
   command: |
     if command -v nginx &>/dev/null; then
       nginx -t && nginx -s reload
     elif systemctl is-active ${SERVICE_NAME}; then
       systemctl reload ${SERVICE_NAME}
     fi
   timeout: 30s
   rollback_on_fail: false

## rollback
1. restore_backup_cert:
   description: "백업된 이전 인증서 복구"
   command: |
     cp ${CERT_PATH}.bak ${CERT_PATH}
     nginx -s reload || systemctl reload ${SERVICE_NAME}
   timeout: 30s

## history
- 2024-12-15: web-042 외 5대 자동 갱신 성공
- 2024-10-01: api-010 갱신 실패 (DNS challenge timeout, 수동 처리)
