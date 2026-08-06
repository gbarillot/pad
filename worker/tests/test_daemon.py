from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app import daemon
from app.transfer import PatientMatchError

from conftest import create_configuration, event_statuses, fetch_file, insert_file


def test_process_extraction_marks_ready_when_confident(monkeypatch, db_path: Path, tmp_path: Path) -> None:
    create_configuration(db_path, manual_mode=0, min_confidence=0.8)
    file_id = insert_file(db_path, name="ready.pdf", status="todo")
    monkeypatch.setattr(daemon, "FILES_DIR", tmp_path)
    monkeypatch.setattr(
        daemon,
        "extract_file",
        lambda pdf_path: {"extraction": {"confidence": 0.95}, "source": str(pdf_path.name)},
    )

    daemon.process_extraction(db_path, file_id=file_id, name="ready.pdf")

    row = fetch_file(db_path, file_id)
    assert row is not None
    assert row["status"] == "ready"
    assert row["confidence"] == 0.95
    assert json.loads(row["extracted_json"])["source"] == "ready.pdf"


def test_process_extraction_marks_review_for_manual_mode(monkeypatch, db_path: Path, tmp_path: Path) -> None:
    create_configuration(db_path, manual_mode=1, min_confidence=0.1)
    file_id = insert_file(db_path, name="manual.pdf", status="todo")
    monkeypatch.setattr(daemon, "FILES_DIR", tmp_path)
    monkeypatch.setattr(daemon, "extract_file", lambda pdf_path: {"extraction": {"confidence": 1.0}})

    daemon.process_extraction(db_path, file_id=file_id, name="manual.pdf")

    assert fetch_file(db_path, file_id)["status"] == "review"  # type: ignore[index]


def test_process_extraction_marks_review_when_confidence_is_low(monkeypatch, db_path: Path, tmp_path: Path) -> None:
    create_configuration(db_path, manual_mode=0, min_confidence=0.9)
    file_id = insert_file(db_path, name="low.pdf", status="todo")
    monkeypatch.setattr(daemon, "FILES_DIR", tmp_path)
    monkeypatch.setattr(daemon, "extract_file", lambda pdf_path: {"extraction": {"confidence": 0.5}})

    daemon.process_extraction(db_path, file_id=file_id, name="low.pdf")

    assert fetch_file(db_path, file_id)["status"] == "review"  # type: ignore[index]


def test_process_extraction_marks_failed_on_exception(monkeypatch, db_path: Path, tmp_path: Path) -> None:
    create_configuration(db_path)
    file_id = insert_file(db_path, name="broken.pdf", status="todo")
    monkeypatch.setattr(daemon, "FILES_DIR", tmp_path)

    def fail(_pdf_path: Path) -> dict[str, object]:
        raise RuntimeError("ocr failed")

    monkeypatch.setattr(daemon, "extract_file", fail)

    daemon.process_extraction(db_path, file_id=file_id, name="broken.pdf")

    row = fetch_file(db_path, file_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "ocr failed"


def test_process_transfer_marks_saved_when_cleanup_is_disabled(monkeypatch, db_path: Path) -> None:
    create_configuration(db_path, auto_cleanup=0)
    file_id = insert_file(db_path, status="ready", extracted_json={"ok": True})
    monkeypatch.setattr(daemon, "transfer_payload", lambda payload, should_import: None)

    daemon.process_transfer(db_path, file_id=file_id, name="sample.pdf", extracted_json=json.dumps({"ok": True}))

    assert fetch_file(db_path, file_id)["status"] == "saved"  # type: ignore[index]


def test_process_transfer_cleans_up_after_success_when_enabled(monkeypatch, db_path: Path, tmp_path: Path) -> None:
    create_configuration(db_path, auto_cleanup=1)
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "sample.pdf").write_bytes(b"pdf")
    file_id = insert_file(db_path, name="sample.pdf", status="ready", extracted_json={"ok": True})
    monkeypatch.setattr(daemon, "FILES_DIR", files_dir)
    monkeypatch.setattr(daemon, "transfer_payload", lambda payload, should_import: None)

    daemon.process_transfer(db_path, file_id=file_id, name="sample.pdf", extracted_json=json.dumps({"ok": True}))

    assert (files_dir / "sample.pdf").exists() is False
    assert fetch_file(db_path, file_id) is None
    assert event_statuses(db_path, file_id) == []


def test_process_transfer_marks_review_on_patient_match_error(monkeypatch, db_path: Path) -> None:
    create_configuration(db_path)
    file_id = insert_file(db_path, status="ready", extracted_json={"ok": True})

    def fail(_payload: dict[str, object], should_import: bool) -> None:
        raise PatientMatchError("ambiguous patient")

    monkeypatch.setattr(daemon, "transfer_payload", fail)

    daemon.process_transfer(db_path, file_id=file_id, name="sample.pdf", extracted_json=json.dumps({"ok": True}))

    row = fetch_file(db_path, file_id)
    assert row is not None
    assert row["status"] == "review"
    assert row["error"] == "ambiguous patient"


