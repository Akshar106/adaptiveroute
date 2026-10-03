"""Wire routers together from their dependencies (composition root for routing)."""

from __future__ import annotations

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.llm import LLMClient
from adaptiveroute.ports import LoadTracker, PerformanceHistory, SequenceCounter
from adaptiveroute.routing.adaptive import AdaptiveRouter
from adaptiveroute.routing.base import AgentProfiles, Router
from adaptiveroute.routing.config import RoutingConfig
from adaptiveroute.routing.llm_router import LLMRouter
from adaptiveroute.routing.simple import EmbeddingRouter, RoundRobinRouter

STRATEGIES: tuple[str, ...] = ("round_robin", "embedding", "llm", "adaptive")


def build_routers(
    registry: AgentRegistry,
    profiles: AgentProfiles,
    history: PerformanceHistory,
    load: LoadTracker,
    counter: SequenceCounter,
    llm: LLMClient | None,
    config: RoutingConfig,
) -> dict[str, Router]:
    """Return every available router keyed by strategy name.

    The LLM router is omitted when no LLM client is configured (no API key), so the
    rest of the system still works offline.
    """
    round_robin = RoundRobinRouter(registry.names, counter)
    embedding = EmbeddingRouter(profiles)
    routers: dict[str, Router] = {
        round_robin.name: round_robin,
        embedding.name: embedding,
        AdaptiveRouter.name: AdaptiveRouter(
            registry.agents, profiles, history, load, config.adaptive
        ),
    }
    if llm is not None:
        fallback = {"embedding": embedding, "round_robin": round_robin}.get(
            config.llm_fallback or ""
        )
        routers[LLMRouter.name] = LLMRouter(
            registry.agents, llm, registry.prices, config.llm, fallback=fallback
        )
    return {name: routers[name] for name in STRATEGIES if name in routers}
