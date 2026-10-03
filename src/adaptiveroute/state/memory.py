"""In-process implementations of the state ports (tests, benchmark replay, single node)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager


class LocalLoadTracker:
    def __init__(self) -> None:
        self._counts: defaultdict[str, int] = defaultdict(int)

    async def inflight(self, agents: Sequence[str]) -> dict[str, int]:
        return {a: self._counts[a] for a in agents}

    @asynccontextmanager
    async def track(self, agent: str) -> AsyncIterator[None]:
        # Single event loop and no await between read and write => no race.
        self._counts[agent] += 1
        try:
            yield
        finally:
            self._counts[agent] -= 1


class LocalCounter:
    def __init__(self) -> None:
        self._values: defaultdict[str, int] = defaultdict(int)

    async def next(self, key: str) -> int:
        value = self._values[key]
        self._values[key] += 1
        return value
