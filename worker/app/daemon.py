#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import database_url, files_dir, ocr_language, ollama_base_url, poll_interval
from app.extraction import resolve_default_model
from app.main import extract_pdf_only
from app.repository import auto_cleanup, claim_file, claim_ready_file, delete_file_tracking, has_active_files, list_ready_files, list_todo_files, manual_mode, mark_failed, mark_ready, mark_review, mark_saved, min_confidence, reset_interrupted_files, set_running, sqlite_path_from_url
from app.transfer import PatientMatchError, transfer_payload
from app.validation import result_confidence


DATABASE_URL = database_url()
FILES_DIR = files_dir()
POLL_INTERVAL = poll_interval()
OLLAMA_BASE_URL = ollama_base_url()
OCR_LANGUAGE = ocr_language()


def main() -> None:
    db_path = sqlite_path_from_url(DATABASE_URL)
    print(f"polling {db_path} every {POLL_INTERVAL:g}s", flush=True)
    recovered_count = reset_interrupted_files(db_path)
    if recovered_count:
        print(f"requeued {recovered_count} interrupted file(s)", flush=True)
    while True:
        try:
            process_pending_files(db_path)
        except Exception as exc:  # noqa: BLE001 - keep the daemon alive after unexpected failures.
            print(f"worker loop error: {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_INTERVAL)


def process_pending_files(db_path: Path) -> None:
    for row in list_todo_files(db_path):
        process_extraction(db_path, file_id=row["id"], name=row["name"])
    for row in list_ready_files(db_path):
        process_transfer(db_path, file_id=row["id"], name=row["name"], extracted_json=row["extracted_json"])
    if not has_active_files(db_path):
        set_running(db_path, False)


def process_extraction(db_path: Path, *, file_id: str, name: str) -> None:
    if not claim_file(db_path, file_id):
        return

    pdf_path = FILES_DIR / name
    try:
        result = extract_file(pdf_path)
        confidence = result_confidence(result)
        status = "review" if manual_mode(db_path) or confidence < min_confidence(db_path) else "ready"
        mark_ready(db_path, file_id, extracted_json=result, confidence=confidence, status=status)
    except Exception as exc:  # noqa: BLE001 - persist extraction failures in the DB.
        mark_failed(db_path, file_id, error=str(exc))


def process_transfer(db_path: Path, *, file_id: str, name: str, extracted_json: str) -> None:
    if not claim_ready_file(db_path, file_id):
        return

    try:
        transfer_payload(load_extracted_json(extracted_json), should_import=True)
        mark_saved(db_path, file_id)
        if auto_cleanup(db_path):
            cleanup_processed_file(db_path, file_id=file_id, name=name)
    except PatientMatchError as exc:
        mark_review(db_path, file_id, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - persist transfer failures in the DB.
        mark_failed(db_path, file_id, error=str(exc))


def cleanup_processed_file(db_path: Path, *, file_id: str, name: str) -> None:
    try:
        (FILES_DIR / name).unlink(missing_ok=True)
        delete_file_tracking(db_path, file_id)
    except Exception as exc:  # noqa: BLE001 - transfer already succeeded; do not requeue it.
        print(f"auto-cleanup failed for {name}: {exc}", file=sys.stderr, flush=True)


def load_extracted_json(value: str) -> dict[str, object]:
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("extracted_json must contain a JSON object")
    return payload


def extract_file(pdf_path: Path) -> dict[str, object]:
    model = resolve_default_model()
    return extract_pdf_only(
        pdf_path,
        model=model,
        ollama_base_url=OLLAMA_BASE_URL,
        ocr_language=OCR_LANGUAGE,
    )


if __name__ == "__main__":
    main()
