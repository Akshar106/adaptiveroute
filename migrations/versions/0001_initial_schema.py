"""Initial schema: api keys, queries, executions, outcomes, idempotency, benchmarks.

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('admin', 'user')", name="ck_api_keys_role"),
    )

    op.create_table(
        "queries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("api_key_id", sa.Uuid(), sa.ForeignKey("api_keys.id", ondelete="SET NULL")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(DIM), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("selected_agent", sa.String(32), nullable=False),
        sa.Column("fallback", sa.String(32)),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("decision_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("routing_latency_ms", sa.Float(), nullable=False),
        sa.Column("embedding_latency_ms", sa.Float()),
        sa.Column("router_cost_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("trace_id", sa.String(32)),
        sa.Column("request_id", sa.String(64)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('routed', 'queued', 'running', 'completed', 'failed')",
            name="ck_queries_status",
        ),
    )
    op.create_index("ix_queries_created_at", "queries", ["created_at"])

    op.create_table(
        "executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "query_id", sa.Uuid(), sa.ForeignKey("queries.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("agent_fingerprint", sa.String(12), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("output", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("llm_attempts", sa.Integer(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("query_id", "attempt_no", name="uq_executions_query_attempt"),
        sa.CheckConstraint(
            "status IN ('success', 'error', 'timeout')", name="ck_executions_status"
        ),
    )
    op.create_index("ix_executions_query_id", "executions", ["query_id"])
    op.create_index("ix_executions_agent_finished", "executions", ["agent", "finished_at"])

    op.create_table(
        "outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "execution_id",
            sa.Uuid(),
            sa.ForeignKey("executions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("agent", sa.String(32), nullable=False),
        sa.Column("embedding", Vector(DIM), nullable=False),
        sa.Column("embedding_model", sa.String(100), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source IN ('user', 'checker', 'benchmark')", name="ck_outcomes_source"),
    )
    op.create_index("ix_outcomes_agent_model", "outcomes", ["agent", "embedding_model"])
    op.create_index(
        "ix_outcomes_embedding_hnsw",
        "outcomes",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "idempotency_keys",
        sa.Column(
            "api_key_id",
            sa.Uuid(),
            sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_body", postgresql.JSONB()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])

    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("dataset_sha256", sa.String(64), nullable=False),
        sa.Column("git_sha", sa.String(40)),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("summary", postgresql.JSONB()),
        sa.Column("report_markdown", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # Per-agent performance over the last 30 days. Refreshed CONCURRENTLY by a Celery
    # beat task (which needs the unique index), so reads never block on a refresh.
    op.execute(
        """
        CREATE MATERIALIZED VIEW agent_stats AS
        SELECT e.agent,
               count(*)                                          AS executions,
               count(*) FILTER (WHERE e.status <> 'success')     AS failed_executions,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.latency_ms) AS p50_latency_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY e.latency_ms) AS p95_latency_ms,
               avg(e.cost_usd)                                   AS mean_cost_usd,
               sum(e.cost_usd)                                   AS total_cost_usd,
               count(o.id)                                       AS labelled,
               count(o.id) FILTER (WHERE o.success)              AS successes,
               max(e.finished_at)                                AS last_execution_at,
               now()                                             AS refreshed_at
        FROM executions e
        LEFT JOIN outcomes o ON o.execution_id = e.id
        WHERE e.finished_at > now() - interval '30 days'
        GROUP BY e.agent
        """
    )
    op.execute("CREATE UNIQUE INDEX ix_agent_stats_agent ON agent_stats (agent)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS agent_stats")
    op.drop_table("benchmark_runs")
    op.drop_table("idempotency_keys")
    op.drop_table("outcomes")
    op.drop_table("executions")
    op.drop_table("queries")
    op.drop_table("api_keys")
