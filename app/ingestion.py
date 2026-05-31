from __future__ import annotations

from pydantic import ValidationError

from app.db import StoreRepository
from app.models import IngestResult, StoreEvent


def ingest_batch(repository: StoreRepository, raw_events: list[dict]) -> IngestResult:
    accepted = 0
    duplicate = 0
    errors = []
    for index, raw_event in enumerate(raw_events):
        try:
            event = StoreEvent.model_validate(raw_event)
        except ValidationError as exc:
            errors.append({"index": index, "error": exc.errors()})
            continue
        if repository.insert_event(event):
            accepted += 1
        else:
            duplicate += 1
    return IngestResult(accepted=accepted, duplicate=duplicate, rejected=len(errors), errors=errors)
