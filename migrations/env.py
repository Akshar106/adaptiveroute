"""Alembic environment (async engine, URL from AR_DATABASE_URL)."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from adaptiveroute.config import get_settings
from adaptiveroute.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Objects managed by hand-written SQL that autogenerate should not try to "fix".
IGNORED_TABLES = {"agent_stats"}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    return not (type_ == "table" and name in IGNORED_TABLES)


def _database_url() -> str:
    # Tests pass an explicit URL via Config.attributes; normal runs use AR_DATABASE_URL.
    return str(config.attributes.get("database_url") or get_settings().database_url)


def _configure(connection: Connection | None = None, url: str | None = None) -> None:
    context.configure(
        connection=connection,
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )


def run_migrations_offline() -> None:
    _configure(url=_database_url())
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
