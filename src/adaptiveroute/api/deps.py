"""FastAPI dependencies: container access, authentication, rate limiting, roles."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends, Header, Request

from adaptiveroute.api.errors import APIError
from adaptiveroute.api.rate_limit import RateLimiter
from adaptiveroute.api.security import Authenticator, Principal
from adaptiveroute.container import Container


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def _authenticator(request: Request) -> Authenticator:
    auth: Authenticator = request.app.state.authenticator
    return auth


def _rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


async def get_principal(
    authenticator: Annotated[Authenticator, Depends(_authenticator)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    """Accept ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``."""
    token = x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise APIError(
            401, "Missing API key", headers={"WWW-Authenticate": 'Bearer realm="adaptiveroute"'}
        )
    principal = await authenticator.authenticate(token)
    if principal is None:
        raise APIError(
            401, "Invalid API key", headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
        )
    structlog.contextvars.bind_contextvars(api_key_id=str(principal.api_key_id))
    return principal


async def rate_limited(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    limiter: Annotated[RateLimiter, Depends(_rate_limiter)],
) -> Principal:
    result = await limiter.hit(str(principal.api_key_id), principal.rate_limit_per_minute)
    headers = {
        "X-RateLimit-Limit": str(result.limit_per_minute),
        "X-RateLimit-Remaining": str(result.remaining),
    }
    if not result.allowed:
        retry = max(1, round(result.retry_after_s + 0.5))
        raise APIError(
            429,
            "Rate limit exceeded",
            f"limit is {result.limit_per_minute} requests/minute; retry in {retry}s",
            headers={**headers, "Retry-After": str(retry)},
        )
    # Attached to whatever response is eventually sent (see RequestContextMiddleware).
    request.state.rate_limit_headers = headers
    return principal


async def require_admin(principal: Annotated[Principal, Depends(rate_limited)]) -> Principal:
    if not principal.is_admin:
        raise APIError(403, "Admin role required")
    return principal


ContainerDep = Annotated[Container, Depends(get_container)]
PrincipalDep = Annotated[Principal, Depends(rate_limited)]
AdminDep = Annotated[Principal, Depends(require_admin)]
