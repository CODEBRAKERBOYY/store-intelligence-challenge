# Choices

## 1. Detection Model and Pipeline Choice

Options considered were YOLO plus ByteTrack, OpenCV HOG plus motion segmentation, manual annotation, and a deterministic metadata/POS-driven event generator. The strongest production answer would be a detector and tracker such as YOLOv8 with ByteTrack, followed by zone crossing rules. I chose a middle path for this submission: OpenCV video analysis by default, with a deterministic fallback if the runtime cannot load OpenCV.

AI suggested YOLOv8 as a standard starting point and noted that the evaluation framework rewards working systems and reasoning over perfect detection. I did not use YOLO because adding PyTorch model downloads would make `docker compose up` heavier and more failure-prone for a take-home review. Instead, `pipeline/detect.py` samples frames from each MP4 with OpenCV, runs the built-in HOG person detector on a subset of frames, computes frame-difference motion masks, filters contour blobs as person proxies, clusters active windows into session estimates, tracks region activity, and estimates billing queue pressure from lower-frame activity. POS is used only to align purchases to converted sessions, not as the source of visitor/session count. The trade-off is lower visual accuracy than YOLO/ByteTrack. The benefit is a real video-dependent detector that is lightweight, auditable, and still robust enough for the provided short clips.

## 2. Event Schema Design

The problem statement already defines the required event schema, so the main decision was how strict to be. Options were permissive dictionaries everywhere, strict Pydantic validation at the API boundary, or a custom JSON Schema validator. AI recommended strict validation with Pydantic because malformed event handling is part of scoring. I chose Pydantic models for `StoreEvent`, `EventMetadata`, and ingest payloads.

The API validates every event independently, which enables partial success: one bad event does not reject a whole 500-event batch. Event IDs are primary keys in SQLite, which makes repeated ingestion idempotent. Metadata is stored as JSON so the schema can support queue depth and SKU zone today while still allowing future detector fields, such as bounding boxes or tracking IDs, without a migration.

## 3. API Architecture Choice

The key API decision was storage and analytics placement. Options were in-memory only, SQLite, or PostgreSQL. AI suggested SQLite or PostgreSQL, with SQLite acceptable for the challenge. I chose SQLite because it has zero service dependency, works well in Docker Compose, and is enough for one store/day of challenge data. PostgreSQL would be better for many live stores, but it would add operational setup that is not needed to pass the acceptance gate.

I also chose to keep analytics in `app/analytics.py` rather than embedding it in route handlers. This makes `/metrics`, `/funnel`, `/heatmap`, and `/anomalies` testable as pure functions over event and POS rows. If the system later moves to PostgreSQL or streaming aggregation, the HTTP layer can remain stable while the repository and analytics implementation evolve.

The main limitation is that current analytics are computed at read time. That is correct and transparent for this dataset, but at forty stores with live traffic the first thing that would break is repeated full scans of the event table. The next production step would be incremental materialized session summaries keyed by store, visitor, and time window.
