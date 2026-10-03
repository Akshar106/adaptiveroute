"""The adaptive router's scoring function (pure: no I/O, fully unit-testable).

For each agent ``a`` and query ``q``::

    score(a) =  w_sim  * semantic(a)                 # how well a's profile matches q
              + w_succ * success(a)                  # P(task succeeds | a, similar past q)
              - w_lat  * min(1, latency(a) / latency_budget)
              - w_cost * min(1, cost(a)    / cost_budget)
              - w_load * min(1, inflight(a) / max_concurrency(a))
              + w_exp  * sqrt(ln(1 + N) / (1 + n(a)))  # optional UCB-style exploration

* ``semantic(a) = exp((cos(q, a) - max_b cos(q, b)) / T)``: the best-matching agent
  gets 1.0, an agent whose similarity is ``T`` lower gets e^-1 ~ 0.37. This keeps the
  term in [0, 1] and makes ``T`` an interpretable "how much does a similarity gap
  matter" knob.
* ``success``, ``latency`` and ``cost`` are *contextual* estimates: weighted averages
  over the agent's nearest past queries, shrunk towards the agent's global average
  (see :func:`estimate_signals`). ``n(a)`` is the effective number of neighbours.
* Agents at or over their concurrency limit are ineligible unless every agent is.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from adaptiveroute.domain import AgentAggregate, AgentSpec, CandidateScore, HistoryRecord


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    similarity: float = 0.45
    success: float = 0.35
    latency: float = 0.08
    cost: float = 0.07
    load: float = 0.05
    exploration: float = 0.0

    def __post_init__(self) -> None:
        for name in ("similarity", "success", "latency", "cost", "load", "exploration"):
            if getattr(self, name) < 0:
                raise ValueError(f"weight {name} must be >= 0")


@dataclass(frozen=True, slots=True)
class AdaptiveConfig:
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    similarity_temperature: float = 0.05
    k_neighbors: int = 25
    min_neighbor_similarity: float = 0.6
    prior_strength: float = 3.0
    default_success_prior: float = 0.7
    latency_budget_ms: float = 20_000.0
    cost_budget_usd: float = 0.002

    def __post_init__(self) -> None:
        if self.similarity_temperature <= 0:
            raise ValueError("similarity_temperature must be > 0")
        if not 0 <= self.min_neighbor_similarity < 1:
            raise ValueError("min_neighbor_similarity must be in [0, 1)")
        if self.prior_strength <= 0:
            raise ValueError("prior_strength must be > 0")
        if self.latency_budget_ms <= 0 or self.cost_budget_usd <= 0:
            raise ValueError("budgets must be > 0")


@dataclass(frozen=True, slots=True)
class AgentSignals:
    """Everything the scorer knows about one agent for one query."""

    agent: str
    similarity: float  # cosine(query, agent profile)
    success: float  # estimated P(success)
    evidence: float  # effective number of relevant past observations
    latency_ms: float  # expected latency
    cost_usd: float  # expected cost
    load: float  # inflight / max_concurrency


def estimate_signals(
    agent: AgentSpec,
    similarity: float,
    neighbors: Sequence[HistoryRecord],
    aggregate: AgentAggregate | None,
    inflight: int,
    cfg: AdaptiveConfig,
) -> AgentSignals:
    """Turn raw history into shrunk, query-specific estimates for one agent.

    Neighbour weight ramps linearly from 0 at ``min_neighbor_similarity`` to 1 for an
    identical query, so a near-duplicate past query counts as one full observation.
    Each estimate is a weighted mean shrunk towards a global value with
    ``prior_strength`` pseudo-observations (Beta-Binomial posterior mean for success):

        est = (k0 * global + sum(w_i * x_i)) / (k0 + sum(w_i))
    """
    k0 = cfg.prior_strength
    lo = cfg.min_neighbor_similarity

    # Global (query-independent) values, themselves shrunk towards config priors.
    if aggregate is not None and aggregate.n > 0:
        global_success = (aggregate.successes + k0 * cfg.default_success_prior) / (aggregate.n + k0)
    else:
        global_success = cfg.default_success_prior
    # Explicit None checks: a measured cost of exactly 0 is data, not "missing".
    measured_latency = aggregate.p50_latency_ms if aggregate else None
    measured_cost = aggregate.mean_cost_usd if aggregate else None
    global_latency = agent.prior_latency_ms if measured_latency is None else measured_latency
    global_cost = agent.prior_cost_usd if measured_cost is None else measured_cost

    w_sum = succ_sum = lat_sum = cost_sum = 0.0
    for rec in neighbors:
        if rec.similarity < lo:
            continue
        w = min(1.0, (rec.similarity - lo) / (1 - lo))
        w_sum += w
        succ_sum += w * (1.0 if rec.success else 0.0)
        lat_sum += w * rec.latency_ms
        cost_sum += w * rec.cost_usd

    return AgentSignals(
        agent=agent.name,
        similarity=similarity,
        success=(k0 * global_success + succ_sum) / (k0 + w_sum),
        evidence=w_sum,
        latency_ms=(k0 * global_latency + lat_sum) / (k0 + w_sum),
        cost_usd=(k0 * global_cost + cost_sum) / (k0 + w_sum),
        load=inflight / agent.max_concurrency,
    )


def score_candidates(signals: Sequence[AgentSignals], cfg: AdaptiveConfig) -> list[CandidateScore]:
    """Score every agent; return candidates best-first (ineligible agents last)."""
    if not signals:
        raise ValueError("no candidates to score")
    w = cfg.weights
    best_sim = max(s.similarity for s in signals)
    total_evidence = sum(s.evidence for s in signals)
    any_eligible = any(s.load < 1.0 for s in signals)

    scored: list[CandidateScore] = []
    for s in signals:
        semantic = math.exp((s.similarity - best_sim) / cfg.similarity_temperature)
        latency_pen = min(1.0, s.latency_ms / cfg.latency_budget_ms)
        cost_pen = min(1.0, s.cost_usd / cfg.cost_budget_usd)
        load_pen = min(1.0, s.load)
        explore = math.sqrt(math.log1p(total_evidence) / (1.0 + s.evidence))
        contrib = {
            "contrib_semantic": w.similarity * semantic,
            "contrib_success": w.success * s.success,
            "contrib_latency": -w.latency * latency_pen,
            "contrib_cost": -w.cost * cost_pen,
            "contrib_load": -w.load * load_pen,
            "contrib_exploration": w.exploration * explore,
        }
        eligible = s.load < 1.0 or not any_eligible
        scored.append(
            CandidateScore(
                agent=s.agent,
                score=sum(contrib.values()),
                components={
                    "similarity": s.similarity,
                    "semantic": semantic,
                    "success": s.success,
                    "evidence": s.evidence,
                    "latency_ms": s.latency_ms,
                    "cost_usd": s.cost_usd,
                    "load": s.load,
                    "eligible": 1.0 if eligible else 0.0,
                    **contrib,
                },
            )
        )
    scored.sort(key=lambda c: (c.components["eligible"], c.score), reverse=True)
    return scored


_TERM_LABELS = {
    "contrib_semantic": "semantic fit",
    "contrib_success": "historical success",
    "contrib_latency": "expected latency",
    "contrib_cost": "expected cost",
    "contrib_load": "current load",
    "contrib_exploration": "exploration bonus",
}


def explain(ranked: Sequence[CandidateScore]) -> str:
    """Human-readable summary of why the top candidate won."""
    best = ranked[0]
    c = best.components
    latency = c["latency_ms"]
    latency_text = f"{latency / 1000:.1f}s" if latency >= 1000 else f"{latency:.0f}ms"
    text = (
        f"'{best.agent}' scored {best.score:.3f}: semantic fit {c['semantic']:.2f} "
        f"(cos {c['similarity']:.3f}), est. success {c['success']:.2f} from "
        f"{c['evidence']:.1f} similar past queries, ~{latency_text}, "
        f"~${c['cost_usd']:.5f}, load {c['load']:.0%}."
    )
    if len(ranked) > 1:
        runner = ranked[1]
        diffs = {
            key: best.components[key] - runner.components[key]
            for key in _TERM_LABELS
            if key in runner.components
        }
        key, gap = max(diffs.items(), key=lambda kv: kv[1])
        text += (
            f" Runner-up '{runner.agent}' scored {runner.score:.3f}; the biggest advantage "
            f"was {_TERM_LABELS[key]} (+{gap:.3f})."
        )
    return text
