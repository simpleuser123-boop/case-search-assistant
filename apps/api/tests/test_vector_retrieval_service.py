from __future__ import annotations

import json
import logging

import pytest

from app.query_processing.models import QueryPlan
from app.retrieval.bm25_fallback import (
    BM25_FALLBACK_SOURCE,
    BM25FallbackRetriever,
    warmup_bm25_fallback,
)
from app.retrieval.embedding_cache import QueryEmbeddingCache
from app.retrieval.models import RetrievalConfigMismatchError, RetrievalDependencyError, RetrievedChunk
from app.retrieval.service import (
    BM25_FALLBACK_FAILED,
    BM25_FALLBACK_USED,
    CHROMA_EMPTY,
    CHROMA_QUERY_FAILED,
    CHROMA_QUERY_TIMEOUT,
    CHROMA_UNAVAILABLE,
    EMBEDDING_MODEL_MISMATCH,
    EMBEDDING_TIMEOUT,
    ORIGINAL_VECTOR_SOURCE,
    ORIGINAL_VECTOR_TOP_K,
    VARIANT_VECTOR_SOURCE,
    VARIANT_VECTOR_TOP_K,
    VectorRetrievalService,
)


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.config = type(
            "Cfg",
            (),
            {
                "EMBEDDING_PROVIDER": "ollama",
                "EMBEDDING_MODEL": "bge-m3",
                "EMBEDDING_DIMENSION": 1024,
                "EMBEDDING_DISTANCE_METRIC": "cosine",
            },
        )()

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index + 1)] * 1024 for index, _ in enumerate(texts)]


