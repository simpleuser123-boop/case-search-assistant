from __future__ import annotations

import json
import socket
import urllib.error

import pytest

from app.core.config import Settings
from app.retrieval.chroma_adapter import ChromaCollectionAdapter
from app.retrieval.embedding import OllamaEmbeddingClient, warmup_ollama_embedding
from app.retrieval.models import RetrievalConfigMismatchError, RetrievalDependencyError


def _settings(**overrides):
    values = {
        "DEEPSEEK_API_KEY": "test-key",
        "EMBEDDING_PROVIDER": "ollama",
        "EMBEDDING_MODEL": "bge-m3",
        "EMBEDDING_DIMENSION": 1024,
        "EMBEDDING_DISTANCE_METRIC": "cosine",
        "EMBEDDING_TIMEOUT_SECONDS": 1,
        "OLLAMA_BASE_URL": "http://ollama.test",
        "CHROMA_COLLECTION": "case_chunks_bge_m3_v1",
        "CHROMA_PERSIST_DIR": "C:/tmp/chroma-from-test-config",
        "CHROMA_QUERY_TIMEOUT_SECONDS": 1,
    }
    values.update(overrides)
    return Settings(**values)


class FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._body).encode("utf-8")


def test_ollama_embedding_dimension_is_validated():
    def fake_urlopen(request, timeout):
        return FakeResponse({"embeddings": [[0.01] * 1024]})

    client = OllamaEmbeddingClient(config=_settings(), urlopen=fake_urlopen)

    result = client.embed_query("夜间进入店铺盗窃现金5000元")

    assert result.provider == "ollama"
    assert result.model == "bge-m3"
    assert result.dimension == 1024
    assert len(result.vector) == 1024


def test_ollama_embedding_runtime_config_must_match_day0_index_contract():
    client = OllamaEmbeddingClient(config=_settings(EMBEDDING_PROVIDER="openai"))
    with pytest.raises(RetrievalConfigMismatchError) as exc_info:
        client.embed_query("夜间进入店铺盗窃现金5000元")
    assert exc_info.value.code == "EMBEDDING_PROVIDER_MISMATCH"

    client = OllamaEmbeddingClient(config=_settings(EMBEDDING_MODEL="not-bge-m3"))
    with pytest.raises(RetrievalConfigMismatchError) as exc_info:
        client.embed_query("夜间进入店铺盗窃现金5000元")
    assert exc_info.value.code == "EMBEDDING_MODEL_MISMATCH"

    client = OllamaEmbeddingClient(config=_settings(EMBEDDING_DIMENSION=768))
    with pytest.raises(RetrievalConfigMismatchError) as exc_info:
        client.embed_query("夜间进入店铺盗窃现金5000元")
    assert exc_info.value.code == "EMBEDDING_DIMENSION_MISMATCH"

    client = OllamaEmbeddingClient(config=_settings(EMBEDDING_DISTANCE_METRIC="l2"))
    with pytest.raises(RetrievalConfigMismatchError) as exc_info:
        client.embed_query("夜间进入店铺盗窃现金5000元")
    assert exc_info.value.code == "EMBEDDING_DISTANCE_MISMATCH"


def test_ollama_embedding_failure_does_not_leak_query():
    sensitive_query = "敏感案情原文XYZ夜间盗窃5000元"

    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("connection refused")

    client = OllamaEmbeddingClient(config=_settings(), urlopen=fake_urlopen)

    with pytest.raises(RetrievalDependencyError) as exc_info:
        client.embed_query(sensitive_query)

    assert exc_info.value.code == "EMBEDDING_UNAVAILABLE"
    assert sensitive_query not in str(exc_info.value)


def test_ollama_embedding_timeout_is_mapped():
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError(socket.timeout("timed out"))

    client = OllamaEmbeddingClient(config=_settings(), urlopen=fake_urlopen)

    with pytest.raises(RetrievalDependencyError) as exc_info:
        client.embed_query("夜间进入店铺盗窃现金5000元")

    assert exc_info.value.code == "EMBEDDING_TIMEOUT"


def test_embedding_warmup_returns_sanitized_status():
    def fake_urlopen(request, timeout):
        return FakeResponse({"embeddings": [[0.01] * 1024]})

    status = warmup_ollama_embedding(config=_settings(), urlopen=fake_urlopen)

    assert status.ok is True
    assert status.duration_ms >= 0


def test_ollama_embedding_dimension_mismatch_is_detected():
    def fake_urlopen(request, timeout):
        return FakeResponse({"embeddings": [[0.01] * 3]})

    client = OllamaEmbeddingClient(config=_settings(), urlopen=fake_urlopen)

    with pytest.raises(RetrievalConfigMismatchError) as exc_info:
        client.embed_query("夜间进入店铺盗窃现金5000元")

    assert exc_info.value.code == "EMBEDDING_DIMENSION_MISMATCH"


