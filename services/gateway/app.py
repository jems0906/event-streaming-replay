import json
import logging
import os
import uuid
from typing import Any, Dict

import httpx
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from shared.event_schema import CapturedEvent
from shared.event_store import append_topic_event
from shared.kafka_config import build_kafka_common_config
from shared.logging_utils import configure_logging, set_correlation_id
from shared.tracing import init_tracing, instrument_fastapi

SERVICE_NAME = os.getenv("SERVICE_NAME", "gateway")
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
INCOMING_TOPIC = os.getenv("INCOMING_TOPIC", "incoming_requests")
MESSAGE_BACKEND = os.getenv("MESSAGE_BACKEND", "kafka").lower()
EVENT_STORE_DIR = os.getenv("EVENT_STORE_DIR", ".local-events")
CORE_PROCESS_URL = os.getenv("CORE_PROCESS_URL", "http://127.0.0.1:8001/process")

logger = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="Gateway Service")
producer: AIOKafkaProducer | None = None
init_tracing(SERVICE_NAME)
instrument_fastapi(app)

REQ_COUNTER = Counter(
    "gateway_requests_total",
    "Total requests captured by gateway",
    ["traffic_type", "status"],
)
REQ_LATENCY = Histogram(
    "gateway_request_latency_seconds",
    "Gateway request latency",
    ["traffic_type", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)


def _extract_trace_id() -> str:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx or not ctx.is_valid:
        return ""
    return f"{ctx.trace_id:032x}"


@app.on_event("startup")
async def startup_event() -> None:
    global producer
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    if MESSAGE_BACKEND == "kafka":
        producer = AIOKafkaProducer(**build_kafka_common_config())
        await producer.start()
        logger.info("Gateway producer started")
    else:
        producer = None
        logger.info("Gateway running in filesystem backend mode")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global producer
    if producer is not None:
        await producer.stop()
        logger.info("Gateway producer stopped")


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/ingest")
async def ingest(request: Request) -> JSONResponse:
    if MESSAGE_BACKEND == "kafka" and producer is None:
        return JSONResponse(status_code=503, content={"error": "producer not ready"})

    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": (await request.body()).decode("utf-8", errors="replace")}

    incoming_headers = {k.lower(): v for k, v in request.headers.items()}
    correlation_id = incoming_headers.get("x-correlation-id", str(uuid.uuid4()))
    replay_id = incoming_headers.get("x-replay-id")
    traffic_type = "replay" if replay_id else "live"

    set_correlation_id(correlation_id)

    with REQ_LATENCY.labels(traffic_type=traffic_type, path=request.url.path).time():
        event = CapturedEvent.new(
            environment=ENVIRONMENT,
            source_service=SERVICE_NAME,
            method=request.method,
            path=request.url.path,
            headers=incoming_headers,
            payload=payload,
            correlation_id=correlation_id,
            trace_id=_extract_trace_id(),
            replay_id=replay_id,
            response_status=202,
            metadata={"client_host": request.client.host if request.client else ""},
        )

        if MESSAGE_BACKEND == "kafka":
            await producer.send_and_wait(INCOMING_TOPIC, json.dumps(event.to_dict()).encode("utf-8"))
        else:
            append_topic_event(EVENT_STORE_DIR, INCOMING_TOPIC, event.to_dict())
            async with httpx.AsyncClient() as client:
                try:
                    await client.post(CORE_PROCESS_URL, json=event.to_dict(), timeout=10.0)
                except Exception:
                    logger.exception("Failed to forward event to core /process")

    REQ_COUNTER.labels(traffic_type=traffic_type, status="accepted").inc()
    logger.info(
        "Captured request",
        extra={
            "event_id": event.event_id,
            "traffic_type": traffic_type,
            "topic": INCOMING_TOPIC,
        },
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "captured",
            "event_id": event.event_id,
            "correlation_id": correlation_id,
            "traffic_type": traffic_type,
            "topic": INCOMING_TOPIC,
        },
    )
