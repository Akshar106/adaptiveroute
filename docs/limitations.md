# Limitations and future work

## Limitations

### Evaluation
- **Small, purpose-built dataset.** 150 items (30 per domain), written for this
  project. CIs are wide (±7–8 points on rates). This is evidence about *these* agents
  and *this* distribution, not a general routing benchmark.
- **Single-specialist labels.** Most items list one acceptable agent, so routing
  accuracy undercounts reasonable alternative choices; task success is the better
  headline.
- **One sample per (item, agent) cell** at temperature 0; provider nondeterminism isn't
  averaged out.
- **Clean feedback.** In replay the adaptive router learns from deterministic checker
  labels after every execution. Production feedback (thumbs up/down) is sparse,
  delayed and noisy, so real learning will be slower.
- **Latency is composed, not end-to-end measured.** Routing latency (replay) plus
  execution latency (collection). Live end-to-end latency appears in Prometheus and in
  the load test.
- **Costs are list-price estimates.** Free-tier usage is billed $0, and prices can
  change (the price date is recorded in every report).

### Dataset
Known weak spots found during authoring:
- **sql-012:** relies on customer ids because names in the fixture aren't unique.
- **sql-015:** contains a filter that doesn't change the result.
- **Unenforceable constraints:** some code items state constraints the tests can't
  enforce (e.g. "don't use `re`"); outputs that ignore them still pass.
- **Hedged answers:** the knowledge checker accepts answers that list several options
  unless a wrong option is in `none_of`.
- **Sentence counting:** the writer checker counts "Ms." as a sentence end.

### System
- **Python checker sandbox.** Model-generated code runs in a separate interpreter with
  an empty environment, CPU and wall-clock limits, and a temp dir. It is **not** a
  security boundary: no seccomp, no network isolation, no filesystem jail. Run
  benchmark collection only on a disposable machine or container.
- **Cache-hit learning edge case.** Feedback on a response-cache hit is recorded
  against the cached execution's tiny latency, which slightly lowers that agent's
  contextual latency estimate. Cache hits are excluded from `agent_stats`, but not
  from the kNN evidence.
- **Round-robin under compare.** `POST /v1/route/compare` advances the shared
  round-robin counter like a real request.
- **Fail-open rate limiting** when Redis is down (deliberate, ADR 0006).
- **Auth revocation delay** of up to 30 s, from the per-process auth cache.
- **No streaming** responses, no multi-turn context, and no tool use by agents.
- **AWS deployment is defined and statically validated but not applied** in this
  repository's history. `terraform validate` and a checkov scan run, but
  `terraform apply` never ran against a real account, so the deploy workflow is
  untested end to end.
- **Groq model availability.** As of September 2026 only `gpt-oss-20b` and
  `gpt-oss-120b` are self-serve; Llama models need an enterprise contract. The agent
  roster reflects that constraint rather than an ideal model mix.

## Future work

1. **Contextual bandit router.** Replace the hand-weighted score with LinUCB or
   Thompson sampling over (query embedding × agent), keeping the cost and latency
   terms as constraints. The replay harness already supports a bandit-feedback
   evaluation.
2. **Learn the weights.** Fit `w` on a dev split with the replay harness as the
   objective (e.g. maximise success − λ·cost) and report on a held-out split.
3. **Feedback quality.** Weight user labels by reliability; use implicit signals
   (retries, copy events); add an LLM-judge for unlabelled outputs, kept clearly
   separate from the deterministic checker.
4. **Cascades.** Try the cheap agent first and escalate on low confidence or a failed
   self-check; the matrix already has the data to simulate this.
5. **Semantic response cache** (embedding-similarity hits with a high threshold) in
   addition to the exact-match cache.
6. **Real sandboxing** for code checks (gVisor / Firecracker / a WASM Python).
7. **Larger public benchmarks** (e.g. adapting RouterBench-style mixes) and multiple
   samples per cell.
8. **Production hardening:**
   - fail-closed rate limiting for paid tiers;
   - per-tenant budgets enforced from `ar_cost_usd_total`;
   - provisioned alert rules;
   - secret rotation for the database URL;
   - blue/green deploys.
