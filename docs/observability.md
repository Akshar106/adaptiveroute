# Observability

Locally, `docker compose up` gives you:

| Tool | URL | What's there |
|---|---|---|
| Grafana | http://localhost:3000 (anonymous viewer; admin/admin) | **AdaptiveRoute – overview** dashboard (provisioned) |
| Prometheus | http://localhost:9090 | raw metrics from `api:8000/metrics` and `worker:9100` |
| Jaeger | http://localhost:16686 | traces for services `adaptiveroute-api` and `adaptiveroute-worker` |
| Dashboard | http://localhost:8000/#/traces/<trace_id> | waterfall view of one query's trace (proxied from Jaeger) |

## Logs

[structlog](https://www.structlog.org) writes one JSON object per line to stdout; on
AWS the lines go to CloudWatch Logs. Every line carries `timestamp`, `level`, `event`
and `logger`. Request-scoped lines also carry `request_id` and `api_key_id`. Lines
emitted inside a span carry `trace_id` and `span_id`, so you can jump from a log line
to its trace. uvicorn and Celery logs go through the same formatter.

Notable events: `http_request` (access log), `llm_retry`, `agent_failed`,
`execution_failover`, `llm_router_failed`, `rate_limiter_unavailable_failing_open`,
`requeued_stuck_queries`, `unhandled_error` (with stack trace).

## Traces

OpenTelemetry SDK → OTLP/gRPC → Jaeger locally, or the ADOT collector → AWS X-Ray in
production. Enable it with `AR_OTEL_ENABLED=true`; the sampling ratio is set by
`AR_OTEL_SAMPLE_RATIO` and is parent-based.

| Span | Source | Key attributes |
|---|---|---|
| `POST /v1/queries` | FastAPI instrumentation | http.* |
| `route <strategy>` | `routing/base.py` | `ar.routing.strategy`, `ar.routing.agent`, `ar.routing.fallback`, `ar.routing.cost_usd` |
| `query.execute` | `services/query_service.py` | `ar.query_id` |
| `agent.execute <agent>` | `agents/executor.py` | `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `ar.cost_usd`, `ar.attempts`, `ar.execution.status` |
| `POST` (to api.groq.com) | httpx instrumentation | one span per HTTP attempt, so retries are visible |
| SQL statements, Redis commands | SQLAlchemy/Redis instrumentation | db.statement |
| `run/execute_query` | Celery instrumentation (async mode) | the trace context is propagated from the API |

Each query row stores its `trace_id`, so the dashboard can fetch the trace for any
historical query.

## Metrics

All metric names start with `ar_`. Label sets are bounded: HTTP metrics use the route
*template* (`/v1/queries/{query_id}`), never raw paths.

| Metric | Type | Labels |
|---|---|---|
| `ar_http_requests_total` | counter | method, route, status |
| `ar_http_request_duration_seconds` | histogram | method, route |
| `ar_routing_decisions_total` | counter | strategy, agent |
| `ar_routing_duration_seconds` | histogram | strategy |
| `ar_routing_fallbacks_total` | counter | strategy |
| `ar_agent_executions_total` | counter | agent, status |
| `ar_agent_execution_duration_seconds` | histogram | agent |
| `ar_agent_inflight` | gauge (multiprocess `livesum`) | agent |
| `ar_execution_failovers_total` | counter | from_agent |
| `ar_llm_tokens_total` | counter | model, kind (input/output) |
| `ar_cost_usd_total` | counter | agent, component (`agent` or `router:<strategy>`) |
| `ar_task_outcomes_total` | counter | agent, source, success |
| `ar_cache_requests_total` | counter | cache (embedding/response), result (hit/miss) |
| `ar_rate_limited_total` | counter | |
| `ar_errors_total` | counter | kind (redis_rate_limit, redis_load, router, sync_timeout, unhandled) |

Celery prefork children write to `PROMETHEUS_MULTIPROC_DIR`; the worker's main process
serves the aggregate on `:9100`.

## Grafana dashboard

`deploy/grafana/dashboards/adaptiveroute-overview.json` is **generated** by
`scripts/build_grafana_dashboard.py`. Panels are declared in Python with their PromQL;
edit the script, not the JSON. Rows:

1. **Overview:** request rate, 5xx ratio, API P95, routing decisions/min, estimated
   spend over the selected range, executions in flight.
2. **API latency & throughput:** P50/P95 by route; requests by status.
3. **Routing decisions:** decisions by strategy, selected-agent distribution, routing
   P95 by strategy, LLM-router fallbacks, execution failovers.
4. **Agent performance:** execution P95, error ratio, labelled task-success rate,
   executions and in-flight by agent. Each agent keeps one colour across all panels,
   matching the React dashboard.
5. **Cost & tokens:** USD/hour by agent plus the LLM router; tokens per second by model.
6. **Caching, rate limiting & errors:** embedding and response cache hit ratios, 429s,
   errors by kind.

## Suggested alerts (not provisioned)

| Alert | Expression (sketch) | Why |
|---|---|---|
| High 5xx ratio | `5xx ratio > 2% for 5m` | user-facing failures |
| Agent error spike | `agent error ratio > 20% for 10m` | provider or model degradation (failover is masking it) |
| LLM-router fallbacks | `rate(ar_routing_fallbacks_total[10m]) > 0.1` | router silently degraded to embedding routing |
| Spend burn | `3600 * sum(rate(ar_cost_usd_total[1h])) > budget_per_hour` | runaway cost |
| Stuck work | `increase(... requeued_stuck_queries log events)` or `sum(ar_agent_inflight) == max for 15m` | crashed or overloaded workers |
