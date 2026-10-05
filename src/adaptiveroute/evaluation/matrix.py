"""Phase A: collect the outcome matrix (every dataset item x every agent) from the real LLM.

Each cell records what actually happened when agent ``a`` handled item ``i``: the
output, whether the deterministic checker passed, measured latency, tokens and
estimated cost. The LLM router's decision for each item is recorded the same way.

The matrix file is append-only JSONL so collection is *resumable*: rerunning the same
command skips cells that are already present for the current agent fingerprint.
That matters on the Groq free tier, whose daily token limits can stop a run midway:
when the provider asks us to wait longer than ``max_wait_s`` we stop cleanly and the
next run picks up where this one left off.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adaptiveroute.agents import AgentExecutor, AgentRegistry
from adaptiveroute.domain import ExecutionStatus
from adaptiveroute.evaluation.checkers import check_output
from adaptiveroute.evaluation.dataset import Dataset, EvalItem
from adaptiveroute.llm import ChatRequest, ChatResponse, LLMClient, LLMRateLimited
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.routing import LLMRouter
from adaptiveroute.state import LocalLoadTracker

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Cell:
    item_id: str
    agent: str
    fingerprint: str
    model: str
    status: str  # ExecutionStatus value
    success: bool
    check_detail: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    attempts: int
    output: str | None
    error: str | None
    collected_at: str


@dataclass(frozen=True, slots=True)
class RouterRecord:
    item_id: str
    router_fingerprint: str
    agent: str | None  # None if the router call failed (replay then uses the fallback)
    confidence: float
    reason: str
    latency_ms: float
    cost_usd: float
    input_tokens: int
    output_tokens: int
    error: str | None
    collected_at: str


@dataclass
class OutcomeMatrix:
    """In-memory view of a matrix file (latest record wins per key)."""

    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)  # (item, agent)
    router: dict[str, RouterRecord] = field(default_factory=dict)  # item -> record

    @classmethod
    def load(cls, path: Path, registry: AgentRegistry, router_fingerprint: str) -> OutcomeMatrix:
        fingerprints = {a.name: a.fingerprint for a in registry}
        matrix = cls()
        if not path.exists():
            return matrix
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            kind = rec.pop("kind")
            if kind == "cell" and fingerprints.get(rec["agent"]) == rec["fingerprint"]:
                matrix.cells[(rec["item_id"], rec["agent"])] = Cell(**rec)
            elif kind == "router" and rec["router_fingerprint"] == router_fingerprint:
                matrix.router[rec["item_id"]] = RouterRecord(**rec)
        return matrix

    def missing(self, items: Iterable[EvalItem], agents: Iterable[str]) -> list[tuple[str, str]]:
        agent_list = list(agents)
        return [(i.id, a) for i in items for a in agent_list if (i.id, a) not in self.cells]


# Errors caused by the provider/our quota rather than by the agent's answer. Only these
# are re-collected by --retry-errors; e.g. an empty completion (the model spent its
# whole token budget reasoning) is a genuine agent outcome and is kept.
_PROVIDER_ERRORS = ("LLMRateLimited", "LLMUnavailable", "LLMTimeout")


def is_provider_failure(cell: Cell) -> bool:
    return cell.status != ExecutionStatus.SUCCESS and (cell.error or "").startswith(
        _PROVIDER_ERRORS
    )


# Router failures are wrapped in RouterError("... : <LLMError message>"); these are the
# message prefixes our LLM client uses for provider-side failures. A 400 such as
# "Failed to validate JSON" is the router's own failure and is kept.
_PROVIDER_ROUTER_MARKERS = (
    "rate limited:",
    "request timed out",
    "provider timeout",
    "provider error",
    "transport error",
    "deadline of",
    "malformed provider response",
)


def is_provider_router_failure(record: RouterRecord) -> bool:
    return record.error is not None and any(m in record.error for m in _PROVIDER_ROUTER_MARKERS)


def router_fingerprint(router: LLMRouter) -> str:
    """Hash of the router's prompt + model config (changes invalidate recorded decisions)."""
    import hashlib

    req = router.build_request("{query}")
    payload = json.dumps([req.model, req.reasoning_effort, req.max_tokens, list(req.messages)])
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


class _ModelPacer:
    """Client-side pacing so we stay under the provider's requests-per-minute limit."""

    def __init__(self, rpm: int) -> None:
        self._interval = 60.0 / rpm
        self._next: defaultdict[str, float] = defaultdict(float)
        self._lock = asyncio.Lock()

    async def wait(self, model: str) -> None:
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next[model])
            self._next[model] = start + self._interval
        await asyncio.sleep(max(0.0, start - time.monotonic()))


