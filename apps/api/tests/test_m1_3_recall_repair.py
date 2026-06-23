from __future__ import annotations

from app.core.config import Settings
from app.query_processing.models import QueryPlan
from app.query_processing.service import QueryProcessingService
from app.rerank.service import FactSimilarityReranker
from app.retrieval.merge import merge_case_candidates
from app.retrieval.models import RetrievedChunk
from app.retrieval.service import (
    CONTROLLED_BM25_SUPPLEMENT_SOURCE,
    ORIGINAL_VECTOR_SOURCE,
    RECALL_ONLY_VECTOR_SOURCE,
    VectorRetrievalService,
)


class FakeEmbeddingClient:
    config = type(
        "Cfg",
        (),
        {
            "EMBEDDING_PROVIDER": "ollama",
            "EMBEDDING_MODEL": "fictional-model",
            "EMBEDDING_DIMENSION": 4,
            "EMBEDDING_DISTANCE_METRIC": "cosine",
        },
    )()

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(index + 1)] * 4 for index, _text in enumerate(texts)]


class FixedVectorStore:
    def __init__(self, *, case_count: int = 40) -> None:
        self.case_count = case_count
        self.calls: list[dict[str, object]] = []

    def query(self, embedding: list[float], *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "embedding_marker": embedding[0],
                "top_k": top_k,
                "retrieval_source": retrieval_source,
            }
        )
        if retrieval_source == RECALL_ONLY_VECTOR_SOURCE:
            return [_chunk("recall-only-case", 0.91, retrieval_source)]
        return [
            _chunk(
                f"vector-case-{index:02d}",
                0.90 - index * 0.005,
                retrieval_source,
            )
            for index in range(self.case_count)
        ]


class ControlledFallbackRetriever:
    def __init__(self, *, overlap: int = 4) -> None:
        self.overlap = overlap
        self.calls: list[dict[str, object]] = []

    def search(self, query_text: str, *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "query_text": query_text,
                "top_k": top_k,
                "retrieval_source": retrieval_source,
            }
        )
        rows = [
            _chunk(f"vector-case-{index:02d}", 1.0 - index * 0.01, retrieval_source)
            for index in range(self.overlap)
        ]
        rows.extend(
            _chunk(case_id, 0.94 - index * 0.01, retrieval_source)
            for index, case_id in enumerate(
                [
                    "supplement-case-1",
                    "target-fictional-case",
                    "supplement-case-3",
                    "supplement-case-4",
                    "supplement-case-5",
                    "supplement-case-6",
                ]
            )
        )
        return rows[:top_k]


def _chunk(case_id: str, score: float, retrieval_source: str) -> RetrievedChunk:
    return RetrievedChunk(
        case_id=case_id,
        chunk_id=f"{case_id}-chunk",
        score=score,
        vector_score=score,
        distance=1.0 - score,
        metadata={
            "case_id": case_id,
            "chunk_id": f"{case_id}-chunk",
            "chunk_type": "fictional_fact",
        },
        text="fictional sanitized fact",
        source="fictional-test-source",
        retrieval_source=retrieval_source,
    )


def _plan(
    *,
    local_mapping_used: bool = False,
    recall_only_query_variants: list[str] | None = None,
) -> QueryPlan:
    cleaned_query = "fictional sanitized dispute facts"
    recall_only = recall_only_query_variants or []
    return QueryPlan(
        cleaned_query=cleaned_query,
        input_hash="fictional-input-hash",
        queries=[cleaned_query, *recall_only],
        recall_only_query_variants=recall_only,
        local_mapping_used=local_mapping_used,
    )


def _service(
    *,
    case_count: int = 40,
    overlap: int = 4,
    enable_targeted_recall_repairs: bool = True,
) -> tuple[VectorRetrievalService, FakeEmbeddingClient, FixedVectorStore, ControlledFallbackRetriever]:
    embedding = FakeEmbeddingClient()
    vector_store = FixedVectorStore(case_count=case_count)
    fallback = ControlledFallbackRetriever(overlap=overlap)
    service = VectorRetrievalService(
        embedding_client=embedding,
        vector_store=vector_store,
        fallback_retriever=fallback,
        embedding_cache=None,
        enable_targeted_recall_repairs=enable_targeted_recall_repairs,
    )
    return service, embedding, vector_store, fallback


