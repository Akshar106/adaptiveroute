from dataclasses import replace

from adaptiveroute.domain import AgentAggregate, AgentSpec, CandidateScore, RoutingDecision


def spec(**overrides: object) -> AgentSpec:
    base = AgentSpec(
        name="code",
        display_name="Code",
        description="writes python",
        model="openai/gpt-oss-120b",
        system_prompt="You write code.",
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_fingerprint_is_stable() -> None:
    assert spec().fingerprint == spec().fingerprint


def test_fingerprint_changes_with_behavioural_fields() -> None:
    base = spec().fingerprint
    assert spec(system_prompt="Different").fingerprint != base
    assert spec(model="openai/gpt-oss-20b").fingerprint != base
    assert spec(reasoning_effort="high").fingerprint != base


def test_fingerprint_ignores_cosmetic_fields() -> None:
    assert spec(display_name="Renamed", description="x").fingerprint == spec().fingerprint


def test_ranked_agents_puts_selected_first_even_if_not_top_score() -> None:
    decision = RoutingDecision(
        strategy="round_robin",
        agent="b",
        candidates=(
            CandidateScore("a", 0.9),
            CandidateScore("b", 0.1),
            CandidateScore("c", 0.5),
        ),
        reasoning="",
        latency_ms=0.1,
    )
    assert decision.ranked_agents() == ["b", "a", "c"]


def test_success_rate_none_without_data() -> None:
    assert AgentAggregate("a", 0, 0, None, None).success_rate is None
    assert AgentAggregate("a", 4, 3, 10.0, 0.1).success_rate == 0.75
