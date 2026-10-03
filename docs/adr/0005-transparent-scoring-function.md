# 0005: Transparent weighted scoring with Bayesian shrinkage (no learned router)

**Context.** The adaptive router must combine semantic similarity, historical success,
latency, cost and load. It must be configurable, explainable per decision, and must
work from a cold start with sparse, noisy feedback.

**Decision.** Use a linear score of normalised terms (see
[system-design §3.1](../system-design.md#31-adaptive-scoring-function)):
- **Semantic fit:** a temperature-scaled similarity, relative to the best agent.
- **Success, latency and cost:** contextual kNN estimates shrunk towards agent-level
  values with a Beta-Binomial-style prior (`k₀` pseudo-observations).
- **Load:** a penalty, plus a hard eligibility rule.
- **Exploration:** an optional UCB bonus.

Weights and constants are in `config/routing.yaml` and were fixed before looking at
benchmark results. Per-term contributions are stored with every decision.

**Consequences.**
- Every decision can be explained ("won on semantic fit by +0.39") and audited from
  the database.
- Cold start degrades gracefully to embedding routing plus latency and cost priors.
  Evidence takes over as labels accumulate, at a rate set by `k₀` and the
  neighbour-weight ramp.
- Only near-tie semantic decisions can be overturned by history (the weights bound
  each term's swing). This was a deliberate safety property, and it is tested.
- A linear model can't capture interactions a learned router could. The ablation table
  in the benchmark shows how much each term contributes.

**Alternatives.**
- A trained classifier (e.g. logistic regression on embeddings): needs labelled
  training data per agent, can't explain cost and latency trade-offs, and must be
  retrained when agents change.
- A full contextual bandit (LinUCB / Thompson sampling): the principled next step,
  listed in future work. The current design's UCB term and online replay are a first
  step towards it.
