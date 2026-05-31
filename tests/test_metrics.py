# PROMPT: Create tests for funnel, heatmap, and POS conversion logic for a CCTV-derived offline store analytics system. Include re-entry handling and billing-window conversion matching.
# CHANGES MADE: Tightened the generated cases to the challenge's five-minute POS correlation rule and direct analytics functions, avoiding brittle HTTP-only assertions.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.analytics import funnel_response, heatmap_response, metrics_response


def test_funnel_uses_sessions_and_reentry_does_not_double_count():
    base = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    events = [
        _event("1", "VIS_A", "ENTRY", base),
        _event("2", "VIS_A", "REENTRY", base + timedelta(minutes=10)),
        _event("3", "VIS_A", "ZONE_ENTER", base + timedelta(minutes=11), "SKINCARE"),
        _event("4", "VIS_A", "BILLING_QUEUE_JOIN", base + timedelta(minutes=20), "BILLING"),
        _event("5", "VIS_B", "ENTRY", base + timedelta(minutes=1)),
        _event("6", "VIS_B", "ZONE_ENTER", base + timedelta(minutes=3), "MAKEUP"),
    ]
    pos = [{"transaction_id": "T1", "store_id": "ST1008", "timestamp": (base + timedelta(minutes=22)).isoformat(), "basket_value_inr": 1000}]

    funnel = funnel_response("ST1008", events, pos)
    assert funnel["stages"][0]["count"] == 2
    assert funnel["stages"][1]["count"] == 2
    assert funnel["stages"][2]["count"] == 1
    assert funnel["stages"][3]["count"] == 1


def test_heatmap_low_confidence_under_twenty_sessions():
    base = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    events = [_event("1", "VIS_A", "ENTRY", base), _event("2", "VIS_A", "ZONE_DWELL", base, "MAKEUP", 30000)]
    heatmap = heatmap_response("ST1008", events)
    assert heatmap["zones"][0]["normalized_frequency"] == 100
    assert heatmap["zones"][0]["data_confidence"] == "LOW"


def test_metrics_correlates_billing_to_pos_within_five_minutes():
    base = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    events = [
        _event("1", "VIS_A", "ENTRY", base),
        _event("2", "VIS_A", "BILLING_QUEUE_JOIN", base + timedelta(minutes=4), "BILLING"),
    ]
    pos = [{"transaction_id": "T1", "store_id": "ST1008", "timestamp": (base + timedelta(minutes=8, seconds=59)).isoformat(), "basket_value_inr": 500}]
    metrics = metrics_response("ST1008", events, pos)
    assert metrics["converted_visitors"] == 1
    assert metrics["conversion_rate"] == 1.0


def _event(event_id, visitor_id, event_type, timestamp, zone=None, dwell=0, staff=False):
    return {
        "event_id": event_id,
        "store_id": "ST1008",
        "camera_id": "CAM_1",
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp.isoformat(),
        "timestamp_dt": timestamp,
        "zone_id": zone,
        "dwell_ms": dwell,
        "is_staff": staff,
        "confidence": 0.9,
        "metadata": {"queue_depth": 2 if event_type == "BILLING_QUEUE_JOIN" else None, "sku_zone": zone, "session_seq": 1},
    }
