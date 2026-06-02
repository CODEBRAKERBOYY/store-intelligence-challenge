# PROMPT: Build tests for a CCTV-to-events pipeline that must parse MP4 metadata, create globally unique event IDs, include staff flags, and emit schema-compliant events from POS data.
# CHANGES MADE: Replaced machine-specific challenge-file paths with synthetic MP4 and POS fixtures, so clean reviewer machines exercise the pipeline without licensed footage.

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from app.models import StoreEvent
from pipeline.detect import (
    _camera_id,
    _cluster_activity_windows,
    _cv_confidence,
    _cv_staff_sessions,
    _empty_signal,
    _even_offsets,
    generate_events,
    post_batches,
)
from pipeline.emit import read_jsonl, write_jsonl
from pipeline.video_meta import parse_mp4_metadata


def test_fallback_pipeline_generates_schema_compliant_unique_events(tmp_path: Path):
    videos = [_write_minimal_mp4(tmp_path / f"CAM {index}.mp4") for index in range(1, 4)]
    pos_csv = _write_pos_csv(tmp_path / "pos_transactions.csv")

    events = generate_events(videos, pos_csv, "ST1008", mode="fallback")

    assert len(events) >= 25
    assert len({event["event_id"] for event in events}) == len(events)
    assert any(event["is_staff"] for event in events)
    assert any(event["event_type"] == "BILLING_QUEUE_ABANDON" for event in events)
    assert any(event["event_type"] == "REENTRY" for event in events)
    assert all(isinstance(event["dwell_ms"], int) for event in events)
    for event in events:
        StoreEvent.model_validate(event)


def test_video_metadata_parser_reads_synthetic_dimensions_and_duration(tmp_path: Path):
    video = _write_minimal_mp4(tmp_path / "CAM_ENTRY_01.mp4", width=1280, height=720, duration_s=42)

    meta = parse_mp4_metadata(video)

    assert meta["file"] == "CAM_ENTRY_01.mp4"
    assert meta["width"] == 1280
    assert meta["height"] == 720
    assert meta["duration_s"] == 42
    assert meta["timescale"] == 1000


def test_fallback_pipeline_varies_with_video_metadata(tmp_path: Path):
    pos_csv = _write_pos_csv(tmp_path / "pos_transactions.csv")
    short_videos = [_write_minimal_mp4(tmp_path / "SHORT_CAM 1.mp4", duration_s=45)]
    long_videos = [
        _write_minimal_mp4(tmp_path / f"LONG_CAM {index}.mp4", duration_s=180, width=1920, height=1080)
        for index in range(1, 4)
    ]

    short_events = generate_events(short_videos, pos_csv, "ST1008", mode="fallback")
    long_events = generate_events(long_videos, pos_csv, "ST1008", mode="fallback")

    assert len(long_events) > len(short_events)
    assert {event["visitor_id"] for event in short_events} != {event["visitor_id"] for event in long_events}
    assert all(event["metadata"].get("fallback_source") == "mp4_metadata" for event in short_events)


def test_write_jsonl_creates_parent_and_serializes_events(tmp_path: Path):
    output = tmp_path / "nested" / "events.jsonl"
    events = [{"event_id": "evt_1", "store_id": "ST1008"}, {"event_id": "evt_2", "store_id": "ST1008"}]

    count = write_jsonl(events, output)

    assert count == 2
    assert output.read_text().count("\n") == 2
    assert read_jsonl(output) == events


def test_cv_helper_functions_are_deterministic(tmp_path: Path):
    video = tmp_path / "CAM Billing.mp4"
    video.write_bytes(b"not a real mp4")
    signal = {
        "motion_score": 9.0,
        "avg_blob_count": 4,
        "avg_person_count": 1.5,
    }
    meta = {"bytes": 8_000_000, "duration_s": 120}

    assert _camera_id("CAM Billing.mp4") == "CAM_BILLING"
    assert _cluster_activity_windows([1, 2, 11, 12, 30], gap_s=5) == [1, 11, 30]
    assert _even_offsets(30, 3, {7}) == [8, 14, 21]
    assert _cv_confidence(signal, meta, 0) > 0.8
    assert _empty_signal(video, meta)["session_estimate"] == 0
    assert _cluster_activity_windows([], gap_s=5) == []
    assert _even_offsets(10, 0, set()) == []


