# 0008: Thin httpx client for an OpenAI-compatible API (Groq), no vendor SDK

**Context.** Agent execution runs against Groq's OpenAI-compatible Chat Completions
API. Retries, timeouts and error classification directly affect the latency,
error-rate and cost numbers we report.

**Decision.** About 150 lines of explicit `httpx` code (`llm/client.py`):
- one deadline per call covering every attempt;
- `Retry-After` honoured on 429s;
- full-jitter exponential backoff on 5xx, network errors and timeouts;
- fail-fast on other 4xx;
- never sleep past the deadline.

A `Protocol` (`LLMClient`) lets tests use a scripted fake. Any OpenAI-compatible
endpoint (vLLM, Ollama, OpenAI) works by changing `AR_LLM_BASE_URL`.

**Consequences.**
- The retry policy is visible, unit-tested (11 tests with `respx`), and explainable.
  There are no hidden SDK retries inflating latency.
- No streaming support yet; it isn't needed for routing.

**Alternatives.**
- The OpenAI Python SDK pointed at Groq: works, but its retry policy is internal and
  harder to test precisely.
- LangChain/LiteLLM: large abstractions for a single provider call.
