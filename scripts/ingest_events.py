from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest JSONL events into the Store Intelligence API.")
    parser.add_argument("--events", type=Path, default=Path("data/events.jsonl"))
    parser.add_argument("--url", default="http://localhost:8000/events/ingest")
    args = parser.parse_args()

    events = [json.loads(line) for line in args.events.read_text().splitlines() if line.strip()]
    totals = {"accepted": 0, "duplicate": 0, "rejected": 0}
    for start in range(0, len(events), 500):
        payload = json.dumps({"events": events[start : start + 500]}).encode("utf-8")
        req = urllib.request.Request(args.url, data=payload, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as response:
            body = json.loads(response.read())
        for key in totals:
            totals[key] += body.get(key, 0)
    print(json.dumps(totals, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
