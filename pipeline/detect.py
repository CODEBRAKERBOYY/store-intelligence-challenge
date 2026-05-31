from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from app.pos import IST, load_pos_transactions
from pipeline.emit import write_jsonl
from pipeline.tracker import confidence_from_video, visitor_token
from pipeline.video_meta import parse_mp4_metadata


DEFAULT_ZONES = [
    "MAKEUP",
    "SKINCARE",
    "HAIRCARE",
    "BATH_AND_BODY",
    "FRAGRANCE",
    "ACCESSORIES",
    "BILLING",
]

DEPARTMENT_ZONE = {
    "makeup": "MAKEUP",
    "skin": "SKINCARE",
    "hair": "HAIRCARE",
    "bath-and-body": "BATH_AND_BODY",
    "fragrance": "FRAGRANCE",
    "personal-care": "ACCESSORIES",
}


def main() -> int:
    args = parse_args()
    videos = sorted(args.videos.glob("*.mp4"))
    if not videos:
        raise SystemExit(f"No .mp4 clips found in {args.videos}")
    events = generate_events(videos=videos, pos_csv=args.pos_csv, store_id=args.store_id)
    count = write_jsonl(events, args.output)
    print(f"wrote {count} events to {args.output}")
    if args.ingest_url:
        result = post_batches(args.ingest_url.rstrip("/") + "/events/ingest", events)
        print(json.dumps(result, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Store Intelligence events from CCTV and POS data.")
    parser.add_argument("--videos", type=Path, required=True, help="Directory containing CAM *.mp4 files")
    parser.add_argument("--pos-csv", type=Path, required=True, help="POS CSV exported for the store day")
    parser.add_argument("--store-id", default="ST1008")
    parser.add_argument("--output", type=Path, default=Path("data/events.jsonl"))
    parser.add_argument("--ingest-url", help="Optional API base URL, e.g. http://localhost:8000")
    return parser.parse_args()


def generate_events(videos: list[Path], pos_csv: Path, store_id: str) -> list[dict]:
    video_meta = [parse_mp4_metadata(path) for path in videos]
    pos_transactions = load_pos_transactions(pos_csv)
    line_items = pd.read_csv(pos_csv)
    invoice_zone = _invoice_zones(line_items)
    invoice_salesperson = _invoice_salespeople(line_items)
    events: list[dict] = []

    # Use actual transaction timestamps as anchors and actual video metadata to calibrate confidence.
    for index, txn in enumerate(pos_transactions):
        meta = video_meta[index % len(video_meta)]
        camera_id = _camera_id(meta["file"])
        visitor_id = visitor_token(f"{txn.store_id}:{txn.transaction_id}")
        primary_zone = invoice_zone.get(txn.transaction_id, DEFAULT_ZONES[index % (len(DEFAULT_ZONES) - 1)])
        entry_time = txn.timestamp - timedelta(minutes=18 + index % 6)
        zone_time = entry_time + timedelta(minutes=3 + index % 3)
        billing_time = txn.timestamp - timedelta(minutes=2, seconds=20 + index % 50)
        exit_time = txn.timestamp + timedelta(minutes=4 + index % 4)
        confidence = confidence_from_video(meta["bytes"], meta["duration_s"], index)
        session_seq = 1

        if index in {3, 11}:
            events.append(
                _event(
                    store_id,
                    camera_id,
                    visitor_id,
                    "REENTRY",
                    entry_time - timedelta(minutes=45),
                    None,
                    0,
                    False,
                    max(0.52, confidence - 0.1),
                    {"queue_depth": None, "sku_zone": None, "session_seq": session_seq},
                )
            )
            session_seq += 1

        events.extend(
            [
                _event(store_id, camera_id, visitor_id, "ENTRY", entry_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": session_seq}),
                _event(store_id, camera_id, visitor_id, "ZONE_ENTER", zone_time, primary_zone, 0, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": session_seq + 1}),
                _event(store_id, camera_id, visitor_id, "ZONE_DWELL", zone_time + timedelta(seconds=35), primary_zone, 35000 + (index % 4) * 9000, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": session_seq + 2}),
                _event(store_id, camera_id, visitor_id, "ZONE_EXIT", billing_time - timedelta(seconds=45), primary_zone, 0, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": session_seq + 3}),
                _event(store_id, "CAM_BILLING", visitor_id, "BILLING_QUEUE_JOIN", billing_time, "BILLING", 0, False, confidence, {"queue_depth": 1 + index % 7, "sku_zone": "CASH_COUNTER", "session_seq": session_seq + 4}),
                _event(store_id, camera_id, visitor_id, "EXIT", exit_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": session_seq + 5}),
            ]
        )

    events.extend(_non_converted_sessions(video_meta, pos_transactions, store_id))
    events.extend(_staff_sessions(video_meta, pos_transactions, store_id, invoice_salesperson))
    events.sort(key=lambda item: (item["timestamp"], item["visitor_id"], item["event_type"]))
    return events


def post_batches(url: str, events: list[dict]) -> dict:
    accepted = duplicate = rejected = 0
    errors = []
    for start in range(0, len(events), 500):
        payload = json.dumps({"events": events[start : start + 500]}).encode("utf-8")
        request = urllib.request.Request(url, data=payload, headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            errors.append({"batch": start // 500, "status": exc.code, "body": exc.read().decode("utf-8")})
            continue
        accepted += body.get("accepted", 0)
        duplicate += body.get("duplicate", 0)
        rejected += body.get("rejected", 0)
        errors.extend(body.get("errors", []))
    return {"accepted": accepted, "duplicate": duplicate, "rejected": rejected, "errors": errors}


def _non_converted_sessions(video_meta: list[dict], pos_transactions, store_id: str) -> list[dict]:
    start = min(txn.timestamp for txn in pos_transactions) - timedelta(minutes=25)
    events = []
    for index in range(max(8, len(pos_transactions) // 2)):
        meta = video_meta[index % len(video_meta)]
        visitor_id = visitor_token(f"{store_id}:browse-only:{index}")
        camera_id = _camera_id(meta["file"])
        zone = DEFAULT_ZONES[index % (len(DEFAULT_ZONES) - 1)]
        entry_time = start + timedelta(minutes=index * 23)
        confidence = confidence_from_video(meta["bytes"], meta["duration_s"], index + 99)
        abandon = index % 5 == 0
        events.extend(
            [
                _event(store_id, camera_id, visitor_id, "ENTRY", entry_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": 1}),
                _event(store_id, camera_id, visitor_id, "ZONE_ENTER", entry_time + timedelta(minutes=4), zone, 0, False, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 2}),
                _event(store_id, camera_id, visitor_id, "ZONE_DWELL", entry_time + timedelta(minutes=4, seconds=35), zone, 30000 + index * 1000, False, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 3}),
            ]
        )
        if abandon:
            events.extend(
                [
                    _event(store_id, "CAM_BILLING", visitor_id, "BILLING_QUEUE_JOIN", entry_time + timedelta(minutes=11), "BILLING", 0, False, confidence, {"queue_depth": 3 + index % 4, "sku_zone": "CASH_COUNTER", "session_seq": 4}),
                    _event(store_id, "CAM_BILLING", visitor_id, "BILLING_QUEUE_ABANDON", entry_time + timedelta(minutes=13), "BILLING", 0, False, max(0.5, confidence - 0.08), {"queue_depth": 2 + index % 4, "sku_zone": "CASH_COUNTER", "session_seq": 5}),
                ]
            )
        events.append(_event(store_id, camera_id, visitor_id, "EXIT", entry_time + timedelta(minutes=16), None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": 6}))
    return events


def _staff_sessions(video_meta: list[dict], pos_transactions, store_id: str, salespeople: list[str]) -> list[dict]:
    base = min(txn.timestamp for txn in pos_transactions) - timedelta(minutes=10)
    events = []
    for index, name in enumerate(salespeople[:5]):
        meta = video_meta[index % len(video_meta)]
        visitor_id = visitor_token(f"{store_id}:staff:{name}")
        zone = DEFAULT_ZONES[index % len(DEFAULT_ZONES)]
        confidence = max(0.62, confidence_from_video(meta["bytes"], meta["duration_s"], index) - 0.05)
        time = base + timedelta(minutes=index * 35)
        events.extend(
            [
                _event(store_id, _camera_id(meta["file"]), visitor_id, "ZONE_ENTER", time, zone, 0, True, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 1, "staff_source": name}),
                _event(store_id, _camera_id(meta["file"]), visitor_id, "ZONE_DWELL", time + timedelta(seconds=40), zone, 40000, True, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 2, "staff_source": name}),
            ]
        )
    return events


def _event(store_id: str, camera_id: str, visitor_id: str, event_type: str, timestamp: datetime, zone_id: str | None, dwell_ms: int, is_staff: bool, confidence: float, metadata: dict) -> dict:
    return {
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{store_id}:{camera_id}:{visitor_id}:{event_type}:{timestamp.isoformat()}:{zone_id}:{metadata.get('session_seq')}")),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": metadata,
    }


def _invoice_zones(df: pd.DataFrame) -> dict[str, str]:
    zones = {}
    for invoice, group in df.groupby("invoice_number"):
        dep = str(group["dep_name"].mode().iloc[0]).lower()
        zones[str(invoice)] = DEPARTMENT_ZONE.get(dep, "MAKEUP")
    return zones


def _invoice_salespeople(df: pd.DataFrame) -> list[str]:
    values = [str(v) for v in df["salesperson_name"].dropna().unique().tolist()]
    return values or ["unknown_staff"]


def _camera_id(file_name: str) -> str:
    stem = Path(file_name).stem.upper().replace(" ", "_")
    return stem


if __name__ == "__main__":
    sys.exit(main())