def test_cv_staff_sessions_and_post_batches(monkeypatch):
    class Txn:
        timestamp = __import__("datetime").datetime(2026, 4, 10, 8, 0, tzinfo=__import__("datetime").UTC)

    video_meta = [{"file": "CAM 1.mp4", "bytes": 8_000_000, "duration_s": 120}]
    signals = [{"fingerprint": "abc", "dominant_region": 1, "motion_score": 4.0, "avg_blob_count": 2, "avg_person_count": 1.0}]

    staff_events = _cv_staff_sessions(video_meta, signals, [Txn()], "ST1008", ["Asha"])

    assert len(staff_events) == 2
    assert all(event["is_staff"] for event in staff_events)
    assert staff_events[0]["metadata"]["cv_mode"] is True

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"accepted": 1, "duplicate": 2, "rejected": 3, "errors": [{"index": 0}]}'

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: FakeResponse())
    result = post_batches("http://api/events/ingest", [{"event_id": "evt_1"}])

    assert result == {"accepted": 1, "duplicate": 2, "rejected": 3, "errors": [{"index": 0}]}


@pytest.mark.skipif(not os.getenv("CHALLENGE_DATA_DIR"), reason="set CHALLENGE_DATA_DIR to run against licensed local footage")
def test_pipeline_generates_events_from_real_challenge_files():
    data_dir = Path(os.environ["CHALLENGE_DATA_DIR"])
    videos = sorted(data_dir.glob("**/*.mp4"))
    csv_files = sorted(data_dir.glob("**/*.csv"))

    assert videos, "CHALLENGE_DATA_DIR must contain .mp4 files"
    assert csv_files, "CHALLENGE_DATA_DIR must contain a POS .csv file"
    events = generate_events(videos, csv_files[0], "ST1008")

    assert len(events) > 100
    assert len({event["event_id"] for event in events}) == len(events)
    assert any(event["metadata"].model_dump() if hasattr(event["metadata"], "model_dump") else event["metadata"] for event in events)
    for event in events[:20]:
        StoreEvent.model_validate(event)


def _write_pos_csv(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "invoice_number,order_date,order_time,store_id,total_amount,dep_name,salesperson_name",
                "INV001,10-04-2026,12:15:05,ST1008,499.00,makeup,Asha",
                "INV002,10-04-2026,12:35:05,ST1008,799.00,skin,Ravi",
                "INV003,10-04-2026,12:55:05,ST1008,299.00,hair,Asha",
                "INV004,10-04-2026,13:15:05,ST1008,999.00,fragrance,Meera",
            ]
        )
        + "\n"
    )
    return path


def _write_minimal_mp4(path: Path, width: int = 1920, height: int = 1080, duration_s: int = 120) -> Path:
    mvhd = bytearray(24)
    struct.pack_into(">I", mvhd, 12, 1000)
    struct.pack_into(">I", mvhd, 16, duration_s * 1000)

    hdlr = bytearray(32)
    hdlr[8:12] = b"vide"

    tkhd = bytearray(84)
    struct.pack_into(">I", tkhd, 76, width << 16)
    struct.pack_into(">I", tkhd, 80, height << 16)

    moov = _box(b"moov", _box(b"mvhd", bytes(mvhd)) + _box(b"trak", _box(b"tkhd", bytes(tkhd)) + _box(b"mdia", _box(b"hdlr", bytes(hdlr)))))
    path.write_bytes(_box(b"ftyp", b"isom\x00\x00\x02\x00isom") + moov)
    return path


def _box(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, name) + payload
