from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: int | None = None
    sku_zone: str | None = None
    session_seq: int | None = None

    model_config = ConfigDict(extra="allow")


class StoreEvent(BaseModel):
    event_id: str = Field(min_length=8)
    store_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    visitor_id: str = Field(min_length=1)
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = Field(ge=0)
    is_staff: bool
    confidence: float = Field(ge=0, le=1)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("zone_id")
    @classmethod
    def blank_zone_to_none(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value


class IngestRequest(BaseModel):
    events: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class IngestResult(BaseModel):
    accepted: int
    duplicate: int
    rejected: int
    errors: list[dict[str, Any]]


class POSTransaction(BaseModel):
    store_id: str
    transaction_id: str
    timestamp: datetime
    basket_value_inr: float


class ErrorResponse(BaseModel):
    error: str
    detail: str
    trace_id: str
