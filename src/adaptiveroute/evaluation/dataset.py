"""Evaluation dataset schema and loader.

One JSON object per line. Every item has a deterministic ``check`` (see checkers.py)
and a ``reference`` answer that must pass its own check. The validator
(``python -m adaptiveroute.evaluation.validate``, also run by
tests/unit/test_dataset.py) enforces that, which is how we know the checkers and the
labels are consistent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Domain = Literal["code", "math", "sql", "writer", "knowledge"]


class _Check(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumericCheck(_Check):
    """Passes if |got - answer| <= max(abs_tol, rel_tol * |answer|) (math.isclose).

    rel_tol is tiny by default so an item's abs_tol (e.g. 0.005 for "round to 2
    decimal places") is the effective limit even for large answers."""

    type: Literal["numeric"]
    answer: float
    abs_tol: float = 1e-6
    rel_tol: float = 1e-9


class PythonCheck(_Check):
    type: Literal["python"]
    entrypoint: str
    tests: list[str] = Field(min_length=3)
    timeout_s: float = 10.0


class SqlCheck(_Check):
    type: Literal["sql"]
    reference_sql: str
    order_matters: bool = False


class ConstraintsCheck(_Check):
    type: Literal["constraints"]
    min_words: int | None = None
    max_words: int | None = None
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    bullet_count: int | None = None
    max_sentences: int | None = None
    ends_with: str | None = None


class ContainsCheck(_Check):
    type: Literal["contains"]
    any_of: list[str] = Field(min_length=1)  # aliases; at least one must appear
    none_of: list[str] = Field(default_factory=list)


Check = Annotated[
    NumericCheck | PythonCheck | SqlCheck | ConstraintsCheck | ContainsCheck,
    Field(discriminator="type"),
]

_CHECK_FOR_DOMAIN = {
    "math": "numeric",
    "code": "python",
    "sql": "sql",
    "writer": "constraints",
    "knowledge": "contains",
}


class EvalItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^(code|math|sql|writer|knowledge)-\d{3}$")
    query: str = Field(min_length=10)
    domain: Domain
    acceptable_agents: list[Domain] = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"]
    tags: list[str] = Field(default_factory=list)
    check: Check
    reference: str = Field(min_length=1)  # a correct answer; must pass `check`

    @model_validator(mode="after")
    def _consistent(self) -> EvalItem:
        if not self.id.startswith(self.domain + "-"):
            raise ValueError(f"{self.id}: id prefix must match domain {self.domain}")
        if self.domain not in self.acceptable_agents:
            raise ValueError(f"{self.id}: domain must be in acceptable_agents")
        if self.check.type != _CHECK_FOR_DOMAIN[self.domain]:
            raise ValueError(f"{self.id}: {self.domain} items use {_CHECK_FOR_DOMAIN[self.domain]}")
        return self


class Dataset:
    def __init__(self, items: list[EvalItem], sha256: str, path: Path | None = None) -> None:
        ids = [i.id for i in items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate item ids in dataset")
        self.items = items
        self.sha256 = sha256
        self.path = path

    @classmethod
    def load(cls, path: Path) -> Dataset:
        raw = path.read_bytes()
        items = [
            EvalItem.model_validate(json.loads(line))
            for line in raw.decode().splitlines()
            if line.strip()
        ]
        return cls(items, hashlib.sha256(raw).hexdigest(), path)

    def __len__(self) -> int:
        return len(self.items)

    def by_domain(self) -> dict[str, list[EvalItem]]:
        out: dict[str, list[EvalItem]] = {}
        for item in self.items:
            out.setdefault(item.domain, []).append(item)
        return out
