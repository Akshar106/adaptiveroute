import json
import random

import httpx
import pytest
import respx

from adaptiveroute.llm import (
    ChatRequest,
    LLMBadRequest,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    ModelPrice,
    OpenAICompatClient,
    PriceTable,
)

BASE = "https://llm.test/v1"


def ok_body(content: str = "hello") -> dict[str, object]:
    return {
        "model": "openai/gpt-oss-20b",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_time": 0.2},
        "x_groq": {"id": "req_123"},
    }


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def client(sleep: FakeSleep) -> OpenAICompatClient:
    return OpenAICompatClient(
        BASE, "test-key", max_attempts=3, backoff_base_s=0.5, sleep=sleep, rng=random.Random(0)
    )


def req(**kw: object) -> ChatRequest:
    defaults: dict[str, object] = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": "hi"}],
    }
    defaults.update(kw)
    return ChatRequest(**defaults)  # type: ignore[arg-type]


@respx.mock
async def test_success_parses_content_usage_and_metadata(client: OpenAICompatClient) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=ok_body())
    )
    resp = await client.chat(req(reasoning_effort="low", max_tokens=99))

    assert resp.content == "hello"
    assert (resp.input_tokens, resp.output_tokens) == (12, 34)
    assert resp.attempts == 1
    assert resp.provider_metadata == {"total_time": 0.2, "request_id": "req_123"}

    sent = json.loads(route.calls.last.request.content)
    assert sent["reasoning_effort"] == "low"
    assert sent["include_reasoning"] is False
    assert sent["max_completion_tokens"] == 99
    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


@respx.mock
async def test_payload_omits_unset_optional_fields(client: OpenAICompatClient) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=ok_body())
    )
    await client.chat(req())
    sent = json.loads(route.calls.last.request.content)
    assert "reasoning_effort" not in sent
    assert "response_format" not in sent
    assert "seed" not in sent


@respx.mock
async def test_429_honours_retry_after(client: OpenAICompatClient, sleep: FakeSleep) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(
                429, headers={"retry-after": "2"}, json={"error": {"message": "slow down"}}
            ),
            httpx.Response(200, json=ok_body()),
        ]
    )
    resp = await client.chat(req())
    assert resp.attempts == 2
    assert len(sleep.calls) == 1
    assert 2.0 <= sleep.calls[0] <= 2.25  # retry-after + small jitter


@respx.mock
async def test_5xx_retries_with_bounded_backoff(
    client: OpenAICompatClient, sleep: FakeSleep
) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json=ok_body())]
    )
    resp = await client.chat(req())
    assert resp.attempts == 3
    # full jitter: attempt n sleeps in [0, base * 2^(n-1)]
    assert 0 <= sleep.calls[0] <= 0.5
    assert 0 <= sleep.calls[1] <= 1.0


@respx.mock
async def test_gives_up_after_max_attempts(client: OpenAICompatClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(500))
    with pytest.raises(LLMUnavailable) as exc_info:
        await client.chat(req())
    assert exc_info.value.attempts == 3


@respx.mock
async def test_4xx_fails_fast(client: OpenAICompatClient, sleep: FakeSleep) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "model not found"}})
    )
    with pytest.raises(LLMBadRequest, match="model not found"):
        await client.chat(req())
    assert sleep.calls == []


@respx.mock
async def test_network_timeout_is_retried(client: OpenAICompatClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=ok_body())]
    )
    resp = await client.chat(req())
    assert resp.attempts == 2


@respx.mock
async def test_timeouts_exhausted_raise_llm_timeout(client: OpenAICompatClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectTimeout("down"))
    with pytest.raises(LLMTimeout):
        await client.chat(req())


@respx.mock
async def test_does_not_sleep_past_deadline(client: OpenAICompatClient, sleep: FakeSleep) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"})
    )
    with pytest.raises(LLMRateLimited) as exc_info:
        await client.chat(req(timeout_s=5))
    assert sleep.calls == []
    assert exc_info.value.retry_after_s == 30


@respx.mock
async def test_malformed_body_is_treated_as_provider_error(client: OpenAICompatClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(200, json={"oops": 1}))
    with pytest.raises(LLMUnavailable, match="malformed"):
        await client.chat(req())


def test_price_table_cost() -> None:
    prices = PriceTable({"m": ModelPrice(0.10, 0.50)}, as_of="2026-01-01", source="test")
    assert prices.cost("m", 1_000_000, 0) == pytest.approx(0.10)
    assert prices.cost("m", 2000, 1000) == pytest.approx(0.0002 + 0.0005)
    with pytest.raises(KeyError, match="no price"):
        prices.cost("unknown", 1, 1)
