"""Phase B: replay routing strategies over the recorded outcome matrix.

For each strategy and seed, items are processed in a seeded random order. For every
item the *real* router code makes a decision (timed in-process; the LLM router's
decision and latency come from its recorded API call), and the outcome of sending
that item to the chosen agent is looked up in the matrix. As in production, if the
chosen agent errored we fail over to the runner-up and pay for both calls.

Learning is bandit-style, like production: the adaptive router only learns the
outcome of the agent(s) it actually executed. Its success labels come from the
deterministic checker (in production they come from user feedback).

Warm-start variant (5-fold): for fold k, the adaptive router first processes the
other four folds online (building history from its own choices), then the held-out
fold is measured. Every item is measured exactly once per seed and never with its
own outcome in history.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.domain import CandidateScore, ExecutionStatus, RoutingDecision
from adaptiveroute.evaluation.dataset import Dataset, EvalItem
from adaptiveroute.evaluation.matrix import Cell, OutcomeMatrix
from adaptiveroute.history import InMemoryHistory
from adaptiveroute.ports import Embedder
from adaptiveroute.routing import (
    AdaptiveConfig,
    AdaptiveRouter,
    AgentProfiles,
    EmbeddingRouter,
    QueryContext,
    RoundRobinRouter,
    Router,
)
from adaptiveroute.state import LocalCounter, LocalLoadTracker


@dataclass(frozen=True)
class StrategySpec:
    name: str  # label in the report
    router: str  # round_robin | embedding | llm | adaptive
    warm_start: bool = False
    weight_overrides: Mapping[str, float] = field(default_factory=dict)
    ablation: bool = False

    def adaptive_config(self, base: AdaptiveConfig) -> AdaptiveConfig:
        weights = replace(base.weights, **self.weight_overrides)
        return replace(base, weights=weights)


MAIN_STRATEGIES = (
    StrategySpec("round_robin", "round_robin"),
    StrategySpec("embedding", "embedding"),
    StrategySpec("llm", "llm"),
    StrategySpec("adaptive", "adaptive"),
    StrategySpec("adaptive_warm", "adaptive", warm_start=True),
)
ABLATIONS = (
    StrategySpec(
        "adaptive_no_history", "adaptive", weight_overrides={"success": 0.0}, ablation=True
    ),
    StrategySpec(
        "adaptive_quality_only",
        "adaptive",
        weight_overrides={"latency": 0.0, "cost": 0.0, "load": 0.0},
        ablation=True,
    ),
    StrategySpec(
        "adaptive_explore", "adaptive", weight_overrides={"exploration": 0.05}, ablation=True
    ),
    StrategySpec(
        "adaptive_history_heavy",
        "adaptive",
        weight_overrides={"similarity": 0.3, "success": 0.5},
        ablation=True,
    ),
)


@dataclass(frozen=True, slots=True)
class ItemResult:
    item_id: str
    domain: str
    difficulty: str
    ambiguous: bool
    strategy: str
    seed: int
    position: int  # 0-based arrival order within this run
    selected_agent: str
    executed_agents: tuple[str, ...]
    routing_correct: bool
    success: bool
    error: bool  # final execution failed (after failover)
    router_fallback: bool
    routing_ms: float
    execution_ms: float
    router_cost_usd: float
    agent_cost_usd: float

    @property
    def total_ms(self) -> float:
        return self.routing_ms + self.execution_ms

    @property
    def total_cost_usd(self) -> float:
        return self.router_cost_usd + self.agent_cost_usd


class ReplayLLMRouter(Router):
    """Serves the LLM router's recorded decisions; falls back like production."""

    name = "llm"

    def __init__(
        self, matrix: OutcomeMatrix, item_by_text: Mapping[str, EvalItem], fallback: Router
    ):
        self._matrix = matrix
        self._items = item_by_text
        self._fallback = fallback

    async def decide(self, ctx: QueryContext) -> RoutingDecision:
        item = self._items[ctx.text]
        rec = self._matrix.router.get(item.id)
        if rec is None:
            raise KeyError(f"no recorded LLM-router decision for {item.id}; run collection first")
        if rec.agent is None:
            fb = await self._fallback.decide(ctx)
            return replace(
                fb,
                strategy=self.name,
                fallback=self._fallback.name,
                cost_usd=rec.cost_usd,
                metadata={"recorded_latency_ms": rec.latency_ms, "router_error": rec.error},
            )
        return RoutingDecision(
            strategy=self.name,
            agent=rec.agent,
            candidates=(CandidateScore(rec.agent, rec.confidence),),
            reasoning=rec.reason,
            latency_ms=0.0,
            cost_usd=rec.cost_usd,
            metadata={"recorded_latency_ms": rec.latency_ms},
        )


