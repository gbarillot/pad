# AGENTS.md

This document defines how AI agents and automated tools should interact with this repository.

## Project overview

- Desktop app: Electron main process with a React/Vite/TypeScript renderer.
- Worker: Python PDF OCR/Ollama processor under `worker/app`.
- Database: local SQLite. Electron uses `better-sqlite3`; the worker uses Python `sqlite3` against the same database file.
- Runtime: macOS host process model. Docker, a backend API service, and a devcontainer are not required for normal development or runtime.

## High-level rules

- Prefer clarity over cleverness.
- Do not introduce new frameworks or architectural patterns without strong justification.
- Minimize dependencies.
- Do not modify generated build output manually, including `frontend/dist`, `frontend/dist-electron`, and `frontend/release`.

## Architecture

- The Electron main process owns application configuration, file tracking, worker process lifecycle, and SQLite schema initialization.
- The React renderer communicates with Electron through the preload IPC API. It should not access Node APIs directly.
- The Python worker polls the `files` table for rows to extract or transfer.
- The current file schema uses `files.name`; do not reintroduce legacy `uploads.stored_filename` concepts except in migration code.

## Configuration

- Desktop runtime configuration is stored in the local SQLite `configuration` table.
- The packaged/default desktop database is `~/Library/Application Support/PAD/pad.sqlite3`.
- The default watched PDF folder is `~/Desktop/fichiers_pad`.
- The default Ollama URL is `http://localhost:11434`.
- REDCap URL and token are set in the PAD configuration screen and stored in SQLite. REDCap field mappings are internal worker defaults or standalone/debug env overrides, not user-facing desktop settings.
- When Electron starts the worker, it reads SQLite configuration and injects worker environment variables such as `DATABASE_URL`, `FILES_DIR`, `OLLAMA_BASE_URL`, `REDCAP_API_URL`, and `REDCAP_TOKEN`.
- Electron sets `PAD_DISABLE_ENV_FILE=true` for the worker, so the root `.env` file is ignored in normal desktop runtime.
- A root `.env` file is optional and only useful for standalone worker/debug commands. Do not require `.env` for the normal app flow.
- Do not commit real REDCap credentials.

## Frontend

- Frontend uses Vite, React, TypeScript, Tailwind CSS, and Electron.
- Preserve the existing IPC boundary through `frontend/electron/preload.ts` and `window.*` APIs declared in `frontend/src/vite-env.d.ts`.
- State management should stay local unless state is truly shared.
- Do not hardcode backend API URLs; there is no active backend API in the current architecture.

## Worker

- Worker code lives under `worker/app`.
- Worker database access goes through `worker/app/repository.py`.
- Worker configuration helpers live in `worker/app/config.py`.
- REDCap transfer logic lives in `worker/app/transfer.py`.
- Standalone worker defaults use `../persistence/data/pad_development.sqlite3` and `~/Desktop/fichiers_pad` unless environment variables override them.

## Testing and tooling

- Install frontend dependencies from `frontend` with `npm install`.
- Install worker dependencies from `worker` with `uv sync`.
- Run the Electron development app with `make start` or `make frontend-start`.
- Run the standalone worker with `make worker-start` only for debugging or worker-only development.
- Run frontend type checks with `make frontend-test`.
- Run extraction tests with `make test on=extract`.
- Run the REDCap transfer debug workflow with `make test on=transfer`; it requires `REDCAP_API_URL` and `REDCAP_TOKEN` in the shell environment or optional root `.env`.
- Packaged macOS builds include the PyInstaller worker bundle and OCR/PDF tools. Ollama remains an external runtime dependency configured by URL.
