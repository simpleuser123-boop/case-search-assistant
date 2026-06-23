"""Chroma collection adapter for Day 1 vector recall."""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from app.core.config import Settings, settings
from app.kernel.rag.retrieval.embedding import (
    EXPECTED_DIMENSION,
    EXPECTED_DISTANCE_METRIC,
    EXPECTED_MODEL,
    EXPECTED_PROVIDER,
)
from app.kernel.rag.retrieval.models import (
    ChromaProbeResult,
    RetrievalConfigMismatchError,
    RetrievalDependencyError,
    RetrievedChunk,
)


ClientFactory = Callable[[str], Any]


class ChromaCollectionAdapter:
    def __init__(
        self,
        *,
        config: Settings = settings,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or self._default_client_factory

    @staticmethod
    def _default_client_factory(path: str) -> Any:
        import chromadb

        return chromadb.PersistentClient(path=path)

    def get_collection(self) -> Any:
        try:
            client = self._client_factory(self.config.CHROMA_PERSIST_DIR)
            return client.get_collection(self.config.CHROMA_COLLECTION)
        except RetrievalDependencyError:
            raise
        except Exception as exc:  # noqa: BLE001 - external adapter sanitizes details
            raise RetrievalDependencyError("CHROMA_UNAVAILABLE", "Chroma collection is unavailable.") from exc

    def count(self) -> int:
        collection = self.get_collection()
        self._validate_collection_metadata(collection)
        return int(collection.count())

    def query(self, embedding: list[float], *, top_k: int, retrieval_source: str = "chroma_vector") -> list[RetrievedChunk]:
        self._validate_query_embedding(embedding)
        collection = self.get_collection()
        self._validate_collection_metadata(collection)

        def _do_query() -> dict[str, Any]:
            return collection.query(
                query_embeddings=[embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )

        raw = self._run_with_timeout(_do_query)
        return self._parse_query_result(raw, retrieval_source=retrieval_source)

    def probe(self) -> ChromaProbeResult:
        try:
            collection = self.get_collection()
            self._validate_collection_metadata(collection)
            chunk_count = int(collection.count())
            queryable = False
            if chunk_count > 0:
                sample = collection.get(limit=1, include=["embeddings"])
                embeddings = sample.get("embeddings")
                if embeddings is not None and len(embeddings) > 0:
                    first_embedding = embeddings[0]
                    if hasattr(first_embedding, "tolist"):
                        first_embedding = first_embedding.tolist()
                    self._validate_query_embedding(first_embedding)
                    collection.query(
                        query_embeddings=[first_embedding],
                        n_results=1,
                        include=["metadatas"],
                    )
                    queryable = True
            return ChromaProbeResult(
                collection=self.config.CHROMA_COLLECTION,
                persist_dir=self.config.CHROMA_PERSIST_DIR,
                queryable=queryable,
                chunk_count=chunk_count,
                metadata_valid=True,
                degraded_reason=None if queryable else "collection_empty_or_not_queryable",
                metadata=dict(collection.metadata or {}),
            )
        except RetrievalDependencyError as exc:
            return ChromaProbeResult(
                collection=self.config.CHROMA_COLLECTION,
                persist_dir=self.config.CHROMA_PERSIST_DIR,
                queryable=False,
                chunk_count=0,
                metadata_valid=False,
                degraded_reason=exc.code,
            )
        except Exception as exc:  # noqa: BLE001 - health probe must not fail caller
            return ChromaProbeResult(
                collection=self.config.CHROMA_COLLECTION,
                persist_dir=self.config.CHROMA_PERSIST_DIR,
                queryable=False,
                chunk_count=0,
                metadata_valid=False,
                degraded_reason=exc.__class__.__name__,
            )

    def _run_with_timeout(self, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        timeout_seconds = max(float(self.config.CHROMA_QUERY_TIMEOUT_SECONDS), 0.1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func)
            try:
                return future.result(timeout=timeout_seconds)
            except FutureTimeoutError as exc:
                raise RetrievalDependencyError("CHROMA_QUERY_TIMEOUT", "Chroma query timed out.") from exc
            except RetrievalDependencyError:
                raise
            except Exception as exc:  # noqa: BLE001 - external adapter sanitizes details
                raise RetrievalDependencyError("CHROMA_QUERY_FAILED", "Chroma query failed.") from exc

    def _validate_query_embedding(self, embedding: list[float]) -> None:
        if len(embedding) != self.config.EMBEDDING_DIMENSION:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DIMENSION_MISMATCH",
                (
                    "Query embedding dimension does not match configured "
                    f"dimension {self.config.EMBEDDING_DIMENSION}."
                ),
            )

    def _validate_collection_metadata(self, collection: Any) -> None:
        metadata = dict(collection.metadata or {})
        self._validate_runtime_config()

        provider = str(metadata.get("embedding_provider") or "").strip().lower()
        model = str(metadata.get("model_name") or metadata.get("embedding_model") or "").strip()
        dimension_raw = metadata.get("vector_dimension") or metadata.get("dimension")
        distance = str(metadata.get("distance_metric") or metadata.get("hnsw:space") or "").strip().lower()

        if provider != self.config.EMBEDDING_PROVIDER.strip().lower():
            raise RetrievalConfigMismatchError(
                "EMBEDDING_PROVIDER_MISMATCH",
                "Chroma collection embedding provider does not match configured provider.",
            )
        if model != self.config.EMBEDDING_MODEL.strip():
            raise RetrievalConfigMismatchError(
                "EMBEDDING_MODEL_MISMATCH",
                "Chroma collection embedding model does not match configured model.",
            )
        try:
            dimension = int(dimension_raw)
        except (TypeError, ValueError) as exc:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DIMENSION_MISMATCH",
                "Chroma collection embedding dimension metadata is missing or invalid.",
            ) from exc
        if dimension != self.config.EMBEDDING_DIMENSION:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DIMENSION_MISMATCH",
                "Chroma collection embedding dimension does not match configured dimension.",
            )
        if distance != self.config.EMBEDDING_DISTANCE_METRIC.strip().lower():
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DISTANCE_MISMATCH",
                "Chroma collection distance metric does not match configured distance metric.",
            )

    def _validate_runtime_config(self) -> None:
        provider = self.config.EMBEDDING_PROVIDER.strip().lower()
        model = self.config.EMBEDDING_MODEL.strip()
        dimension = int(self.config.EMBEDDING_DIMENSION)
        distance = self.config.EMBEDDING_DISTANCE_METRIC.strip().lower()

        if provider != EXPECTED_PROVIDER:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_PROVIDER_MISMATCH",
                f"Expected embedding provider {EXPECTED_PROVIDER}.",
            )
        if model != EXPECTED_MODEL:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_MODEL_MISMATCH",
                f"Expected embedding model {EXPECTED_MODEL}.",
            )
        if dimension != EXPECTED_DIMENSION:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DIMENSION_MISMATCH",
                f"Expected embedding dimension {EXPECTED_DIMENSION}.",
            )
        if distance != EXPECTED_DISTANCE_METRIC:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DISTANCE_MISMATCH",
                f"Expected embedding distance metric {EXPECTED_DISTANCE_METRIC}.",
            )

    def _parse_query_result(self, raw: dict[str, Any], *, retrieval_source: str) -> list[RetrievedChunk]:
        ids = _first(raw.get("ids"))
        documents = _first(raw.get("documents"))
        metadatas = _first(raw.get("metadatas"))
        distances = _first(raw.get("distances"))

        chunks: list[RetrievedChunk] = []
        for index, raw_id in enumerate(ids):
            metadata = _dict_at(metadatas, index)
            text = _str_at(documents, index)
            distance = _float_at(distances, index)
            vector_score = _score_from_distance(distance)
            chunk_id = str(metadata.get("chunk_id") or raw_id or "")
            case_id = str(metadata.get("case_id") or "")
            chunks.append(
                RetrievedChunk(
                    case_id=case_id,
                    chunk_id=chunk_id,
                    score=vector_score,
                    vector_score=vector_score,
                    distance=distance,
                    metadata=metadata,
                    text=text,
                    source=self.config.CHROMA_COLLECTION,
                    retrieval_source=retrieval_source,
                )
            )
        return chunks


def _first(value: Any) -> list[Any]:
    if not value:
        return []
    return list(value[0] or [])


def _dict_at(values: list[Any], index: int) -> dict[str, Any]:
    if index >= len(values) or not isinstance(values[index], dict):
        return {}
    return dict(values[index])


def _str_at(values: list[Any], index: int) -> str:
    if index >= len(values) or values[index] is None:
        return ""
    return str(values[index])


def _float_at(values: list[Any], index: int) -> float | None:
    if index >= len(values) or values[index] is None:
        return None
    return float(values[index])


def _score_from_distance(distance: float | None) -> float:
    if distance is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(distance)))