@dataclass
class ReplayContext:
    dataset: Dataset
    matrix: OutcomeMatrix
    registry: AgentRegistry
    embedder: Embedder
    profiles: AgentProfiles
    adaptive_config: AdaptiveConfig
    failover: bool = True

    def __post_init__(self) -> None:
        self.item_by_text = {i.query: i for i in self.dataset.items}
        missing = self.matrix.missing(self.dataset.items, self.registry.names)
        if missing:
            raise ValueError(
                f"outcome matrix is missing {len(missing)} cells (e.g. {missing[:3]}); "
                "run `adaptiveroute bench collect` first"
            )


def _build_router(spec: StrategySpec, rc: ReplayContext, history: InMemoryHistory) -> Router:
    embedding = EmbeddingRouter(rc.profiles)
    match spec.router:
        case "round_robin":
            return RoundRobinRouter(rc.registry.names, LocalCounter())
        case "embedding":
            return embedding
        case "llm":
            return ReplayLLMRouter(rc.matrix, rc.item_by_text, fallback=embedding)
        case "adaptive":
            return AdaptiveRouter(
                rc.registry.agents,
                rc.profiles,
                history,
                LocalLoadTracker(),
                spec.adaptive_config(rc.adaptive_config),
            )
    raise ValueError(f"unknown router {spec.router}")


def _execute(decision: RoutingDecision, cells: Mapping[str, Cell], failover: bool) -> list[Cell]:
    primary = cells[decision.agent]
    if primary.status == ExecutionStatus.SUCCESS or not failover:
        return [primary]
    ranked = decision.ranked_agents()
    if len(ranked) < 2:  # e.g. the LLM router only scores its pick: use profile order
        ranked += [a for a in cells if a not in ranked]
    return [primary, cells[ranked[1]]]


async def _step(
    item: EvalItem,
    router: Router,
    rc: ReplayContext,
    history: InMemoryHistory,
    learn: bool,
) -> tuple[RoutingDecision, list[Cell], float]:
    ctx = QueryContext(item.query, rc.embedder)
    start = time.perf_counter()
    decision = await router.route(ctx)
    routing_ms = (time.perf_counter() - start) * 1000
    recorded = decision.metadata.get("recorded_latency_ms")
    if recorded is not None:
        # LLM router: its real cost is the recorded API round trip, not the lookup.
        routing_ms = float(recorded) + (routing_ms if decision.fallback else 0.0)
    cells = {a: rc.matrix.cells[(item.id, a)] for a in rc.registry.names}
    executed = _execute(decision, cells, rc.failover)
    if learn:
        vec = await ctx.embedding()
        for cell in executed:
            history.add(vec, cell.agent, cell.success, cell.latency_ms, cell.cost_usd)
    return decision, executed, routing_ms


