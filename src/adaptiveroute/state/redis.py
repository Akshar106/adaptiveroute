"""Redis-backed state shared by all API replicas and workers.

Load tracking uses one sorted set per agent: member = execution token, score = start
time. A crashed process cannot leak a permanent "in-flight" count because entries
older than ``stale_after_s`` (longer than any agent timeout) are ignored and pruned.
Both classes degrade gracefully: if Redis is unavailable, routing still works with
load treated as zero, and the error is logged and counted.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.exceptions import RedisError

from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger

log = get_logger(__name__)


class RedisLoadTracker:
    def __init__(
        self, redis: Redis, *, stale_after_s: float = 300.0, prefix: str = "ar:inflight"
    ) -> None:
        self._redis = redis
        self._stale_after_s = stale_after_s
        self._prefix = prefix

    def _key(self, agent: str) -> str:
        return f"{self._prefix}:{agent}"

    async def inflight(self, agents: Sequence[str]) -> dict[str, int]:
        cutoff = time.time() - self._stale_after_s
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for agent in agents:
                    pipe.zcount(self._key(agent), cutoff, "+inf")
                counts = await pipe.execute()
        except RedisError as exc:
            log.warning("load_tracker_unavailable", error=repr(exc))
            metrics.ERRORS.labels(kind="redis_load").inc()
            return dict.fromkeys(agents, 0)
        return {a: int(c) for a, c in zip(agents, counts, strict=True)}

    @asynccontextmanager
    async def track(self, agent: str) -> AsyncIterator[None]:
        key, token, now = self._key(agent), uuid.uuid4().hex, time.time()
        registered = False
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.zadd(key, {token: now})
                pipe.zremrangebyscore(key, "-inf", now - self._stale_after_s)
                pipe.expire(key, int(self._stale_after_s * 2))
                await pipe.execute()
            registered = True
        except RedisError as exc:
            log.warning("load_tracker_unavailable", error=repr(exc))
            metrics.ERRORS.labels(kind="redis_load").inc()
        metrics.AGENT_INFLIGHT.labels(agent=agent).inc()
        try:
            yield
        finally:
            metrics.AGENT_INFLIGHT.labels(agent=agent).dec()
            if registered:
                try:
                    await self._redis.zrem(key, token)
                except RedisError as exc:  # entry will age out via stale_after_s
                    log.warning("load_tracker_release_failed", error=repr(exc))


class RedisCounter:
    def __init__(self, redis: Redis, prefix: str = "ar:counter") -> None:
        self._redis = redis
        self._prefix = prefix

    async def next(self, key: str) -> int:
        # INCR is atomic across replicas; subtract 1 so the sequence starts at 0.
        return int(await self._redis.incr(f"{self._prefix}:{key}")) - 1
