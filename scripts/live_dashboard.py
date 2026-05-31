from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Terminal live dashboard for one store.")
    parser.add_argument("--store-id", default="ST1008")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--refresh-seconds", type=float, default=2.0)
    parser.add_argument("--iterations", type=int, default=0, help="0 means run forever")
    args = parser.parse_args()

    count = 0
    while args.iterations == 0 or count < args.iterations:
        metrics = _get(f"{args.base_url}/stores/{args.store_id}/metrics")
        funnel = _get(f"{args.base_url}/stores/{args.store_id}/funnel")
        print("\033[2J\033[H", end="")
        print("Store Intelligence Live Dashboard")
        print("=" * 40)
        print(f"Store: {args.store_id}")
        print(f"Visitors: {metrics['unique_visitors_today']}")
        print(f"Converted: {metrics['converted_visitors']}")
        print(f"Conversion rate: {metrics['conversion_rate']:.2%}")
        print(f"Queue depth: {metrics['queue_depth']}")
        print(f"Revenue INR: {metrics['revenue_inr']:,.2f}")
        print("\nFunnel")
        for stage in funnel["stages"]:
            print(f"- {stage['stage']}: {stage['count']} (drop-off {stage['dropoff_pct']:.2%})")
        time.sleep(args.refresh_seconds)
        count += 1
    return 0


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())


if __name__ == "__main__":
    sys.exit(main())
