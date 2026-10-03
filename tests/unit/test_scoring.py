import math
from dataclasses import replace

import pytest

from adaptiveroute.domain import AgentAggregate, AgentSpec, HistoryRecord
from adaptiveroute.routing.scoring import (
    AdaptiveConfig,
    AgentSignals,
    ScoringWeights,
    estimate_signals,
    explain,
    score_candidates,
)

CFG = AdaptiveConfig()


def agent(name: str = "a", **kw: object) -> AgentSpec:
    base = AgentSpec(
        name=name,
        display_name=name,
        description="d" * 20,
        model="m",
        system_prompt="p" * 20,
        prior_latency_ms=2000,
        prior_cost_usd=0.0004,
        max_concurrency=4,
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def sig(name: str, **kw: float) -> AgentSignals:
    base = {
        "similarity": 0.7,
        "success": 0.7,
        "evidence": 0.0,
        "latency_ms": 2000.0,
        "cost_usd": 0.0004,
        "load": 0.0,
    }
    base.update(kw)
    return AgentSignals(agent=name, **base)


def by_agent(cands: list) -> dict:  # type: ignore[type-arg]
    return {c.agent: c for c in cands}


# --- estimate_signals -------------------------------------------------------------


def test_no_history_falls_back_to_priors() -> None:
    s = estimate_signals(agent(), 0.8, [], None, inflight=1, cfg=CFG)
    assert s.success == pytest.approx(CFG.default_success_prior)
    assert s.latency_ms == 2000
    assert s.cost_usd == 0.0004
    assert s.evidence == 0
    assert s.load == pytest.approx(0.25)


def test_neighbors_below_threshold_are_ignored() -> None:
    weak = HistoryRecord("a", similarity=0.59, success=False, latency_ms=99_000, cost_usd=1.0)
    s = estimate_signals(agent(), 0.8, [weak], None, 0, CFG)
    assert s.evidence == 0
    assert s.success == pytest.approx(CFG.default_success_prior)


def test_identical_neighbor_counts_as_one_observation() -> None:
    rec = HistoryRecord("a", similarity=1.0, success=True, latency_ms=1000, cost_usd=0.0001)
    s = estimate_signals(agent(), 0.8, [rec], None, 0, CFG)
    k0 = CFG.prior_strength
    assert s.evidence == pytest.approx(1.0)
    assert s.success == pytest.approx((k0 * 0.7 + 1) / (k0 + 1))
    assert s.latency_ms == pytest.approx((k0 * 2000 + 1000) / (k0 + 1))


def test_neighbor_weight_ramps_linearly_with_similarity() -> None:
    # halfway between threshold (0.6) and 1.0 => weight 0.5
    rec = HistoryRecord("a", similarity=0.8, success=False, latency_ms=1000, cost_usd=0.0)
    s = estimate_signals(agent(), 0.8, [rec], None, 0, CFG)
    assert s.evidence == pytest.approx(0.5)


def test_many_failures_drive_success_towards_zero() -> None:
    recs = [HistoryRecord("a", 0.95, False, 1000, 0.0001) for _ in range(200)]
    s = estimate_signals(agent(), 0.8, recs, None, 0, CFG)
    assert s.success < 0.05


def test_global_aggregate_is_shrunk_towards_prior() -> None:
    agg = AgentAggregate("a", n=10, successes=2, p50_latency_ms=5000, mean_cost_usd=0.001)
    s = estimate_signals(agent(), 0.8, [], agg, 0, CFG)
    k0 = CFG.prior_strength
    assert s.success == pytest.approx((2 + k0 * 0.7) / (10 + k0))
    assert s.latency_ms == 5000  # measured p50 replaces the configured prior
    assert s.cost_usd == 0.001


# --- score_candidates -----------------------------------------------------------


def test_semantic_term_is_relative_to_best_match() -> None:
    T = CFG.similarity_temperature
    out = by_agent(score_candidates([sig("a", similarity=0.8), sig("b", similarity=0.8 - T)], CFG))
    assert out["a"].components["semantic"] == pytest.approx(1.0)
    assert out["b"].components["semantic"] == pytest.approx(math.exp(-1))


def test_score_is_sum_of_contributions() -> None:
    for c in score_candidates([sig("a"), sig("b", similarity=0.6, load=0.5)], CFG):
        contribs = [v for k, v in c.components.items() if k.startswith("contrib_")]
        assert c.score == pytest.approx(sum(contribs))


@pytest.mark.parametrize(
    ("field", "better", "worse"),
    [
        ("similarity", 0.8, 0.7),
        ("success", 0.9, 0.5),
        ("latency_ms", 1000.0, 9000.0),
        ("cost_usd", 0.0001, 0.0015),
        ("load", 0.0, 0.75),
    ],
)
def test_each_signal_moves_score_in_the_right_direction(
    field: str, better: float, worse: float
) -> None:
    ranked = score_candidates(
        [sig("worse", **{field: worse}), sig("better", **{field: better})], CFG
    )
    assert ranked[0].agent == "better"


def test_saturated_agent_is_ineligible() -> None:
    ranked = score_candidates(
        [sig("busy", similarity=0.9, success=0.99, load=1.0), sig("free", similarity=0.5)], CFG
    )
    assert ranked[0].agent == "free"
    assert by_agent(ranked)["busy"].components["eligible"] == 0.0


def test_all_saturated_still_routes_somewhere() -> None:
    ranked = score_candidates([sig("a", load=1.5), sig("b", load=2.0, similarity=0.5)], CFG)
    assert ranked[0].agent == "a"
    assert all(c.components["eligible"] == 1.0 for c in ranked)


def test_similarity_only_weights_reduce_to_embedding_routing() -> None:
    cfg = AdaptiveConfig(weights=ScoringWeights(1, 0, 0, 0, 0, 0))
    signals = [
        sig("a", similarity=0.61, success=0.99),
        sig("b", similarity=0.74, success=0.01, latency_ms=19000),
        sig("c", similarity=0.70),
    ]
    assert score_candidates(signals, cfg)[0].agent == "b"


def test_exploration_bonus_prefers_unexplored_agents() -> None:
    cfg = AdaptiveConfig(weights=ScoringWeights(0, 0, 0, 0, 0, exploration=1.0))
    ranked = score_candidates([sig("known", evidence=50), sig("new", evidence=0)], cfg)
    assert ranked[0].agent == "new"


def test_penalties_are_capped_at_budget() -> None:
    out = by_agent(score_candidates([sig("a", latency_ms=1e9, cost_usd=1e3)], CFG))
    assert out["a"].components["contrib_latency"] == pytest.approx(-CFG.weights.latency)
    assert out["a"].components["contrib_cost"] == pytest.approx(-CFG.weights.cost)


def test_explain_names_winner_and_main_advantage() -> None:
    ranked = score_candidates([sig("math", similarity=0.8), sig("code", similarity=0.6)], CFG)
    text = explain(ranked)
    assert text.startswith("'math' scored")
    assert "Runner-up 'code'" in text
    assert "semantic fit" in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"similarity_temperature": 0},
        {"min_neighbor_similarity": 1.0},
        {"prior_strength": 0},
        {"cost_budget_usd": 0},
    ],
)
def test_invalid_config_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        AdaptiveConfig(**kwargs)  # type: ignore[arg-type]


def test_negative_weight_rejected() -> None:
    with pytest.raises(ValueError):
        ScoringWeights(similarity=-0.1)


def test_empty_candidates_rejected() -> None:
    with pytest.raises(ValueError):
        score_candidates([], CFG)
