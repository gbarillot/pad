from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pypdf.errors import PyPdfError
from pypdf import PdfReader


MIN_TEXT_CHARS_PER_PAGE = 80
MIN_GOOD_TEXT_SCORE = 7
OCR_DPI = int(os.getenv("OCR_DPI", "400"))
TESSERACT_CONFIGS = (
    "--oem 1 --psm 6",
    "--oem 1 --psm 11",
)


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str
    source: str


def extract_pdf_text(pdf_path: Path, *, ocr_language: str) -> str:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    embedded_text = _extract_embedded_pdf_text(pdf_path, source="embedded-text")
    if _is_good_enough_text(embedded_text):
        return embedded_text

    candidates = [embedded_text] if embedded_text.strip() else []

    ocrmypdf_text = _extract_with_ocrmypdf(pdf_path, ocr_language=ocr_language)
    if ocrmypdf_text:
        candidates.append(ocrmypdf_text)
        if _is_good_enough_text(ocrmypdf_text):
            return ocrmypdf_text

    direct_ocr_text = _extract_with_direct_tesseract(pdf_path, ocr_language=ocr_language)
    if direct_ocr_text:
        candidates.append(direct_ocr_text)

    if not candidates:
        return ""
    return max(candidates, key=_candidate_score)


def _extract_embedded_pdf_text(pdf_path: Path, *, source: str) -> str:
    try:
        reader = PdfReader(str(pdf_path))
    except PyPdfError:
        return ""
    pages: list[PageText] = []

    for index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except PyPdfError:
            continue
        if text:
            pages.append(PageText(index, text, source))

    return _join_pages(pages)


def _extract_with_ocrmypdf(pdf_path: Path, *, ocr_language: str) -> str | None:
    if not shutil.which("ocrmypdf"):
        return None

    with tempfile.TemporaryDirectory(prefix="pad-ocrmypdf-") as temp_dir:
        output_path = Path(temp_dir) / "ocr.pdf"
        for text_mode in ("--redo-ocr", "--force-ocr"):
            command = [
                "ocrmypdf",
                text_mode,
                "--rotate-pages",
                "--deskew",
                "--clean",
                "--optimize",
                "0",
                "--jobs",
                "2",
                "--tesseract-timeout",
                "120",
                "-l",
                _normalize_tesseract_language(ocr_language),
                str(pdf_path),
                str(output_path),
            ]
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
                check=False,
            )
            if result.returncode == 0 and output_path.exists():
                return _extract_embedded_pdf_text(output_path, source="ocrmypdf")

        print("ocrmypdf failed; falling back to direct Tesseract OCR", file=sys.stderr, flush=True)
        return None


def _extract_with_direct_tesseract(pdf_path: Path, *, ocr_language: str) -> str | None:
    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    if page_count == 0:
        return None

    images = convert_from_path(
        str(pdf_path),
        dpi=OCR_DPI,
        fmt="png",
        thread_count=2,
    )
    pages: list[PageText] = []
    for page_number, image in enumerate(images, start=1):
        text = _best_tesseract_text(image, ocr_language=ocr_language)
        if text:
            pages.append(PageText(page_number, text, "ocr-tesseract"))

    return _join_pages(pages) if pages else None


def _best_tesseract_text(image: Image.Image, *, ocr_language: str) -> str:
    image = _auto_orient_image(image, ocr_language=ocr_language)
    best_text = ""
    best_score = (-1, -1)

    for candidate in _preprocessed_images(image):
        for config in TESSERACT_CONFIGS:
            try:
                text = pytesseract.image_to_string(
                    candidate,
                    lang=ocr_language,
                    config=config,
                ).strip()
            except pytesseract.TesseractError:
                continue
            score = _candidate_score(text)
            if score > best_score:
                best_text = text
                best_score = score
            if _is_good_enough_text(text):
                return text

    return best_text


def _auto_orient_image(image: Image.Image, *, ocr_language: str) -> Image.Image:
    try:
        osd = pytesseract.image_to_osd(image, lang=ocr_language, config="--psm 0")
    except pytesseract.TesseractError:
        return image

    match = re.search(r"(?m)^Rotate:\s*(\d+)", osd)
    if not match:
        return image
    angle = int(match.group(1))
    if angle == 0:
        return image
    return image.rotate(-angle, expand=True)


def _preprocessed_images(image: Image.Image) -> list[Image.Image]:
    image = _upscale_small_image(image)
    grayscale = ImageOps.grayscale(image)
    grayscale = ImageOps.autocontrast(grayscale)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(2.0)
    grayscale = ImageEnhance.Sharpness(grayscale).enhance(1.5)

    denoised = grayscale.filter(ImageFilter.MedianFilter(size=3))
    binary = denoised.point(lambda value: 255 if value > 170 else 0)

    return [image, grayscale, binary]


def _upscale_small_image(image: Image.Image) -> Image.Image:
    width, height = image.size
    if max(width, height) >= 2400:
        return image
    return image.resize((width * 2, height * 2), Image.Resampling.LANCZOS)


def _join_pages(pages: list[PageText]) -> str:
    blocks = [
        _page_block(page.page_number, page.text, page.source)
        for page in sorted(pages, key=lambda page: page.page_number)
        if page.text.strip()
    ]
    return "\n\n".join(blocks)


def _is_good_enough_text(text: str) -> bool:
    return _text_quality_score(text) >= MIN_GOOD_TEXT_SCORE


def _candidate_score(text: str) -> tuple[int, int]:
    return (_text_quality_score(text), len(text))


def _text_quality_score(text: str) -> int:
    normalized = _normalize_for_scoring(text)
    score = 0
    text_length = len(re.sub(r"\s+", "", normalized))

    if text_length >= MIN_TEXT_CHARS_PER_PAGE:
        score += 1
    if text_length >= 300:
        score += 1
    if text_length >= 800:
        score += 1

    if re.search(r"\b(?:beta|b)?\s*h\s*c\s*g\b|\bbhcg\b", normalized):
        score += 4
    if re.search(r"(?:<|>|=|≤|≥)?\s*\d+(?:[,.]\d+)?\s*(?:m?u[i1l]?\s*/?\s*m?l|m?ui\s*/\s*l)", normalized):
        score += 2
    if re.search(r"\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}", normalized):
        score += 2
    if re.search(r"\b(?:patient|nom|prenom|naissance|ne le)\b", normalized):
        score += 2

    return score


def _normalize_for_scoring(text: str) -> str:
    normalized = text.casefold()
    normalized = normalized.replace("µ", "u").replace("μ", "u")
    normalized = re.sub(r"\bpr[eéè]?n[o0]m\b", "prenom", normalized)
    normalized = re.sub(r"\bn[o0]m\b", "nom", normalized)
    normalized = re.sub(r"\bn[eéè]\s*\(?\s*e?\s*\)?\s*le\b", "ne le", normalized)
    normalized = re.sub(r"\b(?:β|8|b)\s*[-.]?\s*h\s*[. -]?\s*c\s*[. -]?\s*g\b", "beta hcg", normalized)
    normalized = re.sub(r"\bh\s*[. -]?\s*c\s*[. -]?\s*g\b", "hcg", normalized)
    return normalized


def _normalize_tesseract_language(ocr_language: str) -> str:
    languages = [language for language in ocr_language.split("+") if language]
    if "fra" in languages:
        languages.remove("fra")
        languages.insert(0, "fra")
    return "+".join(languages) or "fra+eng"


def _page_block(page_number: int, text: str, source: str) -> str:
    return f"--- page {page_number} ({source}) ---\n{text}"
