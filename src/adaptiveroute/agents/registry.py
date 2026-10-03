"""Load and validate the agent registry from YAML."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from adaptiveroute.domain import AgentSpec
from adaptiveroute.llm.pricing import ModelPrice, PriceTable


class UnknownAgentError(KeyError):
    pass


class _Price(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)


class _Pricing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: str
    source: str
    models: dict[str, _Price]


class _Agent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    display_name: str
    description: str = Field(min_length=20)
    model: str
    system_prompt: str = Field(min_length=20)
    examples: list[str] = Field(default_factory=list)
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_output_tokens: int = Field(default=1024, ge=16, le=32768)
    timeout_s: float = Field(default=45, gt=0, le=300)
    max_concurrency: int = Field(default=8, ge=1)
    prior_latency_ms: float = Field(default=3000, gt=0)
    prior_cost_usd: float = Field(default=0.0005, ge=0)


class _RegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pricing: _Pricing
    agents: list[_Agent] = Field(min_length=2)

    @model_validator(mode="after")
    def _check(self) -> _RegistryFile:
        names = [a.name for a in self.agents]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate agent names: {names}")
        unpriced = {a.model for a in self.agents} - set(self.pricing.models)
        if unpriced:
            raise ValueError(f"models without a price: {sorted(unpriced)}")
        return self


@dataclass(frozen=True, slots=True)
class AgentRegistry:
    agents: tuple[AgentSpec, ...]
    prices: PriceTable

    @classmethod
    def from_yaml(cls, path: Path) -> AgentRegistry:
        raw = yaml.safe_load(path.read_text())
        parsed = _RegistryFile.model_validate(raw)
        agents = tuple(
            AgentSpec(
                name=a.name,
                display_name=a.display_name,
                description=a.description.strip(),
                model=a.model,
                system_prompt=a.system_prompt.strip(),
                examples=tuple(a.examples),
                reasoning_effort=a.reasoning_effort,
                temperature=a.temperature,
                max_output_tokens=a.max_output_tokens,
                timeout_s=a.timeout_s,
                max_concurrency=a.max_concurrency,
                prior_latency_ms=a.prior_latency_ms,
                prior_cost_usd=a.prior_cost_usd,
            )
            for a in parsed.agents
        )
        prices = PriceTable(
            models={
                m: ModelPrice(p.input_per_mtok, p.output_per_mtok)
                for m, p in parsed.pricing.models.items()
            },
            as_of=parsed.pricing.as_of,
            source=parsed.pricing.source,
        )
        return cls(agents=agents, prices=prices)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(a.name for a in self.agents)

    def get(self, name: str) -> AgentSpec:
        for agent in self.agents:
            if agent.name == name:
                return agent
        raise UnknownAgentError(name)

    def __iter__(self) -> Iterator[AgentSpec]:
        return iter(self.agents)

    def __len__(self) -> int:
        return len(self.agents)
