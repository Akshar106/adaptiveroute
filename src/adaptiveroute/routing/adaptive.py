"""Adaptive weighted router: semantic fit + contextual history + latency + cost + load."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from adaptiveroute.domain import AgentSpec, RoutingDecision
from adaptiveroute.ports import LoadTracker, PerformanceHistory
from adaptiveroute.routing.base import AgentProfiles, QueryContext, Router
from adaptiveroute.routing.scoring import (
    AdaptiveConfig,
    estimate_signals,
    explain,
    score_candidates,
)


class AdaptiveRouter(Router):
    name = "adaptive"

    def __init__(
        self,
        agents: Sequence[AgentSpec],
        profiles: AgentProfiles,
        history: PerformanceHistory,
        load: LoadTracker,
        config: AdaptiveConfig,
    ) -> None:
        self._agents = tuple(agents)
        self._names = [a.name for a in agents]
        self._profiles = profiles
        self._history = history
        self._load = load
        self.config = config

    async def decide(self, ctx: QueryContext) -> RoutingDecision:
        query_vec = await ctx.embedding()
        sims = self._profiles.similarities(query_vec)
        # Independent lookups (Postgres kNN, Postgres aggregates, Redis load) run concurrently.
        neighbors, aggregates, inflight = await asyncio.gather(
            self._history.neighbors(query_vec, self._names, self.config.k_neighbors),
            self._history.aggregates(),
            self._load.inflight(self._names),
        )
        signals = [
            estimate_signals(
                agent,
                sims[agent.name],
                neighbors.get(agent.name, []),
                aggregates.get(agent.name),
                inflight.get(agent.name, 0),
                self.config,
            )
            for agent in self._agents
        ]
        ranked = score_candidates(signals, self.config)
        return RoutingDecision(
            strategy=self.name,
            agent=ranked[0].agent,
            candidates=tuple(ranked),
            reasoning=explain(ranked),
            latency_ms=0.0,
            metadata={"neighbors": {a: len(n) for a, n in neighbors.items()}},
        )
