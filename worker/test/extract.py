#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from app.config import ocr_language, ollama_base_url
from app.extraction import resolve_default_model
from app.main import extract_pdf_only

DEFAULT_INPUT_DIR = Path(__file__).resolve().parent / "input"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
DEFAULT_OLLAMA_BASE_URL = ollama_base_url()
DEFAULT_OCR_LANGUAGE = ocr_language()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run worker extraction on every file in test/input and write JSON to test/output."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    files = [path for path in sorted(input_dir.iterdir()) if is_input_file(path)]
    if not files:
        print(f"No input files found in {input_dir}")
        return

    for input_path in files:
        output_path = output_dir / f"{input_path.stem}.json"
        print(f"process {input_path.name} -> {output_path.name}")
        payload = extract_pdf_only(
            input_path,
            model=resolve_default_model(),
            ollama_base_url=DEFAULT_OLLAMA_BASE_URL,
            ocr_language=DEFAULT_OCR_LANGUAGE,
        )
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def is_input_file(path: Path) -> bool:
    return path.is_file() and not path.name.startswith(".")


if __name__ == "__main__":
    main()
