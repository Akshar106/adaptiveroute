# 0009: Celery (Redis broker) for background work

**Context.** We need async query execution (LLM calls of several seconds), periodic
maintenance (stats refresh, stuck-query recovery, idempotency purge) and long-running
benchmark replays.

**Decision.** Celery with Redis as the broker and result backend:
- `acks_late` plus `reject_on_worker_lost`, so a crashed worker's task is redelivered;
- `worker_prefetch_multiplier=1`;
- a visibility timeout longer than the hard time limit;
- beat for schedules.

Tasks call the same async `QueryService` through a per-process event loop
(`worker/runtime.py`). Redelivery is safe because execution claims the query
atomically.

**Consequences.**
- Mature retries, time limits, scheduling and monitoring; one broker shared with
  other Redis uses.
- Celery is synchronous at its core, so async code needs the per-process loop bridge.
  Prometheus needs multiprocess mode for prefork workers.

**Alternatives.**
- arq/Dramatiq: lighter and async-native (arq), but less established and without the
  built-in scheduler and time limits.
- FastAPI `BackgroundTasks`: work is lost on restart, with no retries or scheduling.
- SQS + Lambda: an AWS-only local development story.
