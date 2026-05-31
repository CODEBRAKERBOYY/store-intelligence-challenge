#!/usr/bin/env bash
set -euo pipefail

VIDEOS_DIR="${VIDEOS_DIR:-/data/CCTV Footage}"
POS_CSV="${POS_CSV:-/data/Brigade_Bangalore_10_April_26 (1)bc6219c.csv}"
OUTPUT="${OUTPUT:-data/events.jsonl}"
INGEST_URL="${INGEST_URL:-}"

ARGS=(--videos "$VIDEOS_DIR" --pos-csv "$POS_CSV" --output "$OUTPUT")
if [[ -n "$INGEST_URL" ]]; then
  ARGS+=(--ingest-url "$INGEST_URL")
fi

python -m pipeline.detect "${ARGS[@]}"
