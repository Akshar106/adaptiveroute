# Evaluation methodology

This document defines **how** routing strategies are compared. Results live in
`benchmarks/results/<run_id>/report.md`, and the latest headline numbers are copied
into the README. Everything here was fixed **before** collecting results, including
the headline adaptive-router configuration in `config/routing.yaml`. Ablations are
reported as post-hoc analysis and never used to pick the headline.

## 1. Questions

1. **Routing accuracy:** how often does a strategy pick the labelled specialist?
2. **End-task success:** how often does the user get a correct answer? A
   non-specialist can still succeed, so this can differ from accuracy.
3. At what **latency** (P50/P95), **throughput**, **error rate** and **cost**?
4. Does the adaptive router actually **learn** from outcome history?

## 2. Dataset (`data/eval/dataset.jsonl`)

150 items: 30 per agent domain (code, math, sql, writer, knowledge), split into 50
easy, 60 medium and 40 hard. 26 items are tagged `ambiguous`: they are phrased to
attract a different agent, e.g. a math word problem about sales revenue, a SQL
question that never mentions SQL, or a knowledge question about a programming concept.

Each item has:
- `domain`: the labelled specialist; `acceptable_agents` defaults to `[domain]`;
- `check`: a **deterministic** success checker (below);
- `reference`: a correct answer that must pass its own check.

**Integrity checks**, enforced by `python -m adaptiveroute.evaluation.validate
--embeddings` and the test suite:
- every reference passes its check, and a fixed wrong answer (negative control) fails
  it, so no check is vacuous;
- no evaluation query is a near-duplicate (bge-small cosine ≥ 0.92) of an agent's
  routing example (no train/test leakage into agent profiles), or of another item.

Item authoring was assisted by an LLM and then verified mechanically:
- math answers were recomputed in Python;
- every reference SQL was executed, and an independent second query had to match it;
- reference code passed its tests, and deliberately buggy variants had to fail them;
- writing references were measured with the checker's own counters.

