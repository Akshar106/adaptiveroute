import asyncio
import time

import numpy as np
from redis.asyncio import Redis

from adaptiveroute.embeddings import CachedEmbedder, HashingEmbedder
from adaptiveroute.state.redis import RedisCounter, RedisLoadTracker


async def test_load_tracker_counts_concurrent_executions(redis: Redis) -> None:
    tracker = RedisLoadTracker(redis)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def job() -> None:
        async with tracker.track("code"):
            entered.set()
            await release.wait()

    tasks = [asyncio.create_task(job()) for _ in range(3)]
    await entered.wait()
    await asyncio.sleep(0.05)
    assert await tracker.inflight(["code", "math"]) == {"code": 3, "math": 0}
    release.set()
    await asyncio.gather(*tasks)
    assert await tracker.inflight(["code"]) == {"code": 0}


async def test_load_tracker_releases_on_exception(redis: Redis) -> None:
    tracker = RedisLoadTracker(redis)
    try:
        async with tracker.track("sql"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert await tracker.inflight(["sql"]) == {"sql": 0}


async def test_stale_entries_from_crashed_processes_are_ignored(redis: Redis) -> None:
    tracker = RedisLoadTracker(redis, stale_after_s=60)
    # Simulate a process that registered work 2 minutes ago and died without cleanup.
    await redis.zadd("ar:inflight:writer", {"dead-token": time.time() - 120})
    assert await tracker.inflight(["writer"]) == {"writer": 0}


async def test_load_tracker_degrades_when_redis_is_down() -> None:
    broken = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    tracker = RedisLoadTracker(broken)
    assert await tracker.inflight(["code"]) == {"code": 0}
    async with tracker.track("code"):  # must not raise
        pass
    await broken.aclose()


async def test_counter_is_shared_and_monotonic(redis: Redis) -> None:
    a, b = RedisCounter(redis), RedisCounter(redis)  # e.g. two API replicas
    values = await asyncio.gather(*(c.next("rr") for c in [a, b] * 10))
    assert sorted(values) == list(range(20))


async def test_cached_embedder_hits_after_first_call(redis: Redis) -> None:
    calls = 0
    inner = HashingEmbedder(dim=32)

    class Counting(HashingEmbedder):
        async def embed(self, texts):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += len(texts)
            return await inner.embed(texts)

    cached = CachedEmbedder(Counting(dim=32), redis, ttl_s=60)
    first = await cached.embed(["alpha", "beta"])
    second = await cached.embed(["beta", "alpha", "gamma"])
    assert calls == 3  # alpha, beta, then only gamma
    np.testing.assert_array_equal(first[0], second[1])
    np.testing.assert_array_equal(first[1], second[0])
