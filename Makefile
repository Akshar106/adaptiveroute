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

# Celery's prefork pool is broken on macOS (children are spawned, not forked);
# use the solo pool locally. Containers run Linux and use prefork.
CELERY_POOL := $(if $(filter Darwin,$(shell uname -s)),--pool=solo,--concurrency=4)

.PHONY: run-api
run-api: ## Run the API locally with autoreload (needs `make deps-up`)
	AR_LOG_JSON=false $(UV) run uvicorn adaptiveroute.api.app:create_app --factory --reload --port $${PORT:-8000}

.PHONY: run-worker
run-worker: ## Run a Celery worker locally
	AR_LOG_JSON=false $(UV) run celery -A adaptiveroute.worker.celery_app worker --loglevel=INFO $(CELERY_POOL)

.PHONY: up
up: ## Build and start the full stack (api, worker, beat, db, redis, jaeger, prometheus, grafana)
	docker compose build migrate
	docker compose up -d --wait api worker beat jaeger prometheus grafana

.PHONY: down
down: ## Stop the stack (keeps volumes)
	docker compose down

.PHONY: logs
logs: ## Tail application logs
	docker compose logs -f api worker beat

.PHONY: api-key
api-key: ## Create an admin API key in the running stack
	docker compose exec -T api adaptiveroute create-api-key --name local-admin --role admin

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

.PHONY: bench-collect
bench-collect: ## Collect the outcome matrix from Groq (resumable; needs GROQ_API_KEY)
	$(UV) run python -m adaptiveroute.cli bench collect

.PHONY: bench-run
bench-run: ## Replay all strategies over the matrix and write a report
	$(UV) run --group eval python -m adaptiveroute.cli bench run
