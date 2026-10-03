"""agent_stats: latency and cost from successful executions only.

Failed executions (provider errors, missing API key) often fail fast, which made
agents look *faster* and cheaper than they are when they actually answer. Counts of
executions and failures still include everything.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMON = """
    SELECT e.agent,
           count(*)                                          AS executions,
           count(*) FILTER (WHERE e.status <> 'success')     AS failed_executions,
           {latency_and_cost}
           count(o.id)                                       AS labelled,
           count(o.id) FILTER (WHERE o.success)              AS successes,
           max(e.finished_at)                                AS last_execution_at,
           now()                                             AS refreshed_at
    FROM executions e
    LEFT JOIN outcomes o ON o.execution_id = e.id
    WHERE e.finished_at > now() - interval '30 days'
      AND NOT e.cache_hit  -- cache hits would make agents look faster than they are
    GROUP BY e.agent
"""

_SUCCESS_ONLY = """
           percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.latency_ms)
               FILTER (WHERE e.status = 'success')           AS p50_latency_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY e.latency_ms)
               FILTER (WHERE e.status = 'success')           AS p95_latency_ms,
           avg(e.cost_usd) FILTER (WHERE e.status = 'success') AS mean_cost_usd,
           sum(e.cost_usd)                                   AS total_cost_usd,
"""

_ALL_EXECUTIONS = """
           percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.latency_ms) AS p50_latency_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY e.latency_ms) AS p95_latency_ms,
           avg(e.cost_usd)                                   AS mean_cost_usd,
           sum(e.cost_usd)                                   AS total_cost_usd,
"""


def _recreate(latency_and_cost: str) -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS agent_stats")
    op.execute(
        "CREATE MATERIALIZED VIEW agent_stats AS"
        + _COMMON.format(latency_and_cost=latency_and_cost.strip() + "\n")
    )
    op.execute("CREATE UNIQUE INDEX ix_agent_stats_agent ON agent_stats (agent)")


def upgrade() -> None:
    _recreate(_SUCCESS_ONLY)


def downgrade() -> None:
    _recreate(_ALL_EXECUTIONS)
