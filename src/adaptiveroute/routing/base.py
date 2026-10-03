"""Router interface shared by all strategies."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import ClassVar

import numpy as np
from opentelemetry import trace

from adaptiveroute.domain import AgentSpec, RoutingDecision
from adaptiveroute.observability import metrics
from adaptiveroute.ports import Embedder, Vector

tracer = trace.get_tracer(__name__)


class RouterError(Exception):
    """A router could not produce a decision (and had no fallback)."""


class QueryContext:
    """Routing input for one query, with a lazily computed and memoised embedding.

    Routers that need the embedding call ``await ctx.embedding()``; the first call pays
    for it (and that time is counted in the router's latency), later calls are free.
    The service reuses the same embedding when it persists the query.
    """

    def __init__(self, text: str, embedder: Embedder, embedding: Vector | None = None) -> None:
        self.text = text
        self._embedder = embedder
        self._embedding = embedding
        self.embedding_ms: float | None = None

    async def embedding(self) -> Vector:
        if self._embedding is None:
            start = time.perf_counter()
            self._embedding = (await self._embedder.embed([self.text]))[0]
            self.embedding_ms = (time.perf_counter() - start) * 1000
        return self._embedding


class Router(ABC):
    name: ClassVar[str]

    async def route(self, ctx: QueryContext) -> RoutingDecision:
        """Decide, then stamp latency and emit a span + metrics."""
        start = time.perf_counter()
        with tracer.start_as_current_span(f"route {self.name}") as span:
            decision = await self.decide(ctx)
            latency_ms = (time.perf_counter() - start) * 1000
            decision = replace(decision, latency_ms=latency_ms)
            span.set_attributes(
                {
                    "ar.routing.strategy": self.name,
                    "ar.routing.agent": decision.agent,
                    "ar.routing.fallback": decision.fallback or "",
                    "ar.routing.cost_usd": decision.cost_usd,
                }
            )
        metrics.ROUTING_DECISIONS.labels(strategy=self.name, agent=decision.agent).inc()
        metrics.ROUTING_LATENCY.labels(strategy=self.name).observe(latency_ms / 1000)
        if decision.fallback:
            metrics.ROUTING_FALLBACKS.labels(strategy=self.name).inc()
        if decision.cost_usd:
            metrics.COST_USD.labels(agent="-", component=f"router:{self.name}").inc(
                decision.cost_usd
            )
        return decision

    @abstractmethod
    async def decide(self, ctx: QueryContext) -> RoutingDecision:
        """Pure decision logic (no metrics). ``latency_ms`` is filled in by ``route``."""


@dataclass(frozen=True)
class AgentProfiles:
    """One L2-normalised centroid per agent, built from its description + examples."""

    names: tuple[str, ...]
    centroids: Vector  # shape (n_agents, dim)

    @classmethod
    async def build(cls, agents: Sequence[AgentSpec], embedder: Embedder) -> AgentProfiles:
        rows = []
        for agent in agents:
            vectors = await embedder.embed([agent.description, *agent.examples])
            centroid = vectors.mean(axis=0)
            rows.append(centroid / np.linalg.norm(centroid))
        return cls(tuple(a.name for a in agents), np.stack(rows).astype(np.float32))

    def similarities(self, query: Vector) -> dict[str, float]:
        sims = self.centroids @ query
        return {name: float(s) for name, s in zip(self.names, sims, strict=True)}
