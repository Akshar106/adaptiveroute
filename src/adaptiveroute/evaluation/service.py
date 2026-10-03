"""Benchmark entry points shared by the CLI and the Celery worker."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.config import Settings
from adaptiveroute.db.repositories import BenchmarkRepository, OutcomeRepository, QueryRepository
from adaptiveroute.domain import CandidateScore, ExecutionResult, ExecutionStatus, RoutingDecision
from adaptiveroute.embeddings import FastEmbedEmbedder, HashingEmbedder
from adaptiveroute.evaluation.dataset import Dataset
from adaptiveroute.evaluation.matrix import OutcomeMatrix, collect, router_fingerprint
from adaptiveroute.evaluation.replay import ABLATIONS, MAIN_STRATEGIES
from adaptiveroute.evaluation.report import write_report
from adaptiveroute.evaluation.runner import BenchmarkOptions, run_replay_benchmark_core
from adaptiveroute.llm import ChatRequest, ChatResponse, LLMClient
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.ports import Embedder
from adaptiveroute.routing import LLMRouter, RoutingConfig

if TYPE_CHECKING:
    from adaptiveroute.container import Container

log = get_logger(__name__)


class _NoLLM:
    """Placeholder client for building an LLMRouter whose fingerprint we need."""

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise RuntimeError("not used")


def matrix_path(settings: Settings) -> Path:
    return settings.benchmark_dir / "matrix" / "outcomes.jsonl"


def load_inputs(settings: Settings) -> tuple[Dataset, AgentRegistry, RoutingConfig, str]:
    dataset = Dataset.load(settings.dataset_path)
    registry = AgentRegistry.from_yaml(settings.agents_config_path)
    routing = RoutingConfig.from_yaml(settings.routing_config_path)
    rfp = router_fingerprint(LLMRouter(registry.agents, _NoLLM(), registry.prices, routing.llm))
    return dataset, registry, routing, rfp


def replay_embedder(settings: Settings, backend: str | None = None) -> Embedder:
    """Uncached embedder so routing latency includes computing the embedding."""
    if (backend or settings.embedding_backend) == "hashing":
        return HashingEmbedder(settings.embedding_dim)
    return FastEmbedEmbedder(
        settings.embedding_model, settings.embedding_dim, settings.embedding_cache_dir
    )


async def collect_matrix(
    settings: Settings,
    llm: LLMClient,
    *,
    concurrency: int,
    rpm_per_model: int,
    max_wait_s: float,
    retry_errors: bool,
    limit: int | None = None,
) -> dict[str, int]:
    dataset, registry, routing, _ = load_inputs(settings)
    if limit is not None:
        dataset = Dataset(dataset.items[:limit], dataset.sha256, dataset.path)
    router = LLMRouter(registry.agents, llm, registry.prices, routing.llm)
    return await collect(
        dataset=dataset,
        registry=registry,
        llm=llm,
        router=router,
        path=matrix_path(settings),
        concurrency=concurrency,
        rpm_per_model=rpm_per_model,
        max_wait_s=max_wait_s,
        retry_errors=retry_errors,
    )


def benchmark_options(raw: dict[str, Any]) -> BenchmarkOptions:
    wanted = raw.get("strategies")
    strategies = [
        s for s in MAIN_STRATEGIES if not wanted or s.router in wanted or s.name in wanted
    ]
    return BenchmarkOptions(
        seeds=tuple(raw.get("seeds") or (0, 1, 2)),
        strategies=strategies,
        ablations=ABLATIONS if raw.get("ablations", True) else (),
    )


async def run_benchmark(
    settings: Settings,
    *,
    run_id: str,
    options: BenchmarkOptions,
    embedder: Embedder | None = None,
    out_root: Path | None = None,
) -> dict[str, Any]:
    dataset, registry, routing, rfp = load_inputs(settings)
    path = matrix_path(settings)
    matrix = OutcomeMatrix.load(path, registry, rfp)
    report = await run_replay_benchmark_core(
        run_id=run_id,
        dataset=dataset,
        matrix=matrix,
        matrix_path=path,
        registry=registry,
        routing=routing,
        embedder=embedder or replay_embedder(settings),
        options=options,
        router_fingerprint=rfp,
    )
    out_dir = (out_root or settings.benchmark_dir / "results") / run_id
    write_report(report, out_dir)
    log.info("benchmark_written", run_id=run_id, path=str(out_dir))
    return report


async def store_report(sessions: async_sessionmaker[AsyncSession], report: dict[str, Any]) -> None:
    prov = report["provenance"]
    body = {k: v for k, v in report.items() if k != "report_markdown"}
    async with sessions() as session:
        await BenchmarkRepository(session).upsert(
            {
                "id": report["run_id"],
                "status": "completed",
                "dataset_sha256": prov["dataset_sha256"],
                "git_sha": prov.get("git_sha"),
                "config": {"seeds": prov["seeds"], "routing": prov["routing_config"]},
                "summary": json.loads(json.dumps(body, default=str)),
                "report_markdown": report.get("report_markdown"),
                "finished_at": datetime.fromisoformat(report["created_at"]),
            }
        )


async def import_report(sessions: async_sessionmaker[AsyncSession], results_json: Path) -> str:
    report = json.loads(results_json.read_text())
    md = results_json.with_name("report.md")
    if md.exists():
        report["report_markdown"] = md.read_text()
    await store_report(sessions, report)
    return str(report["run_id"])


async def run_replay_benchmark(
    container: Container, run_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Celery entry point: replay the committed matrix and store the report."""
    repo_values = {
        "id": run_id,
        "status": "running",
        "dataset_sha256": "",
        "config": raw,
    }
    async with container.sessions() as session:
        await BenchmarkRepository(session).upsert(repo_values)
    try:
        report = await run_benchmark(
            container.settings, run_id=run_id, options=benchmark_options(raw)
        )
        await store_report(container.sessions, report)
    except Exception as exc:
        log.exception("benchmark_failed", run_id=run_id)
        async with container.sessions() as session:
            await BenchmarkRepository(session).upsert(
                repo_values
                | {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}"[:2000],
                    "finished_at": datetime.now(UTC),
                }
            )
        raise
    return {"run_id": run_id, "status": "completed"}


