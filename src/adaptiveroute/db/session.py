"""Async engine / session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from adaptiveroute.config import Settings


def create_engine(settings: Settings, *, pool: bool = True) -> AsyncEngine:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if pool:
        kwargs |= {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow}
    else:
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
    return create_async_engine(settings.database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay usable after commit (we serialise them
    # into API responses after the unit of work is done).
    return async_sessionmaker(engine, expire_on_commit=False)
