from __future__ import annotations

from app.retrieval.embedding_cache import QueryEmbeddingCache


def test_query_embedding_cache_hits_before_ttl_expires():
    current = 100.0

    def clock() -> float:
        return current

    cache = QueryEmbeddingCache(ttl_seconds=5, max_entries=2, clock=clock)
    cache.set(
        input_hash="hash-a",
        model_version="ollama:bge-m3:1024:cosine",
        input_fingerprint="fingerprint-a",
        vectors=[[0.1, 0.2]],
        metadata={"input_lengths": [12]},
    )

    hit = cache.get(
        input_hash="hash-a",
        model_version="ollama:bge-m3:1024:cosine",
        input_fingerprint="fingerprint-a",
    )

    assert hit == [[0.1, 0.2]]


def test_query_embedding_cache_expires_and_evicts_lru():
    now = 10.0

    def clock() -> float:
        return now

    cache = QueryEmbeddingCache(ttl_seconds=3, max_entries=1, clock=clock)
    cache.set(
        input_hash="hash-a",
        model_version="m1",
        input_fingerprint="fp-a",
        vectors=[[1.0]],
        metadata={},
    )
    now += 1
    cache.set(
        input_hash="hash-b",
        model_version="m1",
        input_fingerprint="fp-b",
        vectors=[[2.0]],
        metadata={},
    )

    assert cache.get(input_hash="hash-a", model_version="m1", input_fingerprint="fp-a") is None
    assert cache.get(input_hash="hash-b", model_version="m1", input_fingerprint="fp-b") == [[2.0]]

    now += 4
    assert cache.get(input_hash="hash-b", model_version="m1", input_fingerprint="fp-b") is None
