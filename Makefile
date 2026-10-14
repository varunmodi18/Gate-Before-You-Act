# GateBench entry points (plan §J.2). Targets for later tasks are added as they are implemented.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV       ?= uv
PNPM     ?= pnpm
NVM_USE  := source $$HOME/.nvm/nvm.sh >/dev/null 2>&1 && nvm use 22 >/dev/null 2>&1;
API_HOST ?= 127.0.0.1
API_PORT ?= 8000
PROFILE  ?= vllm-awq

.PHONY: index freeze-cases generate-variants import-cases retrieval-cache help setup db data data-fetch catalogue normalise dedup splits splits-doc policy-table lint e2e format test up api worker web build-web model-up model-cmd pilot gpu-test demo-setup demo-up fixture-up budget docs diag demo-record demo-replay regenerate-e2 prepare-e2e new-e2e

help:  ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup:  ## Install Python (uv) and frontend (pnpm) dependencies, and git hooks
	$(UV) sync
	$(NVM_USE) cd frontend && $(PNPM) install --frozen-lockfile
	$(UV) run pre-commit install

db:  ## Create or upgrade data/app.db to the latest migration (alembic upgrade head)
	$(UV) run python -m gbya.store.db

data-fetch:  ## Fetch OTRF at the pinned commit d9d40ef (sparse) into data/raw/otrf
	$(UV) run python -m gbya.data.fetch

catalogue:  ## Catalogue the 100 SDWIN datasets into app.db (windows table)
	$(UV) run python -m gbya.data.catalogue

normalise:  ## Normalise every catalogued window into data/duckdb/windows/<id>.duckdb (read-only)
	$(UV) run python -m gbya.data.normalise

dedup:  ## Eligibility + de-duplication groups (windows.dedup_group; data/dedup_report.json)
	$(UV) run python -m gbya.data.dedup

splits:  ## Seeded stratified split -> data/splits.json (team review before commit)
	$(UV) run python -m gbya.data.split

splits-doc:  ## Render docs/splits.md from data/splits.json (hosts, shares, acting users)
	$(UV) run python -m gbya.data.splits_doc

data: data-fetch catalogue normalise dedup  ## Fetch OTRF, catalogue, normalise, de-duplicate

index:  ## Fetch SigmaHQ (r2026-07-01) + ATT&CK 19.2 at pinned versions, check licences, build data/index
	$(UV) run python -m gbya.retrieval.index

import-cases:  ## Import cases/<scenario>/scenario.json and cases/*.json into app.db (content hashes)
	$(UV) run python -m gbya.cases.store

generate-variants:  ## Build E1-E5 and Set R for SID (case files, patched DBs, prefixes, import)
	$(UV) run python -m gbya.cases.cli generate $(SID)

regenerate-e2:  ## Recompute E2's sampling basis from the current labels (logged): SID=... REASON="..."
	$(UV) run python -m gbya.cases.cli regenerate-e2 $(SID) "$(REASON)"

new-e2e:  ## Scaffold cases/<id>/e2e.json for an end-to-end window (TODO placeholders): SID=<window id>
	$(UV) run python -m gbya.cases.cli new-e2e $(SID)

prepare-e2e:  ## Before annotating an end-to-end window: decide and plant its injection marker: SID=...
	$(UV) run python -m gbya.cases.cli prepare-e2e $(SID)

freeze-cases:  ## TEAM ONLY (T4.10): freeze the adjudicated case set -> cases/FROZEN.json (VERSION=cases-v1)
	$(UV) run python -m gbya.cases.freeze --version $(VERSION)

retrieval-cache:  ## Store bm25 and bm25_rerank rankings for every case of app.db (CPU reranker; Exp 1 reads them)
	$(UV) run python -m gbya.retrieval.cache

policy-table:  ## Regenerate docs/policy_table.md from policy/rules.yaml with the engine
	$(UV) run python -m gbya.policy.table

lint:  ## ruff, mypy, eslint, prettier check
	$(UV) run ruff check backend tests scripts
	$(UV) run ruff format --check backend tests scripts
	$(UV) run mypy
	$(NVM_USE) cd frontend && $(PNPM) lint && $(PNPM) format:check && $(PNPM) typecheck

format:  ## Apply ruff and prettier formatting
	$(UV) run ruff format backend tests scripts
	$(UV) run ruff check --fix backend tests scripts
	$(NVM_USE) cd frontend && $(PNPM) format

test:  ## Python unit + integration tests (no GPU) and frontend unit tests
	$(UV) run pytest
	$(NVM_USE) cd frontend && $(PNPM) test

e2e:  ## Playwright journeys (fixture data, no GPU) at 1280 and 768 px, axe on every page, then the replay-profile demo
	$(NVM_USE) cd frontend && $(PNPM) e2e && $(PNPM) exec playwright test -c playwright.replay.config.ts

build-web:  ## Build the SPA into frontend/dist (served by the API)
	$(NVM_USE) cd frontend && $(PNPM) build

api:  ## Run the API on 127.0.0.1:8000
	$(UV) run uvicorn gbya.api.main:app --app-dir backend --host $(API_HOST) --port $(API_PORT)

worker:  ## Run the experiment worker
	$(UV) run python -m gbya.worker

up: db build-web  ## Migrate app.db, build the SPA, then run API (serving the SPA) and worker together
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) --no-print-directory worker & \
	$(MAKE) --no-print-directory api & \
	wait

