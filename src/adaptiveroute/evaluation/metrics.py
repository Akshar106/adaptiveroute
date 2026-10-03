"""Aggregate per-item replay results into report metrics.

Uncertainty: with ~150 items a few percentage points of difference can be noise, so
every rate comes with a 95% bootstrap confidence interval over items, and each
strategy is compared with the embedding baseline using a *paired* bootstrap on the
per-item difference (the same items are seen by every strategy).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from adaptiveroute.evaluation.replay import ItemResult

N_BOOT = 2000


def _per_item_mean(
    results: Sequence[ItemResult], fn: Callable[[ItemResult], float]
) -> dict[str, float]:
    """Average a per-item quantity across seeds."""
    acc: defaultdict[str, list[float]] = defaultdict(list)
    for r in results:
        acc[r.item_id].append(fn(r))
    return {k: float(np.mean(v)) for k, v in acc.items()}


def bootstrap_ci(values: Sequence[float], seed: int = 0) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(arr, size=(N_BOOT, len(arr)), replace=True).mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def paired_diff(
    a: Sequence[ItemResult],
    b: Sequence[ItemResult],
    fn: Callable[[ItemResult], float],
    seed: int = 0,
) -> dict[str, float]:
    """Mean of fn(a) - fn(b) over shared items, with a paired bootstrap 95% CI."""
    ma, mb = _per_item_mean(a, fn), _per_item_mean(b, fn)
    keys = sorted(set(ma) & set(mb))
    diffs = [ma[k] - mb[k] for k in keys]
    lo, hi = bootstrap_ci(diffs, seed)
    return {"mean": float(np.mean(diffs)), "ci_low": lo, "ci_high": hi}


def _seed_stat(
    results: Sequence[ItemResult], fn: Callable[[list[ItemResult]], float]
) -> tuple[float, float]:
    by_seed: defaultdict[int, list[ItemResult]] = defaultdict(list)
    for r in results:
        by_seed[r.seed].append(r)
    values = [fn(rs) for rs in by_seed.values()]
    return float(np.mean(values)), float(np.std(values))


def _attr_float(attr: str) -> Callable[[ItemResult], float]:
    return lambda r: float(getattr(r, attr))


def _mean_of(attr: str) -> Callable[[list[ItemResult]], float]:
    return lambda rs: float(np.mean([getattr(r, attr) for r in rs]))


def _percentile_of(attr: str, q: float) -> Callable[[list[ItemResult]], float]:
    return lambda rs: float(np.percentile([getattr(r, attr) for r in rs], q))


def summarize(results: Sequence[ItemResult]) -> dict[str, Any]:
    if not results:
        raise ValueError("no results")
    rate, pct = _mean_of, _percentile_of

    out: dict[str, Any] = {
        "n_items": len({r.item_id for r in results}),
        "n_seeds": len({r.seed for r in results}),
    }
    for name, attr in (
        ("routing_accuracy", "routing_correct"),
        ("task_success", "success"),
        ("error_rate", "error"),
        ("router_fallback_rate", "router_fallback"),
    ):
        mean, std = _seed_stat(results, rate(attr))
        lo, hi = bootstrap_ci(list(_per_item_mean(results, _attr_float(attr)).values()))
        out[name] = {"mean": mean, "std_across_seeds": std, "ci95": [lo, hi]}

    for name, attr in (("total_latency_ms", "total_ms"), ("routing_latency_ms", "routing_ms")):
        out[name] = {
            "p50": _seed_stat(results, pct(attr, 50))[0],
            "p95": _seed_stat(results, pct(attr, 95))[0],
            "mean": _seed_stat(results, rate(attr))[0],
        }
    out["cost_usd"] = {
        "mean_per_query": _seed_stat(results, rate("total_cost_usd"))[0],
        "router_mean_per_query": _seed_stat(results, rate("router_cost_usd"))[0],
        "per_1k_queries": 1000 * _seed_stat(results, rate("total_cost_usd"))[0],
    }
    # Sequential routing throughput of one worker: 1 / mean routing time.
    mean_routing_s = out["routing_latency_ms"]["mean"] / 1000
    out["routing_throughput_per_s"] = (1.0 / mean_routing_s) if mean_routing_s > 0 else None
    out["failover_rate"] = _seed_stat(
        results, lambda rs: float(np.mean([len(r.executed_agents) > 1 for r in rs]))
    )[0]
    return out


def breakdown(
    results: Sequence[ItemResult], key: Callable[[ItemResult], str]
) -> dict[str, dict[str, float]]:
    groups: defaultdict[str, list[ItemResult]] = defaultdict(list)
    for r in results:
        groups[key(r)].append(r)
    return {
        g: {
            "n": len({r.item_id for r in rs}),
            "routing_accuracy": float(np.mean([r.routing_correct for r in rs])),
            "task_success": float(np.mean([r.success for r in rs])),
        }
        for g, rs in sorted(groups.items())
    }


def confusion(results: Sequence[ItemResult], agents: Sequence[str]) -> dict[str, dict[str, float]]:
    """Fraction of items of each labelled domain routed to each agent."""
    counts: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in results:
        counts[r.domain][r.selected_agent] += 1
    return {
        d: {a: counts[d][a] / max(1, sum(counts[d].values())) for a in agents}
        for d in sorted(counts)
    }


def learning_curve(results: Sequence[ItemResult], window: int = 25) -> list[dict[str, float]]:
    """Rolling success / routing accuracy by arrival position, averaged over seeds."""
    by_pos: defaultdict[int, list[ItemResult]] = defaultdict(list)
    for r in results:
        by_pos[r.position].append(r)
    positions = sorted(by_pos)
    curve = []
    for p in positions:
        win = [r for q in positions[max(0, p - window + 1) : p + 1] for r in by_pos[q]]
        curve.append(
            {
                "position": p,
                "rolling_success": float(np.mean([r.success for r in win])),
                "rolling_accuracy": float(np.mean([r.routing_correct for r in win])),
            }
        )
    return curve
