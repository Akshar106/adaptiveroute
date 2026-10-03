"""Celery tasks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from adaptiveroute.container import Container
from adaptiveroute.db.repositories import IdempotencyRepository, QueryRepository, StatsRepository
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.worker.celery_app import celery_app
from adaptiveroute.worker.runtime import run

log = get_logger(__name__)

# Infrastructure blips are retried with exponential backoff + jitter. Agent/LLM
# failures are not exceptions here: the executor records them as results.
TRANSIENT = (OperationalError, RedisConnectionError, ConnectionError)


async def enqueue_execution(query_id: uuid.UUID, use_cache: bool) -> None:
    await asyncio.to_thread(execute_query.apply_async, args=(str(query_id), use_cache))


@celery_app.task(
    name="adaptiveroute.execute_query",
    autoretry_for=TRANSIENT,
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=5,
)
def execute_query(query_id: str, use_cache: bool = True) -> dict[str, str]:
    async def go(c: Container) -> str:
        query = await c.service.execute(uuid.UUID(query_id), use_cache=use_cache)
        return query.status

    status = run(go)
    log.info("query_executed", query_id=query_id, status=status)
    return {"query_id": query_id, "status": status}


@celery_app.task(name="adaptiveroute.refresh_agent_stats", autoretry_for=TRANSIENT, max_retries=3)
def refresh_agent_stats() -> None:
    async def go(c: Container) -> None:
        async with c.sessions() as session:
            await StatsRepository(session).refresh()

    run(go)


@celery_app.task(name="adaptiveroute.purge_idempotency_keys", autoretry_for=TRANSIENT)
def purge_idempotency_keys() -> int:
    async def go(c: Container) -> int:
        async with c.sessions() as session:
            return await IdempotencyRepository(session).purge_expired()

    purged = run(go)
    log.info("idempotency_keys_purged", count=purged)
    return purged


@celery_app.task(name="adaptiveroute.recover_stuck_queries", autoretry_for=TRANSIENT)
def recover_stuck_queries(older_than_minutes: int = 10) -> list[str]:
    """Re-queue queries left 'running' by a worker that died mid-execution."""

    async def go(c: Container) -> list[uuid.UUID]:
        async with c.sessions() as session:
            ids = await QueryRepository(session).requeue_stuck(
                timedelta(minutes=older_than_minutes)
            )
            await session.commit()
        return ids

    ids = run(go)
    for query_id in ids:
        execute_query.apply_async(args=(str(query_id), False))
    if ids:
        log.warning("requeued_stuck_queries", count=len(ids))
    return [str(i) for i in ids]


@celery_app.task(name="adaptiveroute.run_benchmark", soft_time_limit=3600, time_limit=3700)
def run_benchmark(run_id: str, options: dict[str, Any]) -> dict[str, Any]:
    """Replay the committed outcome matrix (no new LLM calls) and store the report."""
    from adaptiveroute.evaluation.service import run_replay_benchmark

    async def go(c: Container) -> dict[str, Any]:
        return await run_replay_benchmark(c, run_id, options)

    return run(go)
