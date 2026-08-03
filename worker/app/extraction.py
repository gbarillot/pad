from __future__ import annotations

import base64
from io import BytesIO
import os
import re
from pathlib import Path

from app.models import RawExtraction
from app.ollama import OllamaError, format_as_json
from app.pdf import extract_pdf_text
from app.runtime_tools import poppler_path
from app.validation import (
    DEFAULT_SCHEMA,
    HCG_LABEL_PATTERN,
    HCG_NUMBER_PATTERN,
    HCG_OPERATOR_PATTERN,
    HCG_UNIT_PATTERN,
    prepare_extraction_text,
)
from pdf2image import convert_from_path
from PIL import Image


DEFAULT_MODEL = "qwen3-vl:8b-instruct"
LEGACY_DEFAULT_MODELS = {"llama3.2"}
VISION_DPI = int(os.getenv("VISION_DPI", "200"))
VISION_MAX_PAGES = int(os.getenv("VISION_MAX_PAGES", "3"))
VISION_MAX_IMAGE_DIMENSION = int(os.getenv("VISION_MAX_IMAGE_DIMENSION", "1280"))
OCR_HINT_MAX_CHARS = int(os.getenv("OCR_HINT_MAX_CHARS", "2500"))
OCR_HINT_RETRY_MAX_CHARS = int(os.getenv("OCR_HINT_RETRY_MAX_CHARS", "1200"))

DEFAULT_INSTRUCTIONS = """Extract data from a medical biology laboratory report.
Return the rich nested JSON shape from the schema, not the old flat format.
patient.first_name is the patient's first name.
patient.last_name is the patient's last name.
patient.birth_date is the patient's birth date, formatted as YYYY-MM-DD when possible.
laboratory.name is the name of the laboratory that produced the report.
analysis.date is the date of the test, sample collection, or report, formatted as YYYY-MM-DD when possible.
analysis.method is the test name or assay method used for the HCG result. If no assay method is present, use the HCG or beta-HCG test name.
analysis.anteriority is the previous HCG result if the report mentions a prior or anterior result. Return it as null when no previous result is found.
When analysis.anteriority is present, return exactly these fields: date, value, operator.
analysis.anteriority.date is the date of the previous test, formatted as YYYY-MM-DD when possible.
analysis.anteriority.value is the previous HCG value without comparison operator and without unit.
analysis.anteriority.operator is the previous HCG comparison operator, usually <, >, or =. Use = when no operator is present.
For patient identity, look for French labels such as Patient, Nom, Nom de naissance,
Nom / prénom, Prénom, Né(e) le, Date de naissance, or equivalent OCR variants.
When a line says "Nom / prénom DUCHMOL test", DUCHMOL is last_name and test is first_name.
Do not use the laboratory name, doctor name, prescriber name, biologist name, or recipient as patient identity.
Do not use the doctor name, prescriber name, biologist name, patient name, or recipient as laboratory.name.
analysis.result.value is the HCG or beta-HCG dosage value without comparison operator and without unit.
analysis.result.operator is the HCG comparison operator, usually < or >. Use = when no operator is present.
analysis.result.unit is the HCG measurement unit, for example UI/L or mUI/mL.
analysis.result.target must be HCG or beta-HCG.
Treat all HCG wording variants as the same target: HCG, hCG, beta-HCG, β-HCG, b-HCG, BHCG, dosage de l'HCG plasmatique, HCG plasmatique, hormone chorionique gonadotrope, gonadotrophine chorionique, or pregnancy hormone.
When the report labels the analysis as "DOSAGE DE L'HCG PLASMATIQUE" or an equivalent wording, keep that wording in analysis.name/method and still extract analysis.result.target as HCG.
For HCG text such as "< 5 UI/L", return value "5", operator "<", and unit "UI/L".
For HCG text such as "124 mUI/mL", return value "124", operator "=", and unit "mUI/mL".
Ignore parenthesized reference or reminder values on the same line as the actual result.
For HCG text such as "Taux 6 mUI/mL (<5) 10", return value "6", operator "=", and unit "mUI/mL".
extraction.confidence is your extraction confidence from 0 to 1. Use high confidence when values are explicitly supported by OCR text, and lower confidence only when critical values are unreadable or inferred.
extraction.warnings should list notable ambiguity or OCR quality issues. Return an empty list when there are none.
Final confidence rules: use 1.0 only when patient identity, birth date, analysis date, HCG value, operator, and unit are all explicit and unambiguous.
Cap confidence at 0.85 when either first_name or last_name is missing, 0.55 when both are missing, 0.85 when analysis.date is missing, 0.90 when patient.birth_date is missing, 0.70 when HCG unit is missing, and 0.25 when HCG value is missing.
Set confidence to 0.0 only when the numeric HCG value is strictly lower than 0.2, regardless of operator. Do not lower confidence for "< 0.2" because the numeric value is 0.2."""
OCR_RETRY_INSTRUCTIONS = f"""{DEFAULT_INSTRUCTIONS}

This is a second pass over noisy OCR text from a poor-quality French medical biology PDF.
Correct common OCR mistakes before extracting values: O/0, I/l/1, S/5, Ul/UI, mUl/mUI, Prén0m/Prénom, N0m/Nom, H C G/HCG.
Prefer a plausible patient identity and beta-HCG value supported by nearby text over null values.
Keep the same rich nested JSON shape. Do not return the old flat fields at the top level. Do not return explanations outside JSON."""


