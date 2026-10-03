# 0002: Local ONNX embeddings (fastembed, BAAI/bge-small-en-v1.5)

**Context.** The embedding and adaptive routers embed every query, so the embedding is
on the hot path. Groq (our LLM provider) offers no embeddings endpoint.

**Decision.** Use `fastembed` with `bge-small-en-v1.5` (384 dimensions, ONNX Runtime,
CPU). The model is baked into the Docker image (no network at start-up), loaded once
per process and warmed up before the API accepts traffic. Inference runs in a worker
thread (`asyncio.to_thread`) so it never blocks the event loop. Results are cached in
Redis by `(model, sha256(text))`.

**Consequences.**
- About 4 ms per query on a laptop CPU (measured); zero marginal cost; no provider rate
  limits; deterministic, so benchmarks are reproducible.
- 384 dimensions keeps pgvector indexes small.
- A small model is less accurate than large API embedders. The evaluation measures
  routing accuracy directly, so this cost is visible rather than assumed.
- The image is about 200 MB larger because of onnxruntime and the model.

**Alternatives.**
- OpenAI/Voyage embedding APIs: per-call cost, network latency, another API key and
  another failure mode.
- sentence-transformers with PyTorch: much larger image for similar quality at this
  size.
