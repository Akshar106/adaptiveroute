"""Application service: route a query, persist the decision, execute, persist results.

Transactions are kept short and never span an LLM call: we commit the routing
decision, release the connection, run the agent (seconds), then open a new session to
store the execution. Holding a pooled connection across slow network I/O is the
classic way to exhaust a connection pool under load.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptiveroute.agents import AgentExecutor, AgentRegistry
from adaptiveroute.db.models import Query
from adaptiveroute.db.repositories import OutcomeRepository, QueryRepository
from adaptiveroute.domain import AgentSpec, ExecutionResult, RoutingDecision
from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.ports import Embedder
from adaptiveroute.response_cache import ResponseCache
from adaptiveroute.routing import QueryContext, Router

log = get_logger(__name__)
tracer = trace.get_tracer(__name__)


class UnknownStrategyError(ValueError):
    pass


class QueryNotFoundError(LookupError):
    pass


class ExecutionTimeoutError(TimeoutError):
    def __init__(self, query_id: uuid.UUID) -> None:
        super().__init__(f"query {query_id} did not finish within the request timeout")
        self.query_id = query_id


@dataclass(frozen=True, slots=True)
class SubmitOptions:
    strategy: str
    execute: bool = True
    run_async: bool = False
    use_cache: bool = True


# Celery's task.delay, injected so the service doesn't import the worker.
Enqueue = Callable[[uuid.UUID, bool], Awaitable[None]]


class QueryService:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        routers: dict[str, Router],
        executor: AgentExecutor,
        embedder: Embedder,
        sessions: async_sessionmaker[AsyncSession],
        response_cache: ResponseCache | None,
        failover: bool,
        sync_timeout_s: float,
        enqueue: Enqueue | None = None,
    ) -> None:
        self.registry = registry
        self.routers = routers
        self._executor = executor
        self._embedder = embedder
        self._sessions = sessions
        self._cache = response_cache
        self._failover = failover
        self._sync_timeout_s = sync_timeout_s
        self._enqueue = enqueue

    # --- routing --------------------------------------------------------------------

    def router(self, strategy: str) -> Router:
        try:
            return self.routers[strategy]
        except KeyError:
            raise UnknownStrategyError(
                f"unknown or unavailable strategy {strategy!r}; available: {sorted(self.routers)}"
            ) from None

    async def compare(self, text: str) -> tuple[float | None, dict[str, RoutingDecision]]:
        """Run every router on the same query (no execution, nothing persisted).

        The embedding is computed once and shared, so per-router latencies here exclude
        it; it is reported separately.
        """
        ctx = QueryContext(text, self._embedder)
        await ctx.embedding()
        decisions = {}
        for name, router in self.routers.items():
            decisions[name] = await router.route(ctx)
        return ctx.embedding_ms, decisions

    # --- submit ---------------------------------------------------------------------

    async def submit(
        self,
        text: str,
        options: SubmitOptions,
        *,
        api_key_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> Query:
        router = self.router(options.strategy)
        ctx = QueryContext(text, self._embedder)
        decision = await router.route(ctx)
        embedding = await ctx.embedding()  # free if the router already needed it

        # Every executable query starts 'queued'; execute() claims it atomically.
        status = "queued" if options.execute else "routed"
        span_ctx = trace.get_current_span().get_span_context()
        async with self._sessions() as session:
            query = await QueryRepository(session).create(
                text_=text,
                embedding=embedding,
                embedding_model=self._embedder.model_name,
                embedding_ms=ctx.embedding_ms,
                decision=decision,
                status=status,
                api_key_id=api_key_id,
                request_id=request_id,
                trace_id=format(span_ctx.trace_id, "032x") if span_ctx.is_valid else None,
            )
            await session.commit()

        if status == "routed":
            return query
        if options.run_async:
            if self._enqueue is None:
                raise RuntimeError("async execution requested but no task queue configured")
            await self._enqueue(query.id, options.use_cache)
            return query

        try:
            async with asyncio.timeout(self._sync_timeout_s):
                return await self.execute(query.id, use_cache=options.use_cache)
        except TimeoutError:
            await self._mark(query.id, "failed")
            raise ExecutionTimeoutError(query.id) from None

    # --- execution (sync path and Celery worker) ----------------------------------

    async def execute(self, query_id: uuid.UUID, *, use_cache: bool = True) -> Query:
        async with self._sessions() as session:
            repo = QueryRepository(session)
            claimed = await repo.claim(query_id)
            await session.commit()
            query = await repo.get(query_id)
            if query is None:
                raise QueryNotFoundError(str(query_id))
            if not claimed:
                return query  # already running or finished elsewhere (e.g. task redelivery)
            text = query.text
            ranked = self._ranked_agents(query)

        with tracer.start_as_current_span("query.execute") as span:
            span.set_attribute("ar.query_id", str(query_id))
            attempts: list[tuple[AgentSpec, ExecutionResult]] = []
            primary = self.registry.get(ranked[0])
            attempts.append((primary, await self._run(primary, text, use_cache)))
            if not attempts[0][1].ok and self._failover and len(ranked) > 1:
                backup = self.registry.get(ranked[1])
                log.info("execution_failover", query_id=str(query_id), to_agent=backup.name)
                metrics.FAILOVERS.labels(from_agent=primary.name).inc()
                attempts.append((backup, await self._run(backup, text, use_cache)))

        async with self._sessions() as session:
            repo = QueryRepository(session)
            query = await repo.get(query_id)
            assert query is not None
            for attempt_no, (agent, result) in enumerate(attempts, start=1):
                await repo.add_execution(query, result, agent, attempt_no)
            await repo.set_status(query, "completed" if attempts[-1][1].ok else "failed")
            await session.commit()
            return query

    async def _run(self, agent: AgentSpec, text: str, use_cache: bool) -> ExecutionResult:
        if use_cache and self._cache is not None:
            cached = await self._cache.get(agent, text)
            if cached is not None:
                return cached
        result = await self._executor.run(agent, text)
        if use_cache and self._cache is not None:
            await self._cache.put(agent, text, result)
        return result

    def _ranked_agents(self, query: Query) -> list[str]:
        """Selected agent first, then the rest by router score (for failover)."""
        others = sorted(
            (c for c in query.candidates if c["agent"] != query.selected_agent),
            key=lambda c: c["score"],
            reverse=True,
        )
        known = set(self.registry.names)
        return [query.selected_agent, *(c["agent"] for c in others if c["agent"] in known)]

    async def _mark(self, query_id: uuid.UUID, status: str) -> None:
        async with self._sessions() as session:
            repo = QueryRepository(session)
            query = await repo.get(query_id)
            if query is not None and query.status not in ("completed", "failed"):
                await repo.set_status(query, status)
                await session.commit()

    # --- feedback ---------------------------------------------------------------------

    async def record_feedback(
        self, query_id: uuid.UUID, *, success: bool, comment: str | None, source: str = "user"
    ) -> Query:
        """Label the final execution of a query; this is what the adaptive router learns from."""
        async with self._sessions() as session:
            repo = QueryRepository(session)
            query = await repo.get(query_id)
            if query is None:
                raise QueryNotFoundError(str(query_id))
            if not query.executions:
                raise ValueError("query has not been executed yet")
            final = query.executions[-1]
            await OutcomeRepository(session).record(
                final, query, success=success, source=source, detail=comment
            )
            await session.commit()
            metrics.TASK_OUTCOMES.labels(
                agent=final.agent, source=source, success=str(success).lower()
            ).inc()
            return query

    async def get(self, query_id: uuid.UUID) -> Query:
        async with self._sessions() as session:
            query = await QueryRepository(session).get(query_id)
        if query is None:
            raise QueryNotFoundError(str(query_id))
        return query
