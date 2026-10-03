.DEFAULT_GOAL := help
SHELL := /bin/bash
UV ?= uv
# Don't rely on the editable-install .pth: macOS skips .pth files flagged hidden
# (e.g. when the repo lives in an iCloud-synced Desktop folder).
export PYTHONPATH := src

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Install Python deps (incl. dev) into .venv
	$(UV) sync --python 3.12

.PHONY: fmt
fmt: ## Format code
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

.PHONY: lint
lint: ## Lint + format check + type check
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

.PHONY: test
test: ## Unit tests (no external services)
	$(UV) run pytest tests/unit

.PHONY: test-integration
test-integration: ## Integration tests (needs `make deps-up`)
	$(UV) run pytest tests/integration -m integration

.PHONY: test-all
test-all: ## All non-live tests with coverage
	$(UV) run pytest --cov --cov-report=term-missing:skip-covered

.PHONY: deps-up
deps-up: ## Start Postgres (pgvector) + Redis for local dev/tests
	docker compose up -d --wait postgres redis

.PHONY: migrate
migrate: ## Apply database migrations
	$(UV) run alembic upgrade head

.PHONY: migration-check
migration-check: ## Fail if ORM models and migrations have drifted
	$(UV) run alembic check

.PHONY: validate-dataset
validate-dataset: ## Validate the evaluation dataset (+ leakage check)
	$(UV) run python -m adaptiveroute.evaluation.validate --embeddings
