# Architecture decision records

| # | Decision | Status |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith + Celery worker, routing in-process | Accepted |
| [0002](0002-local-embeddings.md) | Local ONNX embeddings (fastembed, bge-small-en-v1.5) | Accepted |
| [0003](0003-pgvector-routing-memory.md) | pgvector in the primary Postgres for routing history | Accepted |
| [0004](0004-outcome-matrix-replay.md) | Evaluate routers by replay over a recorded outcome matrix | Accepted |
| [0005](0005-transparent-scoring-function.md) | Transparent weighted scoring with Bayesian shrinkage (no learned router) | Accepted |
| [0006](0006-rate-limiting.md) | Redis token bucket per API key, fail-open | Accepted |
| [0007](0007-idempotency-in-postgres.md) | Idempotency keys stored in Postgres | Accepted |
| [0008](0008-thin-llm-client.md) | Thin httpx client for an OpenAI-compatible API (Groq), no vendor SDK | Accepted |
| [0009](0009-celery.md) | Celery (Redis broker) for background work | Accepted |

Format: context, decision, consequences, alternatives considered.
