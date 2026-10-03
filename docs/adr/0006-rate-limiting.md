# 0006: Redis token bucket per API key, fail-open

**Context.** LLM calls cost money and providers rate-limit us, so clients need
per-key limits that hold across API replicas.

**Decision.** A token bucket per API key in Redis (burst = capacity, refill =
per-minute / 60), implemented as one Lua script. Read, refill, decide and write happen
atomically, and time comes from `redis.call('TIME')` so replica clock skew can't mint
tokens. A per-key override exists (`api_keys.rate_limit_per_minute`). If Redis is
unreachable, the limiter **fails open**: it allows the request, logs it and counts it
in `ar_errors_total{kind="redis_rate_limit"}`.

**Consequences.**
- Smooth limits with bursts; one round trip per request; tested for atomicity under 50
  concurrent hits.
- A Redis outage means no enforcement until it recovers. That is acceptable here
  because availability matters more than strict enforcement, and the provider's own
  limits still cap spend. A paid public API would likely fail closed instead.

**Alternatives.**
- Fixed window counters: bursts of 2× at window boundaries.
- Sliding log: memory grows with the rate.
- In-process limits: wrong with more than one replica.
