from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    pos_csv_path: Path | None
    store_layout_path: Path | None


def get_settings() -> Settings:
    return Settings(
        database_path=Path(os.getenv("STORE_DB_PATH", "data/store_intelligence.db")),
        pos_csv_path=_data_file(os.getenv("POS_CSV_PATH"), "*.csv"),
        store_layout_path=_data_file(os.getenv("STORE_LAYOUT_PATH"), "store_layout.*"),
    )


def _data_file(value: str | None, pattern: str) -> Path | None:
    if value:
        path = Path(value)
        if path.exists():
            return path
    data_dir = Path(os.getenv("DATA_DIR", "/data"))
    if data_dir.exists():
        matches = sorted(data_dir.glob(pattern))
        if matches:
            return matches[0]
    if value:
        return Path(value)
    return None
