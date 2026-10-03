"""Query submission, retrieval, feedback and strategy comparison."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Header, Query, status
from fastapi.responses import JSONResponse

from adaptiveroute.api.deps import ContainerDep, PrincipalDep
from adaptiveroute.api.errors import APIError
from adaptiveroute.api.idempotency import request_fingerprint, run_idempotent
from adaptiveroute.api.schemas import (
    CompareIn,
    CompareOut,
    DecisionOut,
    FeedbackIn,
    QueryCreate,
    QueryList,
    QueryOut,
    QuerySummary,
)
from adaptiveroute.db.repositories import QueryRepository
from adaptiveroute.services.query_service import QueryNotFoundError, SubmitOptions

router = APIRouter(prefix="/v1", tags=["queries"])


@router.post(
    "/queries",
    response_model=QueryOut,
    status_code=status.HTTP_200_OK,
    summary="Route a query to an agent (and optionally execute it)",
    responses={
        202: {"description": "Accepted for async execution", "model": QueryOut},
        409: {"description": "Same Idempotency-Key still in progress"},
        422: {"description": "Validation error or Idempotency-Key reused with another body"},
        429: {"description": "Rate limit exceeded"},
        504: {"description": "Sync execution exceeded the request timeout"},
    },
)
async def create_query(
    body: QueryCreate,
    container: ContainerDep,
    principal: PrincipalDep,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    """Routes the query with the chosen strategy, stores the decision and, unless
    `execute=false`, runs the selected agent. With `mode=async` the agent runs on the
    Celery worker and the response is `202` with `status=queued`.

    Send an `Idempotency-Key` header to make retries safe: a repeated request with the
    same key returns the stored response instead of executing again.
    """
    settings = container.settings
    if len(body.query) > settings.max_query_chars:
        raise APIError(422, "Query too long", f"max {settings.max_query_chars} characters")
    strategy = body.strategy or settings.default_strategy
    options = SubmitOptions(
        strategy=strategy,
        execute=body.execute,
        run_async=body.mode == "async",
        use_cache=body.use_cache,
    )

    async def handler() -> tuple[int, dict[str, Any]]:
        query = await container.service.submit(
            body.query,
            options,
            api_key_id=principal.api_key_id,
            request_id=structlog.contextvars.get_contextvars().get("request_id"),
        )
        out = QueryOut.from_row(query, settings.trace_query_url)
        code = status.HTTP_202_ACCEPTED if options.run_async else status.HTTP_200_OK
        return code, out.model_dump(mode="json")

    return await run_idempotent(
        sessions=container.sessions,
        principal=principal,
        key=idempotency_key,
        fingerprint=request_fingerprint("POST", "/v1/queries", body.model_dump()),
        ttl=timedelta(hours=settings.idempotency_ttl_hours),
        handler=handler,
    )


@router.get("/queries", response_model=QueryList, summary="List recent queries")
async def list_queries(
    container: ContainerDep,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[datetime | None, Query(description="Cursor from `next_cursor`")] = None,
) -> QueryList:
    """Newest first, keyset-paginated. Admins see every query; users see their own."""
    async with container.sessions() as session:
        rows = await QueryRepository(session).list_recent(
            limit=limit,
            before=before,
            api_key_id=None if principal.is_admin else principal.api_key_id,
        )
    items = [QuerySummary.from_row(q) for q in rows]
    cursor = rows[-1].created_at.isoformat() if len(rows) == limit else None
    return QueryList(items=items, next_cursor=cursor)


@router.get("/queries/{query_id}", response_model=QueryOut, summary="Get a query and its result")
async def get_query(
    query_id: uuid.UUID, container: ContainerDep, principal: PrincipalDep
) -> QueryOut:
    query = await container.service.get(query_id)
    if not principal.is_admin and query.api_key_id != principal.api_key_id:
        raise QueryNotFoundError(str(query_id))  # don't reveal other users' query ids
    return QueryOut.from_row(query, container.settings.trace_query_url)


@router.post(
    "/queries/{query_id}/feedback",
    response_model=QueryOut,
    summary="Record whether the answer solved the task",
)
async def give_feedback(
    query_id: uuid.UUID, body: FeedbackIn, container: ContainerDep, principal: PrincipalDep
) -> QueryOut:
    """Labels the query's final execution as a success or failure. These labels are the
    training signal for the adaptive router's historical-success term."""
    existing = await container.service.get(query_id)
    if not principal.is_admin and existing.api_key_id != principal.api_key_id:
        raise QueryNotFoundError(str(query_id))
    try:
        query = await container.service.record_feedback(
            query_id, success=body.success, comment=body.comment
        )
    except ValueError as exc:
        raise APIError(409, "Query has no execution to label", str(exc)) from exc
    return QueryOut.from_row(query, container.settings.trace_query_url)


@router.post(
    "/route/compare",
    response_model=CompareOut,
    summary="Run every routing strategy on a query (no execution)",
)
async def compare_strategies(
    body: CompareIn, container: ContainerDep, principal: PrincipalDep
) -> CompareOut:
    """Shows which agent each strategy would choose and why. Nothing is executed or
    stored; the LLM router's call is billed as usual."""
    embedding_ms, decisions = await container.service.compare(body.query)
    out = {name: DecisionOut.from_domain(d) for name, d in decisions.items()}
    return CompareOut(
        query=body.query,
        embedding_latency_ms=embedding_ms,
        decisions=out,
        agreement=len({d.agent for d in decisions.values()}) == 1,
    )
