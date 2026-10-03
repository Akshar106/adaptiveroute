"""Run one agent on one query.

The executor never raises for provider problems: every outcome (success, error,
timeout) becomes an ``ExecutionResult`` so it can be stored, counted and learned from.
"""

from __future__ import annotations

import time

from opentelemetry import trace

from adaptiveroute.domain import AgentSpec, ExecutionResult, ExecutionStatus
from adaptiveroute.llm import ChatRequest, LLMClient, LLMError, LLMTimeout, PriceTable
from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.ports import LoadTracker

log = get_logger(__name__)
tracer = trace.get_tracer(__name__)


class AgentExecutor:
    def __init__(self, llm: LLMClient, prices: PriceTable, load: LoadTracker) -> None:
        self._llm = llm
        self._prices = prices
        self._load = load

    @staticmethod
    def build_request(agent: AgentSpec, query: str) -> ChatRequest:
        return ChatRequest(
            model=agent.model,
            messages=(
                {"role": "system", "content": agent.system_prompt},
                {"role": "user", "content": query},
            ),
            temperature=agent.temperature,
            max_tokens=agent.max_output_tokens,
            reasoning_effort=agent.reasoning_effort,
            timeout_s=agent.timeout_s,
        )

    async def run(self, agent: AgentSpec, query: str) -> ExecutionResult:
        with tracer.start_as_current_span(f"agent.execute {agent.name}") as span:
            span.set_attributes(
                {
                    "ar.agent": agent.name,
                    "gen_ai.system": "openai_compatible",
                    "gen_ai.request.model": agent.model,
                    "ar.agent.fingerprint": agent.fingerprint,
                }
            )
            result = await self._run(agent, query)
            span.set_attributes(
                {
                    "ar.execution.status": result.status.value,
                    "gen_ai.usage.input_tokens": result.input_tokens,
                    "gen_ai.usage.output_tokens": result.output_tokens,
                    "ar.cost_usd": result.cost_usd,
                    "ar.attempts": result.attempts,
                }
            )
            if not result.ok:
                span.set_status(trace.Status(trace.StatusCode.ERROR, result.error or ""))
        self._record_metrics(result)
        return result

    async def _run(self, agent: AgentSpec, query: str) -> ExecutionResult:
        request = self.build_request(agent, query)
        start = time.perf_counter()
        async with self._load.track(agent.name):
            try:
                resp = await self._llm.chat(request)
            except LLMError as exc:
                status = (
                    ExecutionStatus.TIMEOUT
                    if isinstance(exc, LLMTimeout)
                    else ExecutionStatus.ERROR
                )
                log.warning("agent_failed", agent=agent.name, status=status.value, error=str(exc))
                return ExecutionResult(
                    agent=agent.name,
                    model=agent.model,
                    status=status,
                    output=None,
                    error=f"{type(exc).__name__}: {exc}",
                    latency_ms=(time.perf_counter() - start) * 1000,
                    attempts=exc.attempts,
                )

        # An empty completion usually means a reasoning model spent its whole token
        # budget thinking. It is still billed, so tokens and cost are kept.
        empty = not resp.content.strip()
        return ExecutionResult(
            agent=agent.name,
            model=agent.model,
            status=ExecutionStatus.ERROR if empty else ExecutionStatus.SUCCESS,
            output=None if empty else resp.content,
            error=f"empty completion (finish_reason={resp.finish_reason})" if empty else None,
            latency_ms=(time.perf_counter() - start) * 1000,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=self._prices.cost(agent.model, resp.input_tokens, resp.output_tokens),
            attempts=resp.attempts,
        )

    @staticmethod
    def _record_metrics(result: ExecutionResult) -> None:
        metrics.AGENT_EXECUTIONS.labels(agent=result.agent, status=result.status.value).inc()
        metrics.AGENT_LATENCY.labels(agent=result.agent).observe(result.latency_ms / 1000)
        if result.input_tokens or result.output_tokens:
            metrics.LLM_TOKENS.labels(model=result.model, kind="input").inc(result.input_tokens)
            metrics.LLM_TOKENS.labels(model=result.model, kind="output").inc(result.output_tokens)
            metrics.COST_USD.labels(agent=result.agent, component="agent").inc(result.cost_usd)
