"""Validate the evaluation dataset.

    uv run python -m adaptiveroute.evaluation.validate [--embeddings]

For every item:
  1. the schema parses (pydantic, see dataset.py);
  2. the reference answer passes the item's check  (labels/checkers are consistent);
  3. a negative control fails the check            (the check is not vacuous).
With --embeddings it also checks for leakage: no evaluation query may be a
near-duplicate of an agent's routing example or of another evaluation query.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from adaptiveroute.agents import AgentRegistry
from adaptiveroute.config import get_settings
from adaptiveroute.evaluation.checkers import check_output
from adaptiveroute.evaluation.dataset import Dataset, EvalItem

NEAR_DUPLICATE = 0.92


def negative_control(item: EvalItem) -> str:
    check = item.check
    match check.type:
        case "numeric":
            return f"ANSWER: {check.answer * 1.5 + 7}"
        case "python":
            return f"```python\ndef {check.entrypoint}(*args, **kwargs):\n    return None\n```"
        case "sql":
            return "```sql\nSELECT 'not the answer' AS wrong\n```"
        case "constraints":
            return "Lorem ipsum."
        case "contains":
            return "I am not sure about that."
    raise AssertionError(check.type)


def validate_items(dataset: Dataset) -> list[str]:
    errors: list[str] = []
    for item in dataset.items:
        ref = check_output(item.check, item.reference)
        if not ref.passed:
            errors.append(f"{item.id}: reference fails its own check ({ref.detail})")
        neg = check_output(item.check, negative_control(item))
        if neg.passed:
            errors.append(f"{item.id}: negative control passes - check is vacuous")
    return errors


async def find_leakage(dataset: Dataset, registry: AgentRegistry) -> list[str]:
    from adaptiveroute.embeddings import FastEmbedEmbedder

    s = get_settings()
    embedder = FastEmbedEmbedder(s.embedding_model, s.embedding_dim, s.embedding_cache_dir)
    queries = [i.query for i in dataset.items]
    examples = [(a.name, e) for a in registry for e in a.examples]
    q = await embedder.embed(queries)
    ex = await embedder.embed([e for _, e in examples])
    problems: list[str] = []
    for i, j in zip(*((q @ ex.T) >= NEAR_DUPLICATE).nonzero(), strict=True):
        problems.append(f"{dataset.items[i].id} near-duplicates agent example {examples[j]}")
    sims = q @ q.T
    for i in range(len(queries)):
        for j in range(i + 1, len(queries)):
            if sims[i, j] >= NEAR_DUPLICATE:
                a, b = dataset.items[i].id, dataset.items[j].id
                problems.append(f"{a} near-duplicates {b} ({sims[i, j]:.3f})")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=get_settings().dataset_path)
    parser.add_argument("--embeddings", action="store_true", help="also run leakage checks")
    args = parser.parse_args(argv)

    dataset = Dataset.load(args.path)
    errors = validate_items(dataset)
    if args.embeddings:
        registry = AgentRegistry.from_yaml(get_settings().agents_config_path)
        errors += asyncio.run(find_leakage(dataset, registry))

    domains = Counter(i.domain for i in dataset.items)
    difficulty = Counter(i.difficulty for i in dataset.items)
    print(f"{len(dataset)} items  sha256={dataset.sha256[:16]}")
    print("  by domain:    ", dict(sorted(domains.items())))
    print("  by difficulty:", dict(sorted(difficulty.items())))
    print("  tagged ambiguous:", sum("ambiguous" in i.tags for i in dataset.items))
    for err in errors:
        print("ERROR", err, file=sys.stderr)
    print("OK" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
