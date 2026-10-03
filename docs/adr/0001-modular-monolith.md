# 0001: Modular monolith with a Celery worker; routing runs in-process

**Context.** The system needs an HTTP API, routing logic, five agents, async
execution, a database and a cache. It is built and operated by one person. A routing
decision takes 0.05–4 ms (LLM router aside) and is on every request's critical path.

**Decision.** One Python package, deployed as three processes from one image: api,
worker and beat. Routing and agent execution are modules (`routing/`, `agents/`)
called in-process by `QueryService`, which the API and the worker share. Module
boundaries are enforced through `Protocol` ports and a single composition root
(`container.py`) rather than through network boundaries.

**Consequences.**
- No serialization, network hop or extra failure mode between "API" and "router". One
  image to build, scan and deploy.
- API and worker run the exact same code path; the benchmark replay runs it too, with
  in-memory adapters.
- Routing scales with the API process, which is fine at this load (hundreds of req/s
  per process, measured).
- If embedding ever needs a GPU, or routing needs independent scaling, the `Embedder`
  port is the seam to extract it as a service.

**Alternatives.**
- A separate routing microservice with its own deploy: more moving parts and network
  latency, with no independent-scaling need yet.
- A serverless function per agent: cold starts and harder local development.
