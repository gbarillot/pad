from __future__ import annotations

import os
from pathlib import Path

from app.extraction import (
    OCR_HINT_RETRY_MAX_CHARS,
    OCR_RETRY_INSTRUCTIONS,
    extract_pdf_model_result,
    is_context_size_error,
    resolve_default_model,
)
from app.ollama import OllamaError
from app.pdf import extract_pdf_text
from app.validation import choose_better_result, normalize_default_result, prepare_extraction_text, result_confidence


LOW_CONFIDENCE_RETRY_THRESHOLD = float(os.getenv("LOW_CONFIDENCE_RETRY_THRESHOLD", "0.9"))


def extract_pdf_only(
    pdf_path: Path,
    *,
    model: str | None = None,
    ollama_base_url: str,
    ocr_language: str,
) -> dict[str, object]:
    model = model or resolve_default_model()
    raw = extract_pdf_model_result(
        pdf_path,
        model=model,
        ollama_base_url=ollama_base_url,
        ocr_language=ocr_language,
    )
    result = normalize_default_result(raw.model_result, raw.text)
    if result_confidence(result) >= LOW_CONFIDENCE_RETRY_THRESHOLD:
        return result

    try:
        retry_raw = extract_pdf_model_result(
            pdf_path,
            model=model,
            ollama_base_url=ollama_base_url,
            ocr_language=ocr_language,
            instructions=OCR_RETRY_INSTRUCTIONS,
            max_ocr_hint_chars=OCR_HINT_RETRY_MAX_CHARS,
            image_limit=1,
        )
    except OllamaError as exc:
        if not is_context_size_error(exc):
            raise
        return result

    retry_result = normalize_default_result(retry_raw.model_result, retry_raw.text)
    return choose_better_result(result, retry_result)


def extract_pdf_default_result(
    pdf_path: Path,
    *,
    model: str,
    ollama_base_url: str,
    ocr_language: str,
    include_raw_text: bool = False,
) -> dict[str, object]:
    result = extract_pdf_only(
        pdf_path,
        model=model,
        ollama_base_url=ollama_base_url,
        ocr_language=ocr_language,
    )
    if include_raw_text:
        result["raw_text"] = prepare_extraction_text(
            extract_pdf_text(pdf_path, ocr_language=ocr_language)
        )
    return result
