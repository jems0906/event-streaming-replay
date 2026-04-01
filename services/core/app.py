import asyncio
import json
import logging
import os
import random
import time
from contextlib import suppress
from typing import Any, Dict

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

from shared.event_store import append_topic_event
from shared.kafka_config import build_kafka_common_config
from shared.logging_utils import configure_logging, set_correlation_id
from shared.tracing import init_tracing, instrument_fastapi

SERVICE_NAME = os.getenv("SERVICE_NAME", "core")
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
INCOMING_TOPIC = os.getenv("INCOMING_TOPIC", "incoming_requests")
PROCESSED_TOPIC = os.getenv("PROCESSED_TOPIC", "processed_requests")
DLQ_TOPIC = os.getenv("DLQ_TOPIC", "dlq_requests")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "core-consumer-group")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BASE_DELAY_MS = int(os.getenv("RETRY_BASE_DELAY_MS", "200"))
MESSAGE_BACKEND = os.getenv("MESSAGE_BACKEND", "kafka").lower()
EVENT_STORE_DIR = os.getenv("EVENT_STORE_DIR", ".local-events")

logger = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="Core Service")
consumer: AIOKafkaConsumer | None = None
init_tracing(SERVICE_NAME)
instrument_fastapi(app)
producer: AIOKafkaProducer | None = None
consumer_task: asyncio.Task | None = None

PROC_COUNTER = Counter(
    "core_processed_events_total",
    "Total successfully processed events",
    ["traffic_type"],
)
FAIL_COUNTER = Counter(
    "core_failed_events_total",
    "Total failed events sent to DLQ",
    ["traffic_type"],
)
PROC_LATENCY = Histogram(
    "core_processing_duration_seconds",
    "Core event processing duration",
    ["traffic_type"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0),
)
CONSUMER_LAG = Gauge(
    "core_consumer_lag",
    "Approximate lag of core consumer group",
)


