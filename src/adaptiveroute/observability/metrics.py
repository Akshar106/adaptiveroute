"""Prometheus metric definitions (one place, so dashboards and code stay in sync).

Naming follows Prometheus conventions: ``_total`` for counters, base units
(seconds, USD) in the name. Label sets are kept low-cardinality: strategies and
agents are small fixed sets, and HTTP routes use the route *template*, never the
raw path.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)

HTTP_REQUESTS = Counter(
    "ar_http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "ar_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)

ROUTING_DECISIONS = Counter(
    "ar_routing_decisions_total", "Routing decisions by strategy and agent", ["strategy", "agent"]
)
ROUTING_LATENCY = Histogram(
    "ar_routing_duration_seconds",
    "Time to make a routing decision (including embedding / LLM router call)",
    ["strategy"],
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
ROUTING_FALLBACKS = Counter(
    "ar_routing_fallbacks_total", "Router failures that fell back to another strategy", ["strategy"]
)

AGENT_EXECUTIONS = Counter(
    "ar_agent_executions_total", "Agent executions by status", ["agent", "status"]
)
AGENT_LATENCY = Histogram(
    "ar_agent_execution_duration_seconds",
    "Agent execution latency (all LLM attempts)",
    ["agent"],
    buckets=(0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 90),
)
AGENT_INFLIGHT = Gauge(
    "ar_agent_inflight",
    "Executions currently running in this process",
    ["agent"],
    multiprocess_mode="livesum",
)
LLM_TOKENS = Counter("ar_llm_tokens_total", "LLM tokens consumed", ["model", "kind"])
COST_USD = Counter(
    "ar_cost_usd_total", "Estimated spend in USD (list prices)", ["agent", "component"]
)
TASK_OUTCOMES = Counter(
    "ar_task_outcomes_total", "Task-success labels recorded", ["agent", "source", "success"]
)
FAILOVERS = Counter(
    "ar_execution_failovers_total", "Executions retried on the runner-up agent", ["from_agent"]
)

ERRORS = Counter("ar_errors_total", "Handled errors by kind", ["kind"])
RATE_LIMITED = Counter("ar_rate_limited_total", "Requests rejected by the rate limiter")
CACHE_REQUESTS = Counter("ar_cache_requests_total", "Cache lookups", ["cache", "result"])
