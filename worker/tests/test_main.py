from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import main
from app.ollama import OllamaError


def raw_result(confidence: float, *, text: str = "Beta HCG = 12 UI/L") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        model_result={
            "patient": {"first_name": "Camille", "last_name": "DUCHMOL", "birth_date": "01/01/2000"},
            "analysis": {"date": "02/01/2024", "result": {"value": "12", "operator": "=", "unit": "UI/L"}},
            "extraction": {"confidence": confidence},
        },
    )


def test_extract_pdf_only_returns_first_result_when_confident(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_extract(*args, **kwargs):
        calls.append(kwargs)
        return raw_result(0.99)

    monkeypatch.setattr(main, "extract_pdf_model_result", fake_extract)


    result = main.extract_pdf_only(
        tmp_path / "sample.pdf",
        model="llama",
        ollama_base_url="http://ollama.local",
        ocr_language="eng",
    )

    assert len(calls) == 1
    assert result["extraction"]["confidence"] >= 0.9  # type: ignore[index]


def test_extract_pdf_only_retries_and_keeps_better_result(monkeypatch, tmp_path: Path) -> None:
    responses = [raw_result(0.4), raw_result(0.95)]

    def fake_extract(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(main, "extract_pdf_model_result", fake_extract)

    result = main.extract_pdf_only(
        tmp_path / "sample.pdf",
        model="llama",
        ollama_base_url="http://ollama.local",
        ocr_language="eng",
    )

    assert result["extraction"]["confidence"] >= 0.9  # type: ignore[index]


def test_extract_pdf_only_returns_first_result_on_retry_context_error(monkeypatch, tmp_path: Path) -> None:
    first = raw_result(0.4)

    def fake_extract(*args, **kwargs):
        if "instructions" in kwargs:
            raise OllamaError("context length exceeded")
        return first

    monkeypatch.setattr(main, "extract_pdf_model_result", fake_extract)
    monkeypatch.setattr(main, "is_context_size_error", lambda exc: True)

    result = main.extract_pdf_only(
        tmp_path / "sample.pdf",
        model="llama",
        ollama_base_url="http://ollama.local",
        ocr_language="eng",
    )

    assert result["extraction"]["confidence"] < 0.9  # type: ignore[index]


def test_extract_pdf_only_reraises_non_context_retry_error(monkeypatch, tmp_path: Path) -> None:
    def fake_extract(*args, **kwargs):
        if "instructions" in kwargs:
            raise OllamaError("server unavailable")
        return raw_result(0.4)

    monkeypatch.setattr(main, "extract_pdf_model_result", fake_extract)
    monkeypatch.setattr(main, "is_context_size_error", lambda exc: False)

    with pytest.raises(OllamaError, match="server unavailable"):
        main.extract_pdf_only(
            tmp_path / "sample.pdf",
            model="llama",
            ollama_base_url="http://ollama.local",
            ocr_language="eng",
        )


def test_extract_pdf_default_result_can_include_raw_text(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main, "extract_pdf_only", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(main, "extract_pdf_text", lambda *args, **kwargs: "Pr6nom\n8-h.c.g")

    result = main.extract_pdf_default_result(
        tmp_path / "sample.pdf",
        model="llama",
        ollama_base_url="http://ollama.local",
        ocr_language="eng",
        include_raw_text=True,
    )

    assert result["ok"] is True
    assert "hcg" in result["raw_text"]
