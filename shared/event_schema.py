import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CapturedEvent:
    event_id: str
    timestamp_ms: int
    environment: str
    source_service: str
    method: str
    path: str
    headers: Dict[str, str]
    payload: Dict[str, Any]
    correlation_id: str
    trace_id: Optional[str] = None
    replay_id: Optional[str] = None
    response_status: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def new(
        cls,
        *,
        environment: str,
        source_service: str,
        method: str,
        path: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        correlation_id: str,
        trace_id: Optional[str],
        replay_id: Optional[str],
        response_status: Optional[int],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "CapturedEvent":
        return cls(
            event_id=str(uuid.uuid4()),
            timestamp_ms=int(time.time() * 1000),
            environment=environment,
            source_service=source_service,
            method=method,
            path=path,
            headers=headers,
            payload=payload,
            correlation_id=correlation_id,
            trace_id=trace_id,
            replay_id=replay_id,
            response_status=response_status,
            metadata=metadata or {},
        )
