# Load tests

Closed-loop HTTP load tests against the full Docker Compose stack, using
`scripts/loadtest.py`. They measure **this service** (auth → rate limit → embedding →
routing → Postgres write), not the LLM provider: requests use `execute=false`. With
execution enabled, throughput is capped by the provider's rate limits (Groq free tier:
30 requests/min per model), not by this service.

## Setup (2026-10-03)

- **Host:** MacBook Air, Apple M4 (10 cores), 24 GB RAM, Docker Desktop. All
  containers share one VM, including the load generator's target.
- **API:** one container, one uvicorn process (no `--workers`). Postgres 17 + pgvector
  and Redis 7.4 run in the same Compose project.
- **Load:** 16 concurrent virtual users sending requests back to back. 5 s warm-up
  (excluded), then 30 s measured per strategy. Queries are drawn at random from the 150
  evaluation queries, so after the first pass the Redis embedding cache is warm.
- **Data:** the adaptive router's `outcomes` table was empty, so its kNN queries
  returned no rows. With a large history, expect somewhat higher adaptive latency
  (HNSW search).

```bash
uv run python scripts/loadtest.py --url http://localhost:8080 --key $KEY \
  --strategies round_robin,embedding,adaptive --concurrency 16 --duration 30 --warmup 5 --route-only
```

## Results

| Strategy | Tracing (100% sampled) | Throughput (req/s) | P50 (ms) | P95 (ms) | P99 (ms) | Errors |
|---|---|---|---|---|---|---|
| round_robin | on | 549.9 | 26.5 | 38.7 | 58.7 | 0% |
| embedding | on | 553.8 | 26.1 | 43.6 | 57.0 | 0% |
| adaptive | on | 387.0 | 38.5 | 61.1 | 72.0 | 0% |
| round_robin | off | 795.7 | 19.3 | 24.3 | 43.8 | 0% |
| embedding | off | 806.4 | 19.0 | 23.8 | 43.5 | 0% |
| adaptive | off | 566.2 | 26.9 | 37.3 | 52.0 | 0% |

Raw results: `2026-10-03-compose-route-only.json` (tracing on) and
`2026-10-03-compose-route-only-no-tracing.json` (tracing off).

## Reading the numbers

- **round_robin ≈ embedding.** Every strategy embeds the query anyway (it is stored for
  future kNN), so the embedding router's only extra work is a 5×384 matrix-vector
  product.
- **adaptive costs about 30% throughput.** That comes from two more Postgres round trips
  per decision (per-agent pgvector kNN via `LATERAL`, and `agent_stats`, cached for 5 s)
  plus a Redis pipeline for in-flight load.
- **Tracing every request costs about 30% throughput** and roughly 15 ms at P95 here.
  The AWS deployment therefore samples 10% (`AR_OTEL_SAMPLE_RATIO=0.1`).
- **This is one process on a laptop.** The API is stateless, so throughput scales
  horizontally with API containers until Postgres becomes the bottleneck.
