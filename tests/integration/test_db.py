from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptiveroute.db.repositories import (
    IdempotencyRepository,
    OutcomeRepository,
    QueryRepository,
    StatsRepository,
)
from adaptiveroute.domain import (
    AgentSpec,
    CandidateScore,
    ExecutionResult,
    ExecutionStatus,
    RoutingDecision,
)
from adaptiveroute.history import InMemoryHistory
from adaptiveroute.history.postgres import PgHistory

DIM = 384
MODEL = "test-embedder"
AGENT = AgentSpec("math", "Math", "d" * 20, "openai/gpt-oss-20b", "p" * 20)


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def decision(agent: str = "math") -> RoutingDecision:
    return RoutingDecision(
        strategy="adaptive",
        agent=agent,
        candidates=(CandidateScore(agent, 0.9, {"semantic": 1.0}), CandidateScore("code", 0.4)),
        reasoning="because",
        latency_ms=3.2,
        metadata={"neighbors": {"math": 0}},
    )


def result(
    agent: str = "math", status: ExecutionStatus = ExecutionStatus.SUCCESS, **kw: object
) -> ExecutionResult:
    base: dict[str, object] = {
        "agent": agent,
        "model": "openai/gpt-oss-20b",
        "status": status,
        "output": "ANSWER: 4",
        "error": None,
        "latency_ms": 1200.0,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0001,
    }
    base.update(kw)
    return ExecutionResult(**base)  # type: ignore[arg-type]


async def make_query(
    session: AsyncSession, vec: np.ndarray, agent: str = "math", text_: str = "2+2?"
):  # type: ignore[no-untyped-def]
    repo = QueryRepository(session)
    q = await repo.create(
        text_=text_, embedding=vec, embedding_model=MODEL, embedding_ms=4.0,
        decision=decision(agent), status="running",
    )  # fmt: skip
    return repo, q


async def test_migration_created_vector_extension_and_view(session: AsyncSession) -> None:
    ext = await session.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
    assert ext is not None
    count = await session.scalar(text("SELECT count(*) FROM agent_stats"))
    assert count == 0


async def test_query_execution_outcome_roundtrip(session: AsyncSession) -> None:
    repo, q = await make_query(session, unit(1))
    ex = await repo.add_execution(q, result(), AGENT, attempt_no=1)
    await repo.set_status(q, "completed")
    await OutcomeRepository(session).record(ex, q, success=True, source="checker", detail="ok")
    await session.commit()

    loaded = await QueryRepository(session).get(q.id)
    assert loaded is not None
    assert loaded.status == "completed" and loaded.completed_at is not None
    assert loaded.candidates[0] == {"agent": "math", "score": 0.9, "components": {"semantic": 1.0}}
    assert np.allclose(loaded.embedding, unit(1))
    assert [e.attempt_no for e in loaded.executions] == [1]
    assert loaded.executions[0].agent_fingerprint == AGENT.fingerprint
    assert loaded.executions[0].outcome is not None
    assert loaded.executions[0].outcome.success is True


async def test_outcome_upsert_latest_label_wins(session: AsyncSession) -> None:
    repo, q = await make_query(session, unit(2))
    ex = await repo.add_execution(q, result(), AGENT, 1)
    outcomes = OutcomeRepository(session)
    await outcomes.record(ex, q, success=True, source="checker")
    await outcomes.record(ex, q, success=False, source="user", detail="wrong answer")
    await session.commit()
    rows = (await session.execute(text("SELECT success, source FROM outcomes"))).all()
    assert rows == [(False, "user")]


async def test_list_recent_is_newest_first_with_cursor(session: AsyncSession) -> None:
    repo = QueryRepository(session)
    for i in range(3):
        await make_query(session, unit(10 + i), text_=f"q{i}")
        await session.commit()
    page = await repo.list_recent(limit=2)
    assert [q.text for q in page] == ["q2", "q1"]
    rest = await repo.list_recent(limit=2, before=(page[-1].created_at, page[-1].id))
    assert [q.text for q in rest] == ["q0"]


async def _seed_outcomes(
    session_factory: async_sessionmaker[AsyncSession], memory: InMemoryHistory
) -> None:
    async with session_factory() as session:
        for i in range(12):
            agent = "math" if i % 2 else "code"
            vec = unit(100 + i)
            repo, q = await make_query(session, vec, agent)
            ex = await repo.add_execution(q, result(agent, latency_ms=100.0 * i), AGENT, 1)
            await OutcomeRepository(session).record(ex, q, success=i % 3 != 0, source="benchmark")
            memory.add(vec, agent, i % 3 != 0, 100.0 * i, 0.0001)
        await session.commit()


