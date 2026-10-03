# 0003: pgvector in the primary Postgres for routing history

**Context.** The adaptive router needs "the k most similar labelled past queries per
agent" on every decision. Labels arrive in the same transactions as the rest of the
data.

**Decision.** Store embeddings in Postgres with pgvector, in a denormalised `outcomes`
table with an HNSW cosine index, and query it with a per-agent `LATERAL` top-k (see
[database.md](../database.md)).

**Consequences.**
- One datastore to run, back up and secure. Labels, executions and vectors are
  consistent (same transaction). Standard SQL for filtering by agent or model.
- RDS supports pgvector, so production needs nothing extra.
- At tens of millions of vectors a dedicated vector store would scale better. This
  project is many orders of magnitude below that.

**Alternatives.**
- Qdrant/Pinecone/Weaviate: another service and dual writes, which bring consistency
  problems.
- Redis vector search: the memory cost of the history would compete with the
  broker/cache.
- Brute force in Python: fine for the benchmark replay (`InMemoryHistory`), but not
  for a multi-replica API.
