"""Core domain types.

These are plain frozen dataclasses with no I/O so they can be shared by the API,
the Celery worker and the offline benchmark replay without dragging in a database
or HTTP client.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ExecutionStatus(StrEnum):
    """Did the agent *run*? (Whether it solved the task is a separate outcome.)"""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"


class OutcomeSource(StrEnum):
    """Where a task-success label came from."""

    CHECKER = "checker"  # deterministic checker from the evaluation dataset
    USER = "user"  # explicit user feedback via the API
    BENCHMARK = "benchmark"  # imported from a benchmark outcome matrix


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Static definition of a specialised agent (loaded from config/agents.yaml)."""

    name: str
    display_name: str
    description: str
    model: str
    system_prompt: str
    examples: tuple[str, ...] = ()
    reasoning_effort: str | None = None
    temperature: float = 0.0
    max_output_tokens: int = 1024
    timeout_s: float = 45.0
    max_concurrency: int = 8
    prior_latency_ms: float = 3000.0
    prior_cost_usd: float = 0.0005

    @property
    def fingerprint(self) -> str:
        """Short hash of everything that changes the agent's behaviour.

        Used as part of response-cache keys and benchmark cache keys so a prompt or
        model change never silently reuses stale results.
        """
        payload = json.dumps(
            [
                self.model,
                self.system_prompt,
                self.reasoning_effort,
                self.temperature,
                self.max_output_tokens,
            ],
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    agent: str
    model: str
    status: ExecutionStatus
    output: str | None
    error: str | None
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    attempts: int = 1
    cache_hit: bool = False

    @property
    def ok(self) -> bool:
        return self.status is ExecutionStatus.SUCCESS


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """One agent's score inside a routing decision.

    ``components`` holds the (named) inputs that produced ``score`` so the
    decision can be explained in the UI and audited later.
    """

    agent: str
    score: float
    components: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    strategy: str
    agent: str
    candidates: tuple[CandidateScore, ...]
    reasoning: str
    latency_ms: float
    cost_usd: float = 0.0
    # If the requested router failed, the name of the fallback router that decided.
    fallback: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def ranked_agents(self) -> list[str]:
        """Agents ordered best-first (selected agent first)."""
        ordered = sorted(self.candidates, key=lambda c: c.score, reverse=True)
        names = [c.agent for c in ordered if c.agent != self.agent]
        return [self.agent, *names]


@dataclass(frozen=True, slots=True)
class HistoryRecord:
    """A past (query, agent) execution with a known task outcome."""

    agent: str
    similarity: float  # cosine similarity between the past query and the current one
    success: bool
    latency_ms: float
    cost_usd: float


@dataclass(frozen=True, slots=True)
class AgentAggregate:
    """Global (non-contextual) performance of one agent."""

    agent: str
    n: int
    successes: int
    p50_latency_ms: float | None
    mean_cost_usd: float | None

    @property
    def success_rate(self) -> float | None:
        return self.successes / self.n if self.n else None
