import os
import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

try:
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    _HTTPX_INSTRUMENTOR = HTTPXClientInstrumentor()
except ImportError:
    _HTTPX_INSTRUMENTOR = None


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

    # Instrument outgoing httpx calls to propagate W3C trace context
    if _HTTPX_INSTRUMENTOR is not None:
        try:
            _HTTPX_INSTRUMENTOR.instrument()
        except Exception as exc:
            logger.warning("httpx instrumentation failed: %s", exc)


def instrument_fastapi(app) -> None:
    FastAPIInstrumentor.instrument_app(app)
