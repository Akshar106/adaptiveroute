"""In-memory PerformanceHistory (benchmark replay and tests).

Mirrors the semantics of the Postgres implementation: per-agent top-k cosine
neighbours over past queries that have a task-success label.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from adaptiveroute.domain import AgentAggregate, HistoryRecord
from adaptiveroute.ports import Vector


@dataclass
class _AgentLog:
    embeddings: list[Vector] = field(default_factory=list)
    success: list[bool] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)
    cost_usd: list[float] = field(default_factory=list)
    _matrix: Vector | None = None  # cached stack of embeddings, rebuilt after appends


class InMemoryHistory:
    def __init__(self) -> None:
        self._logs: defaultdict[str, _AgentLog] = defaultdict(_AgentLog)

    def add(
        self, embedding: Vector, agent: str, success: bool, latency_ms: float, cost_usd: float
    ) -> None:
        log = self._logs[agent]
        log.embeddings.append(np.asarray(embedding, dtype=np.float32))
        log.success.append(success)
        log.latency_ms.append(latency_ms)
        log.cost_usd.append(cost_usd)
        log._matrix = None

    def __len__(self) -> int:
        return sum(len(log.success) for log in self._logs.values())

    async def neighbors(
        self, embedding: Vector, agents: Sequence[str], k: int
    ) -> dict[str, list[HistoryRecord]]:
        out: dict[str, list[HistoryRecord]] = {}
        for agent in agents:
            log = self._logs.get(agent)
            if log is None or not log.success:
                out[agent] = []
                continue
            if log._matrix is None:
                log._matrix = np.stack(log.embeddings)
            sims = log._matrix @ embedding
            top = np.argsort(-sims, kind="stable")[:k]
            out[agent] = [
                HistoryRecord(
                    agent=agent,
                    similarity=float(sims[i]),
                    success=log.success[i],
                    latency_ms=log.latency_ms[i],
                    cost_usd=log.cost_usd[i],
                )
                for i in top
            ]
        return out

    async def aggregates(self) -> dict[str, AgentAggregate]:
        return {
            agent: AgentAggregate(
                agent=agent,
                n=len(log.success),
                successes=sum(log.success),
                p50_latency_ms=float(np.median(log.latency_ms)),
                mean_cost_usd=float(np.mean(log.cost_usd)),
            )
            for agent, log in self._logs.items()
            if log.success
        }
