# Design

## Architecture Overview

The system is split into two independently testable parts: a CCTV/POS event pipeline and a Store Intelligence API.

The pipeline starts from the raw files provided for the challenge. It reads the five MP4 clips, parses their metadata directly from the MP4 box structure, reads the POS CSV, groups line items into invoice-level transactions, and emits JSONL events in the required schema. When OpenCV is available, `pipeline/detect.py` decodes frames from every video, runs OpenCV's HOG person detector on sampled frames, computes frame-difference motion blobs, clusters active time windows, estimates queue pressure from lower-frame activity, and fingerprints each clip from frame-derived signals. Those signals determine the visitor/session count, confidence, queue depth, visitor tokens, and `cv_*` event metadata, so replacing a video changes the event output. POS is not used to create the visitor count in this path; it is used only to match detected billing-zone sessions to actual purchases within the configured time window. If OpenCV is unavailable, the script falls back to a deterministic POS-calibrated path so the acceptance gate remains runnable in minimal local environments.

The API is a FastAPI service backed by SQLite. SQLite was chosen because the dataset is small, the challenge is take-home, and the acceptance gate rewards a system that starts reliably with `docker compose up`. The repository layer uses the Python standard library `sqlite3` module, so the application has fewer moving parts than a full ORM-based stack. The `events` table is keyed by `event_id`, making ingestion idempotent. POS transactions are stored separately and loaded at startup when `POS_CSV_PATH` is present.

Business logic lives in `app/analytics.py`. This keeps the HTTP routes thin and makes the funnel, metrics, heatmap, anomaly, and health logic easy to test directly. Sessions are grouped by `visitor_id`, staff events are excluded from customer metrics, and conversion is computed by the stated rule: a visitor who appears in the billing zone within the five-minute window before a POS transaction counts as converted.

## Request Flow

1. `pipeline/detect.py` reads the CCTV directory and POS CSV, using OpenCV video analysis in `auto` mode when installed.
2. It writes schema-compliant events to `data/events.jsonl`.
3. `scripts/ingest_events.py` posts the events to `POST /events/ingest`.
4. The API validates each event with Pydantic, deduplicates by `event_id`, and stores valid events.
5. Metrics endpoints query SQLite and compute responses from current events and POS rows.

## Data Model

The event model follows the challenge schema:

- `event_id`, `store_id`, `camera_id`, `visitor_id`
- `event_type`
- UTC `timestamp`
- optional `zone_id`
- `dwell_ms`
- `is_staff`
- calibrated `confidence`
- flexible `metadata` with `queue_depth`, `sku_zone`, and `session_seq`

POS transactions are normalized from line-item CSV rows into invoice-level records: `store_id`, `transaction_id`, UTC `timestamp`, and `basket_value_inr`.

## Edge Cases

Re-entry is represented with `REENTRY` events on the same visitor token, so `/funnel` counts the session once. Staff movement is included but flagged `is_staff=true`; metrics and funnels exclude staff. Billing abandonment is emitted for selected sessions that enter billing but do not match a transaction window. Queue depth comes from billing-region video activity when OpenCV is available. Empty stores and zero-purchase windows return numeric zero values rather than nulls. Low sample heatmaps include a `LOW` data confidence flag when fewer than twenty sessions are present. The checked-in sample `data/events.jsonl` was generated with the OpenCV path from the local five-camera dataset.

## Production Readiness

The service runs with Docker Compose, uses structured JSON request logs, returns structured database failure responses, and includes tests for ingestion idempotency, malformed event partial success, staff exclusion, POS conversion, re-entry funnel behavior, heatmap confidence, anomalies, health staleness, and local pipeline generation. The repository is intentionally small and explicit so a reviewer can inspect the full business logic quickly.

## AI-Assisted Decisions

AI helped shape three areas. First, it suggested separating analytics from HTTP routes so the evaluator can inspect and test the logic without running the server. I agreed because it improves maintainability and test speed.

Second, AI initially suggested using a heavier database abstraction. I overrode that and used direct SQLite because the challenge dataset is small, Docker startup should be boring, and avoiding an ORM reduces setup risk.

Third, AI suggested documenting the gap between the PDF's generic dataset and the actual files. I agreed. The provided dataset has one store and five short clips, so the pipeline is explicit about its assumptions instead of pretending the missing multi-store sample files exist.
