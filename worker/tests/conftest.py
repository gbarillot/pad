from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.repository import connect, file_id_for_name


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "pad.sqlite3"


def create_configuration(
    db_path: Path,
    *,
    manual_mode: int = 0,
    min_confidence: float = 0.9,
    auto_cleanup: int | None = 0,
    running: int = 0,
) -> None:
    columns = [
        "manual_mode INTEGER NOT NULL DEFAULT 0",
        "min_confidence REAL NOT NULL DEFAULT 0.9",
        "running INTEGER NOT NULL DEFAULT 0",
    ]
    values: dict[str, Any] = {
        "manual_mode": manual_mode,
        "min_confidence": min_confidence,
        "running": running,
    }
    if auto_cleanup is not None:
        columns.append("auto_cleanup INTEGER NOT NULL DEFAULT 0")
        values["auto_cleanup"] = auto_cleanup

    with connect(db_path) as connection:
        connection.execute(f"CREATE TABLE configuration ({', '.join(columns)})")
        names = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        connection.execute(
            f"INSERT INTO configuration ({names}) VALUES ({placeholders})",
            tuple(values.values()),
        )


def insert_file(
    db_path: Path,
    *,
    name: str = "sample.pdf",
    status: str = "todo",
    extracted_json: dict[str, Any] | None = None,
    confidence: float | None = None,
    error: str | None = None,
) -> str:
    file_id = file_id_for_name(name)
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO files (id, name, status, extracted_json, confidence, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                name,
                status,
                json.dumps(extracted_json) if extracted_json is not None else None,
                confidence,
                error,
            ),
        )
    return file_id


def fetch_file(db_path: Path, file_id: str) -> sqlite3.Row | None:
    with connect(db_path) as connection:
        return connection.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()


def event_statuses(db_path: Path, file_id: str) -> list[str]:
    with connect(db_path) as connection:
        return [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM file_events WHERE file_id = ? ORDER BY id",
                (file_id,),
            ).fetchall()
        ]
