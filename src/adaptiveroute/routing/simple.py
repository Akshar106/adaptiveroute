"""Baseline routers: round-robin and nearest-profile embedding similarity."""

from __future__ import annotations

from collections.abc import Sequence

from adaptiveroute.domain import CandidateScore, RoutingDecision
from adaptiveroute.ports import SequenceCounter
from adaptiveroute.routing.base import AgentProfiles, QueryContext, Router


class RoundRobinRouter(Router):
    """Ignores the query entirely. A lower bound for "routing intelligence"."""

    name = "round_robin"

    def __init__(self, agents: Sequence[str], counter: SequenceCounter) -> None:
        if not agents:
            raise ValueError("round-robin needs at least one agent")
        self._agents = tuple(agents)
        self._counter = counter

    async def decide(self, ctx: QueryContext) -> RoutingDecision:
        position = await self._counter.next(self.name)
        agent = self._agents[position % len(self._agents)]
        return RoutingDecision(
            strategy=self.name,
            agent=agent,
            candidates=tuple(CandidateScore(a, 1.0 if a == agent else 0.0) for a in self._agents),
            reasoning=(
                f"Round-robin: request #{position} goes to slot {position % len(self._agents)} "
                f"('{agent}'). The query content is not considered."
            ),
            latency_ms=0.0,
        )


class EmbeddingRouter(Router):
    """Pick the agent whose profile centroid is most similar to the query."""

    name = "embedding"

    def __init__(self, profiles: AgentProfiles) -> None:
        self._profiles = profiles

    async def decide(self, ctx: QueryContext) -> RoutingDecision:
        sims = self._profiles.similarities(await ctx.embedding())
        ranked = sorted(sims.items(), key=lambda kv: kv[1], reverse=True)
        (best, best_sim), (runner, runner_sim) = ranked[0], ranked[1]
        return RoutingDecision(
            strategy=self.name,
            agent=best,
            candidates=tuple(CandidateScore(a, s, {"similarity": s}) for a, s in ranked),
            reasoning=(
                f"Closest agent profile is '{best}' (cosine {best_sim:.3f}); runner-up "
                f"'{runner}' ({runner_sim:.3f}), margin {best_sim - runner_sim:.3f}."
            ),
            latency_ms=0.0,
        )
