from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


DEFAULT_MIN_CONFIDENCE = 0.9


def sqlite_path_from_url(database_url: str) -> Path:
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite://"):
        return Path(database_url.removeprefix("sqlite://"))
    return Path(database_url)


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        ensure_file_schema(connection)
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def ensure_file_schema(connection: sqlite3.Connection) -> None:
    migrate_legacy_uploads_schema(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'todo',
          extracted_json TEXT DEFAULT NULL,
          confidence REAL DEFAULT NULL,
          error TEXT DEFAULT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS file_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          file_id TEXT NOT NULL,
          status TEXT NOT NULL,
          error TEXT DEFAULT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_file_events_file_id ON file_events (file_id);
        CREATE TRIGGER IF NOT EXISTS file_events_after_insert
        AFTER INSERT ON files
        BEGIN
          INSERT INTO file_events (file_id, status, error, created_at)
          VALUES (NEW.id, NEW.status, NEW.error, CURRENT_TIMESTAMP);
        END;
        CREATE TRIGGER IF NOT EXISTS file_events_after_status_update
        AFTER UPDATE OF status, error ON files
        WHEN OLD.status IS NOT NEW.status OR OLD.error IS NOT NEW.error
        BEGIN
          INSERT INTO file_events (file_id, status, error, created_at)
          VALUES (NEW.id, NEW.status, NEW.error, CURRENT_TIMESTAMP);
        END;
        CREATE TRIGGER IF NOT EXISTS files_updated_at_after_status_update
        AFTER UPDATE OF status, error ON files
        WHEN OLD.status IS NOT NEW.status OR OLD.error IS NOT NEW.error
        BEGIN
          UPDATE files SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
        END;
        """
    )

    existing_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(files)").fetchall()
    }
    if "extracted_json" not in existing_columns:
        connection.execute("ALTER TABLE files ADD COLUMN extracted_json TEXT")
    if "confidence" not in existing_columns:
        connection.execute("ALTER TABLE files ADD COLUMN confidence REAL")


def migrate_legacy_uploads_schema(connection: sqlite3.Connection) -> None:
    if not table_exists(connection, "uploads"):
        return

    connection.executescript(
        """
        DROP TRIGGER IF EXISTS upload_events_after_insert;
        DROP TRIGGER IF EXISTS upload_events_after_status_update;
        DROP TRIGGER IF EXISTS uploads_updated_at_after_status_update;
        CREATE TABLE IF NOT EXISTS files (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL DEFAULT 'todo',
          extracted_json TEXT DEFAULT NULL,
          confidence REAL DEFAULT NULL,
          error TEXT DEFAULT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS file_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          file_id TEXT NOT NULL,
          status TEXT NOT NULL,
          error TEXT DEFAULT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS ix_file_events_file_id ON file_events (file_id);
        """
    )

    existing_upload_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(uploads)").fetchall()
    }
    if "extracted_json" not in existing_upload_columns:
        connection.execute("ALTER TABLE uploads ADD COLUMN extracted_json TEXT")
    if "confidence" not in existing_upload_columns:
        connection.execute("ALTER TABLE uploads ADD COLUMN confidence REAL")

    legacy_uploads = connection.execute(
        """
        SELECT id,
               stored_filename,
               status,
               extracted_json,
               confidence,
               error,
               created_at,
               updated_at
        FROM uploads
        """
    ).fetchall()
    legacy_upload_id_to_file_id: dict[str, str] = {}
    for upload in legacy_uploads:
        file_id = file_id_for_name(upload["stored_filename"])
        legacy_upload_id_to_file_id[upload["id"]] = file_id
        connection.execute(
            """
            INSERT INTO files (id, name, status, extracted_json, confidence, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name = excluded.name,
              status = excluded.status,
              extracted_json = excluded.extracted_json,
              confidence = excluded.confidence,
              error = excluded.error,
              created_at = COALESCE(files.created_at, excluded.created_at),
              updated_at = excluded.updated_at
            """,
            (
                file_id,
                upload["stored_filename"],
                upload["status"],
                upload["extracted_json"],
                upload["confidence"],
                upload["error"],
                upload["created_at"],
                upload["updated_at"],
            ),
        )

    if table_exists(connection, "upload_events"):
        legacy_events = connection.execute(
            """
            SELECT id, upload_id, status, error, created_at
            FROM upload_events
            ORDER BY id
            """
        ).fetchall()
        for event in legacy_events:
            file_id = legacy_upload_id_to_file_id.get(event["upload_id"])
            if file_id is None:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO file_events (id, file_id, status, error, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event["id"], file_id, event["status"], event["error"], event["created_at"]),
            )
        connection.execute("DROP TABLE upload_events")

    connection.execute("DROP TABLE uploads")


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def file_id_for_name(file_name: str) -> str:
    return hashlib.sha1(file_name.encode()).hexdigest()


def list_todo_files(db_path: Path) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        return connection.execute(
            """
            SELECT id, name
            FROM files
            WHERE status = 'todo'
            ORDER BY created_at
            """
        ).fetchall()


def list_ready_files(db_path: Path) -> list[sqlite3.Row]:
    with connect(db_path) as connection:
        return connection.execute(
            """
            SELECT id, name, extracted_json
            FROM files
            WHERE status = 'ready'
              AND extracted_json IS NOT NULL
            ORDER BY updated_at, created_at
            """
        ).fetchall()


def has_active_files(db_path: Path) -> bool:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM files
            WHERE status IN ('todo', 'ready', 'extracting', 'processing', 'saving', 'registering', 'transferring', 'recording')
            LIMIT 1
            """
        ).fetchone()
        return row is not None


def reset_interrupted_files(db_path: Path) -> int:
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE files
            SET status = CASE
                WHEN status IN ('saving', 'registering', 'transferring', 'recording')
                  AND extracted_json IS NOT NULL THEN 'ready'
                ELSE 'todo'
            END,
            error = NULL
            WHERE status IN ('extracting', 'processing', 'saving', 'registering', 'transferring', 'recording')
            """
        )
        return cursor.rowcount


def set_running(db_path: Path, running: bool) -> None:
    with connect(db_path) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'configuration'"
        ).fetchone()
        if not table_exists:
            return

        connection.execute(
            "UPDATE configuration SET running = ? WHERE rowid = (SELECT rowid FROM configuration LIMIT 1)",
            (1 if running else 0,),
        )


def min_confidence(db_path: Path) -> float:
    with connect(db_path) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'configuration'"
        ).fetchone()
        if not table_exists:
            return DEFAULT_MIN_CONFIDENCE

        row = connection.execute("SELECT min_confidence FROM configuration LIMIT 1").fetchone()
        if row is None or row["min_confidence"] is None:
            return DEFAULT_MIN_CONFIDENCE
        return float(row["min_confidence"])


def manual_mode(db_path: Path) -> bool:
    with connect(db_path) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'configuration'"
        ).fetchone()
        if not table_exists:
            return False

        row = connection.execute("SELECT manual_mode FROM configuration LIMIT 1").fetchone()
        return bool(row is not None and row["manual_mode"])


def auto_cleanup(db_path: Path) -> bool:
    with connect(db_path) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'configuration'"
        ).fetchone()
        if not table_exists:
            return False

        existing_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(configuration)").fetchall()
        }
        if "auto_cleanup" not in existing_columns:
            return False

        row = connection.execute("SELECT auto_cleanup FROM configuration LIMIT 1").fetchone()
        return bool(row is not None and row["auto_cleanup"])


def claim_file(db_path: Path, file_id: str) -> bool:
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE files
            SET status = 'extracting', error = NULL
            WHERE id = ? AND status = 'todo'
            """,
            (file_id,),
        )
        return cursor.rowcount == 1


def claim_ready_file(db_path: Path, file_id: str) -> bool:
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE files
            SET status = 'transferring', error = NULL
            WHERE id = ? AND status = 'ready'
            """,
            (file_id,),
        )
        return cursor.rowcount == 1


def mark_success(db_path: Path, file_id: str) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE files
            SET status = 'success', error = NULL
            WHERE id = ?
            """,
            (file_id,),
        )


def mark_saved(db_path: Path, file_id: str) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE files
            SET status = 'saved', error = NULL
            WHERE id = ?
            """,
            (file_id,),
        )


def delete_file_tracking(db_path: Path, file_id: str) -> None:
    with connect(db_path) as connection:
        connection.execute("DELETE FROM file_events WHERE file_id = ?", (file_id,))
        connection.execute("DELETE FROM files WHERE id = ?", (file_id,))


def mark_review(db_path: Path, file_id: str, *, error: str | None = None) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE files
            SET status = 'review', error = ?
            WHERE id = ?
            """,
            (error, file_id),
        )


def mark_ready(
    db_path: Path,
    file_id: str,
    *,
    extracted_json: dict[str, Any],
    confidence: float,
    status: str = "ready",
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE files
            SET status = ?, extracted_json = ?, confidence = ?, error = NULL
            WHERE id = ?
            """,
            (status, json.dumps(extracted_json, ensure_ascii=False), confidence, file_id),
        )


def mark_failed(db_path: Path, file_id: str, *, error: str) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            UPDATE files
            SET status = 'failed', error = ?
            WHERE id = ?
            """,
            (error, file_id),
        )
