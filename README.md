# PAD

PAD is a macOS desktop app with an Electron/React UI and a Python worker that processes PDF biology reports with OCR and Ollama.

Docker is not required. SQLite, uploaded files, the worker, Ollama, and the Electron app all run on the host Mac.

## Storage

Default host paths:

- SQLite DB: `~/Library/Application Support/PAD/pad.sqlite3`
- PDF folder: `~/Desktop/fichiers_pad`
- Ollama URL: `http://localhost:11434`

## Install On Mac

Install Apple's command line tools:

```sh
xcode-select --install
```

Install Homebrew if it is not already installed:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install system dependencies:

```sh
brew install uv node ocrmypdf poppler tesseract tesseract-lang unpaper ollama
```

Start Ollama:

```sh
ollama serve
```

In another terminal, install the vision model used by the worker:

```sh
ollama pull qwen3-vl:8b-instruct
```

Verify OCR languages are available:

```sh
tesseract --list-langs
```

The output should include `fra` and `eng`.

## Project Setup

The normal desktop app creates and initializes its SQLite database under `~/Library/Application Support/PAD/pad.sqlite3` on launch.

Standalone worker/debug commands create their development SQLite directory and default PDF folder through `make standalone-env` when needed.

Install frontend dependencies:

```sh
cd frontend
npm install
cd ..
```

Install worker dependencies:

```sh
cd worker
uv sync
cd ..
```

## Configuration

Desktop runtime configuration is stored in the local SQLite `configuration` table.

The app uses these defaults:

- SQLite DB: `~/Library/Application Support/PAD/pad.sqlite3`
- PDF folder: `~/Desktop/fichiers_pad`
- Ollama URL: `http://localhost:11434`

On first launch, PAD copies the previous development DB from `../persistence/data/pad_development.sqlite3` when present. Otherwise it copies the packaged seed DB and initializes the schema.

Set REDCap values in the PAD configuration screen if transfers are enabled.
The REDCap record ID, first-name, and last-name field settings must match the variable names used by the target REDCap project.

During normal desktop runtime, Electron reads the SQLite configuration table and injects worker environment variables such as `DATABASE_URL`, `FILES_DIR`, `OLLAMA_BASE_URL`, `REDCAP_API_URL`, and `REDCAP_TOKEN`. The worker does not read REDCap URL/token directly from SQLite.

A root `.env` file is optional and ignored by the worker when Electron starts it. Standalone worker/debug commands can still read shell environment variables or an optional root `.env`, for example:

```sh
REDCAP_API_URL=https://...
REDCAP_TOKEN=...
```

Do not commit real REDCap credentials.

## Run

Run the Electron development app:

```sh
make start
```

Electron starts the worker process as needed and passes configuration from SQLite to the worker environment.

For worker-only debugging, run the standalone worker in a terminal:

```sh
make worker-start
```

Stop either process with `Ctrl-C`.

## Verify

Check the frontend TypeScript build:

```sh
make frontend-test
```

Check worker imports:

```sh
cd worker
uv run python -c "import app.daemon; import app.repository; import app.transfer; print('worker imports ok')"
cd ..
```

Run extraction against files in `../persistence/files/input`:

```sh
make test on=extract
```

The extraction test writes JSON files to `../persistence/files/output`.

Run the REDCap transfer debug workflow:

```sh
make test on=transfer
```

This standalone debug workflow requires `REDCAP_API_URL` and `REDCAP_TOKEN` in the shell environment or optional root `.env`.

## Package The Mac App

Build the Electron app:

```sh
cd frontend
npm run package:mac
cd ..
```

The packaged app is written under `frontend/release`.

The packaged Electron app stores its SQLite DB in `~/Library/Application Support/PAD/pad.sqlite3` and expects the configured PDF folder, Ollama, and worker setup described above.

## Troubleshooting

If the worker cannot connect to Ollama, verify Ollama is running:

```sh
curl http://localhost:11434/api/tags
```

If OCR fails because French is missing, reinstall language data:

```sh
brew reinstall tesseract-lang
tesseract --list-langs
```

If PDF rendering fails, verify Poppler is installed:

```sh
pdftoppm -h
```

If `better-sqlite3` fails after a Node or Electron upgrade, rebuild frontend native dependencies:

```sh
cd frontend
npm run rebuild
cd ..
```

If the worker does nothing, check that the SQLite DB contains `files` rows with `status = 'todo'` and that the matching PDFs exist in the configured PDF folder.