The full audit notes (known weak spots included) are in
[limitations.md](limitations.md#dataset).

### Checkers (`evaluation/checkers.py`)

| Domain | Check | Passes when |
|---|---|---|
| math | `numeric` | the `ANSWER:` line (else `\boxed{}`, else the last number) is within `abs_tol` |
| code | `python` | the function or class from the fenced block passes ≥ 4 asserts in an isolated subprocess (`-I`, empty env, CPU + wall-clock limits) |
| sql | `sql` | the query's result set equals the reference query's result set on the retail fixture (`PRAGMA query_only`; floats to 2 dp; order only if asked) |
| writer | `constraints` | all explicit constraints hold (word/sentence limits, bullet count, required/forbidden whole words, ending) |
| knowledge | `contains` | an accepted alias appears (whole word, case- and accent-insensitive) and no listed wrong answer does |

Checkers look only at the output text, never at which agent produced it.

## 3. Protocol: collect once, replay many times

See [ADR 0004](adr/0004-outcome-matrix-replay.md).

**Phase A: collect** (`adaptiveroute bench collect`). Each of the 150 items is run on
each of the 5 agents against the real Groq API: 750 calls at temperature 0 with the
production executor (deadline, retries, failover-free). Each cell records the checker
verdict, measured latency, tokens and estimated cost. The LLM router's decision for
each item is recorded the same way (150 calls). Client-side pacing happens outside
the timed window, so throttling never inflates latency. The file is append-only and
keyed by a fingerprint of each agent's model and prompt, so collection is resumable
(useful on the Groq free tier's daily limits), and changing an agent invalidates only
its own column.

**Phase B: replay** (`adaptiveroute bench run`). For each strategy and each seed
(0, 1, 2), items arrive in a seeded random order:
1. The **real router** decides. Embeddings are computed live (uncached) and the
   decision is timed in-process. For `llm`, the recorded decision and its recorded API
   latency and cost are used; a recorded failure falls back to the embedding router,
   as in production.
2. The outcome of the chosen agent is looked up in the matrix. If that execution
   errored or timed out, the runner-up's outcome is used and both costs and latencies
   are charged (production failover).
3. The adaptive router **learns online with bandit feedback**: only the executed
   agents' outcomes enter its history.

| Strategy | Notes |
|---|---|
| `round_robin` | lower bound: ignores content |
| `embedding` | nearest agent-profile centroid |
| `llm` | gpt-oss-20b classifier (strict JSON schema) |
| `adaptive` | **cold start**: empty history, learns as items arrive |
| `adaptive_warm` | 5-fold stratified: history built online from the router's own choices on the other 4 folds, then measured on the held-out fold, so an item never sees its own outcome |
| ablations | `adaptive_no_history` (w_succ = 0), `adaptive_quality_only` (no latency/cost/load terms), `adaptive_explore` (UCB 0.05), `adaptive_history_heavy` (w_sim 0.3, w_succ 0.5) |

Matrix reference points (no router): `always_<agent>`, `label_oracle` (always the
labelled agent, so 100% routing accuracy) and `oracle` (the cheapest agent that
succeeds on each item, an upper bound on success).

## 4. Metrics

| Metric | Definition |
|---|---|
| Routing accuracy | chosen agent ∈ `acceptable_agents` |
| Task success | the final executed answer passes the checker |
| Error rate | the final execution is `error`/`timeout` after failover |
| Fallback rate | the LLM router failed and the embedding router decided |
| Latency | routing time + execution time(s); P50/P95 over items, averaged over seeds |
| Routing latency | the router's own decision time (embedding included) |
| Cost | router + agent token cost at list prices (Groq, 2026-09-28) per 1,000 queries |
| Routing throughput | decisions per second of one worker = 1 / mean routing latency (sequential) |

**Uncertainty.** With 150 items, a few points can be noise:
- rates carry 95% bootstrap confidence intervals over items (2,000 resamples, using
  per-item values averaged across seeds);
- each strategy is compared with the embedding router using a **paired** bootstrap of
  per-item differences, and a difference is starred only if its CI excludes 0;
- the seed standard deviation is reported in `results.json`.

## 5. Throughput (load test)

Routing throughput in the report covers a single sequential worker. Service throughput
is measured separately with `scripts/loadtest.py` against the running stack:
closed-loop virtual users, a warm-up excluded, and `execute=false`, so the numbers
measure this service (auth, rate limit, embedding, routing, Postgres write) rather than
the LLM provider. With `execute=true`, throughput is bound by the provider's rate
limits (Groq free tier: 30 requests and 8K tokens per minute per model), which is a
property of the provider account, not the system. Results and the exact command are
recorded in `benchmarks/loadtest/`.

## 6. Reproducing

```bash
uv sync --group eval
docker compose up -d --wait postgres redis           # only needed for --store
# Re-run the replay from the committed matrix (no API key, no cost, deterministic per seed):
uv run python -m adaptiveroute.cli bench run --seeds 0,1,2
# Re-collect from scratch (needs GROQ_API_KEY; ~900 calls; resumable):
mv benchmarks/matrix/outcomes.jsonl /tmp/old-matrix.jsonl
uv run python -m adaptiveroute.cli bench collect --concurrency 4 --rpm 25
```

Each report records:
- git SHA (and whether the tree was dirty);
- dataset and matrix SHA-256;
- agent fingerprints, models and reasoning effort;
- the LLM-router fingerprint;
- the price date;
- the embedding model and package versions;
- the seeds and the full routing configuration.

Replays from the same matrix reproduce exactly, except in-process routing latency,
which depends on the machine.

## 7. Threats to validity

- **Small dataset.** 150 items, written for this project rather than taken from a
  public benchmark. Read the CIs, not just the means.
- **Labels are single-specialist.** `acceptable_agents` rarely lists two agents, so
  routing accuracy can understate a router that picks a capable non-specialist. That is
  why task success is the primary outcome metric.
- **Single sample per cell.** Provider-side nondeterminism isn't averaged out.
- **Checker labels in place of user feedback.** The adaptive router learns from clean,
  dense labels. Real user feedback is sparser and noisier, so the learning curve is
  optimistic.
- **Additive latency.** Routing and execution latencies were measured at different
  times; queueing interactions between them aren't captured.
- **Provider drift.** Groq model weights, routing and prices can change; the report
  records the collection dates and the price date.
