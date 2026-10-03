"""End-to-end API tests: real FastAPI app, real Postgres + Redis, scripted LLM."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from adaptiveroute.api.app import create_app
from adaptiveroute.api.security import create_api_key
from adaptiveroute.config import Settings
from adaptiveroute.container import Container
from adaptiveroute.db.repositories import StatsRepository
from adaptiveroute.embeddings import HashingEmbedder
from adaptiveroute.llm import ChatRequest, ChatResponse, LLMUnavailable
from tests.fakes import FakeLLM, reply
from tests.integration.conftest import REDIS_URL

PEPPER = "test-pepper"


def scripted(request: ChatRequest) -> ChatResponse | LLMUnavailable:
    """Router calls get a JSON choice; agent calls get an answer."""
    if request.response_format is not None:
        return reply(json.dumps({"agent": "math", "confidence": 0.8, "reason": "arithmetic"}))
    return reply("2 + 2 = 4\nANSWER: 4", input_tokens=200, output_tokens=40)


class Harness:
    def __init__(self, client: httpx.AsyncClient, app: FastAPI, llm: FakeLLM, enqueued: list[Any]):
        self.client = client
        self.app = app
        self.llm = llm
        self.enqueued = enqueued

    @property
    def container(self) -> Container:
        container: Container = self.app.state.container
        return container


def build_settings(database_url: str, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": database_url,
        "redis_url": REDIS_URL,
        "embedding_backend": "hashing",
        "otel_enabled": False,
        "log_json": False,
        "api_key_pepper": PEPPER,
        "trace_query_url": None,
        "rate_limit_burst": 50,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def make_harness(
    database_url: str, session_factory: async_sessionmaker[AsyncSession], redis: Any
) -> Callable[..., Any]:
    async def build(
        responder: Callable[[ChatRequest], Any] = scripted,
        with_llm: bool = True,
        **settings_overrides: Any,
    ) -> AsyncIterator[Harness]:
        settings = build_settings(database_url, **settings_overrides)
        llm = FakeLLM(responder)
        enqueued: list[Any] = []

        async def enqueue(query_id: uuid.UUID, use_cache: bool) -> None:
            enqueued.append((query_id, use_cache))

        async def factory(s: Settings) -> Container:
            return await Container.create(
                s,
                llm=llm if with_llm else None,
                embedder=HashingEmbedder(s.embedding_dim),
                enqueue=enqueue,
                db_pool=False,
            )

        app = create_app(settings, container_factory=factory)
        async with LifespanManager(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield Harness(client, app, llm, enqueued)

    return build


@pytest.fixture
async def h(make_harness: Callable[..., Any]) -> AsyncIterator[Harness]:
    async for harness in make_harness():
        yield harness


async def new_key(
    session_factory: async_sessionmaker[AsyncSession],
    role: str = "user",
    rate_limit: int | None = None,
) -> dict[str, str]:
    async with session_factory() as session:
        key, _ = await create_api_key(
            session, name=f"{role}-test", role=role, pepper=PEPPER, rate_limit_per_minute=rate_limit
        )
        await session.commit()
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
async def user(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    return await new_key(session_factory, "user")


@pytest.fixture
async def admin(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    return await new_key(session_factory, "admin")


# --- health ---------------------------------------------------------------------


async def test_health_and_readiness_need_no_auth(h: Harness) -> None:
    assert (await h.client.get("/healthz")).json() == {"status": "ok"}
    ready = (await h.client.get("/readyz")).json()
    assert ready["status"] == "ready"
    assert ready["checks"] == {"postgres": "ok", "redis": "ok", "embedder": "ok"}
    assert ready["strategies"] == ["round_robin", "embedding", "llm", "adaptive"]


async def test_metrics_endpoint_exposes_http_metrics(h: Harness) -> None:
    await h.client.get("/healthz")
    body = (await h.client.get("/metrics")).text
    assert 'ar_http_requests_total{method="GET",route="/healthz",status="200"}' in body


async def test_openapi_documents_routes(h: Harness) -> None:
    spec = (await h.client.get("/openapi.json")).json()
    assert "/v1/queries" in spec["paths"]
    assert "/v1/route/compare" in spec["paths"]
    post = spec["paths"]["/v1/queries"]["post"]["responses"]
    assert {"401", "422", "429"} <= set(post)
    assert post["429"]["content"]["application/problem+json"]["schema"]["$ref"].endswith(
        "ProblemOut"
    )


# --- auth -------------------------------------------------------------------------


async def test_missing_and_invalid_keys_are_rejected(h: Harness) -> None:
    resp = await h.client.get("/v1/agents")
    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["title"] == "Missing API key"
    assert resp.json()["request_id"] == resp.headers["x-request-id"]

    bad = {"Authorization": "Bearer ar_deadbeef_" + "x" * 43}
    assert (await h.client.get("/v1/agents", headers=bad)).status_code == 401


async def test_x_api_key_header_also_works(h: Harness, user: dict[str, str]) -> None:
    key = user["Authorization"].removeprefix("Bearer ")
    assert (await h.client.get("/v1/agents", headers={"X-API-Key": key})).status_code == 200


# --- queries ----------------------------------------------------------------------


@pytest.mark.parametrize("strategy", ["round_robin", "embedding", "llm", "adaptive"])
async def test_sync_query_with_each_strategy(
    h: Harness, user: dict[str, str], strategy: str
) -> None:
    resp = await h.client.post(
        "/v1/queries", headers=user, json={"query": "What is 2 + 2?", "strategy": strategy}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["decision"]["strategy"] == strategy
    assert body["answer"].endswith("ANSWER: 4")
    assert body["executions"][0]["agent"] == body["decision"]["agent"]
    assert body["executions"][0]["cost_usd"] > 0
    assert resp.headers["x-ratelimit-limit"] == "60"
    if strategy == "llm":
        assert body["decision"]["agent"] == "math"
        assert body["decision"]["cost_usd"] > 0


async def test_route_only_does_not_execute(h: Harness, user: dict[str, str]) -> None:
    body = (
        await h.client.post(
            "/v1/queries", headers=user, json={"query": "hi there", "execute": False}
        )
    ).json()
    assert body["status"] == "routed"
    assert body["executions"] == []
    assert h.llm.requests == []  # adaptive router needs no LLM call


async def test_async_mode_enqueues_and_worker_completes(h: Harness, user: dict[str, str]) -> None:
    resp = await h.client.post(
        "/v1/queries", headers=user, json={"query": "What is 2 + 2?", "mode": "async"}
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert h.enqueued == [(uuid.UUID(body["id"]), True)]

    # What the Celery task does:
    await h.container.service.execute(uuid.UUID(body["id"]))
    # A redelivered task must not execute twice.
    await h.container.service.execute(uuid.UUID(body["id"]))
    done = (await h.client.get(body["links"]["self"], headers=user)).json()
    assert done["status"] == "completed"
    assert len(done["executions"]) == 1


async def test_failover_to_runner_up_agent(
    make_harness: Callable[..., Any], user: dict[str, str]
) -> None:
    def math_agent_down(request: ChatRequest) -> Any:
        if "careful mathematician" in request.messages[0]["content"]:
            return LLMUnavailable("provider 503", status=503, attempts=4)
        return scripted(request)

    async for h in make_harness(responder=math_agent_down):
        body = (
            await h.client.post(
                "/v1/queries",
                headers=user,
                json={"query": "Solve for x: 3x + 5 = 20", "strategy": "llm", "use_cache": False},
            )
        ).json()
        assert body["status"] == "completed"
        assert [e["status"] for e in body["executions"]] == ["error", "success"]
        assert body["executions"][0]["agent"] == "math"
        assert body["executions"][1]["agent"] != "math"
        assert body["executions"][0]["llm_attempts"] == 4


async def test_response_cache_serves_repeat_queries(h: Harness, user: dict[str, str]) -> None:
    payload = {"query": "What is 2 + 2?", "strategy": "embedding"}
    first = (await h.client.post("/v1/queries", headers=user, json=payload)).json()
    second = (await h.client.post("/v1/queries", headers=user, json=payload)).json()
    assert first["executions"][0]["cache_hit"] is False
    assert second["executions"][0]["cache_hit"] is True
    assert second["executions"][0]["cost_usd"] == 0
    assert second["answer"] == first["answer"]
    no_cache = (
        await h.client.post("/v1/queries", headers=user, json=payload | {"use_cache": False})
    ).json()
    assert no_cache["executions"][0]["cache_hit"] is False


async def test_sync_timeout_returns_504(
    make_harness: Callable[..., Any], user: dict[str, str]
) -> None:
    class SlowLLM(FakeLLM):
        async def chat(self, request: ChatRequest) -> ChatResponse:
            await asyncio.sleep(5)
            return reply("late")

    async for h in make_harness(sync_request_timeout_s=0.3):
        h.container.service._executor._llm = SlowLLM()  # type: ignore[attr-defined]
        resp = await h.client.post(
            "/v1/queries",
            headers=user,
            json={"query": "slow one", "strategy": "embedding", "use_cache": False},
        )
        assert resp.status_code == 504
        query_id = resp.json()["query_id"]
        assert (await h.client.get(f"/v1/queries/{query_id}", headers=user)).json()[
            "status"
        ] == "failed"


async def test_unavailable_strategy_is_400(
    make_harness: Callable[..., Any], user: dict[str, str]
) -> None:
    async for h in make_harness(with_llm=False):
        resp = await h.client.post(
            "/v1/queries", headers=user, json={"query": "x", "strategy": "llm"}
        )
        assert resp.status_code == 400
        assert "unavailable" in resp.json()["detail"]


async def test_validation_errors_are_problem_json(h: Harness, user: dict[str, str]) -> None:
    resp = await h.client.post("/v1/queries", headers=user, json={"query": "", "strategy": "magic"})
    assert resp.status_code == 422
    locs = {tuple(e["loc"]) for e in resp.json()["errors"]}
    assert ("body", "query") in locs and ("body", "strategy") in locs


# --- idempotency ------------------------------------------------------------------


async def test_idempotent_retry_replays_without_re_executing(
    h: Harness, user: dict[str, str]
) -> None:
    headers = user | {"Idempotency-Key": "order-123"}
    payload = {"query": "What is 2 + 2?", "strategy": "embedding", "use_cache": False}
    first = await h.client.post("/v1/queries", headers=headers, json=payload)
    calls_after_first = len(h.llm.requests)
    second = await h.client.post("/v1/queries", headers=headers, json=payload)
    assert second.status_code == first.status_code == 200
    assert second.headers.get("idempotent-replayed") == "true"
    assert second.json()["id"] == first.json()["id"]
    assert len(h.llm.requests) == calls_after_first

    reused = await h.client.post("/v1/queries", headers=headers, json=payload | {"query": "other"})
    assert reused.status_code == 422


async def test_idempotency_keys_are_scoped_per_api_key(
    h: Harness, user: dict[str, str], admin: dict[str, str]
) -> None:
    payload = {"query": "What is 2 + 2?", "strategy": "embedding"}
    a = await h.client.post("/v1/queries", headers=user | {"Idempotency-Key": "same"}, json=payload)
    b = await h.client.post(
        "/v1/queries", headers=admin | {"Idempotency-Key": "same"}, json=payload
    )
    assert a.json()["id"] != b.json()["id"]


# --- feedback, listing, access control -------------------------------------------


async def test_feedback_labels_execution_and_feeds_stats(h: Harness, user: dict[str, str]) -> None:
    created = (
        await h.client.post("/v1/queries", headers=user, json={"query": "What is 2+2?"})
    ).json()
    resp = await h.client.post(
        f"/v1/queries/{created['id']}/feedback",
        headers=user,
        json={"success": True, "comment": "right"},
    )
    assert resp.status_code == 200
    outcome = resp.json()["executions"][-1]["outcome"]
    assert outcome["success"] is True and outcome["source"] == "user"

    async with h.container.sessions() as session:
        await StatsRepository(session).refresh()
    agents = {a["name"]: a for a in (await h.client.get("/v1/agents", headers=user)).json()}
    agent = created["executions"][-1]["agent"]
    assert agents[agent]["stats"]["labelled"] == 1
    assert agents[agent]["stats"]["success_rate"] == 1.0


async def test_feedback_before_execution_is_409(h: Harness, user: dict[str, str]) -> None:
    created = (
        await h.client.post("/v1/queries", headers=user, json={"query": "x", "execute": False})
    ).json()
    resp = await h.client.post(
        f"/v1/queries/{created['id']}/feedback", headers=user, json={"success": True}
    )
    assert resp.status_code == 409


async def test_users_only_see_their_own_queries(
    h: Harness, session_factory: async_sessionmaker[AsyncSession], admin: dict[str, str]
) -> None:
    alice, bob = await new_key(session_factory), await new_key(session_factory)
    q = (
        await h.client.post(
            "/v1/queries", headers=alice, json={"query": "alice's", "execute": False}
        )
    ).json()
    await h.client.post("/v1/queries", headers=bob, json={"query": "bob's", "execute": False})

    assert (await h.client.get(f"/v1/queries/{q['id']}", headers=bob)).status_code == 404
    assert [
        i["query"] for i in (await h.client.get("/v1/queries", headers=alice)).json()["items"]
    ] == ["alice's"]
    assert len((await h.client.get("/v1/queries", headers=admin)).json()["items"]) == 2


async def test_list_pagination_cursor(h: Harness, user: dict[str, str]) -> None:
    for i in range(3):
        await h.client.post("/v1/queries", headers=user, json={"query": f"q{i}", "execute": False})
    page = (await h.client.get("/v1/queries?limit=2", headers=user)).json()
    assert [i["query"] for i in page["items"]] == ["q2", "q1"]
    assert page["items"][0]["selected_agent"] and page["items"][0]["final_agent"] is None
    rest = (
        await h.client.get(
            "/v1/queries", headers=user, params={"limit": 2, "before": page["next_cursor"]}
        )
    ).json()
    assert [i["query"] for i in rest["items"]] == ["q0"]
    assert rest["next_cursor"] is None
    # an exactly-full last page must not advertise another (empty) page
    full = (await h.client.get("/v1/queries?limit=3", headers=user)).json()
    assert full["next_cursor"] is None
    bad = await h.client.get("/v1/queries", headers=user, params={"before": "not-a-cursor"})
    assert bad.status_code == 400


async def test_cursor_does_not_skip_rows_with_identical_timestamps(
    h: Harness, user: dict[str, str], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from sqlalchemy import text as sql_text

    for i in range(5):
        await h.client.post(
            "/v1/queries", headers=user, json={"query": f"same-{i}", "execute": False}
        )
    async with session_factory() as session:
        await session.execute(sql_text("UPDATE queries SET created_at = '2026-01-01T00:00:00Z'"))
        await session.commit()
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2} | ({"before": cursor} if cursor else {})
        page = (await h.client.get("/v1/queries", headers=user, params=params)).json()
        seen += [i["query"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == [f"same-{i}" for i in range(5)]


async def test_me_endpoint_reports_role(
    h: Harness, user: dict[str, str], admin: dict[str, str]
) -> None:
    assert (await h.client.get("/v1/me", headers=user)).json()["role"] == "user"
    assert (await h.client.get("/v1/me", headers=admin)).json()["role"] == "admin"


async def test_unknown_query_is_404(h: Harness, user: dict[str, str]) -> None:
    resp = await h.client.get(f"/v1/queries/{uuid.uuid4()}", headers=user)
    assert resp.status_code == 404


# --- compare, catalog -----------------------------------------------------------------


async def test_compare_runs_every_strategy(h: Harness, user: dict[str, str]) -> None:
    body = (
        await h.client.post("/v1/route/compare", headers=user, json={"query": "What is 2 + 2?"})
    ).json()
    assert set(body["decisions"]) == {"round_robin", "embedding", "llm", "adaptive"}
    assert body["embedding_latency_ms"] is not None
    adaptive = body["decisions"]["adaptive"]
    assert {"contrib_semantic", "contrib_success"} <= set(adaptive["candidates"][0]["components"])


async def test_catalog_endpoints(h: Harness, user: dict[str, str]) -> None:
    agents = (await h.client.get("/v1/agents", headers=user)).json()
    assert [a["name"] for a in agents] == ["code", "math", "sql", "writer", "knowledge"]
    strategies = (await h.client.get("/v1/strategies", headers=user)).json()
    assert {s["name"]: s["is_default"] for s in strategies["strategies"]}["adaptive"] is True
    assert strategies["adaptive_config"]["weights"]["similarity"] > 0


# --- rate limiting, admin -------------------------------------------------------------


async def test_rate_limit_returns_429_with_retry_after(
    make_harness: Callable[..., Any], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async for h in make_harness(rate_limit_burst=2):
        key = await new_key(session_factory, rate_limit=6)  # 0.1 token/s refill
        codes = [(await h.client.get("/v1/agents", headers=key)).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        resp = await h.client.get("/v1/agents", headers=key)
        assert int(resp.headers["retry-after"]) >= 1
        assert resp.json()["title"] == "Rate limit exceeded"


async def test_admin_endpoints_require_admin_role(
    h: Harness, user: dict[str, str], admin: dict[str, str]
) -> None:
    assert (await h.client.get("/v1/admin/api-keys", headers=user)).status_code == 403
    created = (
        await h.client.post(
            "/v1/admin/api-keys", headers=admin, json={"name": "ci", "role": "user"}
        )
    ).json()
    assert created["api_key"].startswith("ar_")
    new_headers = {"Authorization": f"Bearer {created['api_key']}"}
    assert (await h.client.get("/v1/agents", headers=new_headers)).status_code == 200

    assert (
        await h.client.delete(f"/v1/admin/api-keys/{created['id']}", headers=admin)
    ).status_code == 204
    h.app.state.authenticator._ttl = 0  # skip the 30s auth cache for this assertion
    assert (await h.client.get("/v1/agents", headers=new_headers)).status_code == 401


# --- benchmarks + traces ---------------------------------------------------------


async def test_benchmark_routes(
    h: Harness,
    user: dict[str, str],
    admin: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from adaptiveroute.db.repositories import BenchmarkRepository

    async with session_factory() as session:
        await BenchmarkRepository(session).upsert(
            {"id": "run-1", "status": "completed", "dataset_sha256": "abc", "config": {"seeds": [0]},
             "summary": {"strategies": {"adaptive": {}}}, "report_markdown": "# report"}
        )  # fmt: skip
    listed = (await h.client.get("/v1/benchmarks", headers=user)).json()
    assert [r["id"] for r in listed] == ["run-1"]
    assert "summary" not in listed[0]  # the list is lightweight; detail has the report
    detail = (await h.client.get("/v1/benchmarks/run-1", headers=user)).json()
    assert detail["report_markdown"] == "# report"
    assert (await h.client.get("/v1/benchmarks/nope", headers=user)).status_code == 404

    started: list[tuple[str, dict[str, Any]]] = []

    async def fake_enqueue(run_id: str, options: dict[str, Any]) -> None:
        started.append((run_id, options))

    h.app.state.enqueue_benchmark = fake_enqueue
    assert (await h.client.post("/v1/benchmarks", headers=user, json={})).status_code == 403
    resp = await h.client.post("/v1/benchmarks", headers=admin, json={"seeds": [0, 1]})
    assert resp.status_code == 202
    assert started == [(resp.json()["id"], {"seeds": [0, 1], "strategies": None})]
    # visible immediately, before any worker picks it up
    queued = (await h.client.get(f"/v1/benchmarks/{resp.json()['id']}", headers=user)).json()
    assert queued["status"] == "queued"


JAEGER_TRACE = {
    "data": [
        {
            "traceID": "a" * 32,
            "processes": {"p1": {"serviceName": "adaptiveroute-api"}},
            "spans": [
                {"spanID": "root", "operationName": "POST /v1/queries", "references": [],
                 "startTime": 1_000_000, "duration": 50_000, "processID": "p1",
                 "tags": [{"key": "http.status_code", "value": 200}]},
                {"spanID": "child", "operationName": "route adaptive",
                 "references": [{"refType": "CHILD_OF", "spanID": "root"}],
                 "startTime": 1_002_000, "duration": 4_000, "processID": "p1",
                 "tags": [{"key": "ar.routing.agent", "value": "math"}, {"key": "secret.thing", "value": "x"}]},
                {"spanID": "llm", "operationName": "agent.execute math",
                 "references": [{"refType": "CHILD_OF", "spanID": "root"}],
                 "startTime": 1_010_000, "duration": 30_000, "processID": "p1",
                 "tags": [{"key": "otel.status_code", "value": "ERROR"}]},
            ],
        }
    ]
}  # fmt: skip


async def test_trace_proxy_flattens_jaeger_spans(
    make_harness: Callable[..., Any], user: dict[str, str]
) -> None:
    import respx

    async for h in make_harness(trace_query_url="http://jaeger.test"):
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"http://jaeger.test/api/traces/{'a' * 32}").respond(200, json=JAEGER_TRACE)
            mock.get(f"http://jaeger.test/api/traces/{'b' * 32}").respond(404, json={"data": []})
            body = (await h.client.get(f"/v1/traces/{'a' * 32}", headers=user)).json()
            missing = await h.client.get(f"/v1/traces/{'b' * 32}", headers=user)
        assert body["duration_ms"] == 50.0
        spans = {s["name"]: s for s in body["spans"]}
        assert spans["route adaptive"]["parent_span_id"] == "root"
        assert spans["route adaptive"]["start_offset_ms"] == 2.0
        assert spans["route adaptive"]["attributes"] == {"ar.routing.agent": "math"}  # filtered
        assert spans["agent.execute math"]["status"] == "error"
        assert body["ui_url"] == f"http://jaeger.test/trace/{'a' * 32}"
        assert missing.status_code == 404
        assert (await h.client.get("/v1/traces/not-hex", headers=user)).status_code == 400


async def test_trace_proxy_without_backend_is_503(h: Harness, user: dict[str, str]) -> None:
    assert (await h.client.get(f"/v1/traces/{'a' * 32}", headers=user)).status_code == 503
