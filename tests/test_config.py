# PROMPT: Add tests for environment-driven API configuration and dataset file discovery in Docker-style mounts.
# CHANGES MADE: Kept the tests filesystem-local with monkeypatched environment variables so they do not depend on the challenge dataset.

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def test_settings_use_explicit_existing_paths(tmp_path: Path, monkeypatch):
    db = tmp_path / "store.db"
    pos = tmp_path / "pos_transactions.csv"
    layout = tmp_path / "store_layout.json"
    pos.write_text("invoice_number\n")
    layout.write_text("{}")

    monkeypatch.setenv("STORE_DB_PATH", str(db))
    monkeypatch.setenv("POS_CSV_PATH", str(pos))
    monkeypatch.setenv("STORE_LAYOUT_PATH", str(layout))

    settings = get_settings()

    assert settings.database_path == db
    assert settings.pos_csv_path == pos
    assert settings.store_layout_path == layout


def test_settings_discover_data_files_when_default_paths_are_missing(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pos = data_dir / "Brigade_Bangalore_10_April_26.csv"
    layout = data_dir / "store_layout.xlsx"
    pos.write_text("invoice_number\n")
    layout.write_text("layout")

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("POS_CSV_PATH", str(data_dir / "missing.csv"))
    monkeypatch.setenv("STORE_LAYOUT_PATH", str(data_dir / "missing.json"))

    settings = get_settings()

    assert settings.pos_csv_path == pos
    assert settings.store_layout_path == layout


def test_settings_keep_missing_explicit_path_when_no_data_dir_match(tmp_path: Path, monkeypatch):
    missing = tmp_path / "missing.csv"

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("POS_CSV_PATH", str(missing))
    monkeypatch.delenv("STORE_LAYOUT_PATH", raising=False)

    settings = get_settings()

    assert settings.pos_csv_path == missing
    assert settings.store_layout_path is None
