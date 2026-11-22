.DEFAULT_GOAL := help
PYTHON ?= python3
SYNTH_OUT ?= data/synthetic
INGEST_CONFIG ?= ingestion/configs/ingestion.yaml

.PHONY: help setup lint format test test-unit test-integration test-dq synth validate-synth \
        evidence evidence-phase2 ingest verify-bronze coverage compose-config up down

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

setup: ## Install the package with dev tools and git hooks
	$(PYTHON) -m pip install -e ".[dev,excel]"
	pre-commit install

lint: ## Lint and check formatting (no changes made)
	ruff check .
	ruff format --check .

format: ## Auto-fix lint issues and format code
	ruff check --fix .
	ruff format .

test: ## Run the full test suite
	$(PYTHON) -m pytest

test-unit: ## Run unit tests only
	$(PYTHON) -m pytest -m unit

test-integration: ## Run integration tests only
	$(PYTHON) -m pytest -m integration

test-dq: ## Run data-quality tests only
	$(PYTHON) -m pytest -m data_quality

synth: ## Generate a synthetic (SYNTHETIC_TEST_DATA) dataset into $(SYNTH_OUT)
	$(PYTHON) -m ingestion.synthetic generate --out $(SYNTH_OUT)

validate-synth: ## Validate the dataset in $(SYNTH_OUT)
	$(PYTHON) -m ingestion.synthetic validate $(SYNTH_OUT)

ingest: ## Ingest all enabled sources from $(INGEST_CONFIG) into data/ingestion (run `make synth` first)
	$(PYTHON) -m ingestion ingest --source all --config $(INGEST_CONFIG)

verify-bronze: ## Verify checksums of every committed Bronze batch
	$(PYTHON) -m ingestion verify-bronze --root data/ingestion

coverage: ## Test coverage of the ingestion package (pytest-cov / coverage.py; needs `make setup`)
	$(PYTHON) -m pytest --cov=ingestion --cov-report=term-missing

evidence: ## Collect Phase 1 evidence (tests, lint, metrics, samples) into evidence/phase-1
	$(PYTHON) evidence/collect_evidence.py --phase 1

evidence-phase2: ## Collect Phase 2 evidence (ingestion runs, reports, CLI output, coverage) into evidence/phase-2
	$(PYTHON) evidence/collect_evidence.py --phase 2

compose-config: ## Validate docker-compose.yml against .env.example
	docker compose --env-file .env.example config --quiet

up: ## Start the local stack (copy .env.example to .env first)
	docker compose up --build

down: ## Stop the local stack
	docker compose down
