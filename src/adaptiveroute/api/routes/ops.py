"""Benchmarks, traces, API-key admin, health and metrics endpoints."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, CollectorRegistry, generate_latest
from sqlalchemy import text

from adaptiveroute.api.deps import AdminDep, ContainerDep, PrincipalDep
from adaptiveroute.api.errors import APIError
from adaptiveroute.api.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    BenchmarkCreate,
    BenchmarkDetailOut,
    BenchmarkSummaryOut,
    HealthOut,
    JobAccepted,
    ReadyOut,
    TraceOut,
    TraceSpanOut,
)
from adaptiveroute.api.security import create_api_key
from adaptiveroute.db.repositories import ApiKeyRepository, BenchmarkRepository

benchmarks = APIRouter(prefix="/v1/benchmarks", tags=["benchmarks"])
traces = APIRouter(prefix="/v1/traces", tags=["observability"])
admin = APIRouter(prefix="/v1/admin", tags=["admin"])
health = APIRouter(tags=["health"])


# --- benchmarks -------------------------------------------------------------------


@benchmarks.get("", response_model=list[BenchmarkSummaryOut], summary="List benchmark runs")
async def list_benchmarks(
    container: ContainerDep, principal: PrincipalDep
) -> list[BenchmarkSummaryOut]:
    async with container.sessions() as session:
        runs = await BenchmarkRepository(session).list()
    return [BenchmarkSummaryOut.model_validate(r, from_attributes=True) for r in runs]


@benchmarks.get("/{run_id}", response_model=BenchmarkDetailOut, summary="Get a benchmark report")
async def get_benchmark(
    run_id: str, container: ContainerDep, principal: PrincipalDep
) -> BenchmarkDetailOut:
    async with container.sessions() as session:
        run = await BenchmarkRepository(session).get(run_id)
    if run is None:
        raise APIError(404, "Benchmark run not found")
    return BenchmarkDetailOut.model_validate(run, from_attributes=True)


@benchmarks.post(
    "",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run the routing benchmark in the background (admin)",
)
async def start_benchmark(
    body: BenchmarkCreate, request: Request, container: ContainerDep, principal: AdminDep
) -> JobAccepted:
    """Replays the committed outcome matrix through every strategy on the Celery worker
    (no new agent executions, so it is cheap and deterministic per seed)."""
    enqueue = getattr(request.app.state, "enqueue_benchmark", None)
    if enqueue is None:
        raise APIError(503, "Background jobs unavailable")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    await enqueue(run_id, body.model_dump())
    return JobAccepted(id=run_id, status="queued", links={"self": f"/v1/benchmarks/{run_id}"})


# --- traces -------------------------------------------------------------------------


def _tag_value(tag: dict[str, Any]) -> Any:
    return tag.get("value")


@traces.get("/{trace_id}", response_model=TraceOut, summary="Fetch a trace's spans")
async def get_trace(trace_id: str, container: ContainerDep, principal: PrincipalDep) -> TraceOut:
    """Proxies the Jaeger query API and flattens the spans for the dashboard's
    waterfall view. Only span names, timings and our own `ar.*` / `gen_ai.*` attributes
    are returned."""
    base = container.settings.trace_query_url
    if not base:
        raise APIError(503, "Trace backend not configured")
    if not all(c in "0123456789abcdef" for c in trace_id) or len(trace_id) != 32:
        raise APIError(400, "Invalid trace id")
    try:
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.get(f"/api/traces/{trace_id}")
    except httpx.HTTPError as exc:
        raise APIError(502, "Trace backend unreachable", repr(exc)) from exc
    if resp.status_code == 404 or not resp.json().get("data"):
        raise APIError(404, "Trace not found", "it may not have been exported yet; retry shortly")
    if resp.status_code != 200:
        raise APIError(502, "Trace backend error", f"status {resp.status_code}")

    data = resp.json()["data"][0]
    processes = data.get("processes", {})
    raw_spans = data.get("spans", [])
    start = min(s["startTime"] for s in raw_spans)
    end = max(s["startTime"] + s["duration"] for s in raw_spans)
    spans = []
    for s in sorted(raw_spans, key=lambda s: s["startTime"]):
        parent = next(
            (r["spanID"] for r in s.get("references", []) if r["refType"] == "CHILD_OF"), None
        )
        tags = {t["key"]: _tag_value(t) for t in s.get("tags", [])}
        attributes = {
            k: v
            for k, v in tags.items()
            if k.startswith(("ar.", "gen_ai.", "http.", "db.system", "celery."))
        }
        spans.append(
            TraceSpanOut(
                span_id=s["spanID"],
                parent_span_id=parent,
                name=s["operationName"],
                service=processes.get(s.get("processID"), {}).get("serviceName", "?"),
                start_offset_ms=(s["startTime"] - start) / 1000,
                duration_ms=s["duration"] / 1000,
                status="error"
                if tags.get("otel.status_code") == "ERROR" or tags.get("error")
                else "ok",
                attributes=attributes,
            )
        )
    return TraceOut(
        trace_id=trace_id,
        duration_ms=(end - start) / 1000,
        spans=spans,
        ui_url=f"{base.rstrip('/')}/trace/{trace_id}",
    )


# --- admin: API keys ----------------------------------------------------------------


@admin.post(
    "/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create an API key (admin)",
)
async def create_key(
    body: ApiKeyCreate, container: ContainerDep, principal: AdminDep
) -> ApiKeyCreated:
    async with container.sessions() as session:
        plaintext, key_id = await create_api_key(
            session,
            name=body.name,
            role=body.role,
            pepper=container.settings.api_key_pepper.get_secret_value(),
            rate_limit_per_minute=body.rate_limit_per_minute,
        )
        await session.commit()
    return ApiKeyCreated(id=key_id, name=body.name, role=body.role, api_key=plaintext)


@admin.get("/api-keys", response_model=list[ApiKeyOut], summary="List API keys (admin)")
async def list_keys(container: ContainerDep, principal: AdminDep) -> list[ApiKeyOut]:
    async with container.sessions() as session:
        rows = await ApiKeyRepository(session).list()
    return [ApiKeyOut.model_validate(r, from_attributes=True) for r in rows]


@admin.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key (admin)",
)
async def revoke_key(key_id: uuid.UUID, container: ContainerDep, principal: AdminDep) -> Response:
    async with container.sessions() as session:
        revoked = await ApiKeyRepository(session).revoke(key_id)
        await session.commit()
    if not revoked:
        raise APIError(404, "API key not found or already revoked")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- health / metrics ---------------------------------------------------------------


@health.get("/healthz", response_model=HealthOut, summary="Liveness probe")
async def healthz() -> HealthOut:
    """Process is up. Deliberately checks no dependencies (a DB outage should not make
    the orchestrator restart healthy API containers)."""
    return HealthOut(status="ok")


@health.get(
    "/readyz",
    response_model=ReadyOut,
    summary="Readiness probe",
    responses={503: {"model": ReadyOut}},
)
async def readyz(container: ContainerDep, response: Response) -> ReadyOut:
    """Ready to serve traffic: Postgres and Redis reachable, embedding model loaded."""
    checks: dict[str, str] = {}

    async def db() -> None:
        async with container.sessions() as session:
            await session.execute(text("SELECT 1"))

    async def cache() -> None:
        await container.redis.ping()

    for name, probe in (("postgres", db), ("redis", cache)):
        try:
            await asyncio.wait_for(probe(), timeout=2.0)
            checks[name] = "ok"
        except Exception as exc:  # any failure means "not ready"
            checks[name] = f"error: {type(exc).__name__}"
    checks["embedder"] = "ok"  # loaded during startup, before the app accepts traffic
    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyOut(
        status="ready" if ready else "not_ready",
        checks=checks,
        llm_configured=container.llm is not None,
        strategies=list(container.routers),
    )


@health.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
        from prometheus_client import multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        payload = generate_latest(registry)
    else:
        payload = generate_latest(REGISTRY)
    return Response(payload, media_type=CONTENT_TYPE_LATEST)