def test_targeted_mapping_modes_add_recall_only_variants_without_weight_signals():
    service = QueryProcessingService(
        config=Settings(ENABLE_QUERY_REWRITE=False)
    )

    cause_plus_expansion = service.process("虚构主体持刀暴力抢钱")
    legal_term_only = service.process("虚构主体非法持有毒品甲基苯丙胺")
    unrelated = service.process("虚构主体入室偷东西")

    assert cause_plus_expansion.recall_only_query_variants
    assert legal_term_only.recall_only_query_variants
    assert unrelated.recall_only_query_variants == []
    for plan in (cause_plus_expansion, legal_term_only):
        assert plan.legal_elements == []
        assert plan.case_cause_hint == ""
        assert plan.confidence is None


def test_recall_only_variant_uses_separate_vector_source_and_not_rerank_features():
    service, embedding, vector_store, fallback = _service()
    plan = _plan(
        local_mapping_used=True,
        recall_only_query_variants=["fictional recall-only legal phrase"],
    )

    result = service.retrieve(plan)
    recall_candidate = next(
        candidate
        for candidate in result.candidates
        if candidate.retrieval_source == RECALL_ONLY_VECTOR_SOURCE
    )
    merged = merge_case_candidates(result.candidates)
    ranked = FactSimilarityReranker(
        config=Settings(ENABLE_WEIGHTED_RERANK=True),
        enabled=True,
    ).rerank(plan, merged)
    recall_ranked = next(
        item for item in ranked if item.candidate.case_id == recall_candidate.case_id
    )

    assert embedding.calls == [[plan.cleaned_query, *plan.recall_only_query_variants]]
    assert [call["retrieval_source"] for call in vector_store.calls] == [
        ORIGINAL_VECTOR_SOURCE,
        RECALL_ONLY_VECTOR_SOURCE,
    ]
    assert fallback.calls == []
    assert recall_candidate.matched_by_vector is True
    assert recall_candidate.matched_by_rewrite is True
    assert recall_candidate.recall_stage == "recall_only_mapping_vector"
    assert recall_ranked.score_breakdown["legal_element_overlap"] == 0.0
    assert recall_ranked.score_breakdown["case_cause_match"] == 0.0


def test_targeted_repairs_can_be_disabled_for_same_runner_baseline():
    service, embedding, vector_store, fallback = _service(
        enable_targeted_recall_repairs=False
    )
    plan = _plan(
        local_mapping_used=True,
        recall_only_query_variants=["fictional recall-only legal phrase"],
    )

    result = service.retrieve(plan)

    assert embedding.calls == [[plan.cleaned_query]]
    assert [call["retrieval_source"] for call in vector_store.calls] == [
        ORIGINAL_VECTOR_SOURCE
    ]
    assert fallback.calls == []
    assert {
        candidate.retrieval_source for candidate in result.candidates
    } == {ORIGINAL_VECTOR_SOURCE}


def test_controlled_bm25_supplement_admits_four_cases_after_vector_top5():
    service, _embedding, _vector_store, fallback = _service()

    result = service.retrieve(_plan())
    merged = merge_case_candidates(result.candidates)
    supplement_candidates = [
        candidate
        for candidate in result.candidates
        if candidate.retrieval_source == CONTROLLED_BM25_SUPPLEMENT_SOURCE
    ]
    target_rank = next(
        index
        for index, candidate in enumerate(merged, 1)
        if candidate.case_id == "target-fictional-case"
    )

    assert len(supplement_candidates) == 4
    assert len(merged) == 44
    assert target_rank == 7
    assert all(candidate.matched_by_bm25 for candidate in supplement_candidates)
    assert all(not candidate.matched_by_vector for candidate in supplement_candidates)
    assert len(fallback.calls) == 1


def test_controlled_bm25_supplement_stays_off_outside_narrow_boundaries():
    scenarios = [
        {"case_count": 41, "overlap": 4, "local_mapping_used": False},
        {"case_count": 40, "overlap": 5, "local_mapping_used": False},
        {"case_count": 40, "overlap": 4, "local_mapping_used": True},
    ]

    for scenario in scenarios:
        service, _embedding, _vector_store, _fallback = _service(
            case_count=scenario["case_count"],
            overlap=scenario["overlap"],
        )
        result = service.retrieve(
            _plan(local_mapping_used=scenario["local_mapping_used"])
        )

        assert all(
            candidate.retrieval_source != CONTROLLED_BM25_SUPPLEMENT_SOURCE
            for candidate in result.candidates
        )
        assert len(merge_case_candidates(result.candidates)) == scenario["case_count"]
