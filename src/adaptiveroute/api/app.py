"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from adaptiveroute import __version__
from adaptiveroute.api.errors import install_error_handlers
from adaptiveroute.api.middleware import RequestContextMiddleware
from adaptiveroute.api.rate_limit import RateLimiter
from adaptiveroute.api.routes import catalog, ops, queries
from adaptiveroute.api.security import Authenticator
from adaptiveroute.config import PROJECT_ROOT, Settings, get_settings
from adaptiveroute.container import Container
from adaptiveroute.observability.logs import configure_logging, get_logger
from adaptiveroute.observability.tracing import configure_tracing, instrument_app

log = get_logger(__name__)

DESCRIPTION = """
AdaptiveRoute routes each query to one of five specialised LLM agents
(code, math, sql, writer, knowledge) using one of four strategies:
**round_robin**, **embedding**, **llm** and **adaptive**.

Authenticate with `Authorization: Bearer <api key>` (or `X-API-Key`). Errors use
`application/problem+json`. POST /v1/queries accepts an `Idempotency-Key` header.
"""

ContainerFactory = Callable[[Settings], Awaitable[Container]]


async def _celery_enqueue(query_id: uuid.UUID, use_cache: bool) -> None:
    from adaptiveroute.worker.tasks import execute_query

    # apply_async does blocking broker I/O; keep it off the event loop.
    await asyncio.to_thread(execute_query.apply_async, args=(str(query_id), use_cache))


async def _celery_benchmark(run_id: str, options: dict[str, Any]) -> None:
    from adaptiveroute.worker.tasks import run_benchmark

    await asyncio.to_thread(run_benchmark.apply_async, args=(run_id, options))


async def default_container(settings: Settings) -> Container:
    return await Container.create(settings, enqueue=_celery_enqueue)


def create_app(
    settings: Settings | None = None, container_factory: ContainerFactory | None = None
) -> FastAPI:
    settings = settings or get_settings()
    factory = container_factory or default_container
    configure_logging(settings.log_level, settings.log_json)
    configure_tracing(settings, service_name=f"{settings.service_name}-api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await factory(settings)
        app.state.container = container
        app.state.authenticator = Authenticator(
            container.sessions, settings.api_key_pepper.get_secret_value()
        )
        app.state.rate_limiter = RateLimiter(
            container.redis, settings.rate_limit_per_minute, settings.rate_limit_burst
        )
        app.state.enqueue_benchmark = _celery_benchmark
        log.info(
            "api_started",
            version=__version__,
            strategies=list(container.routers),
            llm_configured=container.llm is not None,
        )
        yield
        await container.close()

    app = FastAPI(
        title="AdaptiveRoute API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "queries", "description": "Submit and inspect routed queries."},
            {"name": "catalog", "description": "Agents and routing strategies."},
            {"name": "benchmarks", "description": "Routing benchmark reports."},
            {"name": "observability", "description": "Distributed traces."},
            {"name": "admin", "description": "API-key management (admin role)."},
            {"name": "health", "description": "Liveness/readiness probes."},
        ],
    )
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    )
    app.add_middleware(RequestContextMiddleware)

    for r in (queries.router, catalog.router, ops.benchmarks, ops.traces, ops.admin, ops.health):
        app.include_router(r)

    # Serve the built dashboard (frontend/dist) from the same origin when present.
    dist = PROJECT_ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")

    instrument_app(app, settings)
    return app