def test_process_transfer_marks_failed_on_unexpected_error(monkeypatch, db_path: Path) -> None:
    create_configuration(db_path)
    file_id = insert_file(db_path, status="ready", extracted_json={"ok": True})

    def fail(_payload: dict[str, object], should_import: bool) -> None:
        raise RuntimeError("redcap unavailable")

    monkeypatch.setattr(daemon, "transfer_payload", fail)

    daemon.process_transfer(db_path, file_id=file_id, name="sample.pdf", extracted_json=json.dumps({"ok": True}))

    row = fetch_file(db_path, file_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "redcap unavailable"


def test_process_pending_files_clears_running_when_no_active_files(monkeypatch, db_path: Path) -> None:
    create_configuration(db_path, running=1)
    monkeypatch.setattr(daemon, "process_extraction", lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon, "process_transfer", lambda *args, **kwargs: None)

    daemon.process_pending_files(db_path)

    with closing(sqlite3.connect(db_path)) as connection:
        running = connection.execute("SELECT running FROM configuration").fetchone()[0]
    assert running == 0


def test_process_pending_files_processes_todo_and_ready_files(monkeypatch, db_path: Path) -> None:
    create_configuration(db_path, running=1)
    todo_id = insert_file(db_path, name="todo.pdf", status="todo")
    ready_id = insert_file(db_path, name="ready.pdf", status="ready", extracted_json={"ok": True})
    calls = []

    monkeypatch.setattr(
        daemon,
        "process_extraction",
        lambda db_path, **kwargs: calls.append(("extract", kwargs["file_id"], kwargs["name"])),
    )
    monkeypatch.setattr(
        daemon,
        "process_transfer",
        lambda db_path, **kwargs: calls.append(("transfer", kwargs["file_id"], kwargs["name"])),
    )

    daemon.process_pending_files(db_path)

    assert calls == [("extract", todo_id, "todo.pdf"), ("transfer", ready_id, "ready.pdf")]


def test_process_extraction_returns_when_file_cannot_be_claimed(monkeypatch, db_path: Path) -> None:
    create_configuration(db_path)
    file_id = insert_file(db_path, status="ready", extracted_json={"ok": True})
    called = False

    def extract(_pdf_path: Path) -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(daemon, "extract_file", extract)

    daemon.process_extraction(db_path, file_id=file_id, name="sample.pdf")

    assert called is False


def test_process_transfer_returns_when_file_cannot_be_claimed(monkeypatch, db_path: Path) -> None:
    create_configuration(db_path)
    file_id = insert_file(db_path, status="review", extracted_json={"ok": True})
    called = False

    def transfer(_payload: dict[str, object], should_import: bool) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(daemon, "transfer_payload", transfer)

    daemon.process_transfer(db_path, file_id=file_id, name="sample.pdf", extracted_json=json.dumps({"ok": True}))

    assert called is False


def test_cleanup_processed_file_logs_but_does_not_raise(monkeypatch, capsys, db_path: Path, tmp_path: Path) -> None:
    monkeypatch.setattr(daemon, "FILES_DIR", tmp_path)

    def fail(_db_path: Path, _file_id: str) -> None:
        raise RuntimeError("delete failed")

    monkeypatch.setattr(daemon, "delete_file_tracking", fail)

    daemon.cleanup_processed_file(db_path, file_id="id", name="missing.pdf")

    assert "auto-cleanup failed for missing.pdf: delete failed" in capsys.readouterr().err


def test_extract_file_uses_resolved_model(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(daemon, "resolve_default_model", lambda: "resolved-model")
    monkeypatch.setattr(daemon, "OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setattr(daemon, "OCR_LANGUAGE", "eng")

    def extract(pdf_path: Path, *, model: str, ollama_base_url: str, ocr_language: str) -> dict[str, object]:
        return {
            "path": pdf_path.name,
            "model": model,
            "ollama_base_url": ollama_base_url,
            "ocr_language": ocr_language,
        }

    monkeypatch.setattr(daemon, "extract_pdf_only", extract)

    assert daemon.extract_file(tmp_path / "sample.pdf") == {
        "path": "sample.pdf",
        "model": "resolved-model",
        "ollama_base_url": "http://ollama.local",
        "ocr_language": "eng",
    }


def test_main_recovers_and_survives_worker_loop_errors(monkeypatch, capsys, tmp_path: Path) -> None:
    calls = {"sleep": 0, "process": 0}
    monkeypatch.setattr(daemon, "DATABASE_URL", f"sqlite:///{tmp_path / 'pad.sqlite3'}")
    monkeypatch.setattr(daemon, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(daemon, "reset_interrupted_files", lambda db_path: 2)

    def process(_db_path: Path) -> None:
        calls["process"] += 1
        raise RuntimeError("loop failed")

    def sleep(_seconds: float) -> None:
        calls["sleep"] += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon, "process_pending_files", process)
    monkeypatch.setattr(daemon.time, "sleep", sleep)

    try:
        daemon.main()
    except KeyboardInterrupt:
        pass

    captured = capsys.readouterr()
    assert "requeued 2 interrupted file(s)" in captured.out
    assert "worker loop error: loop failed" in captured.err
    assert calls == {"sleep": 1, "process": 1}


def test_load_extracted_json_rejects_non_object() -> None:
    assert daemon.load_extracted_json('{"ok": true}') == {"ok": True}

    try:
        daemon.load_extracted_json("[]")
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
