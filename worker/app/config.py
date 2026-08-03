from __future__ import annotations

import os
from pathlib import Path


WORKER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = WORKER_ROOT.parent
PERSISTENCE_ROOT = PROJECT_ROOT.parent / "persistence"
DEFAULT_DATABASE_PATH = PERSISTENCE_ROOT / "data" / "pad_development.sqlite3"
DEFAULT_FILES_DIR = Path("~/Desktop/fichiers_pad")


def load_env_file() -> None:
    if os.getenv("PAD_DISABLE_ENV_FILE", "").lower() in {"1", "true", "yes", "on"}:
        return

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url() -> str:
    return os.getenv("DATABASE_URL") or f"sqlite:///{DEFAULT_DATABASE_PATH}"


def files_dir() -> Path:
    value = os.getenv("FILES_DIR") or os.getenv("PAD_FILES_DIR") or str(DEFAULT_FILES_DIR)
    return Path(value).expanduser()


def ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434"


def ocr_language() -> str:
    return os.getenv("OCR_LANGUAGE") or "fra+eng"


def poll_interval() -> float:
    return float(os.getenv("POLL_INTERVAL") or "1.0")


load_env_file()