@app.on_event("startup")
async def startup_event() -> None:
    global consumer, producer, consumer_task

    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    if MESSAGE_BACKEND == "kafka":
        common_kafka_config = build_kafka_common_config()

        consumer = AIOKafkaConsumer(
            INCOMING_TOPIC,
            **common_kafka_config,
            group_id=CONSUMER_GROUP,
            enable_auto_commit=True,
            auto_offset_reset="earliest",
        )
        producer = AIOKafkaProducer(**common_kafka_config)

        await consumer.start()
        await producer.start()

        consumer_task = asyncio.create_task(consume_loop())
        logger.info("Core consumer loop started")
    else:
        consumer = None
        producer = None
        CONSUMER_LAG.set(0.0)
        logger.info("Core running in filesystem backend mode")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global consumer, producer, consumer_task

    if consumer_task:
        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task

    if consumer is not None:
        await consumer.stop()
    if producer is not None:
        await producer.stop()


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME, "environment": ENVIRONMENT}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def _simulate_processing(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload", {})
    if payload.get("force_fail"):
        raise RuntimeError("Forced failure via payload.force_fail")

    # Small randomized processing delay to emulate realistic service behavior.
    await asyncio.sleep(random.uniform(0.01, 0.08))
    return {
        "event_id": event.get("event_id"),
        "processed_at_ms": int(time.time() * 1000),
        "status": "processed",
        "correlation_id": event.get("correlation_id"),
        "replay_id": event.get("replay_id"),
        "trace_id": event.get("trace_id"),
        "environment": event.get("environment"),
        "source_service": SERVICE_NAME,
        "original_payload": payload,
    }


def _traffic_type(event: Dict[str, Any]) -> str:
    return "replay" if event.get("replay_id") else "live"


async def _publish_json(topic: str, payload: Dict[str, Any]) -> None:
    if MESSAGE_BACKEND == "kafka":
        if producer is None:
            raise RuntimeError("Producer is unavailable")
        await producer.send_and_wait(topic, json.dumps(payload).encode("utf-8"))
        return

    append_topic_event(EVENT_STORE_DIR, topic, payload)


async def _send_to_dlq(event: Dict[str, Any], error: str, attempts: int) -> None:
    dlq_payload = {
        "error": error,
        "attempts": attempts,
        "failed_at_ms": int(time.time() * 1000),
        "event": event,
    }
    await _publish_json(DLQ_TOPIC, dlq_payload)


def _compute_backoff_seconds(attempt: int) -> float:
    return (RETRY_BASE_DELAY_MS * (2 ** max(attempt - 1, 0))) / 1000.0


async def consume_loop() -> None:
    assert consumer is not None

    while True:
        batch = await consumer.getmany(timeout_ms=1000, max_records=100)
        if not batch:
            continue

        for tp, messages in batch.items():
            for msg in messages:
                event = json.loads(msg.value.decode("utf-8"))
                correlation_id = event.get("correlation_id", "")
                set_correlation_id(correlation_id)
                traffic_type = _traffic_type(event)

                start = time.perf_counter()
                success = False
                attempts = 0
                last_error = ""

                for attempt in range(1, MAX_RETRIES + 2):
                    attempts = attempt
                    try:
                        processed_payload = await _simulate_processing(event)
                        await _publish_json(PROCESSED_TOPIC, processed_payload)
                        success = True
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        if attempt <= MAX_RETRIES:
                            backoff_s = _compute_backoff_seconds(attempt)
                            logger.warning(
                                "Retrying failed event",
                                extra={
                                    "attempt": attempt,
                                    "max_retries": MAX_RETRIES,
                                    "event_id": event.get("event_id"),
                                    "backoff_seconds": backoff_s,
                                },
                            )
                            await asyncio.sleep(backoff_s)

                elapsed = time.perf_counter() - start
                PROC_LATENCY.labels(traffic_type=traffic_type).observe(elapsed)

                if success:
                    PROC_COUNTER.labels(traffic_type=traffic_type).inc()
                else:
                    FAIL_COUNTER.labels(traffic_type=traffic_type).inc()
                    await _send_to_dlq(event, last_error, attempts)
                    logger.error(
                        "Event sent to DLQ",
                        extra={
                            "event_id": event.get("event_id"),
                            "error": last_error,
                            "attempts": attempts,
                        },
                    )

                end_offsets = await consumer.end_offsets([tp])
                current_offset = msg.offset + 1
                lag = max(end_offsets.get(tp, current_offset) - current_offset, 0)
                CONSUMER_LAG.set(float(lag))


async def _process_event(event: Dict[str, Any]) -> Dict[str, Any]:
    correlation_id = event.get("correlation_id", "")
    set_correlation_id(correlation_id)
    traffic_type = _traffic_type(event)

    start = time.perf_counter()
    success = False
    attempts = 0
    last_error = ""

    for attempt in range(1, MAX_RETRIES + 2):
        attempts = attempt
        try:
            processed_payload = await _simulate_processing(event)
            await _publish_json(PROCESSED_TOPIC, processed_payload)
            success = True
            break
        except Exception as exc:
            last_error = str(exc)
            if attempt <= MAX_RETRIES:
                backoff_s = _compute_backoff_seconds(attempt)
                await asyncio.sleep(backoff_s)

    elapsed = time.perf_counter() - start
    PROC_LATENCY.labels(traffic_type=traffic_type).observe(elapsed)

    if success:
        PROC_COUNTER.labels(traffic_type=traffic_type).inc()
    else:
        FAIL_COUNTER.labels(traffic_type=traffic_type).inc()
        await _send_to_dlq(event, last_error, attempts)

    return {
        "success": success,
        "attempts": attempts,
        "error": last_error,
        "traffic_type": traffic_type,
    }


@app.post("/process")
async def process_event_endpoint(event: Dict[str, Any]) -> Dict[str, Any]:
    result = await _process_event(event)
    return {"status": "ok" if result["success"] else "failed", **result}
