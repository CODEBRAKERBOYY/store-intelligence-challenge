from __future__ import annotations

import sqlite3
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.analytics import (
    anomalies_response,
    funnel_response,
    health_response,
    heatmap_response,
    metrics_response,
)
from app.config import get_settings
from app.db import StoreRepository
from app.logging_config import configure_logging, request_logging_middleware
from app.models import ErrorResponse, IngestRequest, IngestResult, StoreEvent
from app.pos import load_pos_transactions


settings = get_settings()
repo = StoreRepository(settings.database_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    if settings.pos_csv_path and settings.pos_csv_path.exists():
        repo.upsert_pos(load_pos_transactions(settings.pos_csv_path))
    yield


app = FastAPI(title="Store Intelligence API", version="1.0.0", lifespan=lifespan)
app.middleware("http")(request_logging_middleware)


@app.exception_handler(sqlite3.Error)
async def sqlite_error_handler(request: Request, exc: sqlite3.Error) -> JSONResponse:
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    body = ErrorResponse(error="SERVICE_UNAVAILABLE", detail="Database unavailable", trace_id=trace_id)
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body.model_dump())


@app.post("/events/ingest", response_model=IngestResult)
def ingest_events(payload: IngestRequest) -> IngestResult:
    accepted = 0
    duplicate = 0
    errors = []
    for index, raw_event in enumerate(payload.events):
        try:
            event = StoreEvent.model_validate(raw_event)
        except ValidationError as exc:
            errors.append({"index": index, "error": exc.errors()})
            continue
        if repo.insert_event(event):
            accepted += 1
        else:
            duplicate += 1
    return IngestResult(accepted=accepted, duplicate=duplicate, rejected=len(errors), errors=errors)


@app.get("/stores/{id}/metrics")
def metrics(id: str):
    return metrics_response(id, repo.list_events(id), repo.list_pos(id))


@app.get("/stores/{id}/funnel")
def funnel(id: str):
    return funnel_response(id, repo.list_events(id), repo.list_pos(id))


@app.get("/stores/{id}/heatmap")
def heatmap(id: str):
    return heatmap_response(id, repo.list_events(id))


@app.get("/stores/{id}/anomalies")
def anomalies(id: str):
    return anomalies_response(id, repo.list_events(id), repo.list_pos(id))


@app.get("/health")
def health():
    repo.ping()
    return health_response(repo.health_rows())
