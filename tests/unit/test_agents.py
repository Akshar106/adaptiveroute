import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from adaptiveroute.agents import AgentExecutor, AgentRegistry, UnknownAgentError
from adaptiveroute.config import PROJECT_ROOT
from adaptiveroute.domain import ExecutionStatus
from adaptiveroute.llm import ChatRequest, LLMBadRequest, LLMTimeout
from adaptiveroute.state import LocalLoadTracker
from tests.fakes import FakeLLM, reply

AGENTS_YAML = PROJECT_ROOT / "config" / "agents.yaml"


@pytest.fixture(scope="module")
def registry() -> AgentRegistry:
    return AgentRegistry.from_yaml(AGENTS_YAML)


def test_registry_loads_five_agents(registry: AgentRegistry) -> None:
    assert registry.names == ("code", "math", "sql", "writer", "knowledge")
    assert len(registry) == 5
    assert registry.get("sql").model == "openai/gpt-oss-20b"
    with pytest.raises(UnknownAgentError):
        registry.get("nope")


def test_every_agent_has_examples_and_price(registry: AgentRegistry) -> None:
    for agent in registry:
        assert len(agent.examples) >= 5, agent.name
        registry.prices.cost(agent.model, 1, 1)  # raises if unpriced


def test_sql_prompt_schema_matches_fixture(registry: AgentRegistry) -> None:
    """The SQL agent's embedded schema must not drift from the evaluation database."""
    fixture = (PROJECT_ROOT / "data" / "eval" / "retail.sql").read_text()

    def normalise(sql: str) -> set[str]:
        stmts = re.findall(r"CREATE TABLE.*?\);", sql, flags=re.S)
        return {re.sub(r"\s+", " ", s).replace("( ", "(").replace(" )", ")") for s in stmts}

    assert normalise(registry.get("sql").system_prompt) == normalise(fixture)


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "agents.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _minimal_agent(name: str, model: str = "m") -> dict[str, object]:
    return {
        "name": name,
        "display_name": name.title(),
        "description": "a sufficiently long description",
        "model": model,
        "system_prompt": "a sufficiently long system prompt",
    }


def test_rejects_duplicate_agent_names(tmp_path: Path) -> None:
    data = {
        "pricing": {
            "as_of": "x",
            "source": "y",
            "models": {"m": {"input_per_mtok": 1, "output_per_mtok": 1}},
        },
        "agents": [_minimal_agent("alpha"), _minimal_agent("alpha")],
    }
    with pytest.raises(ValidationError, match="duplicate"):
        AgentRegistry.from_yaml(_write(tmp_path, data))


def test_rejects_unpriced_model(tmp_path: Path) -> None:
    data = {
        "pricing": {"as_of": "x", "source": "y", "models": {}},
        "agents": [_minimal_agent("alpha"), _minimal_agent("beta")],
    }
    with pytest.raises(ValidationError, match="without a price"):
        AgentRegistry.from_yaml(_write(tmp_path, data))


def test_rejects_unknown_fields(tmp_path: Path) -> None:
    agent = _minimal_agent("alpha") | {"temprature": 0.5}  # typo must not be silently ignored
    data = {
        "pricing": {
            "as_of": "x",
            "source": "y",
            "models": {"m": {"input_per_mtok": 1, "output_per_mtok": 1}},
        },
        "agents": [agent, _minimal_agent("beta")],
    }
    with pytest.raises(ValidationError):
        AgentRegistry.from_yaml(_write(tmp_path, data))


# --- executor ------------------------------------------------------------------


async def test_executor_success_computes_cost_and_tracks_load(registry: AgentRegistry) -> None:
    load = LocalLoadTracker()
    seen_inflight: list[int] = []

    def responder(req: ChatRequest) -> object:
        return reply("print('hi')", input_tokens=1000, output_tokens=2000)

    llm = FakeLLM(responder)  # type: ignore[arg-type]
    executor = AgentExecutor(llm, registry.prices, load)

    async def spy_chat(req: ChatRequest):  # type: ignore[no-untyped-def]
        seen_inflight.append((await load.inflight(["code"]))["code"])
        return await FakeLLM.chat(llm, req)

    llm.chat = spy_chat  # type: ignore[method-assign]
    result = await executor.run(registry.get("code"), "write hello world")

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output == "print('hi')"
    # gpt-oss-120b: $0.15 / $0.60 per million tokens
    assert result.cost_usd == pytest.approx((1000 * 0.15 + 2000 * 0.60) / 1e6)
    assert seen_inflight == [1]
    assert (await load.inflight(["code"]))["code"] == 0

    sent = llm.requests[0]
    assert sent.model == "openai/gpt-oss-120b"
    assert sent.reasoning_effort == "medium"
    assert sent.messages[0]["role"] == "system"
    assert sent.messages[1] == {"role": "user", "content": "write hello world"}


async def test_executor_maps_timeout(registry: AgentRegistry) -> None:
    llm = FakeLLM(lambda _r: LLMTimeout("too slow", attempts=3))
    result = await AgentExecutor(llm, registry.prices, LocalLoadTracker()).run(
        registry.get("math"), "1+1"
    )
    assert result.status is ExecutionStatus.TIMEOUT
    assert result.attempts == 3
    assert result.output is None
    assert result.cost_usd == 0


async def test_executor_maps_provider_error(registry: AgentRegistry) -> None:
    llm = FakeLLM(lambda _r: LLMBadRequest("model decommissioned", status=400))
    result = await AgentExecutor(llm, registry.prices, LocalLoadTracker()).run(
        registry.get("writer"), "hi"
    )
    assert result.status is ExecutionStatus.ERROR
    assert result.error is not None and "decommissioned" in result.error


async def test_executor_empty_completion_is_error_but_billed(registry: AgentRegistry) -> None:
    llm = FakeLLM(lambda _r: reply("   ", input_tokens=10, output_tokens=4096))
    result = await AgentExecutor(llm, registry.prices, LocalLoadTracker()).run(
        registry.get("math"), "hard problem"
    )
    assert result.status is ExecutionStatus.ERROR
    assert result.cost_usd > 0
    assert result.output_tokens == 4096
