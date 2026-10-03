"""Celery tasks (eager, real per-process runtime), CLI commands and benchmark storage.

These are *sync* tests on purpose: the worker runtime owns its own event loop, exactly
as in a Celery process, so it must not run inside pytest-asyncio's loop.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from typer.testing import CliRunner

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.cli import app as cli
from adaptiveroute.config import PROJECT_ROOT, get_settings
from adaptiveroute.evaluation.dataset import Dataset
from adaptiveroute.evaluation.matrix import collect
from adaptiveroute.routing import LLMRouter, RoutingConfig
from tests.fakes import FakeLLM
from tests.integration.conftest import REDIS_URL
from tests.unit.test_benchmark import ITEMS, scripted

PEPPER = "test-pepper"


@pytest.fixture
def env(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point get_settings() at the test DB/Redis and a temp benchmark directory."""
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(i.model_dump()) + "\n" for i in ITEMS))
    for key, value in {
        "AR_DATABASE_URL": database_url,
        "AR_REDIS_URL": REDIS_URL,
        "AR_EMBEDDING_BACKEND": "hashing",
        "AR_API_KEY_PEPPER": PEPPER,
        "AR_OTEL_ENABLED": "false",
        "AR_LOG_JSON": "false",
        "AR_BENCHMARK_DIR": str(tmp_path / "benchmarks"),
        "AR_DATASET_PATH": str(dataset),
        "GROQ_API_KEY": "",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _sql(database_url: str, statement: str) -> list[tuple[object, ...]]:
    async def go() -> list[tuple[object, ...]]:
        engine = create_async_engine(database_url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement))
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


@pytest.fixture
def worker_runtime(env: Path) -> Iterator[None]:
    """Eager Celery + a fresh per-process runtime, torn down afterwards."""
    from adaptiveroute.worker import runtime
    from adaptiveroute.worker.celery_app import celery_app

    runtime._state.clear()
    celery_app.conf.task_always_eager = True
    yield
    celery_app.conf.task_always_eager = False
    container = runtime._state.get("container")
    if container is not None:
        runtime.run(lambda c: c.close())
    runtime._state.clear()


def test_execute_task_runs_and_is_idempotent(worker_runtime: None, database_url: str) -> None:
    from adaptiveroute.worker.tasks import execute_query

    _sql(database_url, "TRUNCATE queries CASCADE")
    query_id = _sql(
        database_url,
        "INSERT INTO queries (id, text, embedding, embedding_model, strategy, selected_agent, "
        "reasoning, candidates, decision_metadata, routing_latency_ms, router_cost_usd, status, "
        "source) VALUES (gen_random_uuid(), 'Solve 2x = 4', array_fill(0.01, ARRAY[384])::vector, "
        "'hashing-384', 'embedding', 'math', 'r', "
        '\'[{"agent": "math", "score": 1, "components": {}}, '
        '{"agent": "code", "score": 0.5, "components": {}}]\', \'{}\', 1, 0, \'queued\', \'api\') '
        "RETURNING id",
    )[0][0]

    # No GROQ key in this environment: the agent fails, fails over, and the query
    # ends 'failed' with both attempts recorded - all through the real task.
    result = execute_query.apply(args=(str(query_id), False)).get()
    assert result == {"query_id": str(query_id), "status": "failed"}
    rows = _sql(
        database_url,
        f"SELECT agent, status FROM executions WHERE query_id = '{query_id}' ORDER BY attempt_no",
    )
    assert rows == [("math", "error"), ("code", "error")]

    # Redelivery is a no-op.
    assert execute_query.apply(args=(str(query_id), False)).get()["status"] == "failed"
    assert len(_sql(database_url, f"SELECT 1 FROM executions WHERE query_id = '{query_id}'")) == 2


def test_recovery_and_maintenance_tasks(worker_runtime: None, database_url: str) -> None:
    from adaptiveroute.worker.tasks import (
        purge_idempotency_keys,
        recover_stuck_queries,
        refresh_agent_stats,
    )

    _sql(database_url, "TRUNCATE queries CASCADE")
    _sql(
        database_url,
        "INSERT INTO queries (id, text, embedding, embedding_model, strategy, selected_agent, "
        "reasoning, candidates, decision_metadata, routing_latency_ms, router_cost_usd, status, "
        "source, created_at) VALUES (gen_random_uuid(), 'lost', array_fill(0.01, ARRAY[384])::vector, "
        "'hashing-384', 'embedding', 'writer', 'r', '[{\"agent\": \"writer\", \"score\": 1, "
        "\"components\": {}}]', '{}', 1, 0, 'running', 'api', now() - interval '1 hour')",
    )
    recovered = recover_stuck_queries.apply(args=(10,)).get()
    assert len(recovered) == 1
    # eager mode executed the re-enqueued task immediately
    assert _sql(database_url, "SELECT status FROM queries")[0][0] == "failed"

    refresh_agent_stats.apply().get()
    assert ("writer",) in _sql(database_url, "SELECT agent FROM agent_stats")
    assert purge_idempotency_keys.apply().get() == 0


