"""Run the replay benchmark end to end and assemble the report dictionary."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.config import PROJECT_ROOT
from adaptiveroute.domain import ExecutionStatus
from adaptiveroute.evaluation import metrics
from adaptiveroute.evaluation.dataset import Dataset
from adaptiveroute.evaluation.matrix import OutcomeMatrix
from adaptiveroute.evaluation.replay import (
    ABLATIONS,
    MAIN_STRATEGIES,
    ItemResult,
    ReplayContext,
    StrategySpec,
    matrix_baselines,
    replay,
)
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.ports import Embedder
from adaptiveroute.routing import AgentProfiles, RoutingConfig

log = get_logger(__name__)


@dataclass(frozen=True)
class BenchmarkOptions:
    seeds: Sequence[int] = (0, 1, 2)
    strategies: Sequence[StrategySpec] = MAIN_STRATEGIES
    ablations: Sequence[StrategySpec] = ABLATIONS
    k_folds: int = 5


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _git() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(  # noqa: S603 - fixed argv
                ["git", *args],  # noqa: S607 - git from PATH is intended
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    sha = run("rev-parse", "HEAD") or None
    dirty = bool(run("status", "--porcelain", "--untracked-files=no"))
    return {"git_sha": sha, "git_dirty": dirty}


def _version(pkg: str) -> str | None:
    try:
        return importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return None


def agent_matrix_stats(
    dataset: Dataset, matrix: OutcomeMatrix, registry: AgentRegistry
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for agent in registry:
        cells = [matrix.cells[(i.id, agent.name)] for i in dataset.items]
        own = [matrix.cells[(i.id, agent.name)] for i in dataset.items if i.domain == agent.name]
        out[agent.name] = {
            "model": agent.model,
            "reasoning_effort": agent.reasoning_effort,
            "own_domain_success": float(np.mean([c.success for c in own])) if own else None,
            "overall_success": float(np.mean([c.success for c in cells])),
            "p50_latency_ms": float(np.median([c.latency_ms for c in cells])),
            "p95_latency_ms": float(np.percentile([c.latency_ms for c in cells], 95)),
            "mean_cost_usd": float(np.mean([c.cost_usd for c in cells])),
            "mean_output_tokens": float(np.mean([c.output_tokens for c in cells])),
            "error_rate": float(np.mean([c.status != ExecutionStatus.SUCCESS for c in cells])),
            "retried_cells": sum(c.attempts > 1 for c in cells),
        }
    return out


def _strategy_section(
    results: list[ItemResult], baseline: list[ItemResult] | None, agents: Sequence[str]
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "summary": metrics.summarize(results),
        "by_domain": metrics.breakdown(results, lambda r: r.domain),
        "by_difficulty": metrics.breakdown(results, lambda r: r.difficulty),
        "by_ambiguity": metrics.breakdown(
            results, lambda r: "ambiguous" if r.ambiguous else "other"
        ),
        "confusion": metrics.confusion(results, agents),
    }
    if baseline is not None:
        section["vs_embedding"] = {
            "routing_accuracy": metrics.paired_diff(
                results, baseline, lambda r: float(r.routing_correct)
            ),
            "task_success": metrics.paired_diff(results, baseline, lambda r: float(r.success)),
            "cost_usd": metrics.paired_diff(results, baseline, lambda r: r.total_cost_usd),
            "latency_ms": metrics.paired_diff(results, baseline, lambda r: r.total_ms),
        }
    return section


async def run_replay_benchmark_core(
    *,
    run_id: str,
    dataset: Dataset,
    matrix: OutcomeMatrix,
    matrix_path: Path,
    registry: AgentRegistry,
    routing: RoutingConfig,
    embedder: Embedder,
    options: BenchmarkOptions,
    router_fingerprint: str,
) -> dict[str, Any]:
    profiles = await AgentProfiles.build(registry.agents, embedder)
    rc = ReplayContext(
        dataset, matrix, registry, embedder, profiles, routing.adaptive, routing.failover
    )

    caveats: list[str] = []
    specs = [*options.strategies, *options.ablations]
    if not matrix.router:
        specs = [s for s in specs if s.router != "llm"]
        caveats.append(
            "No recorded LLM-router decisions in the matrix: the llm strategy was skipped."
        )

    results: dict[str, list[ItemResult]] = {}
    for spec in specs:
        res: list[ItemResult] = []
        for seed in options.seeds:
            res += await replay(spec, seed, rc, options.k_folds)
        results[spec.name] = res
        log.info("replayed_strategy", strategy=spec.name, items=len(res))

    agents = registry.names
    baseline = results.get("embedding")
    report: dict[str, Any] = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "strategies": {
            s.name: _strategy_section(
                results[s.name], baseline if s.name != "embedding" else None, agents
            )
            for s in specs
            if not s.ablation
        },
        "ablations": {
            s.name: _strategy_section(results[s.name], baseline, agents)
            for s in specs
            if s.ablation
        },
        "baselines": matrix_baselines(rc),
        "learning_curves": {
            name: metrics.learning_curve(results[name])
            for name in ("embedding", "adaptive", "adaptive_warm")
            if name in results
        },
        "agent_matrix": agent_matrix_stats(dataset, matrix, registry),
    }

    router_failures = sum(r.error is not None for r in matrix.router.values())
    retried = sum(c.attempts > 1 for c in matrix.cells.values())
    collected = sorted(c.collected_at for c in matrix.cells.values())
    caveats += [
        f"Dataset is small ({len(dataset)} items, 30 per domain); differences inside the "
        "reported 95% CIs should not be over-interpreted.",
        "Each (item, agent) cell and each LLM-router decision was collected once at "
        "temperature 0; provider-side nondeterminism is not averaged out. Seeds vary only "
        "arrival order (which affects round-robin and the adaptive router's learning).",
        "End-to-end latency adds routing latency measured during replay to execution "
        "latency measured during collection (network + provider queueing included).",
        f"{retried} of {len(matrix.cells)} matrix cells needed provider retries (usually "
        "429 rate limits on the free tier); their latency includes the backoff.",
        f"{router_failures} of {len(matrix.router)} recorded LLM-router calls failed; in "
        "replay those items fall back to the embedding router, as in production.",
        "Costs are estimates from token counts x list prices; free-tier usage is billed $0.",
        "The adaptive router learns from deterministic checker labels; in production the "
        "labels come from user feedback, which is noisier and sparser.",
    ]
    report["caveats"] = caveats
    report["provenance"] = {
        **_git(),
        "dataset_path": str(dataset.path.relative_to(PROJECT_ROOT)) if dataset.path else None,
        "dataset_sha256": dataset.sha256,
        "dataset_items": len(dataset),
        "matrix_path": str(matrix_path.relative_to(PROJECT_ROOT))
        if matrix_path.is_relative_to(PROJECT_ROOT)
        else str(matrix_path),
        "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest()
        if matrix_path.exists()
        else None,
        "matrix_cells": len(matrix.cells),
        "matrix_collected": [collected[0], collected[-1]] if collected else [None, None],
        "llm_provider": "Groq (OpenAI-compatible API)",
        "agents": {
            a.name: {
                "model": a.model,
                "reasoning_effort": a.reasoning_effort,
                "fingerprint": a.fingerprint,
            }
            for a in registry
        },
        "llm_router": {"model": routing.llm.model, "fingerprint": router_fingerprint},
        "prices_as_of": registry.prices.as_of,
        "prices_source": registry.prices.source,
        "embedding_model": embedder.model_name,
        "seeds": list(options.seeds),
        "k_folds": options.k_folds,
        "routing_config": routing.as_dict(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": {p: _version(p) for p in ("numpy", "fastembed", "onnxruntime")},
    }
    return report
