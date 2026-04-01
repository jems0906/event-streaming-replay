# Event Streaming & Replay Platform — Architecture & Design

## Overview

A microservice ecosystem for **traffic capture, event streaming, replay-based performance testing, and full observability** using Kafka, Python FastAPI, and cloud-native tooling (Prometheus, Grafana, Jaeger, OpenTelemetry).

## System Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        EXTERNAL CLIENTS                          │
│                      (curl, browsers, etc)                       │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP
                         ▼
         ┌───────────────────────────────┐
         │   GATEWAY SERVICE (8000)      │ ← Traffic capture
         │  - Receive requests           │
         │  - Add correlation ID         │ → Publishes to
         │  - Capture metadata           │   "incoming_requests"
         │  - Tag traffic type           │
         │  - Trace propagation          │
         └────────────┬────────────────┬─┘
                      │ JSON events    │
                      ▼                 ▼
        ┌──────────────────────────────────────┐
        │    KAFKA / REDPANDA BROKER           │
        │    ┌──────────────────────────────┐  │
        │    │ incoming_requests (topic)    │  │ ← All captured traffic
        │    │ - Replicated, auto-created   │  │
        │    └──────────────────────────────┘  │
        │    ┌──────────────────────────────┐  │
        │    │ processed_requests (topic)   │  │ ← Successful processing
        │    └──────────────────────────────┘  │
        │    ┌──────────────────────────────┐  │
        │    │ dlq_requests (topic)         │  │ ← Failed events after retries
        │    └──────────────────────────────┘  │
        └────────┬────────────────────────────┘
                 │ Consumes
                 ▼
     ┌─────────────────────────────────┐
     │  CORE SERVICE (8001)            │ ← Event processing
     │  - Consume incoming_requests    │
     │  - Retry with exponential       │ → Publishes success/DLQ
     │    backoff (configurable)       │
     │  - Publish processed_requests   │
     │  - Send to DLQ on exhaustion    │
     │  - Emit metrics (latency, lag)  │
     └────────────────────────────────┘

        Parallel: Traffic Replay

    ┌──────────────────────────────────┐
    │  REPLAY SERVICE (8002)           │ ← Reads from Kafka
    │  - Query historical events (time │ ← Filters: time range,
    │    range, environment filter)    │   environment, environment
    │  - Speedup factor (1x-100x)      │ → Replays to gateway/core
    │  - Replay dispatch at target URL │   at controlled rate
    │  - Emit replay metrics           │
    └──────────────────────────────────┘
```

## Data Flow

### Live Traffic Capture
1. **Client** → `POST /ingest` at Gateway
2. **Gateway** creates `CapturedEvent`:
   - Generates `event_id` (UUID)
   - Adds/extracts `correlation_id` from headers
   - Captures `trace_id` from OpenTelemetry span context
   - Tags as `traffic_type: "live"` (no `replay_id`)
3. **Gateway** → Produces to `incoming_requests` Kafka topic
4. **Core** ← Consumes from `incoming_requests`
5. **Core** → Simulates processing (may fail/retry)
6. **Core** → Publishes to `processed_requests` or `dlq_requests`

### Replay Flow
1. **Client** → `POST /replay` at Replay service with filters:
   - `environment`: filter by env tag
   - `start_time_iso` / `end_time_iso`: time window
   - `speedup_factor`: 1x (real-time), 5x, 10x, etc.
2. **Replay** ← Reads from `incoming_requests` Kafka
3. **Replay** filters events by time/environment
4. **Replay** replays to target URL (default: `http://gateway:8000/ingest`):
   - Adds `x-replay-id` header (identifies replay batch)
   - Respects original timestamps, applies speedup
   - Tracks success/failure per event
5. **Gateway** tags as `traffic_type: "replay"` (has `replay_id`)
6. Both live and replay go through normal processing pipeline

## Event Schema

```python
CapturedEvent:
  event_id: str                    # UUID
  timestamp_ms: int                # Event capture time (milliseconds)
  environment: str                 # dev/test/staging/prod
  source_service: str              # gateway, replay, etc.
  method: str                      # HTTP method (POST, GET, etc.)
  path: str                        # HTTP request path
  headers: Dict[str, str]          # Captured request headers
  payload: Dict[str, Any]          # Request body
  correlation_id: str              # Trace correlation ID (UUID)
  trace_id: Optional[str]          # OpenTelemetry trace ID
  replay_id: Optional[str]         # If replayed, batch ID
  response_status: Optional[int]   # HTTP response status
  metadata: Dict[str, Any]         # Additional attrs (client_host, etc.)
```

## Observability

### Logging
- **Format:** Structured JSON to stdout
- **Fields:** timestamp, level, service, message, correlation_id, logger, exception
- **Correlation:** All logs for a request tagged with `correlation_id` for tracing

