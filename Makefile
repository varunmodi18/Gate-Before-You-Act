# GateBench entry points (plan §J.2). Targets for later tasks are added as they are implemented.
SHELL := /bin/bash
.DEFAULT_GOAL := help

UV       ?= uv
PNPM     ?= pnpm
NVM_USE  := source $$HOME/.nvm/nvm.sh >/dev/null 2>&1 && nvm use 22 >/dev/null 2>&1;
API_HOST ?= 127.0.0.1
API_PORT ?= 8000
PROFILE  ?= vllm-awq

.PHONY: help setup db data-fetch catalogue lint format test up api worker web build-web model-up model-cmd pilot gpu-test

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

build-web:  ## Build the SPA into frontend/dist (served by the API)
	$(NVM_USE) cd frontend && $(PNPM) build

api:  ## Run the API on 127.0.0.1:8000
	$(UV) run uvicorn gbya.api.main:app --app-dir backend --host $(API_HOST) --port $(API_PORT)

worker:  ## Run the experiment worker
	$(UV) run python -m gbya.worker

up: build-web  ## Build the SPA, then run API (serving the SPA) and worker together
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

gpu-test:  ## Tests that need the running model server (pytest -m gpu)
	$(UV) run pytest -m gpu
