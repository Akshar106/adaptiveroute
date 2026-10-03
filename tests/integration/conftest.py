"""Fixtures for tests that need real Postgres (pgvector) and Redis.

Start them with `make deps-up` (docker compose). Tests use a separate database
(`adaptiveroute_test`) migrated once per session with Alembic, and Redis DB 15, and
are skipped automatically when the services are not reachable.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import asyncpg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from adaptiveroute.config import PROJECT_ROOT

DATABASE_URL = os.environ.get(
    "AR_TEST_DATABASE_URL",
    "postgresql+asyncpg://adaptiveroute:adaptiveroute@localhost:5432/adaptiveroute_test",
)
REDIS_URL = os.environ.get("AR_TEST_REDIS_URL", "redis://localhost:6379/15")
TABLES = ["outcomes", "executions", "queries", "idempotency_keys", "api_keys", "benchmark_runs"]


def _reachable(url: str, default_port: int) -> bool:
    parsed = urlparse(url.replace("+asyncpg", ""))
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or default_port), 1
        ):
            return True
    except OSError:
        return False


SERVICES_UP = _reachable(DATABASE_URL, 5432) and _reachable(REDIS_URL, 6379)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip = pytest.mark.skip(reason="Postgres/Redis not reachable (run `make deps-up`)")
    for item in items:
        if "tests/integration" in str(item.path):
            item.add_marker(pytest.mark.integration)
            if not SERVICES_UP:
                item.add_marker(skip)


async def _ensure_database(url: str) -> None:
    parsed = urlparse(url.replace("+asyncpg", ""))
    dbname = parsed.path.lstrip("/")
    admin = await asyncpg.connect(
        user=parsed.username,
        password=parsed.password,
        host=parsed.hostname,
        port=parsed.port or 5432,
        database="postgres",
    )
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await admin.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await admin.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    asyncio.run(_ensure_database(DATABASE_URL))
    cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    cfg.attributes["database_url"] = DATABASE_URL
    command.upgrade(cfg, "head")
    return DATABASE_URL


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(database_url, pool_size=5)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
        await conn.execute(text("REFRESH MATERIALIZED VIEW agent_stats"))
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(REDIS_URL)
    await client.flushdb()
    yield client
    await client.aclose()
