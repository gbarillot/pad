ROOT_DIR := $(CURDIR)
PERSISTENCE_DIR := $(abspath $(ROOT_DIR)/../persistence)
STANDALONE_DATABASE_PATH := $(PERSISTENCE_DIR)/data/pad_development.sqlite3
STANDALONE_FILES_DIR := $(HOME)/Desktop/fichiers_pad

ifneq (,$(wildcard .env))
include .env
endif

DATABASE_URL := $(or $(DATABASE_URL),sqlite:///$(STANDALONE_DATABASE_PATH))
FILES_DIR := $(or $(FILES_DIR),$(STANDALONE_FILES_DIR))
OLLAMA_BASE_URL := $(or $(OLLAMA_BASE_URL),http://localhost:11434)
OCR_LANGUAGE := $(or $(OCR_LANGUAGE),fra+eng)
POLL_INTERVAL := $(or $(POLL_INTERVAL),1.0)

export DATABASE_URL
export FILES_DIR
export OLLAMA_BASE_URL
export OCR_LANGUAGE
export POLL_INTERVAL
export REDCAP_API_URL
export REDCAP_TOKEN
export REDCAP_TIMEOUT
export REDCAP_RETURN_CONTENT
export REDCAP_OVERWRITE
export REDCAP_DATE_FORMAT
export REDCAP_FORCE_AUTO_NUMBER
export REDCAP_VERIFY_SSL
export REDCAP_RECORD_ID_FIELD
export REDCAP_FIRST_NAME_FIELD
export REDCAP_LAST_NAME_FIELD
export REDCAP_HCG_EVENT
export REDCAP_HCG_INSTRUMENT
export REDCAP_HCG_DATE_FIELD
export REDCAP_HCG_VALUE_FIELD
export REDCAP_HCG_DATE_FORMAT
export REDCAP_HCG_REPEATING_MODE

.PHONY: start
start: frontend-start

.PHONY: frontend-start
frontend-start:
	cd frontend && npm run dev

.PHONY: standalone-env
standalone-env:
	mkdir -p "$(dir $(STANDALONE_DATABASE_PATH))" "$(FILES_DIR)"

.PHONY: worker-start
worker-start: standalone-env
	cd worker && uv run python -m app.daemon

.PHONY: test
on ?= extract
test:
	$(if $(filter extract transfer,$(on)),,$(error Unsupported test target on=$(on). Use on=extract or on=transfer))
	$(if $(filter transfer,$(on)),$(MAKE) $(on)-test,$(MAKE) $(on)-test ARGS="$(ARGS)")

.PHONY: extract-test
extract-test: standalone-env
	cd worker && uv run python test/extract.py --input-dir "$(PERSISTENCE_DIR)/files/input" --output-dir "$(PERSISTENCE_DIR)/files/output" $(ARGS)

.PHONY: transfer-test
transfer-test: standalone-env
	cd worker && uv run python -m app.transfer

.PHONY: worker-test
worker-test: extract-test

.PHONY: frontend-test
frontend-test:
	cd frontend && npm run typecheck
