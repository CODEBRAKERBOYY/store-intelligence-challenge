# Store Intelligence API

This project turns the Brigade Road challenge dataset into a working offline store analytics system. It includes a CCTV/POS event pipeline, a FastAPI intelligence API, SQLite persistence, tests, and a terminal live dashboard.

## Five-command setup

1. Put the provided files in `./challenge_data` or set `DATA_DIR` to the directory that contains:
   - `CCTV Footage/CAM 1.mp4` through `CAM 5.mp4`
   - `Brigade_Bangalore_10_April_26 (1)bc6219c.csv`
   - `Brigade Road - Store layoutc5f5d56.xlsx`
   The Docker and pipeline scripts also discover generic `*.csv` POS files under `/data`, so the review dataset can use `pos_transactions.csv` without code changes.
2. Start the API:
   ```bash
   docker compose up --build
   ```
3. Generate events from the clips and POS file:
   ```bash
   DATA_DIR=/path/to/challenge/files docker compose run --rm api bash pipeline/run.sh
   ```
4. Ingest the generated events:
   ```bash
   docker compose run --rm api python scripts/ingest_events.py --events data/events.jsonl --url http://api:8000/events/ingest
   ```
5. Query metrics:
   ```bash
   curl http://localhost:8000/stores/ST1008/metrics
   ```

## Local development

Using any Python 3.12 environment with `requirements.txt` installed:

```bash
python -m pipeline.detect \
  --videos "/path/to/challenge_data/CCTV Footage" \
  --pos-csv "/path/to/challenge_data/pos_transactions.csv" \
  --output data/events.jsonl \
  --mode auto

STORE_DB_PATH=data/dev.db \
POS_CSV_PATH="/path/to/challenge_data/pos_transactions.csv" \
python -m uvicorn app.main:app --reload
```

Then ingest:

```bash
python scripts/ingest_events.py
```

## API endpoints

- `POST /events/ingest`: accepts up to 500 events, validates each event, deduplicates by `event_id`, and returns partial success details.
- `GET /stores/{id}/metrics`: unique visitors, conversion rate, dwell by zone, queue depth, abandonment rate, revenue, and transaction count.
- `GET /stores/{id}/funnel`: session-based Entry -> Zone Visit -> Billing Queue -> Purchase funnel.
- `GET /stores/{id}/heatmap`: zone frequency and dwell values normalized from 0 to 100.
- `GET /stores/{id}/anomalies`: queue spikes, conversion drop, and dead-zone signals.
- `GET /health`: service status, last event timestamp per store, and `STALE_FEED` warning.

## Live dashboard

After the API is running and events are ingested:

```bash
python scripts/live_dashboard.py --store-id ST1008
```

This prints visitor count, conversion rate, queue depth, revenue, and funnel stages, refreshing every two seconds.

## Testing

```bash
coverage run -m pytest
coverage report --fail-under=70
```

The default tests use synthetic MP4 and POS fixtures so coverage does not depend on licensed footage. To also run the real-footage smoke test, set `CHALLENGE_DATA_DIR=/path/to/challenge_data`. The Docker runtime installs OpenCV, so pipeline `--mode auto` performs video-dependent frame analysis; use `--mode fallback` only for debugging minimal environments.

## Dataset assumptions

The problem PDF describes a larger generic dataset. The provided data for this build contains one store (`ST1008`, `Brigade_Bangalore`), five short 1080p clips, a line-item POS CSV with 24 unique invoices, and a layout workbook with the store plan embedded as an image. The pipeline is therefore calibrated to this actual dataset and records that assumption in `DESIGN.md` and `CHOICES.md`.