web:  ## Vite dev server on :5173 (development only)
	$(NVM_USE) cd frontend && $(PNPM) dev --host 127.0.0.1

model-cmd:  ## Print the model-server command for PROFILE (from config/model_profiles.yaml)
	$(UV) run python scripts/model_server.py --profile $(PROFILE) --print

model-up:  ## Start the model server for PROFILE on 127.0.0.1:8001 (foreground; log in logs/model.log)
	$(UV) run python scripts/model_server.py --profile $(PROFILE)

pilot:  ## Throughput pilot against the running server -> docs/pilot/$(PROFILE).json (30-min soak)
	$(UV) run python scripts/pilot.py --model $$($(UV) run python -c "import yaml;print(yaml.safe_load(open('config/model_profiles.yaml'))['profiles']['$(PROFILE)']['served_model_name'])") --soak-minutes 30 --out docs/pilot/$(PROFILE).json

demo-setup:  ## Build data/demo from data/fixtures/demo (T8.1; needs make data and make index)
	$(UV) run python scripts/demo_setup.py

demo-up: build-web  ## API + worker on the demo environment (data/demo; never the research app.db)
	@trap 'kill 0' INT TERM EXIT; \
	GBYA_APP_DB_PATH=data/demo/app.db GBYA_DATA_DIR=data/demo GBYA_CASES_DIR=data/demo/cases \
	  $(UV) run uvicorn gbya.api.main:app --app-dir backend --host $(API_HOST) --port $(API_PORT) & \
	GBYA_APP_DB_PATH=data/demo/app.db GBYA_DATA_DIR=data/demo GBYA_CASES_DIR=data/demo/cases \
	  $(UV) run python -m gbya.worker & \
	wait

CASSETTE ?= data/fixtures/demo/replay/demo.jsonl

demo-record: build-web  ## Demo with the live model, recording every call into CASSETTE (then run scripts/record_replay.py)
	@trap 'kill 0' INT TERM EXIT; \
	GBYA_APP_DB_PATH=data/demo/app.db GBYA_DATA_DIR=data/demo GBYA_CASES_DIR=data/demo/cases GBYA_RECORD_CASSETTE=$(CASSETTE) \
	  $(UV) run uvicorn gbya.api.main:app --app-dir backend --host $(API_HOST) --port $(API_PORT) & \
	GBYA_APP_DB_PATH=data/demo/app.db GBYA_DATA_DIR=data/demo GBYA_CASES_DIR=data/demo/cases GBYA_RECORD_CASSETTE=$(CASSETTE) \
	  $(UV) run python -m gbya.worker & \
	wait

demo-replay: build-web  ## Demo from the recorded CASSETTE, no model (header: REPLAY; runs flagged replay)
	@trap 'kill 0' INT TERM EXIT; \
	GBYA_APP_DB_PATH=data/demo/app.db GBYA_DATA_DIR=data/demo GBYA_CASES_DIR=data/demo/cases GBYA_LLM_BACKEND=replay GBYA_REPLAY_CASSETTE=$(CASSETTE) \
	  $(UV) run uvicorn gbya.api.main:app --app-dir backend --host $(API_HOST) --port $(API_PORT) & \
	GBYA_APP_DB_PATH=data/demo/app.db GBYA_DATA_DIR=data/demo GBYA_CASES_DIR=data/demo/cases GBYA_LLM_BACKEND=replay GBYA_REPLAY_CASSETTE=$(CASSETTE) \
	  $(UV) run python -m gbya.worker & \
	wait

fixture-up: build-web  ## Quick look without OTRF, GPU or model: fixture data + Fake model on 127.0.0.1:8000
	$(UV) run python scripts/e2e_fixture.py data/quick
	@trap 'kill 0' INT TERM EXIT; \
	GBYA_APP_DB_PATH=data/quick/app.db GBYA_DATA_DIR=data/quick GBYA_CASES_DIR=data/quick/cases GBYA_LLM_BACKEND=fake GBYA_RERANKER_DIR=data/quick/no-reranker \
	  $(UV) run uvicorn gbya.api.main:app --app-dir backend --host $(API_HOST) --port $(API_PORT) & \
	GBYA_APP_DB_PATH=data/quick/app.db GBYA_DATA_DIR=data/quick GBYA_CASES_DIR=data/quick/cases GBYA_LLM_BACKEND=fake GBYA_RERANKER_DIR=data/quick/no-reranker \
	  $(UV) run python -m gbya.worker & \
	wait

budget:  ## T8.0 (one command, once 10 dev scenarios are adjudicated): token audit + regenerated budget -> docs/compute_budget.md; PILOT2=<run> PILOT3=<run> optional
	$(UV) run python scripts/token_audit.py
	$(UV) run python scripts/budget.py $(if $(PILOT2),--pilot-exp2 $(PILOT2)) $(if $(PILOT3),--pilot-exp3 $(PILOT3))

docs:  ## Regenerate docs/api.md (OpenAPI) and docs/gate_contract.md (plan §D.6)
	$(UV) run python scripts/gen_docs.py

diag:  ## Diagnostics: versions, GPU memory, disk, database counts, frozen case set, model server
	$(UV) run python scripts/diag.py

gpu-test:  ## Tests that need the running model server (pytest -m gpu)
	$(UV) run pytest -m gpu
