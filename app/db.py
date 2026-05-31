from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.models import POSTransaction, StoreEvent


class StoreRepository:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def init(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    visitor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    zone_id TEXT,
                    dwell_ms INTEGER NOT NULL,
                    is_staff INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_store_time
                    ON events(store_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_events_store_visitor
                    ON events(store_id, visitor_id);

                CREATE TABLE IF NOT EXISTS pos_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    basket_value_inr REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pos_store_time
                    ON pos_transactions(store_id, timestamp);
                """
            )

    def insert_event(self, event: StoreEvent) -> bool:
        with self.connect() as con:
            try:
                con.execute(
                    """
                    INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.store_id,
                        event.camera_id,
                        event.visitor_id,
                        event.event_type.value,
                        event.timestamp.isoformat(),
                        event.zone_id,
                        event.dwell_ms,
                        int(event.is_staff),
                        event.confidence,
                        event.metadata.model_dump_json(),
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def upsert_pos(self, txns: list[POSTransaction]) -> int:
        with self.connect() as con:
            con.executemany(
                """
                INSERT INTO pos_transactions(transaction_id, store_id, timestamp, basket_value_inr)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    store_id=excluded.store_id,
                    timestamp=excluded.timestamp,
                    basket_value_inr=excluded.basket_value_inr
                """,
                [
                    (
                        txn.transaction_id,
                        txn.store_id,
                        txn.timestamp.isoformat(),
                        txn.basket_value_inr,
                    )
                    for txn in txns
                ],
            )
            return len(txns)

    def list_events(self, store_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM events"
        params: tuple[str, ...] = ()
        if store_id:
            query += " WHERE store_id = ?"
            params = (store_id,)
        query += " ORDER BY timestamp ASC, event_id ASC"
        with self.connect() as con:
            return [self._event_row(row) for row in con.execute(query, params)]

    def list_pos(self, store_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM pos_transactions"
        params: tuple[str, ...] = ()
        if store_id:
            query += " WHERE store_id = ?"
            params = (store_id,)
        query += " ORDER BY timestamp ASC"
        with self.connect() as con:
            return [dict(row) for row in con.execute(query, params)]

    def health_rows(self) -> list[dict]:
        with self.connect() as con:
            return [
                dict(row)
                for row in con.execute(
                    """
                    SELECT store_id, MAX(timestamp) AS last_event_timestamp, COUNT(*) AS event_count
                    FROM events
                    GROUP BY store_id
                    """
                )
            ]

    def ping(self) -> None:
        with self.connect() as con:
            con.execute("SELECT 1").fetchone()

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["is_staff"] = bool(data["is_staff"])
        data["metadata"] = json.loads(data["metadata"])
        data["timestamp_dt"] = datetime.fromisoformat(data["timestamp"])
        return data
