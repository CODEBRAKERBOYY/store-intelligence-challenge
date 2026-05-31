# PROMPT: Generate FastAPI tests for a retail store intelligence API that verifies event ingestion is idempotent, malformed events partially fail, and metrics remain valid for zero-purchase and all-staff edge cases.
# CHANGES MADE: Replaced generic fixtures with the project StoreRepository, added explicit temp SQLite isolation, and asserted the exact business fields used by this challenge.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

import app.main as main
from app.db import StoreRepository


def test_ingest_is_idempotent_and_partial_success(tmp_path):
    main.repo = StoreRepository(tmp_path / "test.db")
    client = TestClient(main.app)
    event = _event("evt-0001", "VIS_1", "ENTRY", datetime.now(UTC))

    response = client.post("/events/ingest", json={"events": [event, {"bad": "payload"}]})
    assert response.status_code == 200
    assert response.json()["accepted"] == 1
    assert response.json()["rejected"] == 1

    response = client.post("/events/ingest", json={"events": [event]})
    assert response.status_code == 200
    assert response.json()["duplicate"] == 1


def test_metrics_excludes_staff_and_handles_zero_purchase(tmp_path):
    main.repo = StoreRepository(tmp_path / "test.db")
    client = TestClient(main.app)
    now = datetime.now(UTC)
    events = [
        _event("evt-0001", "VIS_1", "ENTRY", now),
        _event("evt-0002", "VIS_1", "ZONE_DWELL", now + timedelta(seconds=45), zone="MAKEUP", dwell=45000),
        _event("evt-0003", "STAFF_1", "ZONE_DWELL", now + timedelta(seconds=50), zone="MAKEUP", dwell=50000, staff=True),
    ]
    assert client.post("/events/ingest", json={"events": events}).json()["accepted"] == 3

    response = client.get("/stores/ST1008/metrics")
    assert response.status_code == 200
    payload = response.json()
    assert payload["unique_visitors_today"] == 1
    assert payload["staff_event_count"] == 1
    assert payload["conversion_rate"] == 0.0
    assert payload["avg_dwell_ms_per_zone"]["MAKEUP"] == 45000


def _event(event_id, visitor_id, event_type, timestamp, zone=None, dwell=0, staff=False):
    return {
        "event_id": event_id,
        "store_id": "ST1008",
        "camera_id": "CAM_1",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp.isoformat(),
        "zone_id": zone,
        "dwell_ms": dwell,
        "is_staff": staff,
        "confidence": 0.88,
        "metadata": {"queue_depth": None, "sku_zone": zone, "session_seq": 1},
    }
