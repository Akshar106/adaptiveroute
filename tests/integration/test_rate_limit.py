import asyncio

from redis.asyncio import Redis

from adaptiveroute.api.rate_limit import RateLimiter


async def test_bucket_allows_burst_then_blocks(redis: Redis) -> None:
    limiter = RateLimiter(redis, default_per_minute=60, burst=3)
    results = [await limiter.hit("k") for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert results[2].remaining == 0
    assert 0 < results[3].retry_after_s <= 1.0  # 1 token/s refill


async def test_bucket_refills_over_time(redis: Redis) -> None:
    limiter = RateLimiter(redis, default_per_minute=600, burst=1)  # 10 tokens/s
    assert (await limiter.hit("k")).allowed
    assert not (await limiter.hit("k")).allowed
    await asyncio.sleep(0.15)
    assert (await limiter.hit("k")).allowed


async def test_buckets_are_independent_per_identity(redis: Redis) -> None:
    limiter = RateLimiter(redis, default_per_minute=60, burst=1)
    assert (await limiter.hit("alice")).allowed
    assert (await limiter.hit("bob")).allowed
    assert not (await limiter.hit("alice")).allowed


async def test_concurrent_hits_never_exceed_capacity(redis: Redis) -> None:
    """The Lua script is atomic, so 50 concurrent requests get exactly `burst` passes."""
    limiter = RateLimiter(redis, default_per_minute=1, burst=5)
    results = await asyncio.gather(*(limiter.hit("k") for _ in range(50)))
    assert sum(r.allowed for r in results) == 5


async def test_fails_open_when_redis_is_down() -> None:
    broken = Redis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    limiter = RateLimiter(broken, default_per_minute=1, burst=1)
    assert all([(await limiter.hit("k")).allowed for _ in range(3)])
    await broken.aclose()
