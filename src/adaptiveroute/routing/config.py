"""Load config/routing.yaml into typed router configs."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from adaptiveroute.routing.llm_router import LLMRouterConfig
from adaptiveroute.routing.scoring import AdaptiveConfig, ScoringWeights


class _Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")
    similarity: float = Field(ge=0)
    success: float = Field(ge=0)
    latency: float = Field(ge=0)
    cost: float = Field(ge=0)
    load: float = Field(ge=0)
    exploration: float = Field(default=0.0, ge=0)


class _Adaptive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: _Weights
    similarity_temperature: float = Field(gt=0)
    k_neighbors: int = Field(ge=1, le=500)
    min_neighbor_similarity: float = Field(ge=0, lt=1)
    prior_strength: float = Field(gt=0)
    default_success_prior: float = Field(ge=0, le=1)
    latency_budget_ms: float = Field(gt=0)
    cost_budget_usd: float = Field(gt=0)


class _LLM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    max_output_tokens: int = Field(ge=16)
    timeout_s: float = Field(gt=0)
    fallback: Literal["embedding", "round_robin"] | None = "embedding"


class _Execution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    failover: bool = True


class _RoutingFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adaptive: _Adaptive
    llm: _LLM
    execution: _Execution = _Execution()


class RoutingConfig:
    def __init__(
        self,
        adaptive: AdaptiveConfig,
        llm: LLMRouterConfig,
        llm_fallback: str | None,
        failover: bool,
    ) -> None:
        self.adaptive = adaptive
        self.llm = llm
        self.llm_fallback = llm_fallback
        self.failover = failover

    @classmethod
    def from_yaml(cls, path: Path) -> RoutingConfig:
        parsed = _RoutingFile.model_validate(yaml.safe_load(path.read_text()))
        a = parsed.adaptive
        adaptive = AdaptiveConfig(
            weights=ScoringWeights(**a.weights.model_dump()),
            similarity_temperature=a.similarity_temperature,
            k_neighbors=a.k_neighbors,
            min_neighbor_similarity=a.min_neighbor_similarity,
            prior_strength=a.prior_strength,
            default_success_prior=a.default_success_prior,
            latency_budget_ms=a.latency_budget_ms,
            cost_budget_usd=a.cost_budget_usd,
        )
        llm = LLMRouterConfig(
            model=parsed.llm.model,
            reasoning_effort=parsed.llm.reasoning_effort,
            max_output_tokens=parsed.llm.max_output_tokens,
            timeout_s=parsed.llm.timeout_s,
        )
        return cls(adaptive, llm, parsed.llm.fallback, parsed.execution.failover)

    def as_dict(self) -> dict[str, Any]:
        """Resolved configuration, recorded in benchmark reports for provenance."""
        return {
            "adaptive": asdict(self.adaptive),
            "llm": asdict(self.llm),
            "llm_fallback": self.llm_fallback,
            "failover": self.failover,
        }
