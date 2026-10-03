"""PerformanceHistory backed by Postgres + pgvector."""

from __future__ import annotations

import time
from collections.abc import Sequence

from pgvector.sqlalchemy import Vector as PgVector
from sqlalchemy import ARRAY, String, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptiveroute.db.models import EMBEDDING_DIM
from adaptiveroute.domain import AgentAggregate, HistoryRecord
from adaptiveroute.ports import Vector

# For each agent, its k nearest labelled past queries. LATERAL runs the inner
# ORDER BY ... LIMIT once per agent, so every agent gets its own k neighbours instead
# of the most-used agent crowding the others out. `<=>` is pgvector's cosine distance.
_NEIGHBORS_SQL = text(
    """
    SELECT a.agent, n.similarity, n.success, n.latency_ms, n.cost_usd
    FROM unnest(:agents) AS a(agent)
    CROSS JOIN LATERAL (
        SELECT 1 - (o.embedding <=> :query) AS similarity,
               o.success, o.latency_ms, o.cost_usd
        FROM outcomes o
        WHERE o.agent = a.agent AND o.embedding_model = :model
        ORDER BY o.embedding <=> :query
        LIMIT :k
    ) AS n
    ORDER BY a.agent, n.similarity DESC
    """
).bindparams(
    bindparam("agents", type_=ARRAY(String)),
    bindparam("query", type_=PgVector(EMBEDDING_DIM)),
)

_AGGREGATES_SQL = text(
    "SELECT agent, labelled, successes, p50_latency_ms, mean_cost_usd FROM agent_stats"
)


class PgHistory:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_model: str,
        aggregates_ttl_s: float = 5.0,
    ) -> None:
        self._sessions = session_factory
        self._model = embedding_model
        self._ttl = aggregates_ttl_s
        self._cached: tuple[float, dict[str, AgentAggregate]] | None = None

    async def neighbors(
        self, embedding: Vector, agents: Sequence[str], k: int
    ) -> dict[str, list[HistoryRecord]]:
        out: dict[str, list[HistoryRecord]] = {a: [] for a in agents}
        async with self._sessions() as session:
            rows = await session.execute(
                _NEIGHBORS_SQL,
                {"agents": list(agents), "query": embedding, "model": self._model, "k": k},
            )
            for agent, similarity, success, latency_ms, cost_usd in rows:
                out[agent].append(
                    HistoryRecord(agent, float(similarity), bool(success), latency_ms, cost_usd)
                )
        return out

    async def aggregates(self) -> dict[str, AgentAggregate]:
        """Read the materialised view (cached for a few seconds per process)."""
        now = time.monotonic()
        if self._cached is not None and now - self._cached[0] < self._ttl:
            return self._cached[1]
        async with self._sessions() as session:
            rows = (await session.execute(_AGGREGATES_SQL)).all()
        result = {
            agent: AgentAggregate(
                agent=agent,
                n=int(labelled),
                successes=int(successes),
                p50_latency_ms=float(p50) if p50 is not None else None,
                mean_cost_usd=float(cost) if cost is not None else None,
            )
            for agent, labelled, successes, p50, cost in rows
        }
        self._cached = (now, result)
        return result
