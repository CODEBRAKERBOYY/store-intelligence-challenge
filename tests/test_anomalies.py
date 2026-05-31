# PROMPT: Write anomaly tests for queue spikes, conversion drops, and health stale-feed behavior for a retail intelligence API.
# CHANGES MADE: Kept the queue-spike assertion deterministic and used relative timestamps for health so the test remains stable over time.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.analytics import anomalies_response, health_response


def test_queue_spike_anomaly_has_action():
    now = datetime.now(UTC)
    events = [
        {
            "event_id": "1",
            "store_id": "ST1008",
            "camera_id": "CAM_BILLING",
            "visitor_id": "VIS_A",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": now.isoformat(),
            "timestamp_dt": now,
            "zone_id": "BILLING",
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {"queue_depth": 7, "sku_zone": "CASH_COUNTER", "session_seq": 1},
        }
    ]
    response = anomalies_response("ST1008", events, [])
    assert response["anomalies"][0]["type"] == "BILLING_QUEUE_SPIKE"
    assert "billing" in response["anomalies"][0]["suggested_action"].lower()


def test_health_marks_old_feed_stale():
    old = datetime.now(UTC) - timedelta(minutes=11)
    response = health_response([{"store_id": "ST1008", "last_event_timestamp": old.isoformat(), "event_count": 5}])
    assert response["stores"][0]["feed_status"] == "STALE_FEED"
