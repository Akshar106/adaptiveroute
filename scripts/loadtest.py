"""Closed-loop HTTP load test against a running AdaptiveRoute API.

N concurrent virtual users each send requests back-to-back for a fixed duration
(after a warm-up), using queries drawn from the evaluation dataset. Reports
throughput, latency percentiles and error rate per strategy.

    uv run python scripts/loadtest.py --url http://localhost:8000 --key $AR_KEY \
        --strategies round_robin,embedding,adaptive --concurrency 16 --duration 30 --route-only

`--route-only` sends execute=false, which measures the routing service itself
(auth + rate limit + embedding + routing + Postgres write) without LLM calls. Without
it, every request executes an agent against the provider and throughput is bounded
by the provider's rate limits, not by this service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path

import httpx

DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "dataset.jsonl"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q / 100 * (len(ordered) - 1))))
    return ordered[idx]


async def run_strategy(
    client: httpx.AsyncClient,
    strategy: str,
    queries: list[str],
    concurrency: int,
    duration_s: float,
    warmup_s: float,
    route_only: bool,
    seed: int,
) -> dict[str, object]:
    rng = random.Random(seed)
    latencies: list[float] = []
    statuses: dict[int, int] = {}
    errors = 0
    measure_from = time.perf_counter() + warmup_s
    stop_at = measure_from + duration_s

    async def user() -> None:
        nonlocal errors
        while (now := time.perf_counter()) < stop_at:
            body = {"query": rng.choice(queries), "strategy": strategy, "execute": not route_only}
            start = time.perf_counter()
            try:
                resp = await client.post("/v1/queries", json=body)
                code = resp.status_code
            except httpx.HTTPError:
                code = 0
            elapsed = time.perf_counter() - start
            if now >= measure_from:
                latencies.append(elapsed * 1000)
                statuses[code] = statuses.get(code, 0) + 1
                if code != 200:
                    errors += 1

    await asyncio.gather(*(user() for _ in range(concurrency)))
    n = len(latencies)
    return {
        "strategy": strategy,
        "requests": n,
        "throughput_rps": n / duration_s,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "mean_ms": statistics.fmean(latencies) if latencies else float("nan"),
        "error_rate": errors / n if n else float("nan"),
        "status_counts": statuses,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--key", required=True, help="API key (give it a high rate limit)")
    parser.add_argument("--strategies", default="round_robin,embedding,adaptive")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--warmup", type=float, default=5.0)
    parser.add_argument("--route-only", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, help="write JSON results here")
    args = parser.parse_args()

    queries = [
        json.loads(line)["query"] for line in DATASET.read_text().splitlines() if line.strip()
    ]
    headers = {"Authorization": f"Bearer {args.key}"}
    limits = httpx.Limits(max_connections=args.concurrency * 2)
    results = []
    async with httpx.AsyncClient(
        base_url=args.url, headers=headers, timeout=120, limits=limits
    ) as client:
        for strategy in args.strategies.split(","):
            result = await run_strategy(
                client,
                strategy,
                queries,
                args.concurrency,
                args.duration,
                args.warmup,
                args.route_only,
                args.seed,
            )
            results.append(result)
            print(
                f"{strategy:12s} {result['throughput_rps']:8.1f} req/s  "
                f"p50 {result['p50_ms']:7.1f} ms  p95 {result['p95_ms']:7.1f} ms  "
                f"p99 {result['p99_ms']:7.1f} ms  errors {100 * float(result['error_rate']):.2f}%  "
                f"{result['status_counts']}"
            )
    if args.out:
        meta = {
            "url": args.url,
            "concurrency": args.concurrency,
            "duration_s": args.duration,
            "warmup_s": args.warmup,
            "route_only": args.route_only,
        }
        args.out.write_text(json.dumps({"config": meta, "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
