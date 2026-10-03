# 0007: Idempotency keys stored in Postgres

**Context.** A client that times out and retries `POST /v1/queries` must not pay for a
second LLM execution or create a duplicate record.

**Decision.** Store `(api_key_id, key)` in Postgres. The first request claims the key
with `INSERT … ON CONFLICT DO NOTHING`. When it finishes, the response status and body
are stored. Retries with the same body replay the stored response; a retry while the
first is still running gets 409. A different body with the same key gets 422. If the
handler fails with a 5xx or an exception, the key is released so the client can retry.
Records expire after 24 h and are purged hourly by Celery beat.

**Consequences.**
- Durable (survives Redis loss), transactional, and visible for debugging; the key is
  scoped per API key, so tenants can't collide.
- One extra small write per idempotent request.

**Alternatives.**
- Redis `SET NX` with a TTL: faster but not durable, so a lost key could cause a
  double spend.
- Deduplicating on request content: wrong, because users may legitimately ask the same
  thing twice.