### Tracing (OpenTelemetry → Jaeger)
- Gateway creates root span for each inbound request
- Services instrument FastAPI, Kafka clients
- Trace context propagated via HTTP headers (W3C Trace Context)
- All spans exported to Jaeger via OpenTelemetry Collector (gRPC)

### Metrics (Prometheus)
- **Collection:** Services expose `/metrics` endpoint (Prometheus format)
- **Scrape:** Prometheus scrapes all service metrics every 5 seconds
- **Storage:** In-memory, 15-day retention
- **Dashboards:** Grafana provisioned with pre-built panels

#### Key Metrics

**Gateway**
```
gateway_requests_total{traffic_type, status} — Counter
gateway_request_latency_seconds{traffic_type, path} — Histogram
```

**Core**
```
core_processed_events_total{traffic_type} — Counter
core_failed_events_total{traffic_type} — Counter
core_processing_duration_seconds{traffic_type} — Histogram
core_consumer_lag — Gauge (Kafka consumer group lag)
```

**Replay**
```
replay_replayed_events_total{status} — Counter
replay_errors_total — Counter
replay_dispatch_latency_seconds — Histogram
```

## Retry & DLQ Strategy

### Core Consumer Retry Logic
1. First processing attempt: immediate
2. If failed, retry with exponential backoff:
   - Attempt 1: Wait 200ms
   - Attempt 2: Wait 400ms
   - Attempt 3: Wait 800ms
3. Configurable via env:
   - `MAX_RETRIES=3`
   - `RETRY_BASE_DELAY_MS=200`
4. After max retries exhausted: publish to `dlq_requests` with error details

### DLQ Topic Format
```json
{
  "error": "Processing exception message",
  "attempts": 4,
  "failed_at_ms": 1234567890,
  "event": { ... original CapturedEvent ... }
}
```

## Service Responsibilities

### Gateway (Traffic Capture)
- **Port:** 8000
- **Endpoints:**
  - `POST /ingest` — Capture traffic
  - `GET /health` — Liveness/readiness
  - `GET /metrics` — Prometheus metrics
- **Responsibilities:**
  - Accept inbound HTTP requests
  - Extract/generate correlation IDs
  - Capture request metadata (method, path, headers, payload)
  - Tag traffic as live vs. replay
  - Publish to Kafka `incoming_requests` topic
  - Emit gateway metrics (throughput, latency)

### Core (Event Processing)
- **Port:** 8001
- **Endpoints:**
  - `GET /health`
  - `GET /metrics`
- **Responsibilities:**
  - Consume from `incoming_requests` Kafka topic
  - Simulate business logic (with optional forced failures)
  - Retry failed events with exponential backoff
  - Publish successful events to `processed_requests`
  - Publish failed events to `dlq_requests`
  - Emit core metrics (processing latency, consumer lag, errors)
  - Maintain consumer group `core-consumer-group`

### Replay (Traffic Replay)
- **Port:** 8002
- **Endpoints:**
  - `POST /replay` — Start replay
  - `GET /health`
  - `GET /metrics`
- **Request body:**
  ```json
  {
    "start_time_iso": "2026-04-01T10:00:00Z",
    "end_time_iso": "2026-04-01T11:00:00Z",
    "environment": "dev",
    "speedup_factor": 10.0,
    "max_events": 1000,
    "target_url": "http://gateway:8000/ingest"
  }
  ```
- **Responsibilities:**
  - Read historical traffic from `incoming_requests` Kafka
  - Filter by time window, environment
  - Replay to target URL with speedup factor
  - Respect original event timing (scaled by speedup)
  - Track and report replay success/failure rates
  - Emit replay metrics

## Deployment & Scaling

### Single-Node Development (docker-compose)
- All services on same host network
- Redpanda (Kafka) runs locally
- Prometheus, Grafana, Jaeger collocated

### Horizontal Scaling
- **Gateway:** Scale horizontally behind load balancer (stateless)
- **Core:** Scale via consumer group (shared `core-consumer-group`):
  - Each instance consumes subset of partitions
  - Consumer group manages partition assignment
  - All instances read from same Kafka topic
  - Kafka rebalance on instance add/removal
- **Replay:** Scale as needed (stateless, reads from Kafka, replays to external target)

### Production Considerations
1. **Kafka Topics:** Pre-create with:
   - `incoming_requests`: 10+ partitions, 7-day retention
   - `processed_requests`: 3-5 partitions, 1-day retention
   - `dlq_requests`: 1-2 partitions, 30-day retention
2. **Scaling:** Deploy core instances on managed Kubernetes / ECS
3. **Monitoring:** Add Prometheus alert rules, integrate with Alertmanager
4. **Persistence:** Use persistent volumes for Prometheus, Grafana, Redpanda
5. **Security:** Enable Kafka SASL/TLS, service-to-service mTLS, API authentication

