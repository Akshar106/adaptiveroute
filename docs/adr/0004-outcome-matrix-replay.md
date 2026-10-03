# 0004: Evaluate routers by replay over a recorded outcome matrix

**Context.** Comparing four routing strategies by running each one live against the
LLM would cost 4× the API calls, mix provider noise (latency, nondeterminism) into the
strategy comparison, and make results irreproducible. The Groq free tier also limits
calls per day.

**Decision.** Split evaluation into two phases:
1. **Collect.** Run *every* agent on *every* dataset item once against the real
   provider, at temperature 0. Record output, checker verdict, measured latency,
   tokens and cost. Also record the LLM router's real decision (and latency and cost)
   per item. The file is append-only JSONL keyed by a fingerprint of the agent's model
   and prompt, so collection is resumable, and changing a prompt invalidates only that
   agent's column.
2. **Replay.** Run the *real router code* over seeded arrival orders and look up each
   chosen (item, agent) outcome in the matrix. Failover mirrors production. The
   adaptive router learns online with bandit feedback: it only sees outcomes for the
   agents it executed. A 5-fold warm-start variant gives it history from other items
   only.

**Consequences.**
- Every strategy is judged on identical agent outputs, so differences come from routing
  alone.
- Many seeds and ablations cost nothing.
- Matrix reference points (each single agent, a "label oracle", a true oracle) come for
  free.
- Routing latency is measured during replay and execution latency during collection;
  end-to-end latency is their sum, not one wall-clock measurement. The LLM router's
  latency is its recorded API round trip.
- Provider nondeterminism isn't averaged out; each cell is a single sample.

**Alternatives.**
- Live A/B per strategy: costly, noisy and not reproducible.
- An LLM-as-judge for success: cheaper to write but neither reproducible nor
  verifiable. We use deterministic checkers instead.
