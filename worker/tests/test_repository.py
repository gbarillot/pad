from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app import repository

from conftest import create_configuration, event_statuses, fetch_file, insert_file


def test_sqlite_path_from_url_accepts_urls_and_plain_paths() -> None:
    assert repository.sqlite_path_from_url("sqlite:////tmp/pad.sqlite3") == Path("/tmp/pad.sqlite3")
    assert repository.sqlite_path_from_url("sqlite://relative.sqlite3") == Path("relative.sqlite3")
    assert repository.sqlite_path_from_url("sqlite:///relative.sqlite3") == Path("relative.sqlite3")
    assert repository.sqlite_path_from_url("/tmp/plain.sqlite3") == Path("/tmp/plain.sqlite3")


def test_connect_creates_file_schema_and_events(db_path: Path) -> None:
    file_id = insert_file(db_path, status="todo")

    with repository.connect(db_path) as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

    assert {"files", "file_events"}.issubset(tables)
    assert event_statuses(db_path, file_id) == ["todo"]


def test_claim_and_mark_file_lifecycle(db_path: Path) -> None:
    file_id = insert_file(db_path, status="todo")

    assert repository.claim_file(db_path, file_id) is True
    assert repository.claim_file(db_path, file_id) is False

    repository.mark_ready(db_path, file_id, extracted_json={"ok": True}, confidence=0.91)
    row = fetch_file(db_path, file_id)

    assert row is not None
    assert row["status"] == "ready"
    assert json.loads(row["extracted_json"]) == {"ok": True}
    assert row["confidence"] == 0.91
    assert event_statuses(db_path, file_id) == ["todo", "extracting", "ready"]


def test_list_ready_files_includes_name_and_requires_json(db_path: Path) -> None:
    ready_id = insert_file(db_path, name="ready.pdf", status="ready", extracted_json={"ok": True})
    insert_file(db_path, name="empty.pdf", status="ready")
    insert_file(db_path, name="todo.pdf", status="todo")

    rows = repository.list_ready_files(db_path)

    assert [(row["id"], row["name"]) for row in rows] == [(ready_id, "ready.pdf")]


def test_reset_interrupted_files_requeues_by_status(db_path: Path) -> None:
    extracting_id = insert_file(db_path, name="extracting.pdf", status="extracting")
    transferring_id = insert_file(
        db_path,
        name="transferring.pdf",
        status="transferring",
        extracted_json={"ok": True},
    )
    saved_id = insert_file(db_path, name="saved.pdf", status="saved")

    assert repository.reset_interrupted_files(db_path) == 2

    assert fetch_file(db_path, extracting_id)["status"] == "todo"  # type: ignore[index]
    assert fetch_file(db_path, transferring_id)["status"] == "ready"  # type: ignore[index]
    assert fetch_file(db_path, saved_id)["status"] == "saved"  # type: ignore[index]


def test_configuration_helpers_return_defaults_without_configuration_table(db_path: Path) -> None:
    with repository.connect(db_path):
        pass

    assert repository.manual_mode(db_path) is False
    assert repository.auto_cleanup(db_path) is False
    assert repository.min_confidence(db_path) == repository.DEFAULT_MIN_CONFIDENCE
    repository.set_running(db_path, True)


def test_configuration_helpers_read_configuration_values(db_path: Path) -> None:
    create_configuration(db_path, manual_mode=1, min_confidence=0.73, auto_cleanup=1, running=0)

    assert repository.manual_mode(db_path) is True
    assert repository.auto_cleanup(db_path) is True
    assert repository.min_confidence(db_path) == 0.73

    repository.set_running(db_path, True)
    with repository.connect(db_path) as connection:
        running = connection.execute("SELECT running FROM configuration").fetchone()["running"]
    assert running == 1


def test_min_confidence_returns_default_when_value_is_null(db_path: Path) -> None:
    with repository.connect(db_path) as connection:
        connection.execute("CREATE TABLE configuration (min_confidence REAL)")
        connection.execute("INSERT INTO configuration (min_confidence) VALUES (NULL)")

    assert repository.min_confidence(db_path) == repository.DEFAULT_MIN_CONFIDENCE


def test_auto_cleanup_is_false_when_legacy_column_is_missing(db_path: Path) -> None:
    create_configuration(db_path, auto_cleanup=None)

    assert repository.auto_cleanup(db_path) is False


def test_delete_file_tracking_removes_file_and_events(db_path: Path) -> None:
    file_id = insert_file(db_path, status="todo")
    repository.mark_failed(db_path, file_id, error="boom")

    repository.delete_file_tracking(db_path, file_id)

    assert fetch_file(db_path, file_id) is None
    assert event_statuses(db_path, file_id) == []


def test_mark_success_sets_success_status(db_path: Path) -> None:
    file_id = insert_file(db_path, status="ready", extracted_json={"ok": True})

    repository.mark_success(db_path, file_id)

    assert fetch_file(db_path, file_id)["status"] == "success"  # type: ignore[index]


def test_connect_rolls_back_on_error(db_path: Path) -> None:
    try:
        with repository.connect(db_path) as connection:
            connection.execute("INSERT INTO files (id, name, status) VALUES ('id', 'name.pdf', 'todo')")
            raise RuntimeError("abort")
    except RuntimeError:
        pass

    with repository.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


def test_legacy_uploads_are_migrated(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE uploads (
              id TEXT PRIMARY KEY,
              stored_filename TEXT NOT NULL,
              status TEXT NOT NULL,
              error TEXT,
              created_at TEXT,
              updated_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE upload_events (
              id INTEGER PRIMARY KEY,
              upload_id TEXT NOT NULL,
              status TEXT NOT NULL,
              error TEXT,
              created_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO uploads (id, stored_filename, status, error, created_at, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("legacy-id", "legacy.pdf", "failed", "legacy error"),
        )
        connection.execute(
            "INSERT INTO upload_events (id, upload_id, status, error, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (10, "legacy-id", "failed", "legacy error"),
        )
        connection.commit()

    with repository.connect(db_path) as connection:
        file_row = connection.execute("SELECT * FROM files WHERE name = 'legacy.pdf'").fetchone()
        uploads_exists = repository.table_exists(connection, "uploads")
        upload_events_exists = repository.table_exists(connection, "upload_events")

    assert file_row is not None
    assert file_row["status"] == "failed"
    assert file_row["error"] == "legacy error"
    assert uploads_exists is False
    assert upload_events_exists is False


def test_legacy_uploads_migrate_without_event_table(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(
            """
            CREATE TABLE uploads (
              id TEXT PRIMARY KEY,
              stored_filename TEXT NOT NULL,
              status TEXT NOT NULL,
              extracted_json TEXT,
              confidence REAL,
              error TEXT,
              created_at TEXT,
              updated_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO uploads (id, stored_filename, status, extracted_json, confidence, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("legacy-id", "legacy-no-events.pdf", "ready", '{"ok": true}', 0.7, None),
        )
        connection.commit()

    with repository.connect(db_path) as connection:
        file_row = connection.execute("SELECT * FROM files WHERE name = 'legacy-no-events.pdf'").fetchone()

    assert file_row is not None
    assert file_row["status"] == "ready"
    assert file_row["confidence"] == 0.7
