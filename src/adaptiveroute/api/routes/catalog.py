"""Agents and strategies (read-only catalog with live stats)."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from adaptiveroute.api.deps import ContainerDep, PrincipalDep
from adaptiveroute.api.schemas import AgentOut, AgentStatsOut, MeOut, StrategiesOut, StrategyOut
from adaptiveroute.db.repositories import StatsRepository
from adaptiveroute.routing import STRATEGIES
from adaptiveroute.state.redis import RedisLoadTracker

router = APIRouter(prefix="/v1", tags=["catalog"])

_DESCRIPTIONS = {
    "round_robin": "Cycles through agents in order, ignoring the query. Baseline.",
    "embedding": "Nearest agent profile by cosine similarity of local embeddings.",
    "llm": "A small LLM classifies the query into an agent (strict JSON output).",
    "adaptive": "Weighted score of semantic fit, contextual historical success, "
    "expected latency, expected cost and current load.",
}


@router.get("/agents", response_model=list[AgentOut], summary="List agents with live stats")
async def list_agents(container: ContainerDep, principal: PrincipalDep) -> list[AgentOut]:
    async with container.sessions() as session:
        stats = await StatsRepository(session).agent_stats()
    inflight = await RedisLoadTracker(container.redis).inflight(container.registry.names)
    out = []
    for agent in container.registry:
        row = stats.get(agent.name, {})
        labelled = int(row.get("labelled") or 0)
        out.append(
            AgentOut(
                name=agent.name,
                display_name=agent.display_name,
                description=agent.description,
                model=agent.model,
                reasoning_effort=agent.reasoning_effort,
                timeout_s=agent.timeout_s,
                max_concurrency=agent.max_concurrency,
                inflight=inflight.get(agent.name, 0),
                fingerprint=agent.fingerprint,
                stats=AgentStatsOut(
                    executions=int(row.get("executions") or 0),
                    failed_executions=int(row.get("failed_executions") or 0),
                    p50_latency_ms=row.get("p50_latency_ms"),
                    p95_latency_ms=row.get("p95_latency_ms"),
                    mean_cost_usd=row.get("mean_cost_usd"),
                    total_cost_usd=float(row.get("total_cost_usd") or 0.0),
                    labelled=labelled,
                    success_rate=(int(row["successes"]) / labelled) if labelled else None,
                    refreshed_at=row.get("refreshed_at"),
                ),
            )
        )
    return out


@router.get("/me", response_model=MeOut, summary="Who am I (the caller's API key)")
async def whoami(principal: PrincipalDep) -> MeOut:
    """Lets clients adapt to the caller's role (e.g. hide admin-only actions)."""
    return MeOut(
        api_key_id=principal.api_key_id,
        name=principal.name,
        role=principal.role,
        rate_limit_per_minute=principal.rate_limit_per_minute,
    )


@router.get("/strategies", response_model=StrategiesOut, summary="List routing strategies")
async def list_strategies(container: ContainerDep, principal: PrincipalDep) -> StrategiesOut:
    return StrategiesOut(
        strategies=[
            StrategyOut(
                name=name,
                available=name in container.routers,
                is_default=name == container.settings.default_strategy,
                description=_DESCRIPTIONS[name],
            )
            for name in STRATEGIES
        ],
        adaptive_config=asdict(container.routing.adaptive),
    )