# --- CLI -----------------------------------------------------------------------------

runner = CliRunner()


def test_cli_create_api_key(env: Path, database_url: str) -> None:
    result = runner.invoke(cli, ["create-api-key", "--name", "cli-test", "--role", "admin"])
    assert result.exit_code == 0, result.output
    key = result.output.strip().splitlines()[-1]
    assert key.startswith("ar_")
    assert ("cli-test", "admin") in _sql(database_url, "SELECT name, role FROM api_keys")


def test_cli_collect_requires_api_key(env: Path) -> None:
    result = runner.invoke(cli, ["bench", "collect"])
    assert result.exit_code == 2
    assert "GROQ_API_KEY" in result.output


def _collect_matrix(env_dir: Path) -> None:
    registry = AgentRegistry.from_yaml(PROJECT_ROOT / "config" / "agents.yaml")
    routing = RoutingConfig.from_yaml(PROJECT_ROOT / "config" / "routing.yaml")
    llm = FakeLLM(scripted)  # type: ignore[arg-type]
    dataset = Dataset.load(env_dir / "dataset.jsonl")
    asyncio.run(
        collect(
            dataset=dataset,
            registry=registry,
            llm=llm,
            router=LLMRouter(registry.agents, llm, registry.prices, routing.llm),
            path=env_dir / "benchmarks" / "matrix" / "outcomes.jsonl",
            concurrency=8,
            rpm_per_model=100_000,
        )
    )


def test_cli_bench_run_store_import_and_seed_history(env: Path, database_url: str) -> None:
    _sql(database_url, "TRUNCATE queries, benchmark_runs CASCADE")
    _collect_matrix(env)

    result = runner.invoke(
        cli, ["bench", "run", "--seeds", "0", "--run-id", "cli-run", "--store", "--no-ablations"]
    )
    assert result.exit_code == 0, result.output
    report_dir = env / "benchmarks" / "results" / "cli-run"
    assert (report_dir / "report.md").exists()
    assert _sql(database_url, "SELECT id, status FROM benchmark_runs") == [("cli-run", "completed")]

    # Importing the same results.json is an idempotent upsert.
    result = runner.invoke(cli, ["bench", "import", str(report_dir / "results.json")])
    assert result.exit_code == 0, result.output
    assert len(_sql(database_url, "SELECT 1 FROM benchmark_runs")) == 1

    result = runner.invoke(cli, ["bench", "seed-history"])
    assert result.exit_code == 0, result.output
    assert f"Imported {len(ITEMS) * 5} labelled executions" in result.output
    labelled = _sql(
        database_url, "SELECT count(*), sum(success::int) FROM outcomes WHERE source = 'benchmark'"
    )
    assert labelled[0][0] == len(ITEMS) * 5
    assert labelled[0][1] == len(ITEMS) - 1  # every specialist succeeds except the broken cell


def test_benchmark_task_replays_and_stores(
    worker_runtime: None, env: Path, database_url: str
) -> None:
    from adaptiveroute.worker.tasks import run_benchmark

    _sql(database_url, "TRUNCATE benchmark_runs")
    _collect_matrix(env)
    out = run_benchmark.apply(args=("task-run", {"seeds": [0], "ablations": False})).get()
    assert out == {"run_id": "task-run", "status": "completed"}
    status, summary = _sql(
        database_url, "SELECT status, summary->'strategies' ? 'adaptive' FROM benchmark_runs"
    )[0]
    assert (status, summary) == ("completed", True)


def test_benchmark_task_records_failure(worker_runtime: None, env: Path, database_url: str) -> None:
    from adaptiveroute.worker.tasks import run_benchmark

    _sql(database_url, "TRUNCATE benchmark_runs")
    # No matrix collected -> replay refuses to run on missing cells.
    with pytest.raises(ValueError, match="missing"):
        run_benchmark.apply(args=("broken-run", {"seeds": [0]})).get()
    status, error = _sql(database_url, "SELECT status, error FROM benchmark_runs")[0]
    assert status == "failed" and "missing" in str(error)


def test_env_fixture_does_not_leak(env: Path) -> None:
    assert get_settings().embedding_backend == "hashing"
    assert os.environ["AR_DATABASE_URL"].endswith("adaptiveroute_test")
