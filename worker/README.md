# Worker

Python worker that extracts text from medical biology PDF reports, runs OCR when embedded text is missing or low quality, and sends the extracted content to Ollama to produce structured JSON.

The worker polls the shared SQLite database every second, processes `files` rows marked `todo`, extracts structured data with OCR and Ollama, stores the extracted JSON and confidence on the file row, and updates the file status.

## Runtime Storage

- Desktop SQLite DB: `~/Library/Application Support/PAD/pad.sqlite3`, passed by Electron as `DATABASE_URL`.
- Standalone/debug SQLite DB: `../persistence/data/pad_development.sqlite3` by default from the project root.
- PDF files: `~/Desktop/fichiers_pad` by default, or the folder configured in PAD and passed by Electron as `FILES_DIR`.

The worker runs directly on the host. In normal desktop runtime, Electron starts it and injects configuration through environment variables. Electron also sets `PAD_DISABLE_ENV_FILE=true`, so a root `.env` file is ignored in that flow.

For each `files` row with `status = 'todo'`, the daemon changes the status to `extracting`, extracts data from `$FILES_DIR/{name}`, stores `extracted_json` and `confidence`, then marks the row `ready` or `review`. Parsing exceptions mark the row `failed` and store the reason in `error`.

## Modules

- `app.main`: orchestration functions, including `extract_pdf_only`.
- `app.extraction`: PDF text/OCR/vision image preparation and Ollama calls.
- `app.validation`: output normalization, confidence computation, and business rules.
- `app.repository`: SQLite persistence helpers for extraction status/results.
- `app.daemon`: DB polling process.
- `app.transfer`: REDCap transfer boundary using PyCap.

## Commands

Start the worker from the project root:

```sh
make worker-start
```

Run batch extraction test files from the parent directory:

```sh
make test on=extract
```

Run the fixed REDCap transfer debug workflow:

```sh
make test on=transfer
```

The transfer target runs `python -m app.transfer` without CLI options. It exports REDCap events for debugging, looks up each JSON file in `worker/test/output` by patient first and last name, then inserts an HCG row into the configured repeating REDCap instrument when exactly one patient match is found.

## Configuration

In normal desktop runtime, configuration comes from Electron environment variables. Electron reads the SQLite `configuration` table and passes values such as `DATABASE_URL`, `FILES_DIR`, `OLLAMA_BASE_URL`, `REDCAP_API_URL`, and `REDCAP_TOKEN` to the worker process.

For standalone/debug commands, configuration comes from shell environment variables and, if present, the optional root `.env` file. Set `PAD_DISABLE_ENV_FILE=true` to prevent standalone `.env` loading.

- `DATABASE_URL`, desktop value injected by Electron; standalone default `sqlite:///../persistence/data/pad_development.sqlite3`
- `FILES_DIR`, default `~/Desktop/fichiers_pad`
- `POLL_INTERVAL`, default `1.0`
- `OLLAMA_BASE_URL`, default `http://localhost:11434`
- `OLLAMA_VISION_MODEL`, default `qwen3-vl:8b-instruct`
- `OLLAMA_NUM_CTX`, default `65536`
- `OCR_LANGUAGE`, default `fra+eng`
- `LOW_CONFIDENCE_RETRY_THRESHOLD`, default `0.9`
- `REDCAP_API_URL`, injected by Electron from PAD settings in desktop runtime; required from shell env or optional `.env` for standalone REDCap transfers
- `REDCAP_TOKEN`, injected by Electron from PAD settings in desktop runtime; required from shell env or optional `.env` for standalone REDCap transfers
- `REDCAP_TIMEOUT`, default `30`
- `REDCAP_RETURN_CONTENT`, default `count`
- `REDCAP_OVERWRITE`, default `normal`
- `REDCAP_DATE_FORMAT`, default `YMD`
- `REDCAP_FORCE_AUTO_NUMBER`, default `false`
- `REDCAP_VERIFY_SSL`, default `true`
- `REDCAP_RECORD_ID_FIELD`, default `pat`
- `REDCAP_DUPLICATE_CHECK_FIELD`, default `concat_nom_prenom_ddn`
- `REDCAP_FIRST_NAME_FIELD`, default `prenom`
- `REDCAP_LAST_NAME_FIELD`, default `nom`
- REDCap field mapping variables are internal worker defaults/debug overrides; the desktop app does not expose them in user settings.
- `REDCAP_HCG_EVENT`, optional; when empty, uses the matched patient event
- `REDCAP_HCG_INSTRUMENT`, default `table_hcg`
- `REDCAP_HCG_DATE_FIELD`, default `date_hcg`
- `REDCAP_HCG_VALUE_FIELD`, default `hcg`
- `REDCAP_HCG_LESS_THAN_FIELD`, default `signe_inferieur___inferieur`
- `REDCAP_HCG_GREATER_THAN_FIELD`, default `signe_superieur___superieur`
- `REDCAP_HCG_LABORATORY_FIELD`, default `nom_labo_hcg`
- `REDCAP_HCG_METHOD_FIELD`, default `kit_labo`
- `REDCAP_HCG_DATE_FORMAT`, default `DMY`
- `REDCAP_HCG_REPEATING_MODE`, default `instrument`; use `auto` only if the token has project design privileges