## Failure Modes & Resilience

| Failure | Impact | Mitigation |
|---------|--------|-----------|
| Gateway down | New traffic not captured | Load-balance gateway, autorestart |
| Kafka broker down | Topic unavailable | Kafka replication, healthy broker failover |
| Core processing slow | Consumer lag grows | Scale core horizontally, optimize processing |
| Core crashes | Events not consumed | Autorestart, Kafka rebalance picks up where left off |
| Replay service down | Replays not available | Stateless, just restart; Kafka retains historical events |
| Prometheus down | Metrics not scraped | Metrics still emitted by services, Prometheus catches up on restart |
| Grafana down | Dashboards unavailable | Data retained in Prometheus, dashboard JSON backed up |

## Testing Strategy

### Smoke Tests
- Service health checks
- Single live traffic event
- Single replay batch
- Metrics endpoint availability

### Integration Tests
- Kafka topic creation & message flow
- Consumer group status
- Event round-trip (live → Kafka → core → processed_requests)

### Load Tests
- Sustained traffic at target RPS
- Replay at 10x–100x speedup
- Measure latency percentiles, error rates

### Performance Baselines
- **Live throughput:** 100–500 rps (single gateway)
- **Replay throughput:** 500–2000 rps (10x speedup)
- **Gateway latency p95:** < 50ms
- **Core latency p95:** < 100ms
- **Consumer lag at steady state:** < 5 messages

## Technology Stack

| Layer | Tech | Version |
|-------|------|---------|
| **Runtime** | Python | 3.12 |
| **Web Framework** | FastAPI | 0.115 |
| **ASGI Server** | Uvicorn | 0.30 |
| **Kafka Client** | aiokafka | 0.10 |
| **HTTP Client** | httpx | 0.27 |
| **Metrics** | prometheus-client | 0.20 |
| **Tracing** | OpenTelemetry SDK | 1.25 |
| **Tracing Export** | OTLP gRPC | 1.25 |
| **Logs** | Structured JSON (stdlib) | – |
| **Broker** | Redpanda | 24.1 |
| **Metrics DB** | Prometheus | 2.53 |
| **Visualization** | Grafana | 11.0 |
| **Trace Backend** | Jaeger | 1.57 |
| **Orchestration** | docker-compose | – |

## File Structure

```
.
├── README.md                              # User overview
├── OPERATIONS.md                          # Operations guide
├── ARCHITECTURE.md                        # This file
├── Makefile                               # Quick commands
├── docker-compose.yml                     # Full stack config
├── .env.example                           # Env template
├── shared/
│   ├── event_schema.py                    # CapturedEvent dataclass
│   ├── logging_utils.py                   # JSON logging setup
│   └── tracing.py                         # OpenTelemetry init
├── services/
│   ├── gateway/
│   │   ├── app.py                         # HTTP listener, Kafka producer
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── core/
│   │   ├── app.py                         # Kafka consumer, processor, DLQ
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── replay/
│       ├── app.py                         # Replay API, Kafka reader
│       ├── Dockerfile
│       └── requirements.txt
├── observability/
│   ├── prometheus/
│   │   └── prometheus.yml                 # Prometheus config, scrape targets
│   ├── grafana/
│   │   ├── provisioning/
│   │   │   ├── datasources/
│   │   │   │   └── datasource.yml         # Prometheus datasource
│   │   │   └── dashboards/
│   │   │       └── dashboards.yml         # Dashboard provisioning
│   │   └── dashboards/
│   │       └── streaming-overview.json    # Pre-built dashboard
│   └── otel/
│       └── otel-collector.yml             # OpenTelemetry Collector config
└── tests/
    ├── smoke_tests.py                     # Service health & basic flow
    ├── integration_helpers.py             # Kafka topic inspection
    ├── load_test.py                       # Sustained traffic generator
    └── requirements.txt                   # Test dependencies
```

## Future Enhancements

1. **Partitioning Strategy:** Partition by `correlation_id` hash for request ordering
2. **Compacted Topics:** Use log-compacted `customer_state` topic for state replay
3. **Schema Registry:** Protobuf/Avro schemas with schema versioning
4. **Circuit Breaker:** Add fault tolerance to core service
5. **Custom Replay Profiles:** Pre-defined replay patterns (peak hour, regression, canary)
6. **Rate Limiting:** Token bucket or sliding window at gateway
7. **Access Control:** OAuth2/mTLS for service-to-service auth
8. **Metrics Export:** Push metrics to cloud (CloudWatch, Datadog, etc.)
9. **Alerting:** Prometheus alert rules + Alertmanager routing
10. **SLA Tracking:** Automated SLO compliance dashboards
