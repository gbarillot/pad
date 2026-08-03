from __future__ import annotations

import os
import sys
from pathlib import Path

import pytesseract


def bundled_tools_root() -> Path | None:
    value = os.getenv("PAD_WORKER_TOOLS_DIR")
    if value:
        return Path(value)

    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "tools"
        if candidate.exists():
            return candidate

    return None


def configure_runtime_tools() -> None:
    tools_root = bundled_tools_root()
    if not tools_root:
        return

    tesseract_bin = tools_root / "bin" / "tesseract"
    if tesseract_bin.exists():
        pytesseract.pytesseract.tesseract_cmd = str(tesseract_bin)

    tessdata_dir = tools_root / "share" / "tessdata"
    if tessdata_dir.exists():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata_dir))

    bin_dir = tools_root / "bin"
    if bin_dir.exists():
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    lib_dir = tools_root / "lib"
    if lib_dir.exists():
        existing = os.environ.get("DYLD_LIBRARY_PATH", "")
        os.environ["DYLD_LIBRARY_PATH"] = f"{lib_dir}{os.pathsep}{existing}" if existing else str(lib_dir)


def poppler_path() -> str | None:
    tools_root = bundled_tools_root()
    if not tools_root:
        return None

    bin_dir = tools_root / "bin"
    return str(bin_dir) if bin_dir.exists() else None


def packaged_runtime() -> bool:
    return bundled_tools_root() is not None
