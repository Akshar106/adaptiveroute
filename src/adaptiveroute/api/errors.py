"""Consistent RFC 9457 (problem+json) error responses.

Every error response has the same shape and includes the request id, so a user
report can be matched to logs and traces. Unexpected exceptions are logged with a
stack trace but the client only sees a generic 500 (no internals leak).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.routing import RouterError
from adaptiveroute.services.query_service import (
    ExecutionTimeoutError,
    QueryNotFoundError,
    UnknownStrategyError,
)

log = get_logger(__name__)


class APIError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        self.extra = extra or {}


def problem(
    request: Request,
    status: int,
    title: str,
    detail: str | None = None,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "instance": request.url.path,
        "request_id": structlog.contextvars.get_contextvars().get("request_id"),
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(
        body, status_code=status, headers=headers, media_type="application/problem+json"
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError) -> JSONResponse:
        return problem(request, exc.status, exc.title, exc.detail, exc.headers, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = dict(exc.headers) if exc.headers else None
        return problem(request, exc.status_code, str(exc.detail), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return problem(request, 422, "Request validation failed", errors=errors)

    @app.exception_handler(UnknownStrategyError)
    async def _strategy(request: Request, exc: UnknownStrategyError) -> JSONResponse:
        return problem(request, 400, "Unknown strategy", str(exc))

    @app.exception_handler(QueryNotFoundError)
    async def _not_found(request: Request, exc: QueryNotFoundError) -> JSONResponse:
        return problem(request, 404, "Query not found", f"no query with id {exc}")

    @app.exception_handler(ExecutionTimeoutError)
    async def _timeout(request: Request, exc: ExecutionTimeoutError) -> JSONResponse:
        metrics.ERRORS.labels(kind="sync_timeout").inc()
        return problem(
            request,
            504,
            "Execution timed out",
            str(exc),
            query_id=str(exc.query_id),
        )

    @app.exception_handler(RouterError)
    async def _router(request: Request, exc: RouterError) -> JSONResponse:
        metrics.ERRORS.labels(kind="router").inc()
        return problem(request, 502, "Routing failed", str(exc))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        metrics.ERRORS.labels(kind="unhandled").inc()
        log.exception("unhandled_error", path=request.url.path)
        return problem(request, 500, "Internal server error")
