# System design

## 1. Problem

Given a natural-language query, choose which of five specialised LLM agents should
answer it, then run that agent. The choice should balance:

- **semantic relevance**: is this the right specialist?
- **historical task success**: did this agent actually solve similar queries?
- **expected latency** and **expected cost**;
- **current load**: avoid piling work onto a saturated agent.

The system should also record every decision and outcome so it improves with use, and
let four routing strategies be compared fairly.

### Non-goals

- Training a router model. The adaptive router is a transparent scoring function
  whose evidence comes from stored outcomes; see [ADR 0005](adr/0005-transparent-scoring-function.md).
- Multi-turn conversations, tool-using agent loops, streaming responses (future work).
- Multi-tenant isolation beyond per-API-key data scoping.

## 2. The agents

The agents differ in model, reasoning effort, system prompt and output contract, so
they differ in **quality, latency and cost**. That trade-off is what routing exploits.

| Agent | Model | Reasoning | Specialisation |
|---|---|---|---|
| `code` | gpt-oss-120b | medium | Python implementations in a single fenced block |
| `math` | gpt-oss-20b | medium | step-by-step, ends with `ANSWER: <number>` |
| `sql` | gpt-oss-20b | medium | knows the retail DB schema (SQLite), returns one query |
| `writer` | gpt-oss-20b | low | follows length/format constraints exactly |
| `knowledge` | gpt-oss-120b | low | concise factual answers |

The specialisation is real, not cosmetic. For example, only the SQL agent's prompt
contains the database schema, so sending a database question elsewhere usually fails
end to end. The benchmark's capability matrix measures how much each agent can do
outside its own domain.

## 3. Routing strategies

| Strategy | Decision rule | Cost per decision |
|---|---|---|
| `round_robin` | next slot of an atomic Redis counter | ~0 |
| `embedding` | argmax cosine(query, agent profile centroid) | one local embedding (~4 ms) |
| `llm` | gpt-oss-20b classifies into an agent with strict JSON-schema output; falls back to `embedding` on any failure | one LLM call (≈0.3–1 s, ~$0.00003) |
| `adaptive` | weighted score below | embedding + 2 Postgres reads + 1 Redis read (~3–4 ms) |

An **agent profile** is the normalised mean of the embeddings of the agent's
description and 8 hand-written example queries in `config/agents.yaml`. The examples
are kept disjoint from the evaluation set, and a test enforces that.

### 3.1 Adaptive scoring function

For query *q* and agent *a* (implemented in `routing/scoring.py`, a pure function):

```
score(a) =  w_sim  · S(a)
          + w_succ · P̂(a | q)
          − w_lat  · min(1, L̂(a | q) / latency_budget)
          − w_cost · min(1, Ĉ(a | q) / cost_budget)
          − w_load · min(1, inflight(a) / max_concurrency(a))
          + w_exp  · sqrt( ln(1 + N) / (1 + n(a)) )        (optional exploration)
```

- **Semantic fit**: `S(a) = exp((cos(q,a) − max_b cos(q,b)) / T)`. The best match gets
  1.0, and an agent whose similarity is `T` lower gets e⁻¹ ≈ 0.37. `T = 0.05` was set
  from the observed spread of bge-small similarities (top-vs-runner-up gaps are
  typically 0.02–0.2) **before** any benchmark result was seen.
- **Contextual success**, `P̂(a | q)`, is a Beta-Binomial posterior mean over the *k*
  most similar labelled past queries that agent *a* handled:

  ```
  w_i  = clip((sim_i − s_min) / (1 − s_min), 0, 1)        neighbour weight
  P̂   = (k₀ · P_global(a) + Σ w_i · y_i) / (k₀ + Σ w_i)
  ```

  An identical past query counts as one full observation. Queries below `s_min = 0.6`
  count as zero; for bge-small, 90% of query pairs that belong to different agents
  fall below 0.52. `P_global(a)` is the agent's overall success rate, itself shrunk
  towards a prior of 0.7. `k₀ = 3` pseudo-observations stop a couple of lucky or
  unlucky outcomes from dominating.
- **Expected latency and cost** use the same shrinkage, so they are also contextual. A
  long code-generation query looks expensive *for the agents that were expensive on
  similar queries*.
- **Load** comes from Redis sorted sets of in-flight executions. A saturated agent
  (load ≥ 1) is ineligible unless every agent is saturated.
- The **explanation** is computed directly from the per-term contributions stored with
  every candidate, e.g. "'sql' scored 0.671 … the biggest advantage over 'math' was
  semantic fit (+0.39)". The dashboard draws them as a diverging stacked bar.

**A property worth knowing.** With the default weights, history can swing a score by at
most `w_succ = 0.35`, while semantic fit can swing it by up to `w_sim = 0.45`. History
therefore overturns the semantic choice only when the similarity gap is small
(about ≤ 1.4·T). Those near-ties are exactly where embedding routing makes its
mistakes. Two unit tests pin this behaviour down
(`test_adaptive_learns_from_history_on_close_calls`,
`test_history_does_not_override_a_large_semantic_gap`).

All weights and constants live in `config/routing.yaml`, validated at startup, and are
copied into every benchmark report.

## 4. Data model and consistency

See [database.md](database.md) for the schema. Key decisions:

- **Every decision is stored before execution starts** (`queries.status = queued`),
  including the full candidate list with per-term scores and the query embedding.
  Nothing about a decision is lost, even if execution crashes.
