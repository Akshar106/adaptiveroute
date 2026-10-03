"""Exact-match response cache for agent outputs.

Key = (agent, agent fingerprint, sha256 of the whitespace/case-normalised query), so
any change to an agent's model or prompt invalidates its entries automatically. Only
successful executions are cached. A hit costs nothing and is recorded as an execution
with ``cache_hit=True`` (excluded from the agent latency statistics).
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from adaptiveroute.domain import AgentSpec, ExecutionResult, ExecutionStatus
from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger

log = get_logger(__name__)


def normalise_query(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


class ResponseCache:
    def __init__(self, redis: Redis, ttl_s: int, prefix: str = "ar:resp") -> None:
        self._redis = redis
        self._ttl_s = ttl_s
        self._prefix = prefix

    def key(self, agent: AgentSpec, query: str) -> str:
        digest = hashlib.sha256(normalise_query(query).encode()).hexdigest()
        return f"{self._prefix}:{agent.name}:{agent.fingerprint}:{digest}"

    async def get(self, agent: AgentSpec, query: str) -> ExecutionResult | None:
        start = time.perf_counter()
        try:
            raw = await self._redis.get(self.key(agent, query))
        except RedisError as exc:
            log.warning("response_cache_unavailable", error=repr(exc))
            return None
        metrics.CACHE_REQUESTS.labels(cache="response", result="hit" if raw else "miss").inc()
        if raw is None:
            return None
        data = json.loads(raw)
        return ExecutionResult(
            agent=agent.name,
            model=agent.model,
            status=ExecutionStatus.SUCCESS,
            output=data["output"],
            error=None,
            latency_ms=(time.perf_counter() - start) * 1000,
            cache_hit=True,
        )

    async def put(self, agent: AgentSpec, query: str, result: ExecutionResult) -> None:
        if not result.ok or result.cache_hit:
            return
        payload = json.dumps({"output": result.output, "cached_at": time.time()})
        try:
            await self._redis.set(self.key(agent, query), payload, ex=self._ttl_s)
        except RedisError as exc:
            log.warning("response_cache_write_failed", error=repr(exc))
