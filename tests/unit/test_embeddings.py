import numpy as np
import pytest

from adaptiveroute.embeddings import HashingEmbedder, l2_normalize


async def test_hashing_embedder_shape_and_norm() -> None:
    emb = HashingEmbedder(dim=64)
    out = await emb.embed(["hello world", "another sentence here"])
    assert out.shape == (2, 64)
    assert out.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, rtol=1e-5)


async def test_hashing_embedder_is_deterministic_and_lexical() -> None:
    emb = HashingEmbedder()
    a, b, c = await emb.embed(
        [
            "write a python function to reverse a list",
            "write a python function to reverse a string",
            "what is the capital of australia",
        ]
    )
    again = (await emb.embed(["write a python function to reverse a list"]))[0]
    np.testing.assert_array_equal(a, again)
    assert float(a @ b) > float(a @ c)


async def test_empty_text_does_not_produce_nan() -> None:
    out = await HashingEmbedder(dim=16).embed([""])
    assert not np.isnan(out).any()


def test_l2_normalize_handles_zero_rows() -> None:
    m = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    out = l2_normalize(m)
    np.testing.assert_allclose(out[0], [0.6, 0.8])
    np.testing.assert_array_equal(out[1], [0.0, 0.0])


@pytest.mark.slow
async def test_fastembed_semantic_similarity() -> None:
    """Real model (downloads ~130MB on first run). Checks semantics, not just lexical overlap."""
    from adaptiveroute.config import get_settings
    from adaptiveroute.embeddings import FastEmbedEmbedder

    s = get_settings()
    emb = FastEmbedEmbedder(s.embedding_model, s.embedding_dim, s.embedding_cache_dir)
    q, paraphrase, unrelated = await emb.embed(
        [
            "How many ways can I arrange 6 people in a row?",
            "Count the permutations of six distinct objects.",
            "Draft a polite email declining a meeting invitation.",
        ]
    )
    assert q.shape == (384,)
    assert float(q @ paraphrase) > float(q @ unrelated) + 0.1
