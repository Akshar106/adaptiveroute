"""Data access. Repositories take an AsyncSession; the caller owns the transaction."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from adaptiveroute.db.models import (
    ApiKey,
    BenchmarkRun,
    Execution,
    IdempotencyKey,
    Outcome,
    Query,
)
from adaptiveroute.domain import AgentSpec, ExecutionResult, RoutingDecision
from adaptiveroute.ports import Vector


def _candidates_json(decision: RoutingDecision) -> list[dict[str, Any]]:
    return [
        {"agent": c.agent, "score": c.score, "components": dict(c.components)}
        for c in decision.candidates
    ]


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, *, name: str, prefix: str, key_hash: str, role: str, rate_limit: int | None
    ) -> ApiKey:
        key = ApiKey(
            name=name, prefix=prefix, key_hash=key_hash, role=role, rate_limit_per_minute=rate_limit
        )
        self.session.add(key)
        await self.session.flush()
        return key

    async def get_active_by_prefix(self, prefix: str) -> ApiKey | None:
        stmt = select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke(self, key_id: uuid.UUID) -> bool:
        stmt = (
            update(ApiKey)
            .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def list(self) -> Sequence[ApiKey]:
        return (
            (await self.session.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars().all()
        )


class QueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        text_: str,
        embedding: Vector,
        embedding_model: str,
        embedding_ms: float | None,
        decision: RoutingDecision,
        status: str,
        api_key_id: uuid.UUID | None = None,
        source: str = "api",
        trace_id: str | None = None,
        request_id: str | None = None,
        query_id: uuid.UUID | None = None,
    ) -> Query:
        query = Query(
            id=query_id or uuid.uuid4(),
            api_key_id=api_key_id,
            text=text_,
            embedding=embedding,
            embedding_model=embedding_model,
            strategy=decision.strategy,
            selected_agent=decision.agent,
            fallback=decision.fallback,
            reasoning=decision.reasoning,
            candidates=_candidates_json(decision),
            decision_metadata=dict(decision.metadata),
            routing_latency_ms=decision.latency_ms,
            embedding_latency_ms=embedding_ms,
            router_cost_usd=decision.cost_usd,
            status=status,
            source=source,
            trace_id=trace_id,
            request_id=request_id,
        )
        query.executions = []
        self.session.add(query)
        await self.session.flush()
        return query

    async def add_execution(
        self, query: Query, result: ExecutionResult, agent: AgentSpec, attempt_no: int
    ) -> Execution:
        execution = Execution(
            query_id=query.id,
            attempt_no=attempt_no,
            agent=result.agent,
            model=result.model,
            agent_fingerprint=agent.fingerprint,
            status=result.status.value,
            output=result.output,
            error=result.error,
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            llm_attempts=result.attempts,
            cache_hit=result.cache_hit,
        )
        execution.outcome = None
        query.executions.append(execution)
        await self.session.flush()
        return execution

    async def set_status(self, query: Query, status: str) -> None:
        query.status = status
        if status in ("completed", "failed"):
            query.completed_at = datetime.now(UTC)
        await self.session.flush()

    async def claim(self, query_id: uuid.UUID) -> bool:
        """Atomically move queued -> running. Only one caller can win, so a query is
        executed at most once even if its Celery task is delivered twice."""
        stmt = (
            update(Query)
            .where(Query.id == query_id, Query.status == "queued")
            .values(status="running")
            .returning(Query.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def requeue_stuck(self, older_than: timedelta) -> list[uuid.UUID]:
        """Find work that will never finish on its own and return it for re-enqueueing.

        * 'running' for too long: the worker died mid-execution -> reset to 'queued';
        * 'queued' for too long: the Celery message was lost or the task crashed
          before claiming it.
        Re-enqueueing is safe even if the original task is merely slow, because
        execute() claims queued -> running atomically and a duplicate becomes a no-op.
        """
        cutoff = datetime.now(UTC) - older_than
        reset = (
            update(Query)
            .where(Query.status == "running", Query.created_at < cutoff)
            .values(status="queued")
            .returning(Query.id)
        )
        ids = list((await self.session.execute(reset)).scalars().all())
        stale = select(Query.id).where(Query.status == "queued", Query.created_at < cutoff)
        ids += [i for i in (await self.session.execute(stale)).scalars().all() if i not in ids]
        return ids

    async def get(self, query_id: uuid.UUID) -> Query | None:
        return await self.session.get(Query, query_id, populate_existing=True)

    async def list_recent(
        self,
        *,
        limit: int,
        before: tuple[datetime, uuid.UUID] | None = None,
        api_key_id: uuid.UUID | None = None,
    ) -> Sequence[Query]:
        """Newest first. Keyset pagination on (created_at, id): the id tie-breaker means
        rows sharing a timestamp are never skipped or repeated across pages."""
        stmt = select(Query).order_by(Query.created_at.desc(), Query.id.desc()).limit(limit)
        if before is not None:
            stmt = stmt.where(tuple_(Query.created_at, Query.id) < tuple_(*before))
        if api_key_id is not None:
            stmt = stmt.where(Query.api_key_id == api_key_id)
        return (await self.session.execute(stmt)).scalars().all()


class OutcomeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        execution: Execution,
        query: Query,
        *,
        success: bool,
        source: str,
        detail: str | None = None,
    ) -> None:
        """Insert or replace the label for an execution (latest label wins)."""
        values = {
            "id": uuid.uuid4(),
            "execution_id": execution.id,
            "success": success,
            "source": source,
            "detail": detail,
            "agent": execution.agent,
            "embedding": query.embedding,
            "embedding_model": query.embedding_model,
            "latency_ms": execution.latency_ms,
            "cost_usd": execution.cost_usd,
        }
        stmt = pg_insert(Outcome).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Outcome.execution_id],
            set_={"success": success, "source": source, "detail": detail, "created_at": func.now()},
        )
        await self.session.execute(stmt)
        await self.session.refresh(execution, attribute_names=["outcome"])


class IdempotencyRepository:
    """Postgres-backed idempotency records (see api/idempotency.py for the protocol)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def try_begin(
        self, api_key_id: uuid.UUID, key: str, request_hash: str, ttl: timedelta
    ) -> IdempotencyKey | None:
        """Claim ``key``. Returns None if claimed, else the existing record."""
        stmt = (
            pg_insert(IdempotencyKey)
            .values(
                api_key_id=api_key_id,
                key=key,
                request_hash=request_hash,
                status="in_progress",
                expires_at=datetime.now(UTC) + ttl,
            )
            .on_conflict_do_nothing()
            .returning(IdempotencyKey.key)
        )
        claimed = (await self.session.execute(stmt)).scalar_one_or_none()
        await self.session.commit()
        if claimed is not None:
            return None
        existing = await self.session.get(IdempotencyKey, (api_key_id, key), populate_existing=True)
        if existing is not None and existing.expires_at < datetime.now(UTC):
            # Expired but not yet cleaned up: treat as free.
            await self.session.delete(existing)
            await self.session.commit()
            return await self.try_begin(api_key_id, key, request_hash, ttl)
        return existing

    async def complete(
        self, api_key_id: uuid.UUID, key: str, status_code: int, body: dict[str, Any]
    ) -> None:
        await self.session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.api_key_id == api_key_id, IdempotencyKey.key == key)
            .values(status="completed", response_status=status_code, response_body=body)
        )
        await self.session.commit()

    async def release(self, api_key_id: uuid.UUID, key: str) -> None:
        """Forget a key whose request failed, so the client can retry it."""
        await self.session.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.api_key_id == api_key_id, IdempotencyKey.key == key
            )
        )
        await self.session.commit()

    async def purge_expired(self) -> int:
        result = await self.session.execute(
            delete(IdempotencyKey).where(IdempotencyKey.expires_at < func.now())
        )
        await self.session.commit()
        return int(result.rowcount)  # type: ignore[attr-defined]


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def refresh(self) -> None:
        await self.session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY agent_stats"))
        await self.session.commit()

    async def agent_stats(self) -> dict[str, dict[str, Any]]:
        rows = (await self.session.execute(text("SELECT * FROM agent_stats"))).mappings().all()
        return {row["agent"]: dict(row) for row in rows}


class BenchmarkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, run: dict[str, Any]) -> None:
        stmt = pg_insert(BenchmarkRun).values(**run)
        updatable = {k: v for k, v in run.items() if k != "id"}
        stmt = stmt.on_conflict_do_update(index_elements=[BenchmarkRun.id], set_=updatable)
        await self.session.execute(stmt)
        await self.session.commit()

    async def list(self, limit: int = 50) -> Sequence[BenchmarkRun]:
        stmt = select(BenchmarkRun).order_by(BenchmarkRun.created_at.desc()).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def get(self, run_id: str) -> BenchmarkRun | None:
        return await self.session.get(BenchmarkRun, run_id)


def decision_to_dict(decision: RoutingDecision) -> dict[str, Any]:
    data = asdict(decision)
    data["candidates"] = _candidates_json(decision)
    return data
