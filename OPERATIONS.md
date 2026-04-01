# Event Streaming & Replay Platform — Operations Guide

## Quick Start

Once Docker is running and the stack is up:

```bash
docker compose up --build -d
```

## Service URLs

| Service | URL | Port |
|---------|-----|------|
| Gateway (capture) | http://localhost:8000 | 8000 |
| Core (processor) | http://localhost:8001 | 8001 |
| Replay (traffic replay) | http://localhost:8002 | 8002 |
| Prometheus | http://localhost:9090 | 9090 |
| Grafana | http://localhost:3000 | 3000 |
| Jaeger | http://localhost:16686 | 16686 |
| Redpanda (Kafka) | localhost:19092 | 19092 |

## Testing & Validation

### 1. Smoke Test (Verify all services are running)
```bash
python tests/smoke_tests.py
```
Checks:
- Service health endpoints
- Live traffic capture
- Metrics endpoints
- Prometheus scrape targets
- Replay dispatch

### 2. Integration Test (Kafka topics, consumer groups, logs)
```bash
python tests/integration_helpers.py
```
Displays:
- All Kafka topics and message counts
- Consumer group lag
- Recent service logs

### 3. Load Test (Send sustained traffic)
```bash
# Send 100 req/sec for 60 seconds
python tests/load_test.py --rps 100 --duration 60
```

## Manual Testing

### Send live traffic (single request)
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"user_id":123,"action":"purchase"}'
```

**Response:**
```json
{
  "status": "captured",
  "event_id": "uuid-here",
  "correlation_id": "uuid-here",
  "traffic_type": "live",
  "topic": "incoming_requests"
}
```

### Trigger replay (10x speed, last 100 events)
```bash
curl -X POST http://localhost:8002/replay \
  -H "Content-Type: application/json" \
  -d '{
    "environment": "dev",
    "speedup_factor": 10,
    "max_events": 100,
    "target_url": "http://gateway:8000/ingest"
  }'
```

**Response:**
```json
{
  "status": "ok",
  "replay_id": "uuid-here",
  "requested": 50,
  "success": 50,
  "failed": 0,
  "duration_ms": 5000,
  "target_url": "http://gateway:8000/ingest",
  "speedup_factor": 10
}
```

### Query service metrics
```bash
# Gateway metrics
curl http://localhost:8000/metrics | grep "gateway_"

# Core metrics
curl http://localhost:8001/metrics | grep "core_"

# Replay metrics
curl http://localhost:8002/metrics | grep "replay_"
```

## Observability

### Grafana Dashboard
1. Open http://localhost:3000
2. Username: `admin`, Password: `admin`
3. Pre-provisioned dashboard: **Streaming & Replay Overview**

**Dashboard panels:**
- Live vs Replay Throughput (requests per second)
- Gateway Latency (p95/p99)
- Core Error Rate
- Replay Error Rate
- Core Consumer Lag
- Core Performance (processing latency + throughput by traffic type)

### Prometheus Query Examples

```promql
# Live traffic RPS (last 5 min)
sum(rate(gateway_requests_total{traffic_type="live"}[5m]))

# Replay traffic RPS (last 5 min)
sum(rate(gateway_requests_total{traffic_type="replay"}[5m]))

# Core processing latency p95
histogram_quantile(0.95, sum(rate(core_processing_duration_seconds_bucket[5m])) by (le))

# Core consumer lag
core_consumer_lag

# Core error rate
sum(rate(core_failed_events_total[5m]))

# Replay errors per second
sum(rate(replay_errors_total[5m]))
```

### Jaeger Distributed Traces
1. Open http://localhost:16686
2. Select service: **gateway**, **core**, or **replay**
3. View trace waterfall showing span timings and context propagation

## Key Metrics to Monitor

### Gateway
- `gateway_requests_total` — Total requests captured (labels: traffic_type, status)
- `gateway_request_latency_seconds` — Request ingestion latency (histogram)

### Core
- `core_processed_events_total` — Successfully processed events (label: traffic_type)
- `core_failed_events_total` — Events sent to DLQ (label: traffic_type)
- `core_processing_duration_seconds` — Event processing time (histogram)
- `core_consumer_lag` — Kafka consumer lag in messages

### Replay
- `replay_replayed_events_total` — Replayed events (label: status)
- `replay_errors_total` — Replay request failures
- `replay_dispatch_latency_seconds` — Latency of individual replay requests

### Kafka
- `kafka_broker_*` — Broker metrics (from kafka-exporter)
- `kafka_consumer_lag_sum` — Consumer group lag
- `kafka_topic_partition_replicas` — Topic/partition structure

## Troubleshooting

### Services not responding
```bash
docker compose logs gateway core replay
```

### Kafka connectivity issues
```bash
# Check if Redpanda is healthy
docker exec redpanda rpk cluster health

# List topics
docker exec redpanda rpk topic list

# Check consumer group status
docker exec redpanda rpk group list core-consumer-group
```

### Metrics not appearing in Prometheus
1. Check Prometheus targets: http://localhost:9090/api/v1/targets
2. Verify service /metrics endpoints are returning data
3. Check Prometheus scrape config in `observability/prometheus/prometheus.yml`

### High consumer lag
- Increase core service resources (CPU/memory)
- Monitor `core_processing_duration_seconds` — if processing is slow, optimize the business logic
- Scale core horizontally (add worker instances with different consumer group)

## Performance Baseline

Expected performance on a modern dev machine:

- **Live throughput:** 100–500 rps (single gateway instance)
- **Replay throughput:** 500–2000 rps (speedup factor 5–10)
- **Gateway latency p95:** < 50ms
- **Core processing latency p95:** < 100ms (includes Kafka round-trip)
- **Consumer lag at steady state:** < 5 messages

## Configuration

Environment variables (in `docker-compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | dev | Environment tag (dev/test/staging) |
| `KAFKA_BOOTSTRAP_SERVERS` | localhost:9092 | Kafka broker endpoint |
| `INCOMING_TOPIC` | incoming_requests | Topic for captured traffic |
| `PROCESSED_TOPIC` | processed_requests | Topic for processed events |
| `DLQ_TOPIC` | dlq_requests | Dead-letter queue topic |
| `MAX_RETRIES` | 3 | Core consumer retry attempts |
| `RETRY_BASE_DELAY_MS` | 200 | Initial backoff delay (ms) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | http://otel-collector:4317 | OpenTelemetry endpoint |

## Cleanup

```bash
# Stop and remove containers
docker compose down

# Stop, remove, and delete volumes
docker compose down -v
```

## Next Steps

1. **Alert Rules:** Add Prometheus alert rules in `observability/prometheus/prometheus.yml` for SLOs (e.g. 99% p99 latency < 500ms)
2. **Alertmanager:** Integrate with Alertmanager to route alerts
3. **Topic Configuration:** Pre-create topics with explicit partitions and retention settings for production
4. **Scaling:** Use `docker compose --scale core=3` to run multiple core instances (requires shared consumer group)
5. **Custom Dashboards:** Create additional Grafana dashboards for domain-specific metrics
6. **Persistence:** Add persistent volumes for Prometheus/Grafana data
