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
        pos_csv_path=_optional_path(os.getenv("POS_CSV_PATH")),
        store_layout_path=_optional_path(os.getenv("STORE_LAYOUT_PATH")),
    )


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value)