def _result(
    item: EvalItem,
    spec: StrategySpec,
    seed: int,
    position: int,
    decision: RoutingDecision,
    executed: Sequence[Cell],
    routing_ms: float,
) -> ItemResult:
    final = executed[-1]
    return ItemResult(
        item_id=item.id,
        domain=item.domain,
        difficulty=item.difficulty,
        ambiguous="ambiguous" in item.tags,
        strategy=spec.name,
        seed=seed,
        position=position,
        selected_agent=decision.agent,
        executed_agents=tuple(c.agent for c in executed),
        routing_correct=decision.agent in item.acceptable_agents,
        success=final.success,
        error=final.status != ExecutionStatus.SUCCESS,
        router_fallback=decision.fallback is not None,
        routing_ms=routing_ms,
        execution_ms=sum(c.latency_ms for c in executed),
        router_cost_usd=decision.cost_usd,
        agent_cost_usd=sum(c.cost_usd for c in executed),
    )


def folds(items: Sequence[EvalItem], k: int, seed: int) -> list[list[EvalItem]]:
    """Stratified k-fold split by domain (deterministic for a given seed)."""
    rng = random.Random(seed)  # noqa: S311 - reproducible shuffling, not crypto
    out: list[list[EvalItem]] = [[] for _ in range(k)]
    by_domain: dict[str, list[EvalItem]] = {}
    for item in items:
        by_domain.setdefault(item.domain, []).append(item)
    offset = 0
    for domain in sorted(by_domain):
        group = sorted(by_domain[domain], key=lambda i: i.id)
        rng.shuffle(group)
        for j, item in enumerate(group):
            out[(j + offset) % k].append(item)
        offset += len(group)
    return out


async def replay(
    spec: StrategySpec, seed: int, rc: ReplayContext, k_folds: int = 5
) -> list[ItemResult]:
    learns = spec.router == "adaptive"
    rng = random.Random(seed)  # noqa: S311 - reproducible shuffling, not crypto
    if not spec.warm_start:
        order = sorted(rc.dataset.items, key=lambda i: i.id)
        rng.shuffle(order)
        history = InMemoryHistory()
        router = _build_router(spec, rc, history)
        results = []
        for pos, item in enumerate(order):
            decision, executed, routing_ms = await _step(item, router, rc, history, learns)
            results.append(_result(item, spec, seed, pos, decision, executed, routing_ms))
        return results

    results = []
    splits = folds(rc.dataset.items, k_folds, seed)
    for k, test_fold in enumerate(splits):
        train = [i for j, f in enumerate(splits) if j != k for i in f]
        rng.shuffle(train)
        test = list(test_fold)
        rng.shuffle(test)
        history = InMemoryHistory()
        router = _build_router(spec, rc, history)
        for item in train:  # warm-up: build history from the router's own choices
            await _step(item, router, rc, history, learns)
        for pos, item in enumerate(test):
            decision, executed, routing_ms = await _step(item, router, rc, history, learns)
            results.append(_result(item, spec, seed, pos, decision, executed, routing_ms))
    return results


def matrix_baselines(rc: ReplayContext) -> dict[str, dict[str, Any]]:
    """Reference points computed directly from the matrix (no router involved)."""
    items = rc.dataset.items
    agents = rc.registry.names
    cells = rc.matrix.cells

    def summary(choice: Mapping[str, str]) -> dict[str, Any]:
        picked = [cells[(i.id, choice[i.id])] for i in items]
        return {
            "task_success": sum(c.success for c in picked) / len(items),
            "routing_accuracy": sum(choice[i.id] in i.acceptable_agents for i in items)
            / len(items),
            "mean_cost_usd": sum(c.cost_usd for c in picked) / len(items),
            "mean_execution_ms": sum(c.latency_ms for c in picked) / len(items),
            "error_rate": sum(c.status != ExecutionStatus.SUCCESS for c in picked) / len(items),
        }

    out: dict[str, dict[str, Any]] = {}
    for agent in agents:
        out[f"always_{agent}"] = summary({i.id: agent for i in items})
    out["label_oracle"] = summary({i.id: i.domain for i in items})

    def cheapest_success(item: EvalItem) -> str:
        ok = [a for a in agents if cells[(item.id, a)].success]
        pool = ok or list(agents)
        return min(pool, key=lambda a: cells[(item.id, a)].cost_usd)

    out["oracle"] = summary({i.id: cheapest_success(i) for i in items})
    return out
