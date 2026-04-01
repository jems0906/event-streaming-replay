import os
import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


_initialized = False
logger = logging.getLogger(__name__)


def init_tracing(service_name: str) -> None:
    global _initialized
    if _initialized:
        return

    if os.getenv("TRACING_ENABLED", "true").lower() != "true":
        _initialized = True
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"

    try:
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=insecure)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)
        _initialized = True
    except Exception as exc:
        logger.warning("Tracing init failed, continuing without exporter: %s", exc)
        _initialized = True


def instrument_fastapi(app) -> None:
    FastAPIInstrumentor.instrument_app(app)
