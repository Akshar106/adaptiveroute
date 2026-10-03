"""The committed evaluation dataset must stay valid (runs the same checks as the CLI)."""

from collections import Counter

import pytest

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.config import PROJECT_ROOT
from adaptiveroute.evaluation.dataset import Dataset
from adaptiveroute.evaluation.validate import find_leakage, validate_items

DATASET = PROJECT_ROOT / "data" / "eval" / "dataset.jsonl"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return Dataset.load(DATASET)


def test_dataset_is_balanced(dataset: Dataset) -> None:
    assert Counter(i.domain for i in dataset.items) == dict.fromkeys(
        ["code", "math", "sql", "writer", "knowledge"], 30
    )
    assert sum("ambiguous" in i.tags for i in dataset.items) >= 20


def test_every_reference_passes_and_every_negative_control_fails(dataset: Dataset) -> None:
    assert validate_items(dataset) == []


@pytest.mark.slow
async def test_no_leakage_between_eval_queries_and_agent_examples(dataset: Dataset) -> None:
    registry = AgentRegistry.from_yaml(PROJECT_ROOT / "config" / "agents.yaml")
    assert await find_leakage(dataset, registry) == []
