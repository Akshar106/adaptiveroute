"""Run async application code from synchronous Celery tasks.

Each worker *process* owns one event loop and one Container, created lazily on the
first task (i.e. after fork - async engines and Redis pools must never be shared
across a fork). Reusing the loop keeps DB/Redis connection pools warm between tasks
instead of paying ``asyncio.run`` + pool setup per task.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

from adaptiveroute.config import get_settings
from adaptiveroute.container import Container
from adaptiveroute.observability.tracing import configure_tracing

_state: dict[str, object] = {}


def _loop() -> asyncio.AbstractEventLoop:
    if _state.get("pid") != os.getpid():  # first use in this (possibly forked) process
        _state.clear()
        _state["pid"] = os.getpid()
        _state["loop"] = asyncio.new_event_loop()
        settings = get_settings()
        configure_tracing(settings, service_name=f"{settings.service_name}-worker")
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        if settings.otel_enabled:
            CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
    loop = _state["loop"]
    assert isinstance(loop, asyncio.AbstractEventLoop)
    return loop


async def _container() -> Container:
    container = _state.get("container")
    if container is None:
        from adaptiveroute.worker.tasks import enqueue_execution

        container = await Container.create(get_settings(), enqueue=enqueue_execution)
        _state["container"] = container
    assert isinstance(container, Container)
    return container


def run[T](fn: Callable[[Container], Awaitable[T]]) -> T:
    loop = _loop()

    async def main() -> T:
        return await fn(await _container())

    return loop.run_until_complete(main())
