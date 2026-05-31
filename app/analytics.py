from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any


BILLING_ZONES = {"BILLING", "BILLING_COUNTER", "CASH_COUNTER", "PMU"}


def non_staff(events: list[dict]) -> list[dict]:
    return [event for event in events if not event["is_staff"]]


def sessions(events: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        grouped[event["visitor_id"]].append(event)
    return {key: sorted(value, key=lambda item: item["timestamp"]) for key, value in grouped.items()}


def converted_sessions(events: list[dict], pos: list[dict]) -> set[str]:
    result: set[str] = set()
    by_store_session = sessions(non_staff(events))
    billing_events = []
    for visitor_id, evts in by_store_session.items():
        for event in evts:
            if _is_billing_event(event):
                billing_events.append((visitor_id, event["timestamp_dt"]))

    for txn in pos:
        txn_time = datetime.fromisoformat(txn["timestamp"])
        for visitor_id, event_time in billing_events:
            if timedelta(0) <= txn_time - event_time <= timedelta(minutes=5):
                result.add(visitor_id)
                break
    return result


def metrics_response(store_id: str, events: list[dict], pos: list[dict]) -> dict[str, Any]:
    customer_events = non_staff(events)
    session_map = sessions(customer_events)
    unique_visitors = len(_entry_sessions(session_map))
    converted = converted_sessions(events, pos)
    dwell_by_zone: dict[str, list[int]] = defaultdict(list)
    for event in customer_events:
        if event["event_type"] == "ZONE_DWELL" and event["zone_id"]:
            dwell_by_zone[event["zone_id"]].append(event["dwell_ms"])

    queue_events = [
        event
        for event in customer_events
        if event["event_type"] == "BILLING_QUEUE_JOIN"
    ]
    abandons = [
        event
        for event in customer_events
        if event["event_type"] == "BILLING_QUEUE_ABANDON"
    ]
    latest_queue_depth = 0
    if queue_events:
        latest = max(queue_events, key=lambda event: event["timestamp"])
        latest_queue_depth = int(latest["metadata"].get("queue_depth") or 0)

    return {
        "store_id": store_id,
        "unique_visitors_today": unique_visitors,
        "conversion_rate": _rate(len(converted), unique_visitors),
        "converted_visitors": len(converted),
        "avg_dwell_ms_per_zone": {
            zone: round(mean(values), 2) for zone, values in sorted(dwell_by_zone.items())
        },
        "queue_depth": latest_queue_depth,
        "abandonment_rate": _rate(len(abandons), len(queue_events)),
        "event_count": len(events),
        "staff_event_count": len(events) - len(customer_events),
        "transaction_count": len(pos),
        "revenue_inr": round(sum(float(txn["basket_value_inr"]) for txn in pos), 2),
    }


def funnel_response(store_id: str, events: list[dict], pos: list[dict]) -> dict[str, Any]:
    session_map = sessions(non_staff(events))
    entry = _entry_sessions(session_map)
    zone_visit = {
        visitor_id
        for visitor_id in entry
        if any(evt["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"} for evt in session_map[visitor_id])
    }
    billing = {
        visitor_id
        for visitor_id in zone_visit
        if any(_is_billing_event(evt) for evt in session_map[visitor_id])
    }
    purchase = converted_sessions(events, pos)
    stages = [
        ("entry", entry),
        ("zone_visit", zone_visit),
        ("billing_queue", billing),
        ("purchase", purchase & entry),
    ]
    response_stages = []
    previous_count = None
    for name, visitor_ids in stages:
        count = len(visitor_ids)
        dropoff = 0.0 if previous_count in (None, 0) else round((previous_count - count) / previous_count, 4)
        response_stages.append({"stage": name, "count": count, "dropoff_pct": dropoff})
        previous_count = count
    return {"store_id": store_id, "unit": "visitor_session", "stages": response_stages}


def heatmap_response(store_id: str, events: list[dict]) -> dict[str, Any]:
    customer_events = non_staff(events)
    session_count = len(_entry_sessions(sessions(customer_events)))
    visits: dict[str, set[str]] = defaultdict(set)
    dwell: dict[str, list[int]] = defaultdict(list)
    for event in customer_events:
        zone = event["zone_id"]
        if not zone:
            continue
        if event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL", "BILLING_QUEUE_JOIN"}:
            visits[zone].add(event["visitor_id"])
        if event["event_type"] == "ZONE_DWELL":
            dwell[zone].append(event["dwell_ms"])

    max_visits = max((len(value) for value in visits.values()), default=1)
    zones = []
    for zone in sorted(set(visits) | set(dwell)):
        visit_count = len(visits.get(zone, set()))
        zones.append(
            {
                "zone_id": zone,
                "visit_count": visit_count,
                "avg_dwell_ms": round(mean(dwell[zone]), 2) if dwell.get(zone) else 0,
                "normalized_frequency": round(visit_count / max_visits * 100, 2),
                "data_confidence": "LOW" if session_count < 20 else "NORMAL",
            }
        )
    return {"store_id": store_id, "session_count": session_count, "zones": zones}


def anomalies_response(store_id: str, events: list[dict], pos: list[dict]) -> dict[str, Any]:
    customer_events = non_staff(events)
    anomalies = []
    queue_depth = metrics_response(store_id, events, pos)["queue_depth"]
    if queue_depth >= 5:
        anomalies.append(
            {
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL" if queue_depth >= 8 else "WARN",
                "message": f"Billing queue depth is {queue_depth}.",
                "suggested_action": "Open another billing counter or move a staff member to checkout.",
            }
        )

    metric = metrics_response(store_id, events, pos)
    if metric["unique_visitors_today"] >= 5 and metric["conversion_rate"] < 0.15:
        anomalies.append(
            {
                "type": "CONVERSION_DROP",
                "severity": "WARN",
                "message": "Conversion is below the configured 15% operating threshold.",
                "suggested_action": "Review staff coverage and billing-zone dwell in the current window.",
            }
        )

    latest_time = max((event["timestamp_dt"] for event in customer_events), default=None)
    if latest_time:
        zones = {
            event["zone_id"]
            for event in customer_events
            if event["zone_id"] and event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"}
        }
        for zone in sorted(zones):
            zone_latest = max(
                event["timestamp_dt"]
                for event in customer_events
                if event["zone_id"] == zone and event["event_type"] in {"ZONE_ENTER", "ZONE_DWELL"}
            )
            if latest_time - zone_latest > timedelta(minutes=30):
                anomalies.append(
                    {
                        "type": "DEAD_ZONE",
                        "severity": "INFO",
                        "message": f"No recent visits in {zone}.",
                        "suggested_action": "Check merchandising visibility or camera coverage for this zone.",
                    }
                )
    return {"store_id": store_id, "anomalies": anomalies}


def health_response(rows: list[dict]) -> dict[str, Any]:
    now = datetime.now(UTC)
    stores = []
    for row in rows:
        last_event = datetime.fromisoformat(row["last_event_timestamp"])
        lag = now - last_event
        stores.append(
            {
                "store_id": row["store_id"],
                "last_event_timestamp": row["last_event_timestamp"],
                "event_count": row["event_count"],
                "feed_status": "STALE_FEED" if lag > timedelta(minutes=10) else "OK",
                "lag_seconds": max(0, int(lag.total_seconds())),
            }
        )
    return {"status": "OK", "stores": stores}


def _entry_sessions(session_map: dict[str, list[dict]]) -> set[str]:
    return {
        visitor_id
        for visitor_id, evts in session_map.items()
        if any(evt["event_type"] in {"ENTRY", "REENTRY"} for evt in evts)
    }


def _is_billing_event(event: dict) -> bool:
    return event["event_type"] == "BILLING_QUEUE_JOIN" or (event["zone_id"] or "").upper() in BILLING_ZONES


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
