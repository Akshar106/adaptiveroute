"""Minimal async client for OpenAI-compatible chat-completion APIs (Groq by default).

Why not the vendor SDK? The retry/timeout policy is a core part of this system's
behaviour (it affects latency, cost and error-rate metrics), so it lives here in ~150
explicit lines instead of being hidden inside an SDK:

* one *deadline* per call covers every attempt (no "4 retries x 60s" surprises),
* 429s honour the provider's ``Retry-After`` header,
* 5xx / network errors / timeouts back off exponentially with full jitter,
* other 4xx errors fail fast because retrying cannot fix them,
* we never sleep past the deadline.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from adaptiveroute.observability.logs import get_logger

log = get_logger(__name__)


class LLMError(Exception):
    retryable: bool = False

    def __init__(self, message: str, *, status: int | None = None, attempts: int = 1) -> None:
        super().__init__(message)
        self.status = status
        self.attempts = attempts


class LLMRateLimited(LLMError):
    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None, attempts: int = 1) -> None:
        super().__init__(message, status=429, attempts=attempts)
        self.retry_after_s = retry_after_s


class LLMUnavailable(LLMError):
    """5xx or network failure."""

    retryable = True


class LLMTimeout(LLMError):
    retryable = True


class LLMBadRequest(LLMError):
    """4xx (other than 408/429): retrying will not help."""


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: Sequence[Mapping[str, Any]]
    temperature: float = 0.0
    max_tokens: int = 1024
    reasoning_effort: str | None = None
    response_format: Mapping[str, Any] | None = None
    seed: int | None = None
    timeout_s: float = 60.0

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(m) for m in self.messages],
            "temperature": self.temperature,
            "max_completion_tokens": self.max_tokens,
        }
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
            # Reasoning tokens are still billed, but we don't need them in the payload.
            body["include_reasoning"] = False
        if self.response_format is not None:
            body["response_format"] = dict(self.response_format)
        if self.seed is not None:
            body["seed"] = self.seed
        return body


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    model: str
    finish_reason: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float  # wall-clock across all attempts, including backoff
    attempts: int
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    async def chat(self, request: ChatRequest) -> ChatResponse: ...


SleepFn = Callable[[float], Awaitable[None]]


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        max_attempts: int = 4,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 20.0,
        connect_timeout_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._connect_timeout_s = connect_timeout_s
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        start = time.perf_counter()
        deadline = time.monotonic() + request.timeout_s
        payload = request.payload()
        attempt = 0
        while True:
            attempt += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LLMTimeout(f"deadline of {request.timeout_s}s exceeded", attempts=attempt - 1)
            try:
                resp = await self._http.post(
                    "/chat/completions",
                    json=payload,
                    timeout=httpx.Timeout(
                        remaining, connect=min(self._connect_timeout_s, remaining)
                    ),
                )
                if resp.status_code == 200:
                    return self._parse(resp, request, attempt, start)
                raise self._classify(resp, attempt)
            except httpx.TimeoutException as exc:
                err: LLMError = LLMTimeout(f"request timed out: {exc!r}", attempts=attempt)
            except httpx.TransportError as exc:
                err = LLMUnavailable(f"transport error: {exc!r}", attempts=attempt)
            except LLMError as exc:
                err = exc

            if not err.retryable or attempt >= self._max_attempts:
                raise err
            delay = self._retry_delay(err, attempt)
            if delay >= deadline - time.monotonic():
                raise err  # waiting would blow the deadline anyway
            log.warning(
                "llm_retry",
                model=request.model,
                attempt=attempt,
                delay_s=round(delay, 3),
                error=type(err).__name__,
                status=err.status,
            )
            await self._sleep(delay)

    def _retry_delay(self, err: LLMError, attempt: int) -> float:
        if isinstance(err, LLMRateLimited) and err.retry_after_s is not None:
            return err.retry_after_s + self._rng.uniform(0, 0.25)
        cap = min(self._backoff_max_s, self._backoff_base_s * 2 ** (attempt - 1))
        return self._rng.uniform(0, cap)  # "full jitter"

    @staticmethod
    def _classify(resp: httpx.Response, attempt: int) -> LLMError:
        detail = _error_detail(resp)
        status = resp.status_code
        if status == 429:
            return LLMRateLimited(
                f"rate limited: {detail}",
                retry_after_s=_parse_retry_after(resp.headers.get("retry-after")),
                attempts=attempt,
            )
        if status == 408:
            return LLMTimeout(f"provider timeout: {detail}", status=status, attempts=attempt)
        if status >= 500:
            return LLMUnavailable(
                f"provider error {status}: {detail}", status=status, attempts=attempt
            )
        return LLMBadRequest(f"bad request {status}: {detail}", status=status, attempts=attempt)

    @staticmethod
    def _parse(
        resp: httpx.Response, request: ChatRequest, attempt: int, start: float
    ) -> ChatResponse:
        try:
            body = resp.json()
            choice = body["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMUnavailable(f"malformed provider response: {exc!r}", attempts=attempt) from exc
        usage = body.get("usage") or {}
        meta: dict[str, Any] = {}
        for key in ("queue_time", "prompt_time", "completion_time", "total_time"):
            if key in usage:
                meta[key] = usage[key]
        if "x_groq" in body and isinstance(body["x_groq"], dict):
            meta["request_id"] = body["x_groq"].get("id")
        return ChatResponse(
            content=message.get("content") or "",
            model=body.get("model", request.model),
            finish_reason=choice.get("finish_reason"),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=(time.perf_counter() - start) * 1000,
            attempts=attempt,
            provider_metadata=meta,
        )


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form; fall back to exponential backoff


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message", body["error"]))[:300]
    return str(body)[:300]