async def test_pg_history_matches_in_memory_history(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Contract test: both PerformanceHistory implementations give the same answers."""
    memory = InMemoryHistory()
    await _seed_outcomes(session_factory, memory)
    async with session_factory() as s:
        await StatsRepository(s).refresh()
    pg = PgHistory(session_factory, MODEL, aggregates_ttl_s=0)

    probe = unit(105)  # identical to one stored "math" query
    agents = ["code", "math", "writer"]
    got, want = await pg.neighbors(probe, agents, k=3), await memory.neighbors(probe, agents, k=3)
    assert got["writer"] == want["writer"] == []
    for agent in ("code", "math"):
        assert [r.success for r in got[agent]] == [r.success for r in want[agent]]
        assert [r.similarity for r in got[agent]] == pytest.approx(
            [r.similarity for r in want[agent]], abs=1e-5
        )
    assert got["math"][0].similarity == pytest.approx(1.0, abs=1e-5)

    pg_agg, mem_agg = await pg.aggregates(), await memory.aggregates()
    assert set(pg_agg) == set(mem_agg) == {"code", "math"}
    for agent in pg_agg:
        assert (pg_agg[agent].n, pg_agg[agent].successes) == (
            mem_agg[agent].n,
            mem_agg[agent].successes,
        )
        assert pg_agg[agent].p50_latency_ms == pytest.approx(mem_agg[agent].p50_latency_ms)


async def test_pg_history_ignores_other_embedding_models(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_outcomes(session_factory, InMemoryHistory())
    pg = PgHistory(session_factory, "some-other-model")
    assert await pg.neighbors(unit(1), ["math"], k=5) == {"math": []}


async def test_idempotency_claim_conflict_and_release(session: AsyncSession) -> None:
    from adaptiveroute.db.repositories import ApiKeyRepository

    key = await ApiKeyRepository(session).create(
        name="t", prefix="abcd1234", key_hash="h" * 64, role="user", rate_limit=None
    )
    await session.commit()
    repo = IdempotencyRepository(session)
    assert await repo.try_begin(key.id, "k1", "hash-a", timedelta(hours=1)) is None
    existing = await repo.try_begin(key.id, "k1", "hash-a", timedelta(hours=1))
    assert existing is not None and existing.status == "in_progress"

    await repo.complete(key.id, "k1", 200, {"ok": True})
    replay = await repo.try_begin(key.id, "k1", "hash-a", timedelta(hours=1))
    assert replay is not None and replay.status == "completed"
    assert replay.response_body == {"ok": True}

    await repo.release(key.id, "k1")
    assert await repo.try_begin(key.id, "k1", "hash-b", timedelta(hours=1)) is None


async def test_expired_idempotency_key_is_reclaimable(session: AsyncSession) -> None:
    from adaptiveroute.db.repositories import ApiKeyRepository

    key = await ApiKeyRepository(session).create(
        name="t", prefix="efgh5678", key_hash="h" * 64, role="user", rate_limit=None
    )
    await session.commit()
    repo = IdempotencyRepository(session)
    assert await repo.try_begin(key.id, "k", "h", timedelta(seconds=-1)) is None
    assert await repo.try_begin(key.id, "k", "h2", timedelta(hours=1)) is None
    assert await repo.purge_expired() == 0


async def test_requeue_stuck_finds_lost_running_and_queued_work(session: AsyncSession) -> None:
    repo = QueryRepository(session)
    _, running = await make_query(session, unit(201))  # created with status 'running'
    _, queued = await make_query(session, unit(202))
    queued.status = "queued"
    _, fresh = await make_query(session, unit(203))
    await session.commit()
    await session.execute(
        text("UPDATE queries SET created_at = now() - interval '1 hour' WHERE id IN (:a, :b)"),
        {"a": running.id, "b": queued.id},
    )
    await session.commit()

    ids = await repo.requeue_stuck(timedelta(minutes=10))
    await session.commit()
    assert set(ids) == {running.id, queued.id}
    assert (await repo.get(running.id)).status == "queued"  # type: ignore[union-attr]
    assert (await repo.get(fresh.id)).status == "running"  # type: ignore[union-attr]


async def test_agent_stats_latency_ignores_failed_executions(session: AsyncSession) -> None:
    repo, q = await make_query(session, unit(301))
    await repo.add_execution(q, result(latency_ms=2000.0, cost_usd=0.0004), AGENT, 1)
    _, q2 = await make_query(session, unit(302))
    await repo.add_execution(
        q2, result(status=ExecutionStatus.ERROR, latency_ms=1.0, cost_usd=0.0), AGENT, 1
    )
    await session.commit()
    stats = StatsRepository(session)
    await stats.refresh()
    row = (await stats.agent_stats())["math"]
    assert row["executions"] == 2 and row["failed_executions"] == 1
    assert row["p50_latency_ms"] == 2000.0  # the fast failure doesn't drag latency down
    assert row["mean_cost_usd"] == pytest.approx(0.0004)