class FakeVectorStore:
    def __init__(self, responses: list[list[RetrievedChunk]] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict[str, object]] = []

    def query(self, embedding: list[float], *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "embedding_marker": embedding[0],
                "top_k": top_k,
                "retrieval_source": retrieval_source,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return [
            _chunk(
                case_id=f"{retrieval_source}-case-{index}",
                chunk_id=f"{retrieval_source}-chunk-{index}",
                retrieval_source=retrieval_source,
            )
            for index in range(top_k)
        ]


class FailingEmbeddingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[list[str]] = []

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        raise self.error


class FailingVectorStore:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    def query(self, embedding: list[float], *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        self.calls.append({"top_k": top_k, "retrieval_source": retrieval_source})
        raise self.error


class FakeFallbackRetriever:
    def __init__(
        self,
        *,
        fallback_count: int = 6,
        relaxed_count: int = 6,
    ) -> None:
        self.fallback_count = fallback_count
        self.relaxed_count = relaxed_count
        self.calls: list[dict[str, object]] = []

    def search(self, query_text: str, *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "query_text": query_text,
                "top_k": top_k,
                "retrieval_source": retrieval_source,
            }
        )
        count = self.relaxed_count if "relaxed_recall" in retrieval_source else self.fallback_count
        return [
            _chunk(
                case_id=f"{retrieval_source}-case-{index}",
                chunk_id=f"{retrieval_source}-chunk-{index}",
                retrieval_source=retrieval_source,
                vector_score=max(0.1, 1.0 - index * 0.05),
            )
            for index in range(min(top_k, count))
        ]


class FailingFallbackRetriever:
    def search(self, query_text: str, *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        raise RuntimeError("bm25 unavailable")


def _plan(
    *,
    cleaned_query: str = "2019年夜间进入店铺盗窃现金5000元",
    query_variants: list[str] | None = None,
    case_cause_hint: str = "盗窃罪",
    legal_elements: list[str] | None = None,
) -> QueryPlan:
    return QueryPlan(
        cleaned_query=cleaned_query,
        input_hash="hash-for-test",
        queries=[cleaned_query, *(query_variants or [])],
        legal_elements=legal_elements or [],
        query_variants=query_variants or [],
        case_cause_hint=case_cause_hint,
    )


def _chunk(
    *,
    case_id: str = "case-1",
    chunk_id: str = "chunk-1",
    retrieval_source: str = ORIGINAL_VECTOR_SOURCE,
    metadata: dict | None = None,
    text: str = "普通事实段落",
    vector_score: float = 0.5,
) -> RetrievedChunk:
    return RetrievedChunk(
        case_id=case_id,
        chunk_id=chunk_id,
        score=vector_score,
        vector_score=vector_score,
        distance=1.0 - vector_score,
        metadata=metadata or {"case_id": case_id, "chunk_id": chunk_id, "chunk_type": "fact"},
        text=text,
        source="case_chunks_bge_m3_v1",
        retrieval_source=retrieval_source,
    )


def test_bm25_fallback_reads_processed_jsonl_and_marks_source(tmp_path):
    cases_path = tmp_path / "cases.jsonl"
    chunks_path = tmp_path / "chunks.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "case-bm25",
                "title": "盗窃罪测试判决书",
                "court": "测试法院",
                "case_cause": "盗窃罪",
                "judgment_date": "2019-01-02",
                "source_name": "JuDGE",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    chunks_path.write_text(
        json.dumps(
            {
                "case_id": "case-bm25",
                "chunk_id": "case-bm25-c1",
                "chunk_type": "fact",
                "text": "被告人夜间进入店铺盗窃现金5000元。",
                "quality_score": 0.9,
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "case_id": "case-other",
                "chunk_id": "case-other-c1",
                "chunk_type": "fact",
                "text": "被告人危险驾驶造成交通事故。",
                "quality_score": 0.8,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    retriever = BM25FallbackRetriever(cases_path=cases_path, chunks_path=chunks_path)

    chunks = retriever.search("夜间入店盗窃现金", top_k=2)

    assert chunks[0].case_id == "case-bm25"
    assert chunks[0].chunk_id == "case-bm25-c1"
    assert chunks[0].retrieval_source == BM25_FALLBACK_SOURCE
    assert chunks[0].metadata["case_cause"] == "盗窃罪"


def test_bm25_warmup_prepares_read_only_index_with_fictional_fixture(tmp_path):
    cases_path = tmp_path / "fictional-cases.jsonl"
    chunks_path = tmp_path / "fictional-chunks.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "fictional-case",
                "title": "fictional title",
                "case_cause": "fictional cause",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    chunks_path.write_text(
        json.dumps(
            {
                "case_id": "fictional-case",
                "chunk_id": "fictional-chunk",
                "chunk_type": "fictional",
                "text": "fictional alpha evidence",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = warmup_bm25_fallback(
        cases_path=cases_path,
        chunks_path=chunks_path,
    )

    assert result.ok is True
    assert result.document_count == 1
    assert result.degraded_reason is None


def test_original_query_uses_top50_and_variants_use_top30():
    embedding_client = FakeEmbeddingClient()
    vector_store = FakeVectorStore()
    service = VectorRetrievalService(embedding_client=embedding_client, vector_store=vector_store)
    plan = _plan(query_variants=["盗窃现金5000元相似事实", "夜间入店盗窃类案"])

    result = service.retrieve(plan)

    assert embedding_client.calls == [[plan.cleaned_query, *plan.query_variants]]
    assert vector_store.calls == [
        {"embedding_marker": 1.0, "top_k": ORIGINAL_VECTOR_TOP_K, "retrieval_source": ORIGINAL_VECTOR_SOURCE},
        {"embedding_marker": 2.0, "top_k": VARIANT_VECTOR_TOP_K, "retrieval_source": VARIANT_VECTOR_SOURCE},
        {"embedding_marker": 3.0, "top_k": VARIANT_VECTOR_TOP_K, "retrieval_source": VARIANT_VECTOR_SOURCE},
    ]
    assert len(result.candidates) == 110


def test_query_embedding_cache_skips_second_embedding_call():
    embedding_client = FakeEmbeddingClient()
    vector_store = FakeVectorStore()
    cache = QueryEmbeddingCache(ttl_seconds=60, max_entries=8)
    service = VectorRetrievalService(
        embedding_client=embedding_client,
        vector_store=vector_store,
        embedding_cache=cache,
    )
    plan = _plan(query_variants=[])

    service.retrieve(plan)
    service.retrieve(plan)

    assert embedding_client.calls == [[plan.cleaned_query]]
    assert len(vector_store.calls) == 2


def test_retrieval_source_and_candidate_shape_are_preserved():
    service = VectorRetrievalService(embedding_client=FakeEmbeddingClient(), vector_store=FakeVectorStore())
    result = service.retrieve(_plan(query_variants=["盗窃现金5000元相似事实"]))

    source_counts = {
        ORIGINAL_VECTOR_SOURCE: sum(1 for item in result.candidates if item.retrieval_source == ORIGINAL_VECTOR_SOURCE),
        VARIANT_VECTOR_SOURCE: sum(1 for item in result.candidates if item.retrieval_source == VARIANT_VECTOR_SOURCE),
    }
    assert source_counts == {ORIGINAL_VECTOR_SOURCE: 50, VARIANT_VECTOR_SOURCE: 30}
    candidate = result.candidates[0]
    assert candidate.case_id
    assert candidate.chunk_id
    assert candidate.vector_score is not None
    assert candidate.metadata["chunk_id"] == candidate.chunk_id
    assert candidate.matched_text == "普通事实段落"
    assert candidate.source == "case_chunks_bge_m3_v1"
    assert isinstance(result.retrieval_duration_ms, int)
    assert result.retrieval_duration_ms >= 0


def test_soft_filter_adds_conservative_score_without_hard_cutting_candidates():
    matching = _chunk(
        case_id="match",
        chunk_id="match-c1",
        metadata={
            "case_id": "match",
            "chunk_id": "match-c1",
            "case_cause": "盗窃罪",
            "judgment_year": 2019,
            "chunk_type": "court_found",
        },
        text="本院查明,被告人夜间进入店铺盗窃现金。",
        vector_score=0.40,
    )
    non_matching = _chunk(
        case_id="miss",
        chunk_id="miss-c1",
        metadata={
            "case_id": "miss",
            "chunk_id": "miss-c1",
            "case_cause": "诈骗罪",
            "judgment_year": 2008,
            "chunk_type": "fact",
        },
        vector_score=0.39,
    )
    service = VectorRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        vector_store=FakeVectorStore(responses=[[matching, non_matching]]),
        min_candidates_before_relaxed_recall=1,
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert {item.chunk_id for item in result.candidates} == {"match-c1", "miss-c1"}
    boosted = next(item for item in result.candidates if item.chunk_id == "match-c1")
    assert boosted.soft_filter_breakdown == {
        "case_cause_match": pytest.approx(0.03),
        "year_match": pytest.approx(0.02),
        "key_paragraph_match": pytest.approx(0.02),
    }
    assert boosted.retrieval_score > boosted.vector_score
    unboosted = next(item for item in result.candidates if item.chunk_id == "miss-c1")
    assert unboosted.soft_filter_breakdown == {}


def test_missing_case_cause_hint_does_not_force_case_cause_weighting():
    candidate = _chunk(
        metadata={
            "case_id": "case-1",
            "chunk_id": "chunk-1",
            "case_cause": "盗窃罪",
            "chunk_type": "fact",
        }
    )
    service = VectorRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        vector_store=FakeVectorStore(responses=[[candidate]]),
        min_candidates_before_relaxed_recall=1,
    )

    result = service.retrieve(_plan(case_cause_hint="", cleaned_query="夜间进入店铺盗窃现金5000元"))

    assert result.candidates[0].soft_filter_breakdown == {}
    assert result.candidates[0].soft_filter_score == 0.0


def test_retrieval_log_records_duration_without_raw_query(caplog):
    raw_query = "不得进入日志的原始案情XYZ,2019年夜间盗窃现金5000元"
    variant = "不得进入日志的改写案情XYZ,夜间盗窃现金类案"
    service = VectorRetrievalService(embedding_client=FakeEmbeddingClient(), vector_store=FakeVectorStore())
    caplog.set_level(logging.INFO, logger="case_search")

    service.retrieve(_plan(cleaned_query=raw_query, query_variants=[variant]))

    assert raw_query not in caplog.text
    assert variant not in caplog.text
    assert "hash-for-test" in caplog.text
    assert "retrieval_duration_ms" in caplog.text


def test_chroma_exception_triggers_bm25_fallback():
    fallback = FakeFallbackRetriever(fallback_count=6)
    service = VectorRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        vector_store=FailingVectorStore(RetrievalDependencyError("CHROMA_QUERY_FAILED", "query failed")),
        fallback_retriever=fallback,
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert result.degraded is True
    assert result.degraded_reasons == [CHROMA_QUERY_FAILED, BM25_FALLBACK_USED]
    assert fallback.calls[0]["retrieval_source"] == BM25_FALLBACK_SOURCE
    assert all(candidate.retrieval_source == BM25_FALLBACK_SOURCE for candidate in result.candidates)


def test_chroma_unavailable_reason_is_preserved():
    fallback = FakeFallbackRetriever(fallback_count=6)
    service = VectorRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        vector_store=FailingVectorStore(RetrievalDependencyError("CHROMA_UNAVAILABLE", "offline")),
        fallback_retriever=fallback,
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert result.degraded_reasons == [CHROMA_UNAVAILABLE, BM25_FALLBACK_USED]
    assert all(candidate.retrieval_source == BM25_FALLBACK_SOURCE for candidate in result.candidates)


def test_chroma_timeout_reason_is_preserved():
    fallback = FakeFallbackRetriever(fallback_count=6)
    service = VectorRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        vector_store=FailingVectorStore(RetrievalDependencyError("CHROMA_QUERY_TIMEOUT", "timeout")),
        fallback_retriever=fallback,
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert result.degraded_reasons == [CHROMA_QUERY_TIMEOUT, BM25_FALLBACK_USED]


def test_embedding_dimension_mismatch_triggers_bm25_fallback():
    fallback = FakeFallbackRetriever(fallback_count=6)
    service = VectorRetrievalService(
        embedding_client=FailingEmbeddingClient(
            RetrievalConfigMismatchError("EMBEDDING_DIMENSION_MISMATCH", "dimension mismatch")
        ),
        vector_store=FakeVectorStore(),
        fallback_retriever=fallback,
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert result.degraded is True
    assert result.degraded_reasons == [EMBEDDING_MODEL_MISMATCH, BM25_FALLBACK_USED]
    assert fallback.calls[0]["retrieval_source"] == BM25_FALLBACK_SOURCE
    assert all(candidate.retrieval_source == BM25_FALLBACK_SOURCE for candidate in result.candidates)


def test_chroma_empty_collection_triggers_bm25_fallback():
    fallback = FakeFallbackRetriever(fallback_count=6)
    service = VectorRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        vector_store=FakeVectorStore(responses=[[]]),
        fallback_retriever=fallback,
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert result.degraded is True
    assert result.degraded_reasons == [CHROMA_EMPTY, BM25_FALLBACK_USED]
    assert fallback.calls[0]["retrieval_source"] == BM25_FALLBACK_SOURCE
    assert all(candidate.retrieval_source == BM25_FALLBACK_SOURCE for candidate in result.candidates)


def test_candidate_shortage_triggers_relaxed_recall():
    fallback = FakeFallbackRetriever(fallback_count=1, relaxed_count=4)
    service = VectorRetrievalService(
        embedding_client=FakeEmbeddingClient(),
        vector_store=FakeVectorStore(
            responses=[
                [_chunk(case_id="case-1", chunk_id="vector-c1", retrieval_source=ORIGINAL_VECTOR_SOURCE, vector_score=0.81)]
            ]
        ),
        fallback_retriever=fallback,
    )

    result = service.retrieve(_plan(query_variants=[]))

    relaxed_candidates = [candidate for candidate in result.candidates if "relaxed_recall" in candidate.retrieval_source]
    assert relaxed_candidates
    assert result.degraded is True
    assert result.degraded_reasons == [BM25_FALLBACK_USED]
    assert fallback.calls == [
        {
            "query_text": "2019年夜间进入店铺盗窃现金5000元 盗窃罪",
            "top_k": 30,
            "retrieval_source": "bm25_fallback_relaxed_recall",
        }
    ]


def test_degraded_log_still_does_not_include_raw_query(caplog):
    raw_query = "不得进入日志的原始案情XYZ,被告人夜间入店盗窃现金5000元"
    variant = "不得进入日志的改写案情XYZ,盗窃相似事实"
    fallback = FakeFallbackRetriever(fallback_count=6)
    service = VectorRetrievalService(
        embedding_client=FailingEmbeddingClient(RetrievalDependencyError("EMBEDDING_UNAVAILABLE", "offline")),
        vector_store=FakeVectorStore(),
        fallback_retriever=fallback,
    )
    caplog.set_level(logging.INFO, logger="case_search")

    result = service.retrieve(_plan(cleaned_query=raw_query, query_variants=[variant]))

    assert result.degraded_reasons == ["EMBEDDING_UNAVAILABLE", BM25_FALLBACK_USED]
    assert raw_query not in caplog.text
    assert variant not in caplog.text
    assert "hash-for-test" in caplog.text
    assert "degraded_reasons" in caplog.text


def test_embedding_timeout_triggers_bm25_fallback():
    fallback = FakeFallbackRetriever(fallback_count=6)
    service = VectorRetrievalService(
        embedding_client=FailingEmbeddingClient(RetrievalDependencyError("EMBEDDING_TIMEOUT", "timed out")),
        vector_store=FakeVectorStore(),
        fallback_retriever=fallback,
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert result.degraded_reasons == [EMBEDDING_TIMEOUT, BM25_FALLBACK_USED]
    assert all(candidate.retrieval_source == BM25_FALLBACK_SOURCE for candidate in result.candidates)


def test_bm25_failure_returns_explicit_empty_degraded_result():
    service = VectorRetrievalService(
        embedding_client=FailingEmbeddingClient(RetrievalDependencyError("EMBEDDING_TIMEOUT", "timed out")),
        vector_store=FakeVectorStore(),
        fallback_retriever=FailingFallbackRetriever(),
    )

    result = service.retrieve(_plan(query_variants=[]))

    assert result.candidates == []
    assert result.degraded is True
    assert result.degraded_reasons == [EMBEDDING_TIMEOUT, BM25_FALLBACK_FAILED]