def resolve_default_model() -> str:
    model = os.getenv("OLLAMA_VISION_MODEL") or os.getenv("OLLAMA_MODEL")
    if not model or model in LEGACY_DEFAULT_MODELS:
        return DEFAULT_MODEL
    return model


def extract_pdf_model_result(
    pdf_path: Path,
    *,
    model: str,
    ollama_base_url: str,
    ocr_language: str,
    instructions: str = DEFAULT_INSTRUCTIONS,
    max_ocr_hint_chars: int = OCR_HINT_MAX_CHARS,
    image_limit: int | None = None,
) -> RawExtraction:
    raw_text = extract_pdf_text(pdf_path, ocr_language=ocr_language)
    text = prepare_extraction_text(raw_text)
    images = build_vision_images(pdf_path, model=model, ocr_language=ocr_language)
    if image_limit is not None:
        images = images[:image_limit]
    return RawExtraction(
        text=text,
        model_result=format_as_json(
            text=build_ocr_hint_text(text, max_chars=max_ocr_hint_chars),
            instructions=instructions,
            schema=DEFAULT_SCHEMA,
            model=model,
            ollama_base_url=ollama_base_url,
            images=images,
        ),
    )


def build_ocr_hint_text(text: str, *, max_chars: int = OCR_HINT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    selected_indices = set(range(min(12, len(lines))))
    for index, line in enumerate(lines):
        if is_relevant_ocr_hint_line(line):
            selected_indices.update(range(max(0, index - 1), min(len(lines), index + 2)))

    selected: list[str] = []
    char_count = 0
    for index in sorted(selected_indices):
        line = lines[index]
        next_count = char_count + len(line) + 1
        if next_count > max_chars:
            break
        selected.append(line)
        char_count = next_count

    return "\n".join(selected) if selected else text[:max_chars]


def is_relevant_ocr_hint_line(line: str) -> bool:
    return bool(
        re.search(HCG_LABEL_PATTERN, line)
        or re.search(HCG_UNIT_PATTERN, line, flags=re.I)
        or re.search(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}", line)
        or re.search(
            r"(?i)\b(?:patient|nom|pr[eé]nom|naissance|laboratoire|lab|"
            r"r[eé]sultat|dosage|pr[eé]l[eè]vement|ant[eé]riorit[eé]|"
            r"ant[eé]rieur|pr[eé]c[eé]dent|dossier)\b",
            line,
        )
    )


def is_context_size_error(error: OllamaError) -> bool:
    message = str(error).casefold()
    return "exceed_context_size" in message or "exceeds the available context size" in message


def build_vision_images(
    pdf_path: Path,
    *,
    model: str,
    ocr_language: str,
) -> list[str]:
    del model, ocr_language
    pages = convert_from_path(
        str(pdf_path),
        dpi=VISION_DPI,
        first_page=1,
        last_page=max(1, VISION_MAX_PAGES),
        fmt="png",
        thread_count=2,
        poppler_path=poppler_path(),
    )
    return [encode_vision_image(page) for page in pages]


def encode_vision_image(image: Image.Image) -> str:
    image = resize_vision_image(image)
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def resize_vision_image(image: Image.Image) -> Image.Image:
    max_dimension = max(1, VISION_MAX_IMAGE_DIMENSION)
    if max(image.size) <= max_dimension:
        return image
    resized = image.copy()
    resized.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    return resized
