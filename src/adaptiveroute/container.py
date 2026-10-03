"""Composition root: build every dependency once from Settings.

Used by the FastAPI lifespan and by each Celery worker process. This is the only
place that knows which concrete implementation backs each port; everything else
receives its dependencies through constructors.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adaptiveroute.agents import AgentExecutor, AgentRegistry
from adaptiveroute.config import Settings
from adaptiveroute.db.session import create_engine, create_session_factory
from adaptiveroute.embeddings import CachedEmbedder, FastEmbedEmbedder, HashingEmbedder
from adaptiveroute.history.postgres import PgHistory
from adaptiveroute.llm import (
    ChatRequest,
    ChatResponse,
    LLMBadRequest,
    LLMClient,
    OpenAICompatClient,
)
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.ports import Embedder
from adaptiveroute.response_cache import ResponseCache
from adaptiveroute.routing import AgentProfiles, Router, RoutingConfig, build_routers
from adaptiveroute.services.query_service import Enqueue, QueryService
from adaptiveroute.state.redis import RedisCounter, RedisLoadTracker

log = get_logger(__name__)


@dataclass
class Container:
    settings: Settings
    registry: AgentRegistry
    routing: RoutingConfig
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    embedder: Embedder
    llm: LLMClient | None
    routers: dict[str, Router]
    service: QueryService

    @classmethod
    async def create(
        cls,
        settings: Settings,
        *,
        enqueue: Enqueue | None = None,
        llm: LLMClient | None = None,
        embedder: Embedder | None = None,
        db_pool: bool = True,
    ) -> Container:
        """``llm`` / ``embedder`` overrides exist for tests; production builds its own."""
        registry = AgentRegistry.from_yaml(settings.agents_config_path)
        routing = RoutingConfig.from_yaml(settings.routing_config_path)
        engine = create_engine(settings, pool=db_pool)
        sessions = create_session_factory(engine)
        redis = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)

        if embedder is None:
            base: Embedder
            if settings.embedding_backend == "fastembed":
                fe = FastEmbedEmbedder(
                    settings.embedding_model, settings.embedding_dim, settings.embedding_cache_dir
                )
                fe.warmup()
                base = fe
            else:
                base = HashingEmbedder(settings.embedding_dim)
            embedder = CachedEmbedder(base, redis, settings.embedding_cache_ttl_s)

        if llm is None and settings.llm_configured:
            assert settings.llm_api_key is not None
            llm = OpenAICompatClient(
                settings.llm_base_url,
                settings.llm_api_key.get_secret_value(),
                max_attempts=settings.llm_max_attempts,
                backoff_base_s=settings.llm_backoff_base_s,
                backoff_max_s=settings.llm_backoff_max_s,
                connect_timeout_s=settings.llm_connect_timeout_s,
            )
        if llm is None:
            log.warning("llm_not_configured", hint="set GROQ_API_KEY to enable agents + LLM router")

        load = RedisLoadTracker(redis)
        profiles = await AgentProfiles.build(registry.agents, embedder)
        history = PgHistory(sessions, embedder.model_name)
        routers = build_routers(
            registry, profiles, history, load, RedisCounter(redis), llm, routing
        )
        executor = AgentExecutor(llm or _MissingLLM(), registry.prices, load)
        service = QueryService(
            registry=registry,
            routers=routers,
            executor=executor,
            embedder=embedder,
            sessions=sessions,
            response_cache=ResponseCache(redis, settings.response_cache_ttl_s),
            failover=routing.failover,
            sync_timeout_s=settings.sync_request_timeout_s,
            enqueue=enqueue,
        )
        return cls(
            settings, registry, routing, engine, sessions, redis, embedder, llm, routers, service
        )

    async def close(self) -> None:
        if isinstance(self.llm, OpenAICompatClient):
            await self.llm.aclose()
        await self.redis.aclose()
        await self.engine.dispose()


class _MissingLLM:
    """Stands in when no API key is configured: executions fail fast and clearly."""

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise LLMBadRequest(f"no LLM provider configured for {request.model} (set GROQ_API_KEY)")
