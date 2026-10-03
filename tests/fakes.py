"""Hand-written test doubles for the ports (preferred over mocks: explicit and typed)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from adaptiveroute.domain import AgentAggregate, HistoryRecord
from adaptiveroute.llm import ChatRequest, ChatResponse, LLMError
from adaptiveroute.ports import Vector

Responder = Callable[[ChatRequest], ChatResponse | LLMError]


def reply(content: str, input_tokens: int = 100, output_tokens: int = 50) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="fake",
        finish_reason="stop",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=1.0,
        attempts=1,
    )


class FakeLLM:
    """Returns scripted responses; records every request it receives."""

    def __init__(self, responder: Responder | None = None) -> None:
        self.requests: list[ChatRequest] = []
        self._responder = responder or (lambda _req: reply("ok"))

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        result = self._responder(request)
        if isinstance(result, LLMError):
            raise result
        return result


class KeywordEmbedder:
    """Deterministic toy embedder: one dimension per keyword (plus a bias dimension)."""

    def __init__(self, keywords: Sequence[str]) -> None:
        self._keywords = [k.lower() for k in keywords]

    @property
    def model_name(self) -> str:
        return "keyword-test"

    @property
    def dim(self) -> int:
        return len(self._keywords) + 1

    async def embed(self, texts: Sequence[str]) -> Vector:
        rows = []
        for text in texts:
            low = text.lower()
            vec = [float(low.count(k)) for k in self._keywords] + [0.1]
            rows.append(vec)
        arr = np.asarray(rows, dtype=np.float32)
        return arr / np.linalg.norm(arr, axis=1, keepdims=True)


class StaticHistory:
    def __init__(
        self,
        neighbors: dict[str, list[HistoryRecord]] | None = None,
        aggregates: dict[str, AgentAggregate] | None = None,
    ) -> None:
        self._neighbors = neighbors or {}
        self._aggregates = aggregates or {}

    async def neighbors(
        self, embedding: Vector, agents: Sequence[str], k: int
    ) -> dict[str, list[HistoryRecord]]:
        return {a: self._neighbors.get(a, [])[:k] for a in agents}

    async def aggregates(self) -> dict[str, AgentAggregate]:
        return dict(self._aggregates)