class FakeCollection:
    def __init__(self, metadata: dict | None = None, *, count: int = 2) -> None:
        self.metadata = metadata or {
            "embedding_provider": "ollama",
            "model_name": "bge-m3",
            "vector_dimension": 1024,
            "distance_metric": "cosine",
            "hnsw:space": "cosine",
        }
        self._count = count

    def count(self) -> int:
        return self._count

    def get(self, limit: int, include: list[str]):
        return {"ids": ["chunk-1"], "embeddings": [[0.01] * 1024]}

    def query(self, query_embeddings, n_results: int, include: list[str]):
        return {
            "ids": [["chunk-1"]],
            "documents": [["本院查明,被告人夜间进入店铺盗窃现金。"]],
            "metadatas": [[{"case_id": "case-1", "chunk_id": "chunk-1", "chunk_type": "facts"}]],
            "distances": [[0.23]],
        }


class FakeClient:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection

    def get_collection(self, name: str):
        assert name == "case_chunks_bge_m3_v1"
        return self.collection


def test_chroma_persist_dir_is_read_from_config():
    captured_paths: list[str] = []

    def fake_factory(path: str):
        captured_paths.append(path)
        return FakeClient(FakeCollection())

    config = _settings(CHROMA_PERSIST_DIR="D:/ascii/chroma-configured")
    adapter = ChromaCollectionAdapter(config=config, client_factory=fake_factory)

    assert adapter.count() == 2
    assert captured_paths == ["D:/ascii/chroma-configured"]


def test_chroma_collection_name_is_read_from_central_config():
    requested_names: list[str] = []

    class NamedFakeClient(FakeClient):
        def get_collection(self, name: str):
            requested_names.append(name)
            return self.collection

    adapter = ChromaCollectionAdapter(
        config=_settings(CHROMA_COLLECTION="configured_collection_name"),
        client_factory=lambda path: NamedFakeClient(FakeCollection()),
    )

    assert adapter.count() == 2
    assert requested_names == ["configured_collection_name"]


def test_chroma_collection_count_is_readable():
    adapter = ChromaCollectionAdapter(
        config=_settings(),
        client_factory=lambda path: FakeClient(FakeCollection(count=7)),
    )

    assert adapter.count() == 7


def test_chroma_collection_metadata_mismatch_is_detected():
    collection = FakeCollection(metadata={
        "embedding_provider": "ollama",
        "model_name": "not-bge-m3",
        "vector_dimension": 1024,
        "distance_metric": "cosine",
    })
    adapter = ChromaCollectionAdapter(
        config=_settings(),
        client_factory=lambda path: FakeClient(collection),
    )

    with pytest.raises(RetrievalConfigMismatchError) as exc_info:
        adapter.count()

    assert exc_info.value.code == "EMBEDDING_MODEL_MISMATCH"


@pytest.mark.parametrize(
    ("metadata_override", "expected_code"),
    [
        ({"embedding_provider": "openai"}, "EMBEDDING_PROVIDER_MISMATCH"),
        ({"vector_dimension": 768}, "EMBEDDING_DIMENSION_MISMATCH"),
        ({"distance_metric": "l2", "hnsw:space": "l2"}, "EMBEDDING_DISTANCE_MISMATCH"),
    ],
)
def test_chroma_collection_provider_dimension_and_distance_mismatch_are_detected(
    metadata_override,
    expected_code,
):
    metadata = {
        "embedding_provider": "ollama",
        "model_name": "bge-m3",
        "vector_dimension": 1024,
        "distance_metric": "cosine",
        "hnsw:space": "cosine",
    }
    metadata.update(metadata_override)
    adapter = ChromaCollectionAdapter(
        config=_settings(),
        client_factory=lambda path: FakeClient(FakeCollection(metadata=metadata)),
    )

    with pytest.raises(RetrievalConfigMismatchError) as exc_info:
        adapter.count()

    assert exc_info.value.code == expected_code


def test_chroma_query_adapter_returns_stable_chunk_shape():
    adapter = ChromaCollectionAdapter(
        config=_settings(),
        client_factory=lambda path: FakeClient(FakeCollection()),
    )

    chunks = adapter.query([0.01] * 1024, top_k=1)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.case_id == "case-1"
    assert chunk.chunk_id == "chunk-1"
    assert chunk.score == pytest.approx(0.77)
    assert chunk.vector_score == pytest.approx(0.77)
    assert chunk.distance == pytest.approx(0.23)
    assert chunk.metadata["chunk_type"] == "facts"
    assert chunk.text.startswith("本院查明")
    assert chunk.source == "case_chunks_bge_m3_v1"
    assert chunk.retrieval_source == "chroma_vector"
