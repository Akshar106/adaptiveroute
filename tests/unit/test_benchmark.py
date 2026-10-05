"""Benchmark pipeline: collect (with a scripted LLM) -> replay -> metrics -> report."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.config import PROJECT_ROOT
from adaptiveroute.embeddings import HashingEmbedder
from adaptiveroute.evaluation import metrics
from adaptiveroute.evaluation.dataset import Dataset, EvalItem
from adaptiveroute.evaluation.matrix import (
    OutcomeMatrix,
    QuotaExhausted,
    collect,
    router_fingerprint,
)
from adaptiveroute.evaluation.replay import ABLATIONS, MAIN_STRATEGIES, ItemResult
from adaptiveroute.evaluation.report import render_markdown, write_report
from adaptiveroute.evaluation.runner import BenchmarkOptions, run_replay_benchmark_core
from adaptiveroute.llm import ChatRequest, LLMRateLimited, LLMUnavailable
from adaptiveroute.routing import LLMRouter, RoutingConfig
from tests.fakes import FakeLLM, reply

REGISTRY = AgentRegistry.from_yaml(PROJECT_ROOT / "config" / "agents.yaml")
ROUTING = RoutingConfig.from_yaml(PROJECT_ROOT / "config" / "routing.yaml")
FULL = Dataset.load(PROJECT_ROOT / "data" / "eval" / "dataset.jsonl")
# Two items per domain keeps the test fast (code checks spawn subprocesses).
ITEMS = [i for d, items in FULL.by_domain().items() for i in items[:2]]
DATASET = Dataset(ITEMS, FULL.sha256, FULL.path)
BY_QUERY = {i.query: i for i in ITEMS}
PROMPT_TO_AGENT = {a.system_prompt: a.name for a in REGISTRY}
BROKEN_CELL = (ITEMS[0].id, ITEMS[0].domain)  # this specialist errors -> failover
WRONG_ROUTE = ITEMS[1].id  # LLM router picks a wrong agent here
FAILED_ROUTE = ITEMS[2].id  # LLM router call fails here


def scripted(request: ChatRequest) -> object:
    if request.response_format is not None:  # LLM router
        query = request.messages[1]["content"].removeprefix("<query>\n").removesuffix("\n</query>")
        item = BY_QUERY[query]
        if item.id == FAILED_ROUTE:
            return LLMUnavailable("router provider down", status=503)
        agent = "writer" if item.id == WRONG_ROUTE and item.domain != "writer" else item.domain
        return reply(json.dumps({"agent": agent, "confidence": 0.9, "reason": "test"}))
    agent = PROMPT_TO_AGENT[request.messages[0]["content"]]
    item = BY_QUERY[request.messages[1]["content"]]
    if (item.id, agent) == BROKEN_CELL:
        return LLMUnavailable("agent provider down", status=503, attempts=4)
    if agent == item.domain:
        return reply(item.reference, input_tokens=300, output_tokens=200)
    return reply("I'm not sure about that.", input_tokens=300, output_tokens=20)


def router(llm: FakeLLM, registry: AgentRegistry = REGISTRY) -> LLMRouter:
    return LLMRouter(registry.agents, llm, registry.prices, ROUTING.llm)


async def collect_into(
    path: Path, llm: FakeLLM, registry: AgentRegistry = REGISTRY
) -> dict[str, int]:
    return await collect(
        dataset=DATASET,
        registry=registry,
        llm=llm,
        router=router(llm, registry),
        path=path,
        concurrency=8,
        rpm_per_model=100_000,
    )


@pytest.fixture(scope="module")
async def matrix_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("bench") / "outcomes.jsonl"
    counts = await collect_into(path, FakeLLM(scripted))  # type: ignore[arg-type]
    assert counts == {"cells": len(ITEMS) * 5, "router": len(ITEMS)}
    return path


def load(path: Path) -> OutcomeMatrix:
    rfp = router_fingerprint(router(FakeLLM()))
    return OutcomeMatrix.load(path, REGISTRY, rfp)


# --- collection -------------------------------------------------------------------


async def test_matrix_cells_reflect_checker_verdicts(matrix_file: Path) -> None:
    m = load(matrix_file)
    assert len(m.cells) == len(ITEMS) * 5
    for item in ITEMS:
        for agent in REGISTRY.names:
            cell = m.cells[(item.id, agent)]
            if (item.id, agent) == BROKEN_CELL:
                assert cell.status == "error" and not cell.success
            else:
                assert cell.success == (agent == item.domain), (item.id, agent, cell.check_detail)
    assert m.router[FAILED_ROUTE].agent is None and m.router[FAILED_ROUTE].error
    assert m.router[WRONG_ROUTE].agent == "writer"


async def test_collection_resumes_without_repeating_work(matrix_file: Path, tmp_path: Path) -> None:
    copy = tmp_path / "m.jsonl"
    copy.write_bytes(matrix_file.read_bytes())
    llm = FakeLLM(scripted)  # type: ignore[arg-type]
    assert await collect_into(copy, llm) == {"cells": 0, "router": 0}
    assert llm.requests == []


async def test_changed_agent_prompt_invalidates_only_that_agent(
    matrix_file: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "m.jsonl"
    copy.write_bytes(matrix_file.read_bytes())
    writer = REGISTRY.get("writer")
    changed = replace(writer, system_prompt=writer.system_prompt + " Be brief.")
    registry = replace(
        REGISTRY, agents=tuple(changed if a.name == "writer" else a for a in REGISTRY)
    )

    def responder(req: ChatRequest) -> object:
        if req.messages[0]["content"] == changed.system_prompt:
            return reply("new writer output")
        return scripted(req)

    counts = await collect_into(copy, FakeLLM(responder), registry)  # type: ignore[arg-type]
    assert counts["cells"] == len(ITEMS)  # one column re-collected


async def test_daily_quota_stops_cleanly_and_keeps_progress(tmp_path: Path) -> None:
    path = tmp_path / "m.jsonl"

    def responder(req: ChatRequest) -> object:
        if req.model == "openai/gpt-oss-120b":
            return LLMRateLimited("tokens per day exceeded", retry_after_s=3600)
        return scripted(req)

    with pytest.raises(QuotaExhausted):
        await collect_into(path, FakeLLM(responder))  # type: ignore[arg-type]
    saved = Counter(
        json.loads(line)["agent"] for line in path.read_text().splitlines() if '"cell"' in line
    )
    # 20b agents (math, sql, writer) completed; 120b agents (code, knowledge) are pending.
    assert set(saved) == {"math", "sql", "writer"}


# --- replay + report -------------------------------------------------------------


@pytest.fixture(scope="module")
async def report(matrix_file: Path) -> dict:  # type: ignore[type-arg]
    return await run_replay_benchmark_core(
        run_id="test-run",
        dataset=DATASET,
        matrix=load(matrix_file),
        matrix_path=matrix_file,
        registry=REGISTRY,
        routing=ROUTING,
        embedder=HashingEmbedder(),
        options=BenchmarkOptions(seeds=(0, 1), strategies=MAIN_STRATEGIES, ablations=ABLATIONS),
        router_fingerprint=router_fingerprint(router(FakeLLM())),
    )


def test_report_covers_every_strategy_and_ablation(report: dict) -> None:  # type: ignore[type-arg]
    assert list(report["strategies"]) == [s.name for s in MAIN_STRATEGIES]
    assert list(report["ablations"]) == [s.name for s in ABLATIONS]
    for section in [*report["strategies"].values(), *report["ablations"].values()]:
        s = section["summary"]
        assert s["n_items"] == len(ITEMS) and s["n_seeds"] == 2
        for key in ("routing_accuracy", "task_success", "error_rate"):
            lo, hi = s[key]["ci95"]
            assert 0 <= lo <= s[key]["mean"] + 1e-9 <= hi + 1e-9 <= 1 + 1e-9


def test_llm_strategy_replays_recorded_decisions(report: dict) -> None:  # type: ignore[type-arg]
    s = report["strategies"]["llm"]["summary"]
    # 10 items: one wrong pick, one failed call that fell back to the embedding router.
    assert s["router_fallback_rate"]["mean"] == pytest.approx(1 / len(ITEMS))
    assert s["routing_accuracy"]["mean"] <= (len(ITEMS) - 1) / len(ITEMS)
    assert s["cost_usd"]["router_mean_per_query"] > 0
    assert s["routing_latency_ms"]["mean"] > 0


def test_round_robin_spreads_evenly(report: dict) -> None:  # type: ignore[type-arg]
    conf = report["strategies"]["round_robin"]["confusion"]
    chosen = Counter()
    for row in conf.values():
        for agent, share in row.items():
            chosen[agent] += share
    assert len([a for a, v in chosen.items() if v > 0]) == 5


def test_baselines_from_matrix(report: dict) -> None:  # type: ignore[type-arg]
    b = report["baselines"]
    assert b["label_oracle"]["routing_accuracy"] == 1.0
    # every item is solved by its specialist except the one broken cell
    assert b["label_oracle"]["task_success"] == pytest.approx((len(ITEMS) - 1) / len(ITEMS))
    assert b["oracle"]["task_success"] == b["label_oracle"]["task_success"]
    for agent in REGISTRY.names:
        assert b[f"always_{agent}"]["task_success"] <= 2 / len(ITEMS)


def test_failover_recovers_the_broken_specialist_call(report: dict) -> None:  # type: ignore[type-arg]
    s = report["strategies"]["embedding"]["summary"]
    assert s["error_rate"]["mean"] < 1 / len(ITEMS)  # the broken cell was failed over


def test_vs_embedding_paired_diffs_present(report: dict) -> None:  # type: ignore[type-arg]
    d = report["strategies"]["adaptive"]["vs_embedding"]["task_success"]
    assert d["ci_low"] <= d["mean"] <= d["ci_high"]
    assert "vs_embedding" not in report["strategies"]["embedding"]


def test_report_files_written(report: dict, tmp_path: Path) -> None:  # type: ignore[type-arg]
    path = write_report(report, tmp_path / "run")
    md = path.read_text()
    assert "## Headline results" in md
    assert "| **adaptive** |" in md
    assert json.loads((tmp_path / "run" / "results.json").read_text())["run_id"] == "test-run"
    assert render_markdown(report, []).count("| **") == len(MAIN_STRATEGIES)


def test_provenance_is_recorded(report: dict) -> None:  # type: ignore[type-arg]
    p = report["provenance"]
    assert p["dataset_sha256"] == FULL.sha256
    assert p["matrix_cells"] == len(ITEMS) * 5
    assert set(p["agents"]) == set(REGISTRY.names)
    assert p["prices_as_of"] == "2026-09-28"
    assert p["seeds"] == [0, 1]


# --- metrics helpers ------------------------------------------------------------------


def _r(item: str, seed: int, success: bool) -> ItemResult:
    return ItemResult(
        item_id=item, domain="math", difficulty="easy", ambiguous=False, strategy="s", seed=seed,
        position=0, selected_agent="math", executed_agents=("math",), routing_correct=success,
        success=success, error=False, router_fallback=False, routing_ms=1.0, execution_ms=10.0,
        router_cost_usd=0.0, agent_cost_usd=0.001,
    )  # fmt: skip


def test_paired_diff_detects_consistent_improvement() -> None:
    better = [_r(f"i{k}", 0, True) for k in range(40)]
    worse = [_r(f"i{k}", 0, k % 2 == 0) for k in range(40)]
    d = metrics.paired_diff(better, worse, lambda r: float(r.success))
    assert d["mean"] == pytest.approx(0.5)
    assert d["ci_low"] > 0


def test_bootstrap_ci_brackets_the_mean() -> None:
    lo, hi = metrics.bootstrap_ci([1.0] * 30 + [0.0] * 10)
    assert lo < 0.75 < hi


def test_folds_are_stratified_and_disjoint() -> None:
    from adaptiveroute.evaluation.replay import folds

    parts = folds(FULL.items, k=5, seed=0)
    ids = [i.id for p in parts for i in p]
    assert len(ids) == len(set(ids)) == len(FULL)
    for p in parts:
        assert Counter(i.domain for i in p) == dict.fromkeys(
            ["code", "math", "sql", "writer", "knowledge"], 6
        )


def test_eval_item_type_is_reexported() -> None:
    assert isinstance(ITEMS[0], EvalItem)


def test_retry_errors_only_targets_provider_failures() -> None:
    from adaptiveroute.evaluation.matrix import Cell, is_provider_failure

    def cell(status: str, error: str | None) -> Cell:
        return Cell("i", "a", "fp", "m", status, False, "", 1.0, 0, 0, 0.0, 1, None, error, "t")

    assert is_provider_failure(cell("error", "LLMRateLimited: rate limited: tokens per minute"))
    assert is_provider_failure(cell("timeout", "LLMTimeout: deadline exceeded"))
    assert not is_provider_failure(cell("error", "empty completion (finish_reason=length)"))
    assert not is_provider_failure(cell("success", None))


def test_router_retry_only_targets_provider_failures() -> None:
    from adaptiveroute.evaluation.matrix import RouterRecord, is_provider_router_failure

    def rec(error: str | None) -> RouterRecord:
        return RouterRecord("i", "fp", None, 0.0, "", 0.0, 0.0, 0, 0, error, "t")

    prefix = "RouterError: LLM router failed and no fallback configured: "
    assert is_provider_router_failure(rec(prefix + "rate limited: Rate limit reached"))
    assert is_provider_router_failure(rec(prefix + "request timed out: ReadTimeout('')"))
    assert not is_provider_router_failure(rec(prefix + "bad request 400: Failed to validate JSON"))
    assert not is_provider_router_failure(rec(None))
