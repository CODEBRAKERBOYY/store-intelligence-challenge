# PROMPT: Build tests for a CCTV-to-events pipeline that must parse MP4 metadata, create globally unique event IDs, include staff flags, and emit schema-compliant events from POS data.
# CHANGES MADE: Used the real local challenge files when present and skipped gracefully otherwise, so CI can still run without redistributing licensed footage.

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import StoreEvent
from pipeline.detect import generate_events
from pipeline.video_meta import parse_mp4_metadata


VIDEO_DIR = Path("/Users/alok/Documents/CCTV Footage")
POS_CSV = Path("/Users/alok/Documents/Brigade_Bangalore_10_April_26 (1)bc6219c.csv")


@pytest.mark.skipif(not VIDEO_DIR.exists() or not POS_CSV.exists(), reason="local challenge dataset not mounted")
def test_pipeline_generates_schema_compliant_unique_events():
    videos = sorted(VIDEO_DIR.glob("*.mp4"))
    events = generate_events(videos, POS_CSV, "ST1008")
    assert len(events) > 100
    assert len({event["event_id"] for event in events}) == len(events)
    assert any(event["is_staff"] for event in events)
    assert any(event["event_type"] == "BILLING_QUEUE_ABANDON" for event in events)
    for event in events[:20]:
        StoreEvent.model_validate(event)


@pytest.mark.skipif(not VIDEO_DIR.exists(), reason="local challenge dataset not mounted")
def test_video_metadata_parser_reads_dimensions_and_duration():
    meta = parse_mp4_metadata(VIDEO_DIR / "CAM 1.mp4")
    assert meta["width"] == 1920
    assert meta["height"] == 1080
    assert meta["duration_s"] > 100
