"""Per-API-key token-bucket rate limiting in Redis.

The whole read-refill-decide-write cycle runs as one Lua script, so it is atomic
across API replicas without locks. Time comes from Redis (``TIME``) rather than the
callers, so clock skew between replicas cannot mint extra tokens.

Each key has ``capacity`` tokens (burst size) refilled continuously at
``per_minute / 60`` tokens per second; a request costs one token.

If Redis is unreachable we fail *open* (allow the request, log, count it): for this
service availability matters more than strict enforcement. See ADR 0006.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger

log = get_logger(__name__)

_RATE_LIMIT_LUA = """
local capacity = tonumber(ARGV[1])
local rate     = tonumber(ARGV[2])   -- tokens per second
local cost     = tonumber(ARGV[3])
local t        = redis.call('TIME')
local now      = tonumber(t[1]) + tonumber(t[2]) / 1000000

local state  = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts     = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

tokens = math.min(capacity, tokens + math.max(0, now - ts) * rate)
local allowed = 0
local retry_after = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after = (cost - tokens) / rate
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', KEYS[1], math.ceil(capacity / rate) + 60)
return {allowed, tostring(tokens), tostring(retry_after)}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    limit_per_minute: int
    remaining: int
    retry_after_s: float


class RateLimiter:
    def __init__(self, redis: Redis, default_per_minute: int, burst: int) -> None:
        self._redis = redis
        self._default = default_per_minute
        self._burst = burst
        self._script = redis.register_script(_RATE_LIMIT_LUA)

    async def hit(self, identity: str, per_minute: int | None = None) -> RateLimitResult:
        limit = per_minute or self._default
        capacity = max(self._burst, 1)
        try:
            allowed, tokens, retry_after = await self._script(
                keys=[f"ar:ratelimit:{identity}"], args=[capacity, limit / 60.0, 1]
            )
        except RedisError as exc:
            log.warning("rate_limiter_unavailable_failing_open", error=repr(exc))
            metrics.ERRORS.labels(kind="redis_rate_limit").inc()
            return RateLimitResult(True, limit, capacity, 0.0)
        result = RateLimitResult(
            allowed=bool(int(allowed)),
            limit_per_minute=limit,
            remaining=int(float(tokens)),
            retry_after_s=float(retry_after),
        )
        if not result.allowed:
            metrics.RATE_LIMITED.inc()
        return result
