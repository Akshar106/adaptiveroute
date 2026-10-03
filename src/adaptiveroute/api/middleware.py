"""Request middleware: request id, structured access log, HTTP metrics."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger

log = get_logger("adaptiveroute.access")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SKIP_ACCESS_LOG = {"/healthz", "/readyz", "/metrics"}


class RequestContextMiddleware:
    """Pure ASGI middleware (no BaseHTTPMiddleware: keeps contextvars and streaming intact)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for key, value in scope.get("state", {}).get("rate_limit_headers", {}).items():
                    headers.setdefault(key, value)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = scope.get("route")
            # Label by route *template* (/v1/queries/{query_id}), never the raw path,
            # to keep Prometheus label cardinality bounded.
            template = getattr(route, "path", "unmatched")
            method = scope["method"]
            metrics.HTTP_REQUESTS.labels(method=method, route=template, status=str(status)).inc()
            metrics.HTTP_LATENCY.labels(method=method, route=template).observe(elapsed)
            if scope["path"] not in _SKIP_ACCESS_LOG:
                log.info(
                    "http_request",
                    method=method,
                    path=scope["path"],
                    route=template,
                    status=status,
                    duration_ms=round(elapsed * 1000, 2),
                )
