"""Request/response models (these also generate the OpenAPI documentation)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from adaptiveroute.db.models import Execution, Query
from adaptiveroute.domain import RoutingDecision

Strategy = Literal["round_robin", "embedding", "llm", "adaptive"]


class QueryCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"query": "What is 17.5% of 640?", "strategy": "adaptive", "mode": "sync"}]
        },
    )

    query: str = Field(min_length=1, max_length=4000, description="The user's request.")
    strategy: Strategy | None = Field(
        default=None, description="Routing strategy; defaults to the server's default."
    )
    execute: bool = Field(default=True, description="False = route only, don't run the agent.")
    mode: Literal["sync", "async"] = Field(
        default="sync", description="async returns 202 immediately; poll GET /v1/queries/{id}."
    )
    use_cache: bool = Field(default=True, description="Allow serving an identical cached answer.")


class CandidateOut(BaseModel):
    agent: str
    score: float
    components: dict[str, float]


class DecisionOut(BaseModel):
    strategy: str
    agent: str
    fallback: str | None
    reasoning: str
    latency_ms: float
    embedding_latency_ms: float | None = None
    cost_usd: float
    candidates: list[CandidateOut]
    metadata: dict[str, Any]

    @classmethod
    def from_domain(cls, d: RoutingDecision, embedding_ms: float | None = None) -> DecisionOut:
        return cls(
            strategy=d.strategy,
            agent=d.agent,
            fallback=d.fallback,
            reasoning=d.reasoning,
            latency_ms=d.latency_ms,
            embedding_latency_ms=embedding_ms,
            cost_usd=d.cost_usd,
            candidates=[
                CandidateOut(agent=c.agent, score=c.score, components=dict(c.components))
                for c in d.candidates
            ],
            metadata=dict(d.metadata),
        )


class OutcomeOut(BaseModel):
    success: bool
    source: str
    detail: str | None
    created_at: datetime


class ExecutionOut(BaseModel):
    attempt_no: int
    agent: str
    model: str
    status: str
    output: str | None
    error: str | None
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    llm_attempts: int
    cache_hit: bool
    finished_at: datetime
    outcome: OutcomeOut | None

    @classmethod
    def from_row(cls, e: Execution) -> ExecutionOut:
        o = e.outcome
        return cls(
            attempt_no=e.attempt_no,
            agent=e.agent,
            model=e.model,
            status=e.status,
            output=e.output,
            error=e.error,
            latency_ms=e.latency_ms,
            input_tokens=e.input_tokens,
            output_tokens=e.output_tokens,
            cost_usd=e.cost_usd,
            llm_attempts=e.llm_attempts,
            cache_hit=e.cache_hit,
            finished_at=e.finished_at,
            outcome=(
                OutcomeOut(
                    success=o.success, source=o.source, detail=o.detail, created_at=o.created_at
                )
                if o is not None
                else None
            ),
        )


class QueryOut(BaseModel):
    id: uuid.UUID
    query: str
    status: Literal["routed", "queued", "running", "completed", "failed"]
    created_at: datetime
    completed_at: datetime | None
    decision: DecisionOut
    executions: list[ExecutionOut]
    answer: str | None = Field(description="Output of the final successful execution, if any.")
    total_cost_usd: float = Field(description="Router + all agent executions (estimated).")
    trace_id: str | None
    links: dict[str, str]

    @classmethod
    def from_row(cls, q: Query, trace_base_url: str | None = None) -> QueryOut:
        executions = [ExecutionOut.from_row(e) for e in q.executions]
        final = q.executions[-1] if q.executions else None
        links = {"self": f"/v1/queries/{q.id}"}
        if q.trace_id:
            links["trace"] = f"/v1/traces/{q.trace_id}"
            if trace_base_url:
                links["trace_ui"] = f"{trace_base_url.rstrip('/')}/trace/{q.trace_id}"
        return cls(
            id=q.id,
            query=q.text,
            status=q.status,
            created_at=q.created_at,
            completed_at=q.completed_at,
            decision=DecisionOut(
                strategy=q.strategy,
                agent=q.selected_agent,
                fallback=q.fallback,
                reasoning=q.reasoning,
                latency_ms=q.routing_latency_ms,
                embedding_latency_ms=q.embedding_latency_ms,
                cost_usd=q.router_cost_usd,
                candidates=[CandidateOut(**c) for c in q.candidates],
                metadata=q.decision_metadata,
            ),
            executions=executions,
            answer=final.output if final is not None and final.status == "success" else None,
            total_cost_usd=q.router_cost_usd + sum(e.cost_usd for e in q.executions),
            trace_id=q.trace_id,
            links=links,
        )


class QuerySummary(BaseModel):
    id: uuid.UUID
    query: str
    status: str
    strategy: str
    agent: str
    created_at: datetime
    routing_latency_ms: float
    execution_latency_ms: float | None
    total_cost_usd: float
    success: bool | None = Field(description="Task-success label, if one was recorded.")

    @classmethod
    def from_row(cls, q: Query) -> QuerySummary:
        final = q.executions[-1] if q.executions else None
        return cls(
            id=q.id,
            query=q.text[:200],
            status=q.status,
            strategy=q.strategy,
            agent=final.agent if final is not None else q.selected_agent,
            created_at=q.created_at,
            routing_latency_ms=q.routing_latency_ms,
            execution_latency_ms=sum(e.latency_ms for e in q.executions) if final else None,
            total_cost_usd=q.router_cost_usd + sum(e.cost_usd for e in q.executions),
            success=(final.outcome.success if final is not None and final.outcome else None),
        )


class QueryList(BaseModel):
    items: list[QuerySummary]
    next_cursor: str | None = Field(description="Pass as `before` to fetch the next page.")


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool = Field(description="Did the answer solve the task?")
    comment: str | None = Field(default=None, max_length=1000)


class CompareIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=4000)


class CompareOut(BaseModel):
    query: str
    embedding_latency_ms: float | None
    decisions: dict[str, DecisionOut]
    agreement: bool = Field(description="True when every strategy picked the same agent.")


class AgentStatsOut(BaseModel):
    executions: int = 0
    failed_executions: int = 0
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    mean_cost_usd: float | None = None
    total_cost_usd: float = 0.0
    labelled: int = 0
    success_rate: float | None = None
    refreshed_at: datetime | None = None


class AgentOut(BaseModel):
    name: str
    display_name: str
    description: str
    model: str
    reasoning_effort: str | None
    timeout_s: float
    max_concurrency: int
    inflight: int
    fingerprint: str
    stats: AgentStatsOut


class StrategyOut(BaseModel):
    name: str
    available: bool
    is_default: bool
    description: str


class StrategiesOut(BaseModel):
    strategies: list[StrategyOut]
    adaptive_config: dict[str, Any]


class BenchmarkSummaryOut(BaseModel):
    id: str
    status: str
    created_at: datetime
    finished_at: datetime | None
    dataset_sha256: str
    git_sha: str | None
    summary: dict[str, Any] | None


class BenchmarkDetailOut(BenchmarkSummaryOut):
    config: dict[str, Any]
    report_markdown: str | None
    error: str | None


class BenchmarkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seeds: list[int] = Field(default=[0, 1, 2], min_length=1, max_length=10)
    strategies: list[Strategy] | None = None


class JobAccepted(BaseModel):
    id: str
    status: str
    links: dict[str, str]


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "user"] = "user"
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10_000)


class ApiKeyCreated(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    api_key: str = Field(description="Shown once. Store it now; it cannot be retrieved later.")


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    role: str
    rate_limit_per_minute: int | None
    created_at: datetime
    revoked_at: datetime | None


class TraceSpanOut(BaseModel):
    span_id: str
    parent_span_id: str | None
    name: str
    service: str
    start_offset_ms: float
    duration_ms: float
    status: str
    attributes: dict[str, Any]


class TraceOut(BaseModel):
    trace_id: str
    duration_ms: float
    spans: list[TraceSpanOut]
    ui_url: str | None


class HealthOut(BaseModel):
    status: Literal["ok"]


class ReadyOut(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]
    llm_configured: bool
    strategies: list[str]
