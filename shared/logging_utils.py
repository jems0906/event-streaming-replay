import json
import logging
import os
import sys
from contextvars import ContextVar
from typing import Optional

correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": os.getenv("SERVICE_NAME", "unknown"),
            "message": record.getMessage(),
            "correlation_id": correlation_id_ctx.get(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root.handlers = [handler]


def set_correlation_id(value: Optional[str]) -> None:
    correlation_id_ctx.set(value or "")
