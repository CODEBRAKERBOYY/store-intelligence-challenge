# Purplle Hackathon Submission Content

## Title

Store Intelligence API: CCTV and POS Analytics for Retail Insights

## Description

Store Intelligence API is an offline retail analytics system built for the Purplle Tech Challenge 2026 Round 2. It starts from raw CCTV footage and POS transactions, emits structured visitor behavior events, and exposes business-ready store intelligence through a FastAPI service.

The solution includes:

- CCTV/POS event generation pipeline
- Challenge-schema JSONL event stream
- FastAPI ingestion and analytics API
- SQLite persistence with idempotent event ingestion
- Store metrics: visitors, conversion rate, dwell time, queue depth, abandonment rate, revenue, and transactions
- Session-based funnel analysis
- Zone heatmap data normalized for rendering
- Anomaly detection for queue spikes, conversion drops, and dead zones
- Health endpoint with stale-feed warning
- Terminal live dashboard
- Docker Compose setup
- Automated tests with 78% coverage

The repository includes DESIGN.md and CHOICES.md explaining the architecture, assumptions, trade-offs, edge-case handling, and AI-assisted decisions.

## Theme

Choose the closest available option:

- Retail Tech
- AI / ML
- Data Analytics
- Computer Vision

If only one theme can be selected, choose Data Analytics or AI / ML.

## Demo Link

Use the repository URL if no hosted demo is available. The project is designed for local demo through Docker Compose.

## Repository URL

Paste your GitHub repository URL here after pushing the code.

## Source Code Upload

Upload:

`purplle-store-intelligence-final-source.zip`

Do not upload `/Users/alok/Documents/purple new project.zip`; that older zip contains local cache and git artifacts.

## Presentation Upload

Upload:

`Purplle_Store_Intelligence_Pitch.pdf`

## Instructions to Run

```text
1. Unzip the submitted source code.

2. Put the provided challenge files in ./challenge_data or set DATA_DIR to a clean directory that contains only the challenge dataset:
   - CCTV Footage/CAM 1.mp4 through CAM 5.mp4
   - Brigade_Bangalore_10_April_26 (1)bc6219c.csv
   - Brigade Road - Store layoutc5f5d56.xlsx

   If you receive CCTV Footage.zip, unzip it so the MP4 files live under:
   ./challenge_data/CCTV Footage/

3. Start the API:
   docker compose up --build

4. Generate events from the challenge files:
   DATA_DIR=/path/to/challenge/files docker compose run --rm api bash pipeline/run.sh

   If DATA_DIR contains unrelated CSV files, use explicit paths:
   DATA_DIR=/path/to/challenge/files VIDEOS_DIR="/data/CCTV Footage" POS_CSV="/data/Brigade_Bangalore_10_April_26 (1)bc6219c.csv" docker compose run --rm api bash pipeline/run.sh

5. Ingest the included sample events:
   docker compose run --rm api python scripts/ingest_events.py --events data/events.jsonl --url http://api:8000/events/ingest

6. Query the API:
   curl http://localhost:8000/stores/ST1008/metrics
   curl http://localhost:8000/stores/ST1008/funnel
   curl http://localhost:8000/stores/ST1008/heatmap
   curl http://localhost:8000/stores/ST1008/anomalies

7. API documentation is available at:
   http://localhost:8000/docs

8. Run tests:
   coverage run -m pytest
   coverage report --fail-under=70
```

## Validation Summary

- Tests: 16 passed, 1 skipped
- Coverage: 78%
- Local API smoke test: passed for startup, ingestion, health, metrics, funnel, heatmap, and anomalies
- Docker Compose config: valid
- Full Docker runtime check: blocked locally because Docker Desktop was unable to start
