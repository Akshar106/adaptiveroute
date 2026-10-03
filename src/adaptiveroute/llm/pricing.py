"""Token pricing used to *estimate* cost.

Estimates use the provider's published list prices (recorded with an ``as_of`` date
in ``config/agents.yaml``). They are estimates: free-tier usage is actually billed at
$0, and the provider may change prices at any time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


@dataclass(frozen=True, slots=True)
class PriceTable:
    models: Mapping[str, ModelPrice]
    as_of: str
    source: str

    def cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        try:
            price = self.models[model]
        except KeyError:
            raise KeyError(f"no price configured for model {model!r}") from None
        return (
            input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok
        ) / 1_000_000
