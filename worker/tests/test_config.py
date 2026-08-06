from __future__ import annotations

from app import config


def test_config_defaults(monkeypatch) -> None:
    for name in ("DATABASE_URL", "FILES_DIR", "PAD_FILES_DIR", "OLLAMA_BASE_URL", "OCR_LANGUAGE", "POLL_INTERVAL"):
        monkeypatch.delenv(name, raising=False)

    assert config.database_url().startswith("sqlite:///")
    assert config.files_dir().name == "fichiers_pad"
    assert config.ollama_base_url() == "http://localhost:11434"
    assert config.ocr_language() == "fra+eng"
    assert config.poll_interval() == 1.0


def test_config_environment_overrides(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///custom.sqlite3")
    monkeypatch.setenv("FILES_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.local")
    monkeypatch.setenv("OCR_LANGUAGE", "eng")
    monkeypatch.setenv("POLL_INTERVAL", "2.5")

    assert config.database_url() == "sqlite:///custom.sqlite3"
    assert config.files_dir() == tmp_path / "files"
    assert config.ollama_base_url() == "http://ollama.local"
    assert config.ocr_language() == "eng"
    assert config.poll_interval() == 2.5


def test_load_env_file_sets_missing_values(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# ignored",
                "DATABASE_URL='sqlite:///from-env-file.sqlite3'",
                "OLLAMA_BASE_URL=http://from-env-file.local",
                "INVALID_LINE",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://already-set.local")
    monkeypatch.delenv("PAD_DISABLE_ENV_FILE", raising=False)

    config.load_env_file()

    assert config.database_url() == "sqlite:///from-env-file.sqlite3"
    assert config.ollama_base_url() == "http://already-set.local"


def test_load_env_file_can_be_disabled(monkeypatch, tmp_path) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///ignored.sqlite3", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PAD_DISABLE_ENV_FILE", "true")

    config.load_env_file()

    assert config.database_url() != "sqlite:///ignored.sqlite3"
