"""Interfaces (ports) between the routing core and infrastructure.

Each port has a production implementation (Postgres, Redis, Groq, fastembed) and a
lightweight in-memory one used by unit tests and by the offline benchmark replay.
Routers only depend on these protocols, which is what makes the benchmark exercise
exactly the same routing code that serves API traffic.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol

import numpy as np
import numpy.typing as npt

from adaptiveroute.domain import AgentAggregate, HistoryRecord

Vector = npt.NDArray[np.float32]


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> Vector:
        """Return an (n, dim) float32 matrix of L2-normalised embeddings."""
        ...


class PerformanceHistory(Protocol):
    async def neighbors(
        self, embedding: Vector, agents: Sequence[str], k: int
    ) -> dict[str, list[HistoryRecord]]:
        """For each agent, the ``k`` most similar past queries it handled (with outcomes)."""
        ...

    async def aggregates(self) -> dict[str, AgentAggregate]:
        """Global per-agent performance (success rate, latency, cost)."""
        ...


class LoadTracker(Protocol):
    async def inflight(self, agents: Sequence[str]) -> dict[str, int]:
        """Number of executions currently running per agent."""
        ...

    def track(self, agent: str) -> AbstractAsyncContextManager[None]:
        """Context manager that counts one in-flight execution for ``agent``."""
        ...


class SequenceCounter(Protocol):
    async def next(self, key: str) -> int:
        """Monotonic counter shared by all API replicas (used by round-robin)."""
        ...
