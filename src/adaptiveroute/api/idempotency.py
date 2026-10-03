"""Idempotency-Key support for POST /v1/queries.

A client that retries a POST (e.g. after a network timeout) must not trigger a second
paid LLM execution. Protocol, per (API key, Idempotency-Key):

* first request  -> claim the key (INSERT ... ON CONFLICT DO NOTHING), run, store response
* same key, same body, finished   -> replay the stored response (`Idempotent-Replayed: true`)
* same key, same body, still running -> 409, retry later
* same key, different body        -> 422 (client bug: keys must not be reused)
* handler fails with 5xx/exception -> release the key so a retry can run

Records live in Postgres (durable, transactional with the rest of our data) and
expire after AR_IDEMPOTENCY_TTL_HOURS; a Celery beat task purges expired rows.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptiveroute.api.errors import APIError
from adaptiveroute.api.security import Principal
from adaptiveroute.db.repositories import IdempotencyRepository

_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

Handler = Callable[[], Awaitable[tuple[int, dict[str, Any]]]]


def request_fingerprint(method: str, path: str, body: dict[str, Any]) -> str:
    canonical = json.dumps({"m": method, "p": path, "b": body}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run_idempotent(
    *,
    sessions: async_sessionmaker[AsyncSession],
    principal: Principal,
    key: str | None,
    fingerprint: str,
    ttl: timedelta,
    handler: Handler,
) -> JSONResponse:
    if key is None:
        status, payload = await handler()
        return JSONResponse(payload, status_code=status)
    if not _KEY_RE.match(key):
        raise APIError(400, "Invalid Idempotency-Key", "use 1-128 chars of [A-Za-z0-9_.:-]")

    async with sessions() as session:
        repo = IdempotencyRepository(session)
        existing = await repo.try_begin(principal.api_key_id, key, fingerprint, ttl)
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise APIError(
                    422,
                    "Idempotency-Key reused with a different request",
                    "each distinct request needs its own Idempotency-Key",
                )
            if existing.status != "completed" or existing.response_body is None:
                raise APIError(
                    409,
                    "Request with this Idempotency-Key is still in progress",
                    headers={"Retry-After": "2"},
                )
            return JSONResponse(
                existing.response_body,
                status_code=existing.response_status or 200,
                headers={"Idempotent-Replayed": "true"},
            )

        try:
            status, payload = await handler()
        except Exception:
            await repo.release(principal.api_key_id, key)
            raise
        if status >= 500:
            await repo.release(principal.api_key_id, key)
        else:
            await repo.complete(principal.api_key_id, key, status, payload)
        return JSONResponse(payload, status_code=status)
