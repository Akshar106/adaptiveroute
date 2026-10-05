"""Command-line entry point: ``adaptiveroute --help``."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from adaptiveroute.config import get_settings

app = typer.Typer(help="AdaptiveRoute operations CLI.", no_args_is_help=True)


@app.command("create-api-key")
def create_api_key_cmd(
    name: Annotated[str, typer.Option(help="Human-readable owner/purpose")],
    role: Annotated[str, typer.Option(help="admin or user")] = "user",
    rate_limit: Annotated[int | None, typer.Option(help="Requests/minute override")] = None,
) -> None:
    """Create an API key and print it once (only its HMAC is stored)."""
    from adaptiveroute.api.security import create_api_key
    from adaptiveroute.db.session import create_engine, create_session_factory

    settings = get_settings()

    async def go() -> str:
        engine = create_engine(settings, pool=False)
        try:
            async with create_session_factory(engine)() as session:
                plaintext, _ = await create_api_key(
                    session,
                    name=name,
                    role=role,
                    pepper=settings.api_key_pepper.get_secret_value(),
                    rate_limit_per_minute=rate_limit,
                )
                await session.commit()
                return plaintext
        finally:
            await engine.dispose()

    key = asyncio.run(go())
    typer.echo(f"Created {role} key for {name!r}. Store it now; it will not be shown again:")
    typer.echo(key)


bench = typer.Typer(help="Benchmark: collect the outcome matrix, replay strategies, report.")
app.add_typer(bench, name="bench")


@bench.command("collect")
def bench_collect(
    concurrency: Annotated[int, typer.Option(help="Parallel provider calls")] = 4,
    rpm: Annotated[int, typer.Option(help="Client-side requests/minute per model")] = 25,
    max_wait: Annotated[
        float, typer.Option(help="Stop (resumably) if asked to wait longer")
    ] = 120.0,
    retry_errors: Annotated[
        bool, typer.Option(help="Re-run cells that failed for provider reasons (429/5xx/timeout)")
    ] = False,
    limit: Annotated[int | None, typer.Option(help="Only the first N items (smoke test)")] = None,
) -> None:
    """Run every agent on every dataset item against the real LLM (resumable)."""
    from adaptiveroute.evaluation.matrix import QuotaExhausted
    from adaptiveroute.evaluation.service import collect_matrix, matrix_path
    from adaptiveroute.llm import OpenAICompatClient
    from adaptiveroute.observability.logs import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    if not settings.llm_configured or settings.llm_api_key is None:
        typer.echo("GROQ_API_KEY is not set (put it in .env).", err=True)
        raise typer.Exit(2)
    llm = OpenAICompatClient(
        settings.llm_base_url,
        settings.llm_api_key.get_secret_value(),
        max_attempts=settings.llm_max_attempts,
        backoff_base_s=settings.llm_backoff_base_s,
        backoff_max_s=settings.llm_backoff_max_s,
    )

    async def go() -> dict[str, int]:
        try:
            return await collect_matrix(
                settings,
                llm,
                concurrency=concurrency,
                rpm_per_model=rpm,
                max_wait_s=max_wait,
                retry_errors=retry_errors,
                limit=limit,
            )
        finally:
            await llm.aclose()

    try:
        counts = asyncio.run(go())
    except QuotaExhausted as exc:
        typer.echo(f"Stopped: {exc}", err=True)
        raise typer.Exit(3) from exc
    typer.echo(f"Collected {counts} -> {matrix_path(settings)}")


@bench.command("run")
def bench_run(
    seeds: Annotated[str, typer.Option(help="Comma-separated seeds")] = "0,1,2",
    ablations: Annotated[bool, typer.Option(help="Include adaptive ablations")] = True,
    embedding_backend: Annotated[str | None, typer.Option(help="fastembed|hashing")] = None,
    run_id: Annotated[str | None, typer.Option(help="Defaults to a UTC timestamp")] = None,
    store: Annotated[bool, typer.Option(help="Also store the report in Postgres")] = False,
) -> None:
    """Replay every routing strategy over the outcome matrix and write a report."""
    from adaptiveroute.evaluation.runner import new_run_id
    from adaptiveroute.evaluation.service import (
        benchmark_options,
        replay_embedder,
        run_benchmark,
        store_report,
    )
    from adaptiveroute.observability.logs import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    rid = run_id or new_run_id()
    options = benchmark_options(
        {"seeds": [int(s) for s in seeds.split(",")], "ablations": ablations}
    )

    async def go() -> None:
        report = await run_benchmark(
            settings,
            run_id=rid,
            options=options,
            embedder=replay_embedder(settings, embedding_backend),
        )
        if store:
            from adaptiveroute.db.session import create_engine, create_session_factory

            engine = create_engine(settings, pool=False)
            try:
                await store_report(create_session_factory(engine), report)
            finally:
                await engine.dispose()

    asyncio.run(go())
    typer.echo(f"Report: {settings.benchmark_dir / 'results' / rid / 'report.md'}")


@bench.command("import")
def bench_import(
    results_json: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Load a committed results.json into Postgres so the dashboard can show it."""
    from adaptiveroute.db.session import create_engine, create_session_factory
    from adaptiveroute.evaluation.service import import_report

    settings = get_settings()

    async def go() -> str:
        engine = create_engine(settings, pool=False)
        try:
            return await import_report(create_session_factory(engine), results_json)
        finally:
            await engine.dispose()

    typer.echo(f"Imported benchmark run {asyncio.run(go())}")


@bench.command("seed-history")
def bench_seed_history() -> None:
    """Import the outcome matrix as labelled history for the live adaptive router."""
    from adaptiveroute.db.repositories import StatsRepository
    from adaptiveroute.db.session import create_engine, create_session_factory
    from adaptiveroute.evaluation.service import replay_embedder, seed_history

    settings = get_settings()

    async def go() -> int:
        engine = create_engine(settings, pool=False)
        sessions = create_session_factory(engine)
        try:
            n = await seed_history(settings, sessions, replay_embedder(settings))
            async with sessions() as session:
                await StatsRepository(session).refresh()
            return n
        finally:
            await engine.dispose()

    typer.echo(f"Imported {asyncio.run(go())} labelled executions")


if __name__ == "__main__":
    app()