async def seed_history(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    item_ids: Sequence[str] | None = None,
) -> int:
    """Import matrix cells as labelled executions so a fresh deployment's adaptive
    router has history to learn from (source='benchmark'). For demos only: never
    seed a database you later benchmark against with the same items."""
    dataset, registry, _, rfp = load_inputs(settings)
    matrix = OutcomeMatrix.load(matrix_path(settings), registry, rfp)
    items = [i for i in dataset.items if item_ids is None or i.id in item_ids]
    vectors = await embedder.embed([i.query for i in items])
    count = 0
    async with sessions() as session:
        queries, outcomes = QueryRepository(session), OutcomeRepository(session)
        for item, vec in zip(items, vectors, strict=True):
            for agent in registry:
                cell = matrix.cells.get((item.id, agent.name))
                if cell is None:
                    continue
                decision = RoutingDecision(
                    strategy="benchmark_import",
                    agent=agent.name,
                    candidates=(CandidateScore(agent.name, 1.0),),
                    reasoning=f"imported from benchmark matrix ({item.id})",
                    latency_ms=0.0,
                )
                query = await queries.create(
                    text_=item.query,
                    embedding=vec,
                    embedding_model=embedder.model_name,
                    embedding_ms=None,
                    decision=decision,
                    status="completed",
                    source="benchmark",
                )
                result = ExecutionResult(
                    agent=agent.name,
                    model=cell.model,
                    status=ExecutionStatus(cell.status),
                    output=cell.output,
                    error=cell.error,
                    latency_ms=cell.latency_ms,
                    input_tokens=cell.input_tokens,
                    output_tokens=cell.output_tokens,
                    cost_usd=cell.cost_usd,
                    attempts=cell.attempts,
                )
                execution = await queries.add_execution(query, result, agent, attempt_no=1)
                await outcomes.record(
                    execution,
                    query,
                    success=cell.success,
                    source="benchmark",
                    detail=cell.check_detail,
                )
                count += 1
        await session.commit()
    return count
