import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from shared.event_store import read_topic_events
from shared.kafka_config import build_kafka_common_config
from shared.logging_utils import configure_logging, set_correlation_id
from shared.tracing import init_tracing, instrument_fastapi

SERVICE_NAME = os.getenv("SERVICE_NAME", "replay")
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
INCOMING_TOPIC = os.getenv("INCOMING_TOPIC", "incoming_requests")
DEFAULT_REPLAY_TARGET = os.getenv("DEFAULT_REPLAY_TARGET", "http://127.0.0.1:8000/ingest")
MESSAGE_BACKEND = os.getenv("MESSAGE_BACKEND", "kafka").lower()
EVENT_STORE_DIR = os.getenv("EVENT_STORE_DIR", ".local-events")

logger = logging.getLogger(SERVICE_NAME)
app = FastAPI(title="Replay Service")
init_tracing(SERVICE_NAME)
instrument_fastapi(app)

REPLAY_COUNTER = Counter(
    "replay_replayed_events_total",
    "Total replayed events",
    ["status"],
)
REPLAY_ERRORS = Counter(
    "replay_errors_total",
    "Replay request-level errors",
)
REPLAY_LATENCY = Histogram(
    "replay_dispatch_latency_seconds",
    "Latency of replay dispatch requests",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0),
)


class ReplayRequest(BaseModel):
    # ISO-8601 time window (e.g. "2026-04-02T10:00:00Z")
    start_time_iso: Optional[str] = Field(default=None)
    end_time_iso: Optional[str] = Field(default=None)
    # Unix-epoch millisecond time window (alternative to ISO)
    from_timestamp_ms: Optional[int] = Field(default=None)
    to_timestamp_ms: Optional[int] = Field(default=None)
    environment: Optional[str] = Field(default=None)
    speedup_factor: float = Field(default=1.0, gt=0)
    max_events: int = Field(default=1000, gt=0, le=100000)
    target_url: str = Field(default=DEFAULT_REPLAY_TARGET)


@app.on_event("startup")
async def startup_event() -> None:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    logger.info("Replay service started")


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "environment": ENVIRONMENT}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _parse_iso_ms(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


async def _collect_events(req: ReplayRequest) -> List[Dict[str, Any]]:
    # Accept either ISO or epoch-ms time bounds
    start_ms = _parse_iso_ms(req.start_time_iso) or req.from_timestamp_ms
    end_ms = _parse_iso_ms(req.end_time_iso) or req.to_timestamp_ms

    if MESSAGE_BACKEND == "filesystem":
        collected = read_topic_events(EVENT_STORE_DIR, INCOMING_TOPIC)
    else:
        consumer = AIOKafkaConsumer(
            INCOMING_TOPIC,
            **build_kafka_common_config(),
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )

        collected = []
        try:
            await consumer.start()

            empty_polls = 0
            while len(collected) < req.max_events and empty_polls < 5:
                records = await consumer.getmany(timeout_ms=1000, max_records=500)
                if not records:
                    empty_polls += 1
                    continue
                empty_polls = 0

                for _, messages in records.items():
                    for msg in messages:
                        event = json.loads(msg.value.decode("utf-8"))
                        collected.append(event)
                        if len(collected) >= req.max_events:
                            break
                    if len(collected) >= req.max_events:
                        break
        finally:
            await consumer.stop()

    filtered: List[Dict[str, Any]] = []
    for event in collected:
        ts = int(event.get("timestamp_ms", 0))
        env = event.get("environment")

        if start_ms is not None and ts < start_ms:
            continue
        if end_ms is not None and ts > end_ms:
            continue
        if req.environment and env != req.environment:
            continue

        filtered.append(event)
        if len(filtered) >= req.max_events:
            break

    filtered.sort(key=lambda e: int(e.get("timestamp_ms", 0)))
    return filtered


async def _dispatch_event(
    client: httpx.AsyncClient,
    event: Dict[str, Any],
    target_url: str,
    replay_id: str,
) -> bool:
    corr_id = event.get("correlation_id") or str(uuid.uuid4())
    set_correlation_id(corr_id)

    headers = {
        "x-correlation-id": corr_id,
        "x-replay-id": replay_id,
        "content-type": "application/json",
    }

    payload = event.get("payload", {})

    with REPLAY_LATENCY.time():
        response = await client.post(target_url, json=payload, headers=headers, timeout=10.0)

    return 200 <= response.status_code < 300


@app.post("/replay")
async def replay(req: ReplayRequest) -> Dict[str, Any]:
    replay_id = str(uuid.uuid4())
    started_ms = int(time.time() * 1000)

    try:
        events = await _collect_events(req)
    except Exception as exc:
        REPLAY_ERRORS.inc()
        logger.exception("Failed to collect replay events")
        return {
            "status": "error",
            "message": str(exc),
            "replay_id": replay_id,
        }

    if not events:
        return {
            "status": "ok",
            "replay_id": replay_id,
            "message": "No matching events found",
            "replayed": 0,
        }

    success_count = 0
    failure_count = 0

    first_ts = int(events[0].get("timestamp_ms", started_ms))
    prev_ts = first_ts

    async with httpx.AsyncClient() as client:
        for idx, event in enumerate(events):
            current_ts = int(event.get("timestamp_ms", first_ts))
            if idx > 0:
                delta_ms = max(current_ts - prev_ts, 0)
                sleep_s = (delta_ms / 1000.0) / req.speedup_factor
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)
            prev_ts = current_ts

            try:
                ok = await _dispatch_event(client, event, req.target_url, replay_id)
                if ok:
                    success_count += 1
                    REPLAY_COUNTER.labels(status="success").inc()
                else:
                    failure_count += 1
                    REPLAY_COUNTER.labels(status="failed").inc()
            except Exception:
                failure_count += 1
                REPLAY_COUNTER.labels(status="failed").inc()

    ended_ms = int(time.time() * 1000)
    logger.info(
        "Replay completed",
        extra={
            "replay_id": replay_id,
            "requested": len(events),
            "success": success_count,
            "failed": failure_count,
            "duration_ms": ended_ms - started_ms,
        },
    )

    return {
        "status": "ok",
        "replay_id": replay_id,
        "requested": len(events),
        "success": success_count,
        "failed": failure_count,
        "duration_ms": ended_ms - started_ms,
        "target_url": req.target_url,
        "speedup_factor": req.speedup_factor,
    }