class _QuotaGuard:
    """Turns "wait N minutes" rate limits (daily quotas) into a clean, resumable stop.

    Short 429 waits are retried by the client as in production (and show up in the
    cell's ``attempts``); pacing happens *before* the executor starts timing, so our
    own throttling never inflates measured agent latency.
    """

    def __init__(self, inner: LLMClient, max_wait_s: float) -> None:
        self._inner = inner
        self._max_wait_s = max_wait_s

    async def chat(self, request: ChatRequest) -> ChatResponse:
        try:
            return await self._inner.chat(request)
        except LLMRateLimited as exc:
            if exc.retry_after_s is not None and exc.retry_after_s > self._max_wait_s:
                raise QuotaExhausted(request.model, exc.retry_after_s) from exc
            raise


class QuotaExhausted(Exception):
    def __init__(self, model: str, retry_after_s: float) -> None:
        super().__init__(
            f"provider asked to wait {retry_after_s:.0f}s for {model} (likely a daily token "
            "limit). Progress is saved; rerun the same command later to resume."
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def collect(
    *,
    dataset: Dataset,
    registry: AgentRegistry,
    llm: LLMClient,
    router: LLMRouter,
    path: Path,
    concurrency: int = 4,
    rpm_per_model: int = 25,
    max_wait_s: float = 120.0,
    retry_errors: bool = False,
) -> dict[str, int]:
    """Fill in every missing cell (and router record) and append them to ``path``."""
    rfp = router_fingerprint(router)
    matrix = OutcomeMatrix.load(path, registry, rfp)
    guarded = _QuotaGuard(llm, max_wait_s)
    pacer = _ModelPacer(rpm_per_model)
    executor = AgentExecutor(guarded, registry.prices, LocalLoadTracker())
    guarded_router = LLMRouter(registry.agents, guarded, registry.prices, router.config)

    todo_cells = matrix.missing(dataset.items, registry.names)
    if retry_errors:
        todo_cells += [k for k, c in matrix.cells.items() if is_provider_failure(c)]
    todo_router = [i for i in dataset.items if i.id not in matrix.router]
    if retry_errors:
        todo_router += [
            i
            for i in dataset.items
            if i.id in matrix.router and is_provider_router_failure(matrix.router[i.id])
        ]
    items = {i.id: i for i in dataset.items}
    log.info("collect_plan", cells=len(todo_cells), router=len(todo_router), path=str(path))

    path.parent.mkdir(parents=True, exist_ok=True)
    write_lock = asyncio.Lock()
    sem = asyncio.Semaphore(concurrency)
    counts = {"cells": 0, "router": 0}

    async def append(kind: str, record: Any) -> None:
        async with write_lock:
            with path.open("a") as fh:
                fh.write(json.dumps({"kind": kind, **asdict(record)}) + "\n")
            counts[kind if kind == "router" else "cells"] += 1

    async def do_cell(item_id: str, agent_name: str) -> None:
        async with sem:
            agent = registry.get(agent_name)
            item = items[item_id]
            await pacer.wait(agent.model)
            result = await executor.run(agent, item.query)
            verdict = await asyncio.to_thread(check_output, item.check, result.output)
            await append(
                "cell",
                Cell(
                    item_id=item_id,
                    agent=agent_name,
                    fingerprint=agent.fingerprint,
                    model=agent.model,
                    status=result.status.value,
                    success=result.ok and verdict.passed,
                    check_detail=verdict.detail,
                    latency_ms=result.latency_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_usd=result.cost_usd,
                    attempts=result.attempts,
                    output=result.output,
                    error=result.error,
                    collected_at=_now(),
                ),
            )

    async def do_router(item: EvalItem) -> None:
        from adaptiveroute.embeddings import HashingEmbedder
        from adaptiveroute.routing import QueryContext

        async with sem:
            await pacer.wait(router.config.model)
            # No fallback here: we want to record genuine router failures.
            try:
                decision = await guarded_router.route(QueryContext(item.query, HashingEmbedder()))
                meta = decision.metadata
                record = RouterRecord(
                    item_id=item.id,
                    router_fingerprint=rfp,
                    agent=decision.agent,
                    confidence=decision.candidates[0].score,
                    reason=decision.reasoning,
                    latency_ms=decision.latency_ms,
                    cost_usd=decision.cost_usd,
                    input_tokens=int(meta.get("input_tokens", 0)),
                    output_tokens=int(meta.get("output_tokens", 0)),
                    error=None,
                    collected_at=_now(),
                )
            except QuotaExhausted:
                raise
            except Exception as exc:  # recorded as a router failure, not fatal
                record = RouterRecord(
                    item.id,
                    rfp,
                    None,
                    0.0,
                    "",
                    0.0,
                    0.0,
                    0,
                    0,
                    f"{type(exc).__name__}: {exc}",
                    _now(),
                )
            await append("router", record)

    tasks = [do_cell(i, a) for i, a in todo_cells] + [do_router(i) for i in todo_router]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    quota = [r for r in results if isinstance(r, QuotaExhausted)]
    other = [
        r for r in results if isinstance(r, BaseException) and not isinstance(r, QuotaExhausted)
    ]
    if other:
        raise other[0]
    if quota:
        log.warning("collect_stopped_on_quota", detail=str(quota[0]), saved=counts)
        raise quota[0]
    return counts
