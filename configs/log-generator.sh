#!/bin/sh
# Generates realistic application logs and pushes them to Elasticsearch.
# Runs inside the log-generator container.

ES_URL="http://elasticsearch:9200"
INDEX="app-logs-000001"

echo "[log-generator] Waiting for Elasticsearch..."
until curl -sf "${ES_URL}/_cluster/health" > /dev/null 2>&1; do
  sleep 3
done
echo "[log-generator] Elasticsearch is ready."

apk add --no-cache curl > /dev/null 2>&1

curl -sf -X PUT "${ES_URL}/${INDEX}" \
  -H 'Content-Type: application/json' \
  -d '{
  "settings": { "number_of_shards": 1, "number_of_replicas": 0 },
  "mappings": {
    "properties": {
      "@timestamp":      { "type": "date" },
      "level":           { "type": "keyword" },
      "service":         { "type": "keyword" },
      "host":            { "type": "object", "properties": { "name": { "type": "keyword" } } },
      "message":         { "type": "text" },
      "status_code":     { "type": "integer" },
      "response_time_ms":{ "type": "float" },
      "trace_id":        { "type": "keyword" }
    }
  }
}' > /dev/null 2>&1 || true

curl -sf -X POST "${ES_URL}/_aliases" \
  -H 'Content-Type: application/json' \
  -d "{\"actions\":[{\"add\":{\"index\":\"${INDEX}\",\"alias\":\"app-logs\"}}]}" \
  > /dev/null 2>&1 || true

echo "[log-generator] Index '${INDEX}' ready. Generating logs..."

i=0
while true; do
  i=$((i + 1))
  ts=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

  case $((i % 4)) in
    0) svc="payment-api"   ;;
    1) svc="user-service"  ;;
    2) svc="order-service" ;;
    3) svc="gateway"       ;;
  esac

  case $((i % 3)) in
    0) host="sre-app-web-1" ;;
    1) host="sre-app-web-2" ;;
    2) host="sre-app-api"   ;;
  esac

  r=$((i % 10))
  if [ $r -eq 0 ]; then
    level="error"; status=500; rt=$((RANDOM % 5000 + 1000))
    msg="java.lang.NullPointerException: Cannot invoke method on null object at com.api.PaymentService.process(PaymentService.java:142)"
  elif [ $r -eq 1 ]; then
    level="error"; status=502; rt=$((RANDOM % 3000 + 2000))
    msg="upstream connect error or disconnect/reset before headers. reset reason: connection timeout to ${svc}-upstream:8080"
  elif [ $r -eq 2 ]; then
    level="error"; status=503; rt=$((RANDOM % 4000 + 3000))
    msg="CircuitBreaker 'orderService' is OPEN and does not permit further calls. Failures: 12/20 in last 30s"
  elif [ $r -le 4 ]; then
    level="warn"; status=429; rt=$((RANDOM % 1000 + 500))
    msg="Rate limit exceeded for client IP 10.0.$((RANDOM % 255)).$((RANDOM % 255)) — 429 Too Many Requests"
  elif [ $r -eq 5 ]; then
    level="warn"; status=200; rt=$((RANDOM % 2000 + 800))
    msg="Slow query detected: SELECT * FROM orders WHERE user_id=? took ${rt}ms (threshold: 500ms)"
  else
    level="info"; status=200; rt=$((RANDOM % 200 + 10))
    msg="HTTP ${status} GET /api/v1/${svc}/health ${rt}ms"
  fi

  trace_id=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')

  curl -sf -X POST "${ES_URL}/${INDEX}/_doc" \
    -H 'Content-Type: application/json' \
    -d "{
      \"@timestamp\": \"${ts}\",
      \"level\": \"${level}\",
      \"service\": \"${svc}\",
      \"host\": {\"name\": \"${host}\"},
      \"message\": \"${msg}\",
      \"status_code\": ${status},
      \"response_time_ms\": ${rt},
      \"trace_id\": \"${trace_id}\"
    }" > /dev/null 2>&1

  # ~30 logs per minute
  sleep 2
done
