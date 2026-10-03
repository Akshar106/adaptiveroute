"""Text embedders.

* ``FastEmbedEmbedder`` - local ONNX model (default BAAI/bge-small-en-v1.5, 384-d).
  No API cost or network hop on the routing hot path; inference runs in a worker
  thread so it never blocks the event loop.
* ``HashingEmbedder`` - dependency-free feature-hashing embedder for tests and
  offline smoke runs. Captures lexical overlap only; never used for reported results.
* ``CachedEmbedder`` - Redis read-through cache in front of either.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from redis.asyncio import Redis
from redis.exceptions import RedisError

from adaptiveroute.observability import metrics
from adaptiveroute.observability.logs import get_logger
from adaptiveroute.ports import Embedder, Vector

log = get_logger(__name__)


def l2_normalize(matrix: Vector) -> Vector:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class FastEmbedEmbedder:
    def __init__(self, model_name: str, dim: int, cache_dir: Path | None = None) -> None:
        self._model_name = model_name
        self._dim = dim
        self._cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # heavy import, done lazily

                log.info("loading_embedding_model", model=self._model_name)
                self._model = TextEmbedding(
                    model_name=self._model_name,
                    cache_dir=str(self._cache_dir) if self._cache_dir else None,
                )
            return self._model

    def warmup(self) -> None:
        """Load the model eagerly (called at startup so the first request is fast)."""
        self._embed_sync(["warmup"])

    def _embed_sync(self, texts: Sequence[str]) -> Vector:
        model = self._load()
        rows = np.asarray(list(model.embed(list(texts))), dtype=np.float32)
        if rows.shape[1] != self._dim:
            raise ValueError(f"model returned dim {rows.shape[1]}, expected {self._dim}")
        return l2_normalize(rows)

    async def embed(self, texts: Sequence[str]) -> Vector:
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        return await asyncio.to_thread(self._embed_sync, texts)


_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Signed feature hashing over word unigrams/bigrams and character trigrams."""

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def model_name(self) -> str:
        return f"hashing-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def _features(self, text: str) -> list[str]:
        words = _TOKEN.findall(text.lower())
        feats = [f"w:{w}" for w in words]
        feats += [f"b:{a}_{b}" for a, b in itertools.pairwise(words)]
        for w in words:
            padded = f"#{w}#"
            feats += [f"c:{padded[i : i + 3]}" for i in range(len(padded) - 2)]
        return feats

    def embed_sync(self, texts: Sequence[str]) -> Vector:
        out = np.zeros((len(texts), self._dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat in self._features(text):
                digest = hashlib.blake2b(feat.encode(), digest_size=8).digest()
                h = int.from_bytes(digest, "little")
                out[row, h % self._dim] += 1.0 if (h >> 63) & 1 else -1.0
        return l2_normalize(out)

    async def embed(self, texts: Sequence[str]) -> Vector:
        return self.embed_sync(texts)


class CachedEmbedder:
    """Read-through Redis cache keyed by (model, sha256(text)).

    Cache failures are never fatal: on any Redis error we just compute embeddings.
    """

    def __init__(self, inner: Embedder, redis: Redis, ttl_s: int, prefix: str = "ar:emb") -> None:
        self._inner = inner
        self._redis = redis
        self._ttl_s = ttl_s
        self._prefix = prefix

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def dim(self) -> int:
        return self._inner.dim

    def _key(self, text: str) -> str:
        digest = hashlib.sha256(text.encode()).hexdigest()
        return f"{self._prefix}:{self._inner.model_name}:{digest}"

    async def embed(self, texts: Sequence[str]) -> Vector:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        keys = [self._key(t) for t in texts]
        try:
            cached = await self._redis.mget(keys)
        except RedisError as exc:
            log.warning("embedding_cache_unavailable", error=repr(exc))
            cached = [None] * len(texts)

        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        missing: list[int] = []
        for i, blob in enumerate(cached):
            if blob is not None and len(blob) == self.dim * 4:
                out[i] = np.frombuffer(blob, dtype=np.float32)
            else:
                missing.append(i)
        metrics.CACHE_REQUESTS.labels(cache="embedding", result="hit").inc(
            len(texts) - len(missing)
        )
        metrics.CACHE_REQUESTS.labels(cache="embedding", result="miss").inc(len(missing))

        if missing:
            fresh = await self._inner.embed([texts[i] for i in missing])
            out[missing] = fresh
            try:
                async with self._redis.pipeline(transaction=False) as pipe:
                    for row, i in enumerate(missing):
                        pipe.set(keys[i], fresh[row].astype(np.float32).tobytes(), ex=self._ttl_s)
                    await pipe.execute()
            except RedisError as exc:
                log.warning("embedding_cache_write_failed", error=repr(exc))
        return out
