from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response


logger = logging.getLogger("store_intelligence")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


async def request_logging_middleware(request: Request, call_next: Callable) -> Response:
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["x-trace-id"] = trace_id
        return response
    finally:
        route = request.url.path
        event_count = None
        if route.endswith("/events/ingest"):
            try:
                body = await request.body()
                event_count = body.count(b"event_id")
            except Exception:
                event_count = None
        logger.info(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "store_id": request.path_params.get("id"),
                    "endpoint": route,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                    "event_count": event_count,
                    "status_code": status_code,
                }
            )
        )
