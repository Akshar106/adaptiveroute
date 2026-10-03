"""SQLAlchemy ORM models.

Tables
------
api_keys          hashed API keys (the plaintext is shown once at creation)
queries           one row per routed query: decision, candidates, embedding
executions        one row per agent run (primary, plus at most one failover)
outcomes          task-success label per execution. Denormalised copy of the agent,
                  embedding, latency and cost so the adaptive router's kNN lookup is a
                  single-table pgvector search (see docs/database.md)
idempotency_keys  stored responses for Idempotency-Key replays
benchmark_runs    benchmark reports (summary JSON + markdown)
agent_stats       materialised view of per-agent performance (migration 0001)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 384  # must match AR_EMBEDDING_DIM; changing it requires a migration


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Query(TimestampMixin, Base):
    __tablename__ = "queries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str] = mapped_column(String(100))
    strategy: Mapped[str] = mapped_column(String(32))
    selected_agent: Mapped[str] = mapped_column(String(32))
    fallback: Mapped[str | None] = mapped_column(String(32))
    reasoning: Mapped[str] = mapped_column(Text)
    candidates: Mapped[list[Any]] = mapped_column(JSONB)
    decision_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    routing_latency_ms: Mapped[float] = mapped_column(Float)
    embedding_latency_ms: Mapped[float | None] = mapped_column(Float)
    router_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16))  # routed|queued|running|completed|failed
    source: Mapped[str] = mapped_column(String(16), default="api")  # api|benchmark
    trace_id: Mapped[str | None] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    executions: Mapped[list[Execution]] = relationship(
        back_populates="query",
        order_by="Execution.attempt_no",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_queries_created_at", "created_at"),)


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("queries.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)  # 1 = selected agent, 2 = failover
    agent: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    agent_fingerprint: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(16))  # success|error|timeout
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[float] = mapped_column(Float)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    llm_attempts: Mapped[int] = mapped_column(Integer, default=1)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    query: Mapped[Query] = relationship(back_populates="executions")
    outcome: Mapped[Outcome | None] = relationship(
        back_populates="execution", uselist=False, lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("query_id", "attempt_no", name="uq_executions_query_attempt"),
        Index("ix_executions_agent_finished", "agent", "finished_at"),
    )


class Outcome(TimestampMixin, Base):
    __tablename__ = "outcomes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), unique=True
    )
    success: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(16))  # user|checker|benchmark
    detail: Mapped[str | None] = mapped_column(Text)
    # Denormalised from executions/queries for the router's kNN lookup.
    agent: Mapped[str] = mapped_column(String(32))
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str] = mapped_column(String(100))
    latency_ms: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)

    execution: Mapped[Execution] = relationship(back_populates="outcome")

    __table_args__ = (
        Index("ix_outcomes_agent_model", "agent", "embedding_model"),
        Index(
            "ix_outcomes_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class IdempotencyKey(TimestampMixin, Base):
    __tablename__ = "idempotency_keys"

    api_key_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # in_progress|completed
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BenchmarkRun(TimestampMixin, Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))  # running|completed|failed
    dataset_sha256: Mapped[str] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(40))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    report_markdown: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
