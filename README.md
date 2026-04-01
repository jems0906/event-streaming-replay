# Observability-Centric Event Streaming & Replay Platform

This project implements a local microservice ecosystem for traffic capture, event streaming, replay-based debugging/load testing, and full observability.

## Architecture

- **Gateway service** (`:8000`)
  - Receives inbound HTTP traffic.
  - Adds correlation ID and trace context.
  - Captures request metadata and payload.
  - Publishes events to message backend (`incoming_requests` topic/file).

- **Core service** (`:8001`)
  - Consumes `incoming_requests` from the selected backend.
  - Processes events with retry + backoff.
  - Publishes successful outcomes to `processed_requests`.
  - Sends exhausted failures to `dlq_requests`.

- **Replay service** (`:8002`)
  - Reads historical traffic from selected backend.
  - Filters by time range/environment.
  - Replays traffic to gateway/core.
  - Supports speedup factor for accelerated load tests.

- **Observability stack**
  - Prometheus: metrics scraping and query (`:9090`)
  - Grafana: dashboards (`:3000`, admin/admin)
  - OpenTelemetry Collector: trace ingestion
  - Jaeger: distributed trace UI (`:16686`)
  - Kafka Exporter: Kafka lag/throughput metrics (`:9308/metrics`)

## Event Schema

See shared model in `shared/event_schema.py`.

Captured event fields include:

- `event_id`, `timestamp_ms`, `environment`
- `source_service`, `method`, `path`
- `headers`, `payload`
- `correlation_id`, `trace_id`, `replay_id`
- `response_status`

## Run

```bash
docker compose up --build
```

## Run Without Docker

You can run all Python services directly from your local `.venv`.

1. Activate venv:

```powershell
& ".\.venv\Scripts\Activate.ps1"
```

2. Start all services in **filesystem backend** mode (no Kafka required):

```powershell
./scripts/start-local.ps1 -MessageBackend filesystem -TracingEnabled false
```

3. Stop local services:

```powershell
./scripts/stop-local.ps1
```

Optional: use managed Kafka instead of filesystem backend:

```powershell
$kafkaPassword = Read-Host "Kafka Password" -AsSecureString

./scripts/start-local.ps1 `
  -MessageBackend kafka `
  -EnvironmentName dev `
  -KafkaBootstrapServers "<broker-host>:9092" `
  -KafkaSecurityProtocol SASL_SSL `
  -KafkaSaslMechanism PLAIN `
  -KafkaSaslUsername "<username>" `
  -KafkaSaslPassword $kafkaPassword `
  -TracingEnabled false
```

Notes:

- `MessageBackend=filesystem` writes events to `.local-events/*.jsonl`.
- `MessageBackend=kafka` uses the configured Kafka broker and credentials.
- `TRACING_ENABLED=false` disables OTLP exporter when collector/Jaeger is not running.
- Metrics endpoints still work locally at `/metrics` for each service.

## Deploy to Render

This repository includes a Render Blueprint at `render.yaml`.

Important:

- Use `MESSAGE_BACKEND=kafka` on Render.
- Do not use `filesystem` mode in production across multiple services.
- Provision a managed Kafka broker (Redpanda Cloud, Confluent, etc.) and set broker credentials.

Deploy steps:

1. In Render, choose **New +** -> **Blueprint**.
2. Connect this GitHub repository.
3. Render will detect `render.yaml` and create 3 web services:
  - `event-streaming-gateway`
  - `event-streaming-core`
  - `event-streaming-replay`
4. Replace all `REPLACE_ME` Kafka values with real credentials.
5. Deploy.

Post-deploy checks:

- Open each service `/health` endpoint.
- Test ingest: `POST /ingest` on gateway.
- Test replay: `POST /replay` on replay service.

If Render assigns different service URLs than the defaults in `render.yaml`, update:

- `CORE_PROCESS_URL` in gateway
- `DEFAULT_REPLAY_TARGET` in replay

## Smoke Test

1. Send live traffic:

```bash
curl -X POST http://127.0.0.1:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"user_id":123,"action":"checkout"}'
```

2. Trigger replay (10x speed):

```bash
curl -X POST http://127.0.0.1:8002/replay \
  -H "Content-Type: application/json" \
  -d '{
    "environment":"dev",
    "speedup_factor":10,
    "max_events":100,
    "target_url":"http://127.0.0.1:8000/ingest"
  }'
```

## Endpoints

- Gateway
  - `POST /ingest`
  - `GET /health`
  - `GET /metrics`

- Core
  - `GET /health`
  - `GET /metrics`

- Replay
  - `POST /replay`
  - `GET /health`
  - `GET /metrics`

## Metrics to Track

- `gateway_requests_total` with traffic type labels (`live` vs `replay`)
- `gateway_request_latency_seconds`
- `core_processed_events_total`, `core_failed_events_total`
- `core_processing_duration_seconds`
- `core_consumer_lag`
- `replay_replayed_events_total`, `replay_errors_total`
- `replay_dispatch_latency_seconds`
- `kafka_exporter` lag/throughput metrics

## Dashboards

Provisioned dashboard: **Streaming & Replay Overview**

Includes panels for:

- Live vs replay throughput
- p95 gateway latency
- Core error rate
- Replay error rate
- Kafka consumer lag
- Processing duration percentiles

## Notes

- Topics auto-create in Redpanda for local development.
- In production, pre-create topics with explicit partitions and retention settings.
- This project focuses on observability and replay workflows rather than business-domain logic.
