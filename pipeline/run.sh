#!/usr/bin/env bash
set -euo pipefail

VIDEOS_DIR="${VIDEOS_DIR:-/data/CCTV Footage}"
POS_CSV="${POS_CSV:-/data/pos_transactions.csv}"
OUTPUT="${OUTPUT:-data/events.jsonl}"
INGEST_URL="${INGEST_URL:-}"

if [[ ! -d "$VIDEOS_DIR" ]]; then
  first_video="$(find /data -type f -name '*.mp4' -print -quit 2>/dev/null || true)"
  if [[ -n "$first_video" ]]; then
    VIDEOS_DIR="$(dirname "$first_video")"
  fi
fi

if [[ ! -f "$POS_CSV" ]]; then
  POS_CSV="$(find /data -type f -name '*.csv' -print -quit 2>/dev/null || true)"
fi

if [[ -z "$POS_CSV" || ! -f "$POS_CSV" ]]; then
  echo "No POS CSV found. Set POS_CSV=/path/to/file.csv or mount it under /data." >&2
  exit 1
fi

ARGS=(--videos "$VIDEOS_DIR" --pos-csv "$POS_CSV" --output "$OUTPUT")
if [[ -n "$INGEST_URL" ]]; then
  ARGS+=(--ingest-url "$INGEST_URL")
fi

python -m pipeline.detect "${ARGS[@]}"
