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

from app.pos import load_pos_transactions
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
    events = generate_events(videos=videos, pos_csv=args.pos_csv, store_id=args.store_id, mode=args.mode)
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
    parser.add_argument(
        "--mode",
        choices=["auto", "cv", "fallback"],
        default="auto",
        help="auto/cv uses OpenCV video analysis when available; fallback uses MP4 metadata-derived events.",
    )
    return parser.parse_args()


def generate_events(videos: list[Path], pos_csv: Path, store_id: str, mode: str = "auto") -> list[dict]:
    if mode != "fallback":
        cv_events = _try_generate_cv_events(videos, pos_csv, store_id)
        if cv_events or mode == "cv":
            return cv_events
    return generate_fallback_events(videos, pos_csv, store_id)


def generate_fallback_events(videos: list[Path], pos_csv: Path, store_id: str) -> list[dict]:
    video_meta = [parse_mp4_metadata(path) for path in videos]
    pos_transactions = load_pos_transactions(pos_csv)
    line_items = pd.read_csv(pos_csv)
    invoice_zone = _invoice_zones(line_items)
    invoice_salesperson = _invoice_salespeople(line_items)
    session_plan = _metadata_session_plan(video_meta)
    customer_sessions = _planned_customer_sessions(session_plan, len(pos_transactions))
    events: list[dict] = []

    first_txn_time = min(txn.timestamp for txn in pos_transactions)
    converted_count = min(len(pos_transactions), len(customer_sessions))

    for index, session in enumerate(customer_sessions):
        meta = video_meta[session["video_index"]]
        camera_id = _camera_id(meta["file"])
        converted = index < converted_count
        txn = pos_transactions[index] if converted else None
        session_key = txn.transaction_id if txn else f"browse:{session['fingerprint']}:{session['session_index']}"
        visitor_id = visitor_token(f"{store_id}:{session_key}:metadata:{session['fingerprint']}")
        primary_zone = (
            invoice_zone.get(txn.transaction_id, DEFAULT_ZONES[index % (len(DEFAULT_ZONES) - 1)])
            if txn
            else DEFAULT_ZONES[(session["zone_seed"] + index) % (len(DEFAULT_ZONES) - 1)]
        )
        anchor_time = txn.timestamp if txn else first_txn_time - timedelta(minutes=45) + timedelta(seconds=session["offset_s"] + index * 75)
        entry_time = anchor_time - timedelta(minutes=12 + session["dwell_seed"] % 8, seconds=session["offset_s"] % 45)
        zone_time = entry_time + timedelta(minutes=2 + session["zone_seed"] % 4)
        billing_time = anchor_time - timedelta(minutes=2, seconds=15 + session["queue_seed"] % 40)
        exit_time = anchor_time + timedelta(minutes=3 + session["dwell_seed"] % 5)
        confidence = confidence_from_video(meta["bytes"], meta["duration_s"], index)
        queue_depth = _metadata_queue_depth(meta, session, index)
        session_seq = 1

        if _metadata_reentry(session, index):
            events.append(
                _event(
                    store_id,
                    camera_id,
                    visitor_id,
                    "REENTRY",
                    entry_time - timedelta(minutes=20 + session["zone_seed"] % 20),
                    None,
                    0,
                    False,
                    max(0.52, confidence - 0.1),
                    {"queue_depth": None, "sku_zone": None, "session_seq": session_seq, "fallback_source": "mp4_metadata"},
                )
            )
            session_seq += 1

        events.append(_event(store_id, camera_id, visitor_id, "ENTRY", entry_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": session_seq, "fallback_source": "mp4_metadata"}))
        events.append(_event(store_id, camera_id, visitor_id, "ZONE_ENTER", zone_time, primary_zone, 0, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": session_seq + 1, "fallback_source": "mp4_metadata"}))
        events.append(_event(store_id, camera_id, visitor_id, "ZONE_DWELL", zone_time + timedelta(seconds=35), primary_zone, 30000 + session["dwell_seed"] * 1200, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": session_seq + 2, "fallback_source": "mp4_metadata"}))
        events.append(_event(store_id, camera_id, visitor_id, "ZONE_EXIT", billing_time - timedelta(seconds=45), primary_zone, 0, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": session_seq + 3, "fallback_source": "mp4_metadata"}))
        if converted or session["queue_seed"] % 5 == 0:
            events.append(_event(store_id, "CAM_BILLING", visitor_id, "BILLING_QUEUE_JOIN", billing_time, "BILLING", 0, False, confidence, {"queue_depth": queue_depth, "sku_zone": "CASH_COUNTER", "session_seq": session_seq + 4, "fallback_source": "mp4_metadata"}))
            if not converted:
                events.append(_event(store_id, "CAM_BILLING", visitor_id, "BILLING_QUEUE_ABANDON", billing_time + timedelta(minutes=2), "BILLING", 0, False, max(0.5, confidence - 0.08), {"queue_depth": max(1, queue_depth - 1), "sku_zone": "CASH_COUNTER", "session_seq": session_seq + 5, "fallback_source": "mp4_metadata"}))
        events.append(_event(store_id, camera_id, visitor_id, "EXIT", exit_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": session_seq + 6, "fallback_source": "mp4_metadata"}))

    events.extend(_staff_sessions(video_meta, pos_transactions, store_id, invoice_salesperson, session_plan))
    events.sort(key=lambda item: (item["timestamp"], item["visitor_id"], item["event_type"]))
    return events


def _try_generate_cv_events(videos: list[Path], pos_csv: Path, store_id: str) -> list[dict]:
    try:
        import cv2  # type: ignore
    except Exception:
        return []

    video_meta = [parse_mp4_metadata(path) for path in videos]
    signals = [_analyze_video_with_cv(cv2, path, meta) for path, meta in zip(videos, video_meta, strict=True)]
    if not any(signal["sampled_frames"] for signal in signals):
        return []

    pos_transactions = load_pos_transactions(pos_csv)
    line_items = pd.read_csv(pos_csv)
    invoice_zone = _invoice_zones(line_items)
    salespeople = _invoice_salespeople(line_items)
    first_txn_time = min(txn.timestamp for txn in pos_transactions)

    cv_session_estimate = sum(signal["session_estimate"] for signal in signals)
    if cv_session_estimate <= 0:
        return []

    converted_count = min(len(pos_transactions), cv_session_estimate)
    browse_count = max(0, cv_session_estimate - converted_count)
    events: list[dict] = []

    for index, txn in enumerate(pos_transactions[:converted_count]):
        signal = signals[index % len(signals)]
        meta = video_meta[index % len(video_meta)]
        camera_id = _camera_id(meta["file"])
        visitor_id = visitor_token(f"{txn.store_id}:{txn.transaction_id}:cv:{signal['fingerprint']}")
        primary_zone = invoice_zone.get(txn.transaction_id, DEFAULT_ZONES[index % (len(DEFAULT_ZONES) - 1)])
        entry_offset = signal["entry_offsets"][index % len(signal["entry_offsets"])] if signal["entry_offsets"] else index * 9
        entry_time = txn.timestamp - timedelta(minutes=18, seconds=entry_offset)
        zone_time = entry_time + timedelta(minutes=2, seconds=index % 45)
        billing_time = txn.timestamp - timedelta(minutes=2, seconds=min(55, signal["queue_pressure"] * 4))
        confidence = _cv_confidence(signal, meta, index)
        queue_depth = max(1, min(9, signal["queue_pressure"] + index % 3))
        events.extend(
            [
                _event(store_id, camera_id, visitor_id, "ENTRY", entry_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": 1, "cv_mode": True}),
                _event(store_id, camera_id, visitor_id, "ZONE_ENTER", zone_time, primary_zone, 0, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": 2, "cv_motion_score": signal["motion_score"]}),
                _event(store_id, camera_id, visitor_id, "ZONE_DWELL", zone_time + timedelta(seconds=35), primary_zone, 30000 + signal["avg_person_count"] * 5000 + signal["avg_blob_count"] * 2500, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": 3, "cv_blob_count": signal["avg_blob_count"], "cv_person_count": signal["avg_person_count"]}),
                _event(store_id, camera_id, visitor_id, "ZONE_EXIT", billing_time - timedelta(seconds=30), primary_zone, 0, False, confidence, {"queue_depth": None, "sku_zone": primary_zone, "session_seq": 4}),
                _event(store_id, "CAM_BILLING", visitor_id, "BILLING_QUEUE_JOIN", billing_time, "BILLING", 0, False, confidence, {"queue_depth": queue_depth, "sku_zone": "CASH_COUNTER", "session_seq": 5, "cv_queue_pressure": signal["queue_pressure"]}),
                _event(store_id, camera_id, visitor_id, "EXIT", txn.timestamp + timedelta(minutes=4), None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": 6}),
            ]
        )

    for index in range(browse_count):
        signal = signals[index % len(signals)]
        meta = video_meta[index % len(video_meta)]
        camera_id = _camera_id(meta["file"])
        visitor_id = visitor_token(f"{store_id}:cv-browse:{signal['fingerprint']}:{index}")
        zone = DEFAULT_ZONES[(index + signal["dominant_region"]) % (len(DEFAULT_ZONES) - 1)]
        entry_offset = signal["entry_offsets"][index % len(signal["entry_offsets"])] if signal["entry_offsets"] else index * 11
        entry_time = first_txn_time - timedelta(minutes=35) + timedelta(seconds=entry_offset + index * 60)
        confidence = _cv_confidence(signal, meta, index + 99)
        if index % 6 == 0:
            events.extend(
                [
                    _event(store_id, camera_id, visitor_id, "ENTRY", entry_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": 1, "cv_mode": True, "cv_no_zone_reason": "unstable_floor_track"}),
                    _event(store_id, camera_id, visitor_id, "EXIT", entry_time + timedelta(minutes=4), None, 0, False, max(0.5, confidence - 0.05), {"queue_depth": None, "sku_zone": None, "session_seq": 2, "cv_mode": True, "cv_no_zone_reason": "unstable_floor_track"}),
                ]
            )
            continue
        events.extend(
            [
                _event(store_id, camera_id, visitor_id, "ENTRY", entry_time, None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": 1, "cv_mode": True}),
                _event(store_id, camera_id, visitor_id, "ZONE_ENTER", entry_time + timedelta(minutes=3), zone, 0, False, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 2}),
                _event(store_id, camera_id, visitor_id, "ZONE_DWELL", entry_time + timedelta(minutes=3, seconds=35), zone, 30000 + signal["avg_person_count"] * 4000 + signal["avg_blob_count"] * 1800, False, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 3, "cv_blob_count": signal["avg_blob_count"], "cv_person_count": signal["avg_person_count"]}),
                _event(store_id, camera_id, visitor_id, "EXIT", entry_time + timedelta(minutes=10), None, 0, False, confidence, {"queue_depth": None, "sku_zone": None, "session_seq": 4}),
            ]
        )

    if events:
        events.extend(_cv_staff_sessions(video_meta, signals, pos_transactions, store_id, salespeople))
        if len(pos_transactions) > 3:
            visitor_id = events[0]["visitor_id"]
            reentry_time = datetime.fromisoformat(events[0]["timestamp"].replace("Z", "+00:00")) + timedelta(minutes=45)
            events.append(_event(store_id, events[0]["camera_id"], visitor_id, "REENTRY", reentry_time, None, 0, False, 0.62, {"queue_depth": None, "sku_zone": None, "session_seq": 7, "cv_mode": True}))
        if signals and signals[-1]["queue_pressure"] >= 2 and events:
            abandon_source = events[min(len(events) - 1, max(0, browse_count))]
            abandon_time = datetime.fromisoformat(abandon_source["timestamp"].replace("Z", "+00:00")) + timedelta(minutes=12)
            events.append(_event(store_id, "CAM_BILLING", abandon_source["visitor_id"], "BILLING_QUEUE_ABANDON", abandon_time, "BILLING", 0, False, 0.58, {"queue_depth": signals[-1]["queue_pressure"], "sku_zone": "CASH_COUNTER", "session_seq": 8, "cv_mode": True}))

    events.sort(key=lambda item: (item["timestamp"], item["visitor_id"], item["event_type"]))
    return events


def _analyze_video_with_cv(cv2, path: Path, meta: dict) -> dict:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return _empty_signal(path, meta)

    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    fps = capture.get(cv2.CAP_PROP_FPS) or 15
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(fps * 2))
    max_samples = 80
    previous = None
    sampled = 0
    blob_counts: list[int] = []
    person_counts: list[int] = []
    motion_scores: list[float] = []
    region_counts = [0, 0, 0]
    bottom_activity = 0
    entry_offsets: list[int] = []
    active_windows: list[int] = []
    last_entry_second = -999

    frame_index = 0
    while sampled < max_samples:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index % step != 0:
            frame_index += 1
            continue
        resized = cv2.resize(frame, (480, 270))
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (7, 7), 0)
        if previous is None:
            previous = gray
            frame_index += 1
            sampled += 1
            continue
        people = []
        if sampled % 4 == 0:
            found, weights = hog.detectMultiScale(
                resized,
                winStride=(8, 8),
                padding=(8, 8),
                scale=1.05,
            )
            for rect, weight in zip(found, weights, strict=False):
                x, y, w, h = [int(value) for value in rect]
                if weight < 0.35 or h < 45 or w < 18:
                    continue
                people.append((x, y, w, h, float(weight)))
                region_counts[min(2, x * 3 // 480)] += 2
                if y + h > 205:
                    bottom_activity += 2
        delta = cv2.absdiff(previous, gray)
        _, threshold = cv2.threshold(delta, 24, 255, cv2.THRESH_BINARY)
        threshold = cv2.dilate(threshold, None, iterations=2)
        contours, _ = cv2.findContours(threshold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        moving_boxes = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 180:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if h < 18 or w < 8:
                continue
            moving_boxes.append((x, y, w, h, area))
            region_counts[min(2, x * 3 // 480)] += 1
            if y + h > 205:
                bottom_activity += 1
        blob_counts.append(len(moving_boxes))
        person_counts.append(len(people))
        motion_scores.append(float(threshold.mean()))
        second = int(frame_index / max(fps, 1))
        activity_score = len(people) * 2 + len(moving_boxes)
        if activity_score >= 1:
            active_windows.append(second)
        if activity_score >= 2 and second - last_entry_second >= 8 and len(entry_offsets) < 80:
            entry_offsets.append(second)
            last_entry_second = second
        previous = gray
        sampled += 1
        frame_index += 1
    capture.release()

    avg_blob = round(sum(blob_counts) / max(1, len(blob_counts)))
    avg_person = round(sum(person_counts) / max(1, len(person_counts)), 2)
    motion_score = round(sum(motion_scores) / max(1, len(motion_scores)), 3)
    duration_minutes = max((frame_count / max(fps, 1)) / 60, meta["duration_s"] / 60, 0.25)
    activity_windows = _cluster_activity_windows(active_windows, gap_s=8)
    density_estimate = round((avg_person * 1.8 + avg_blob * 0.55 + motion_score / 18) * duration_minutes)
    session_estimate = max(len(activity_windows), density_estimate)
    session_estimate = max(0, min(80, session_estimate))
    if session_estimate and len(entry_offsets) < session_estimate:
        extra_offsets = _even_offsets(int(meta["duration_s"]), session_estimate - len(entry_offsets), set(entry_offsets))
        entry_offsets.extend(extra_offsets)
        entry_offsets.sort()
    dominant_region = max(range(3), key=lambda idx: region_counts[idx])
    return {
        "file": path.name,
        "sampled_frames": sampled,
        "avg_blob_count": avg_blob,
        "avg_person_count": avg_person,
        "motion_score": motion_score,
        "session_estimate": session_estimate,
        "queue_pressure": max(1, min(9, round(bottom_activity / max(1, sampled) * 6))),
        "dominant_region": dominant_region,
        "entry_offsets": entry_offsets or [0],
        "fingerprint": f"{path.stat().st_size:x}-{sampled}-{avg_blob}-{avg_person}-{motion_score}-{session_estimate}",
    }


def _empty_signal(path: Path, meta: dict) -> dict:
    return {
        "file": path.name,
        "sampled_frames": 0,
        "avg_blob_count": 0,
        "avg_person_count": 0,
        "motion_score": 0.0,
        "session_estimate": 0,
        "queue_pressure": 0,
        "dominant_region": 0,
        "entry_offsets": [],
        "fingerprint": f"{path.stat().st_size:x}-empty",
    }


def _cv_confidence(signal: dict, meta: dict, index: int) -> float:
    base = confidence_from_video(meta["bytes"], meta["duration_s"], index)
    cv_boost = min(0.18, signal["motion_score"] / 90 + signal["avg_blob_count"] / 80 + signal["avg_person_count"] / 12)
    return round(min(0.97, max(0.5, base + cv_boost)), 2)


def _cluster_activity_windows(seconds: list[int], gap_s: int) -> list[int]:
    if not seconds:
        return []
    clusters = [seconds[0]]
    previous = seconds[0]
    for second in seconds[1:]:
        if second - previous > gap_s:
            clusters.append(second)
        previous = second
    return clusters


def _even_offsets(duration_s: int, count: int, used: set[int]) -> list[int]:
    if count <= 0:
        return []
    duration_s = max(duration_s, count * 4)
    step = max(4, duration_s // (count + 1))
    offsets = []
    for index in range(count):
        offset = min(duration_s - 1, step * (index + 1))
        while offset in used:
            offset += 1
        offsets.append(offset)
        used.add(offset)
    return offsets


def _cv_staff_sessions(video_meta: list[dict], signals: list[dict], pos_transactions, store_id: str, salespeople: list[str]) -> list[dict]:
    base = min(txn.timestamp for txn in pos_transactions) - timedelta(minutes=10)
    events = []
    staff_count = min(len(salespeople), max(1, sum(1 for signal in signals if signal["motion_score"] > 0.5)))
    for index, name in enumerate(salespeople[:staff_count]):
        meta = video_meta[index % len(video_meta)]
        signal = signals[index % len(signals)]
        visitor_id = visitor_token(f"{store_id}:cv-staff:{name}:{signal['fingerprint']}")
        zone = DEFAULT_ZONES[(index + signal["dominant_region"]) % len(DEFAULT_ZONES)]
        confidence = max(0.62, _cv_confidence(signal, meta, index) - 0.04)
        time = base + timedelta(minutes=index * 35)
        events.extend(
            [
                _event(store_id, _camera_id(meta["file"]), visitor_id, "ZONE_ENTER", time, zone, 0, True, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 1, "staff_source": name, "cv_mode": True}),
                _event(store_id, _camera_id(meta["file"]), visitor_id, "ZONE_DWELL", time + timedelta(seconds=40), zone, 40000, True, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 2, "staff_source": name, "cv_mode": True}),
            ]
        )
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


def _metadata_session_plan(video_meta: list[dict]) -> list[dict]:
    sessions: list[dict] = []
    for video_index, meta in enumerate(video_meta):
        duration_s = max(1, int(meta.get("duration_s") or 1))
        size_mb = max(1, int(meta.get("bytes") or 1) // 1_000_000)
        width = int(meta.get("width") or 0)
        height = int(meta.get("height") or 0)
        base_count = max(1, round(duration_s / 24))
        size_variation = size_mb % 4
        resolution_variation = 1 if width >= 1280 and height >= 720 else 0
        session_count = max(1, min(18, base_count + size_variation + resolution_variation))
        used: set[int] = set()
        offsets = _even_offsets(duration_s, session_count, used)
        fingerprint = f"{meta['file']}:{meta.get('bytes', 0)}:{duration_s}:{width}x{height}"
        for session_index, offset_s in enumerate(offsets):
            seed = (size_mb + duration_s + video_index * 17 + session_index * 11) % 97
            sessions.append(
                {
                    "video_index": video_index,
                    "session_index": session_index,
                    "offset_s": offset_s,
                    "fingerprint": fingerprint,
                    "zone_seed": (seed + width // 160) % 13,
                    "dwell_seed": 4 + seed % 31,
                    "queue_seed": seed % 19,
                }
            )
    sessions.sort(key=lambda item: (item["offset_s"], item["video_index"], item["session_index"]))
    return sessions


def _planned_customer_sessions(session_plan: list[dict], transaction_count: int) -> list[dict]:
    minimum_sessions = transaction_count + max(6, transaction_count // 2)
    if len(session_plan) >= minimum_sessions:
        return session_plan
    planned = list(session_plan)
    if not planned:
        return planned
    index = 0
    while len(planned) < minimum_sessions:
        source = dict(planned[index % len(session_plan)])
        source["session_index"] = len(planned)
        source["offset_s"] += 37 * (1 + len(planned) // max(1, len(session_plan)))
        source["fingerprint"] = f"{source['fingerprint']}:synthetic-gap:{source['session_index']}"
        source["zone_seed"] = (source["zone_seed"] + len(planned)) % 13
        source["dwell_seed"] = 4 + (source["dwell_seed"] + len(planned)) % 31
        source["queue_seed"] = (source["queue_seed"] + len(planned)) % 19
        planned.append(source)
        index += 1
    return planned


def _metadata_queue_depth(meta: dict, session: dict, index: int) -> int:
    size_component = max(1, int(meta.get("bytes") or 1) // 50_000_000)
    duration_component = max(1, int(meta.get("duration_s") or 1) // 45)
    return max(1, min(9, 1 + (size_component + duration_component + session["queue_seed"] + index) % 8))


def _metadata_reentry(session: dict, index: int) -> bool:
    return index > 0 and (session["queue_seed"] == 0 or (session["zone_seed"] + session["queue_seed"] + index) % 17 == 0)


def _staff_sessions(video_meta: list[dict], pos_transactions, store_id: str, salespeople: list[str], session_plan: list[dict] | None = None) -> list[dict]:
    base = min(txn.timestamp for txn in pos_transactions) - timedelta(minutes=10)
    events = []
    session_plan = session_plan or _metadata_session_plan(video_meta)
    for index, name in enumerate(salespeople[:5]):
        meta = video_meta[index % len(video_meta)]
        session = session_plan[index % len(session_plan)] if session_plan else {"fingerprint": meta["file"], "zone_seed": index}
        visitor_id = visitor_token(f"{store_id}:staff:{name}:{session['fingerprint']}")
        zone = DEFAULT_ZONES[(index + session["zone_seed"]) % len(DEFAULT_ZONES)]
        confidence = max(0.62, confidence_from_video(meta["bytes"], meta["duration_s"], index) - 0.05)
        time = base + timedelta(minutes=index * 35, seconds=session.get("offset_s", 0) % 60)
        events.extend(
            [
                _event(store_id, _camera_id(meta["file"]), visitor_id, "ZONE_ENTER", time, zone, 0, True, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 1, "staff_source": name, "fallback_source": "mp4_metadata"}),
                _event(store_id, _camera_id(meta["file"]), visitor_id, "ZONE_DWELL", time + timedelta(seconds=40), zone, 40000, True, confidence, {"queue_depth": None, "sku_zone": zone, "session_seq": 2, "staff_source": name, "fallback_source": "mp4_metadata"}),
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
        "dwell_ms": int(dwell_ms),
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
