"""Generate deploy/grafana/dashboards/adaptiveroute-overview.json.

Dashboards as code: panels are declared below with their PromQL, so a reviewer can
read the queries instead of diffing thousands of lines of exported JSON.

    uv run python scripts/build_grafana_dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = (
    Path(__file__).resolve().parents[1]
    / "deploy"
    / "grafana"
    / "dashboards"
    / "adaptiveroute-overview.json"
)
DS = {"type": "prometheus", "uid": "prometheus"}
NOT_PROBES = 'route!~"/metrics|/healthz|/readyz"'

# Fixed colour per agent (same slots as the React dashboard).
AGENT_COLORS = {
    "code": "#2a78d6",
    "math": "#eb6834",
    "sql": "#1baf7a",
    "writer": "#eda100",
    "knowledge": "#e87ba4",
}

_next_id = 0


def _id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def target(expr: str, legend: str = "", instant: bool = False) -> dict[str, Any]:
    return {
        "datasource": DS,
        "expr": expr,
        "legendFormat": legend,
        "refId": "ABCDEFGH"[0],
        "instant": instant,
    }


def _targets(*items: tuple[str, str]) -> list[dict[str, Any]]:
    out = []
    for i, (expr, legend) in enumerate(items):
        t = target(expr, legend)
        t["refId"] = "ABCDEFGH"[i]
        out.append(t)
    return out


def agent_overrides() -> list[dict[str, Any]]:
    return [
        {
            "matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}],
        }
        for name, color in AGENT_COLORS.items()
    ]


def row(title: str, y: int) -> dict[str, Any]:
    return {
        "type": "row",
        "title": title,
        "id": _id(),
        "collapsed": False,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def stat(
    title: str, expr: str, unit: str, x: int, y: int, w: int = 4, decimals: int = 2, desc: str = ""
) -> dict[str, Any]:
    return {
        "type": "stat",
        "id": _id(),
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"h": 4, "w": w, "x": x, "y": y},
        "targets": [target(expr, "", instant=True)],
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "none",
            "graphMode": "none",
            "textMode": "value",
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": decimals,
                "color": {"mode": "fixed", "fixedColor": "#2a78d6"},
            },
            "overrides": [],
        },
    }


def timeseries(
    title: str,
    targets: list[dict[str, Any]],
    unit: str,
    x: int,
    y: int,
    w: int = 12,
    h: int = 8,
    by_agent: bool = False,
    desc: str = "",
    stack: bool = False,
) -> dict[str, Any]:
    return {
        "type": "timeseries",
        "id": _id(),
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": targets,
        "options": {
            "legend": {
                "displayMode": "table",
                "placement": "right",
                "calcs": ["lastNotNull", "max"],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "palette-classic"},
                "custom": {
                    "lineWidth": 2,
                    "fillOpacity": 10 if stack else 0,
                    "showPoints": "never",
                    "spanNulls": True,
                    "stacking": {"mode": "normal" if stack else "none"},
                },
            },
            "overrides": agent_overrides() if by_agent else [],
        },
    }


def bargauge(
    title: str,
    expr: str,
    legend: str,
    unit: str,
    x: int,
    y: int,
    w: int = 12,
    h: int = 8,
    desc: str = "",
) -> dict[str, Any]:
    return {
        "type": "bargauge",
        "id": _id(),
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [target(expr, legend, instant=True)],
        "options": {
            "orientation": "horizontal",
            "displayMode": "basic",
            "showUnfilled": True,
            "reduceOptions": {"calcs": ["lastNotNull"]},
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "min": 0,
                "color": {"mode": "fixed", "fixedColor": "#2a78d6"},
            },
            "overrides": agent_overrides(),
        },
    }


def q(quantile: float, metric: str, by: str = "", match: str = "") -> str:
    group = f"le, {by}" if by else "le"
    sel = f"{{{match}}}" if match else ""
    return f"histogram_quantile({quantile}, sum by ({group}) (rate({metric}_bucket{sel}[5m])))"


def build() -> dict[str, Any]:
    panels: list[dict[str, Any]] = []
    y = 0
    panels.append(row("Overview", y))
    y += 1
    panels += [
        stat(
            "Request rate", f"sum(rate(ar_http_requests_total{{{NOT_PROBES}}}[5m]))", "reqps", 0, y
        ),
        stat(
            "HTTP 5xx ratio",
            '(sum(rate(ar_http_requests_total{status=~"5.."}[5m])) or vector(0)) '
            "/ clamp_min(sum(rate(ar_http_requests_total[5m])), 1e-9)",
            "percentunit",
            4,
            y,
        ),
        stat(
            "API latency P95",
            q(0.95, "ar_http_request_duration_seconds", match=NOT_PROBES),
            "s",
            8,
            y,
        ),
        stat(
            "Routing decisions / min",
            "60 * sum(rate(ar_routing_decisions_total[5m]))",
            "short",
            12,
            y,
            decimals=1,
        ),
        stat(
            "Estimated spend (range)",
            "sum(increase(ar_cost_usd_total[$__range])) or vector(0)",
            "currencyUSD",
            16,
            y,
            decimals=4,
            desc="Token counts x list prices (estimate).",
        ),
        stat(
            "Executions in flight",
            "sum(ar_agent_inflight) or vector(0)",
            "short",
            20,
            y,
            decimals=0,
        ),
    ]
    y += 4

    panels.append(row("API latency & throughput", y))
    y += 1
    panels += [
        timeseries(
            "HTTP latency by route (P50 / P95)",
            _targets(
                (q(0.5, "ar_http_request_duration_seconds", "route", NOT_PROBES), "p50 {{route}}"),
                (q(0.95, "ar_http_request_duration_seconds", "route", NOT_PROBES), "p95 {{route}}"),
            ),
            "s",
            0,
            y,
        ),
        timeseries(
            "Requests by status",
            _targets(
                (
                    f"sum by (status) (rate(ar_http_requests_total{{{NOT_PROBES}}}[5m]))",
                    "{{status}}",
                )
            ),
            "reqps",
            12,
            y,
            stack=True,
        ),
    ]
    y += 8

    panels.append(row("Routing decisions", y))
    y += 1
    panels += [
        timeseries(
            "Decisions by strategy",
            _targets(("sum by (strategy) (rate(ar_routing_decisions_total[5m]))", "{{strategy}}")),
            "ops",
            0,
            y,
            w=8,
        ),
        bargauge(
            "Selected agent (range)",
            "sum by (agent) (increase(ar_routing_decisions_total[$__range]))",
            "{{agent}}",
            "short",
            8,
            y,
            w=8,
        ),
        timeseries(
            "Routing latency P95 by strategy",
            _targets((q(0.95, "ar_routing_duration_seconds", "strategy"), "{{strategy}}")),
            "s",
            16,
            y,
            w=8,
            desc="Includes the embedding (embedding/adaptive) or the LLM call (llm).",
        ),
    ]
    y += 8
    panels += [
        timeseries(
            "LLM-router fallbacks",
            _targets(("sum(rate(ar_routing_fallbacks_total[5m]))", "fallbacks/s")),
            "ops",
            0,
            y,
            w=12,
            h=6,
        ),
        timeseries(
            "Execution failovers by original agent",
            _targets(
                ("sum by (from_agent) (rate(ar_execution_failovers_total[5m]))", "{{from_agent}}")
            ),
            "ops",
            12,
            y,
            w=12,
            h=6,
        ),
    ]
    y += 6

    panels.append(row("Agent performance", y))
    y += 1
    panels += [
        timeseries(
            "Agent execution latency P95",
            _targets((q(0.95, "ar_agent_execution_duration_seconds", "agent"), "{{agent}}")),
            "s",
            0,
            y,
            w=8,
            by_agent=True,
        ),
        timeseries(
            "Agent error ratio (errors + timeouts)",
            _targets(
                (
                    'sum by (agent) (rate(ar_agent_executions_total{status!="success"}[15m])) '
                    "/ clamp_min(sum by (agent) (rate(ar_agent_executions_total[15m])), 1e-9)",
                    "{{agent}}",
                )
            ),
            "percentunit",
            8,
            y,
            w=8,
            by_agent=True,
        ),
        timeseries(
            "Task success rate (labelled outcomes, 1h)",
            _targets(
                (
                    'sum by (agent) (increase(ar_task_outcomes_total{success="true"}[1h])) '
                    "/ clamp_min(sum by (agent) (increase(ar_task_outcomes_total[1h])), 1e-9)",
                    "{{agent}}",
                )
            ),
            "percentunit",
            16,
            y,
            w=8,
            by_agent=True,
            desc="Labels come from user feedback (POST /v1/queries/{id}/feedback).",
        ),
    ]
    y += 8
    panels += [
        timeseries(
            "Executions by agent",
            _targets(("sum by (agent) (rate(ar_agent_executions_total[5m]))", "{{agent}}")),
            "ops",
            0,
            y,
            by_agent=True,
            stack=True,
        ),
        timeseries(
            "In-flight executions by agent",
            _targets(("sum by (agent) (ar_agent_inflight)", "{{agent}}")),
            "short",
            12,
            y,
            by_agent=True,
        ),
    ]
    y += 8

    panels.append(row("Cost & tokens", y))
    y += 1
    panels += [
        timeseries(
            "Estimated spend rate (USD / hour)",
            _targets(
                (
                    '3600 * sum by (agent) (rate(ar_cost_usd_total{component="agent"}[15m]))',
                    "{{agent}}",
                ),
                ('3600 * sum(rate(ar_cost_usd_total{component=~"router:.*"}[15m]))', "llm router"),
            ),
            "currencyUSD",
            0,
            y,
            by_agent=True,
        ),
        timeseries(
            "Tokens / s by model",
            _targets(
                ("sum by (model, kind) (rate(ar_llm_tokens_total[5m]))", "{{model}} {{kind}}")
            ),
            "short",
            12,
            y,
        ),
    ]
    y += 8

    panels.append(row("Caching, rate limiting & errors", y))
    y += 1
    panels += [
        timeseries(
            "Cache hit ratio",
            _targets(
                (
                    'sum by (cache) (rate(ar_cache_requests_total{result="hit"}[5m])) '
                    "/ clamp_min(sum by (cache) (rate(ar_cache_requests_total[5m])), 1e-9)",
                    "{{cache}}",
                )
            ),
            "percentunit",
            0,
            y,
            w=8,
        ),
        timeseries(
            "Rate-limited requests",
            _targets(("sum(rate(ar_rate_limited_total[5m]))", "429s/s")),
            "reqps",
            8,
            y,
            w=8,
        ),
        timeseries(
            "Handled errors by kind",
            _targets(("sum by (kind) (rate(ar_errors_total[5m]))", "{{kind}}")),
            "ops",
            16,
            y,
            w=8,
        ),
    ]

    return {
        "uid": "adaptiveroute-overview",
        "title": "AdaptiveRoute - overview",
        "tags": ["adaptiveroute"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "10s",
        "time": {"from": "now-1h", "to": "now"},
        "editable": True,
        "graphTooltip": 1,
        "panels": panels,
        "templating": {"list": []},
        "annotations": {"list": []},
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT}")