- **Outcome labels are denormalised.** The adaptive router's main read is "the k most
  similar labelled queries per agent", which becomes a single-table pgvector search
  (`outcomes` carries agent, embedding, latency and cost) instead of a three-way join.
- **Exactly-once execution, per query.** `execute()` atomically claims the query
  (`UPDATE … SET status='running' WHERE status='queued' RETURNING id`). Celery's
  at-least-once delivery (`acks_late`) plus this claim gives effectively-once LLM
  spend per query. A beat task re-queues queries stuck in `running` after a worker
  crash.
- **Idempotent client retries.** `Idempotency-Key` records in Postgres replay the
  stored response for retries, return 409 while the first request is still running,
  and return 422 if the key is reused with a different body.
- **Short transactions.** No DB connection is held across an LLM call (which takes
  seconds), so a slow provider cannot exhaust the connection pool.

## 5. Failure modes

| Failure | Behaviour |
|---|---|
| Provider 429 | retry after `Retry-After` (never past the request deadline) |
| Provider 5xx / network error / timeout | exponential backoff with full jitter, bounded attempts, one deadline per call |
| Provider 4xx (bad request) | fail fast (retrying can't help) |
| Selected agent still fails | **fail over** to the runner-up agent; both executions are stored |
| LLM router fails or returns invalid JSON | fall back to the embedding router; `fallback="embedding"` is recorded and counted |
| Sync request exceeds `AR_SYNC_REQUEST_TIMEOUT_S` | 504 with the query id; query marked `failed` |
| Redis down | rate limiter **fails open**; load tracker reports 0; caches miss. All logged and counted ([ADR 0006](adr/0006-rate-limiting.md)) |
| Postgres down | Requests fail fast with problem+json 503/500; `/readyz` reports `not_ready`. On ECS the ALB checks `/healthz` (liveness) on purpose: ECS *replaces* targets that fail the ALB check, so a dependency-aware check would turn a DB outage into a restart storm |
| Worker dies mid-task | message redelivered (`acks_late`); the claim prevents double execution; the stuck-query sweeper recovers anything left `running` |
| Daily provider quota hit during benchmark collection | collection stops cleanly; the matrix file is append-only, so re-running resumes |

## 6. Capacity and scaling

**Measured** (see [evaluation.md](evaluation.md#5-throughput-load-test)): a single
uvicorn process handles several hundred route-only requests per second. That figure
includes auth, the rate limit, embedding, routing and a Postgres write. The adaptive
strategy costs about 4 ms more than the embedding strategy (pgvector kNN plus stats).
End-to-end throughput with execution is bound by the LLM provider's rate limits
(Groq free tier: 30 requests and 8K tokens per minute per model), not by this
service.

Scaling levers, in the order they'd be needed:
1. More API replicas. The API process is stateless; all shared state is in Redis or
   Postgres.
2. More Celery workers for async execution (I/O-bound; prefork concurrency 4 per
   container).
3. pgvector: HNSW (`m=16, ef_construction=64`) keeps kNN sub-linear. At millions of
   labelled outcomes, partition `outcomes` by agent or add a recency window.
4. Embedding: about 4 ms on CPU, cached in Redis by text hash. At high QPS, batch
   requests or move to a GPU embedding service (this would be the first real reason to
   split out a service).

## 7. Security

- **API keys:** random 256-bit secrets. Only `HMAC-SHA256(pepper, key)` is stored; the
  pepper lives in env or Secrets Manager. Lookup is by a public 8-character prefix,
  then a constant-time compare. Keys are shown once and revocable; the auth cache TTL
  of 30 s bounds revocation latency.
- **Roles:** `user` sees only their own queries; `admin` manages keys and benchmarks.
  Another user's query id returns 404, not 403, so ids aren't confirmed.
- **Limits:** per-key token-bucket rate limiting; request size limits (4,000 characters
  per query); strict pydantic models (`extra="forbid"`).
- **LLM router injection:** the query is wrapped in `<query>` tags and marked
  untrusted, and the output is constrained by a JSON schema whose `agent` field is an
  enum. Even a successful injection can only pick a valid agent.
- **Errors:** RFC 9457 problem+json with a request id; unhandled exceptions are logged
  server-side with stack traces, and clients get a generic 500.
- **Benchmark code execution:** the Python checker runs model-generated code in an
  isolated interpreter (`-I`, empty environment, CPU and wall-clock limits, temp dir).
  That is adequate for a benchmark job, but it is **not** a security sandbox
  ([limitations](limitations.md)).
- **Static analysis:** ruff's bandit rules (`S`) run in CI. Secrets never appear in
  logs (`SecretStr`) or the repository (`.env` is gitignored).

## 8. Observability

- **Logs:** structlog JSON with `request_id`, `api_key_id`, `trace_id` and `span_id`
  on every line.
- **Traces:** OpenTelemetry across FastAPI, SQLAlchemy, Redis, httpx and Celery, plus
  custom `route <strategy>` and `agent.execute <agent>` spans carrying `ar.*` and
  `gen_ai.*` attributes (model, tokens, cost). The dashboard's trace view shows a
  waterfall for any query.
- **Metrics:** Prometheus exposition from the API and the worker (multiprocess mode).
  Grafana's "AdaptiveRoute – overview" dashboard shows request latency, routing
  decisions, agent performance, errors, throughput and cost.

Details are in [observability.md](observability.md).
