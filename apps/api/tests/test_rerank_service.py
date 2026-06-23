from __future__ import annotations

import pytest

from app.query_processing.models import QueryPlan
from app.rerank import DEFAULT_RERANK_WEIGHTS, FactSimilarityReranker, RerankWeights
from app.retrieval.bm25_fallback import BM25_FALLBACK_SOURCE
from app.retrieval.models import CaseCandidate
from app.retrieval.service import ORIGINAL_VECTOR_SOURCE


def _plan(
    *,
    legal_elements: list[str] | None = None,
    case_cause_hint: str = "盗窃罪",
) -> QueryPlan:
    return QueryPlan(
        cleaned_query="夜间进入店铺盗窃现金5000元",
        input_hash="hash-for-test",
        queries=["夜间进入店铺盗窃现金5000元"],
        legal_elements=legal_elements if legal_elements is not None else ["夜间进入店铺", "现金5000元"],
        case_cause_hint=case_cause_hint,
    )


def _candidate(
    *,
    case_id: str = "case-1",
    vector_score: float | None = 0.8,
    fallback_score: float | None = None,
    retrieval_score: float | None = None,
    retrieval_source: list[str] | None = None,
    matched_by_vector: bool = True,
    matched_by_bm25: bool = False,
    matched_by_rewrite: bool = False,
    matched_text: str = "本院查明,被告人夜间进入店铺盗窃现金5000元。",
    metadata: dict | None = None,
) -> CaseCandidate:
    score = retrieval_score
    if score is None:
        score = vector_score if vector_score is not None else fallback_score
    return CaseCandidate(
        case_id=case_id,
        top_chunk_id=f"{case_id}-chunk-1",
        source_chunk_ids=[f"{case_id}-chunk-1"],
        hit_chunk_ids=[f"{case_id}-chunk-1"],
        retrieval_source=retrieval_source or [ORIGINAL_VECTOR_SOURCE],
        metadata={
            "title": f"{case_id}刑事判决书",
            "court": "测试高级人民法院",
            "court_level": "高级法院",
            "trial_level": "二审",
            "case_cause": "盗窃罪",
            "judgment_date": "2021-05-01",
            "chunk_type": "本院查明",
        }
        if metadata is None
        else metadata,
        matched_text=matched_text,
        source="unit-test",
        vector_score=vector_score,
        fallback_score=fallback_score,
        top_chunk_score=score or 0.0,
        retrieval_score=score or 0.0,
        matched_by_vector=matched_by_vector,
        matched_by_bm25=matched_by_bm25,
        matched_by_rewrite=matched_by_rewrite,
    )


def test_final_score_uses_configured_weights():
    reranker = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS)

    ranked = reranker.rerank(_plan(), [_candidate(vector_score=0.8)])[0]

    assert ranked.final_score == pytest.approx(0.88)
    breakdown = ranked.score_breakdown
    assert breakdown["vector_similarity"] == 0.8
    assert breakdown["legal_element_overlap"] == 1.0
    assert breakdown["case_cause_match"] == 1.0
    assert breakdown["key_paragraph_match"] == 1.0
    assert breakdown["authority_signal"] == 0.8
    assert breakdown["score_mode"] == "weighted_rerank"


def test_score_breakdown_contains_required_components():
    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        _plan(),
        [_candidate()],
    )[0]

    assert {
        "vector_similarity",
        "legal_element_overlap",
        "case_cause_match",
        "key_paragraph_match",
        "authority_signal",
    }.issubset(ranked.score_breakdown)


def test_disabled_weighted_rerank_returns_to_base_retrieval_order():
    candidates = [
        _candidate(case_id="lower-base", vector_score=0.5),
        _candidate(
            case_id="higher-base",
            vector_score=0.9,
            matched_text="普通段落,事实命中较少。",
            metadata={"case_cause": "合同纠纷", "court": "基层人民法院"},
        ),
    ]

    ranked = FactSimilarityReranker(enabled=False, weights=DEFAULT_RERANK_WEIGHTS).rerank(_plan(), candidates)

    assert [item.candidate.case_id for item in ranked] == ["higher-base", "lower-base"]
    assert ranked[0].final_score == 0.9
    assert ranked[0].score_breakdown["score_mode"] == "base_retrieval"
    assert ranked[0].score_breakdown["weighted_rerank_enabled"] is False


def test_invalid_weight_config_falls_back_to_defaults_with_clear_breakdown():
    invalid_weights = RerankWeights(
        vector_similarity=0.9,
        legal_element_overlap=0.2,
        case_cause_match=0.1,
        key_paragraph_match=0.1,
        authority_signal=0.1,
    )

    ranked = FactSimilarityReranker(enabled=True, weights=invalid_weights).rerank(_plan(), [_candidate()])[0]

    assert ranked.score_breakdown["weight_config_valid"] is False
    assert ranked.score_breakdown["weight_config_error"].startswith("invalid_weight_sum:")
    assert ranked.score_breakdown["weights"] == DEFAULT_RERANK_WEIGHTS.as_dict()


def test_missing_legal_elements_does_not_interrupt_ranking():
    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        _plan(legal_elements=[]),
        [_candidate()],
    )[0]

    assert ranked.score_breakdown["legal_element_overlap"] == 0.0
    assert ranked.final_score > 0


def test_missing_case_cause_hint_does_not_interrupt_ranking():
    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        _plan(case_cause_hint=""),
        [_candidate()],
    )[0]

    assert ranked.score_breakdown["case_cause_match"] == 0.0
    assert ranked.final_score > 0


def test_key_paragraph_match_without_fact_support_falls_back_to_base_score():
    plain = _candidate(
        case_id="plain",
        vector_score=0.5,
        matched_text="普通段落,被告人进入店铺盗窃。",
        metadata={},
    )
    key = _candidate(
        case_id="key",
        vector_score=0.5,
        matched_text="本院认为,被告人进入店铺盗窃。",
        metadata={},
    )

    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        _plan(legal_elements=[], case_cause_hint=""),
        [plain, key],
    )

    by_case = {item.candidate.case_id: item for item in ranked}
    assert by_case["key"].score_breakdown["key_paragraph_match"] == 1.0
    assert by_case["plain"].score_breakdown["key_paragraph_match"] == 0.0
    assert by_case["key"].score_breakdown["effective_key_paragraph_match"] == 0.0
    assert by_case["key"].score_breakdown["final_score_source"] == "base_retrieval_guard"
    assert by_case["key"].final_score == by_case["plain"].final_score


def test_authority_signal_cannot_overpower_fact_similarity():
    fact_match = _candidate(
        case_id="fact-match",
        vector_score=0.8,
        matched_text="被告人夜间进入店铺盗窃现金5000元。",
        metadata={"case_cause": "盗窃罪"},
    )
    authoritative_but_weak = _candidate(
        case_id="authority-only",
        vector_score=0.7,
        matched_text="合同履行争议。",
        metadata={
            "case_cause": "合同纠纷",
            "court_level": "最高人民法院",
            "trial_level": "再审",
            "judgment_date": "2024-01-01",
        },
    )

    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        _plan(),
        [authoritative_but_weak, fact_match],
    )

    assert ranked[0].candidate.case_id == "fact-match"
    assert ranked[1].score_breakdown["authority_signal"] > ranked[0].score_breakdown["authority_signal"]


def test_bm25_fallback_candidate_is_marked_as_fallback_similarity():
    fallback = _candidate(
        case_id="fallback",
        vector_score=None,
        fallback_score=0.86,
        retrieval_score=0.86,
        retrieval_source=[BM25_FALLBACK_SOURCE],
    )

    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(_plan(), [fallback])[0]

    assert ranked.score_breakdown["vector_similarity"] == 0.86
    assert ranked.score_breakdown["similarity_source"] == "fallback"
    assert ranked.score_breakdown["vector_score"] is None
    assert ranked.score_breakdown["fallback_score"] == 0.86


def test_key_paragraph_and_authority_do_not_raise_non_fact_candidate_over_fact_match():
    fact_match = _candidate(
        case_id="fact-match",
        vector_score=0.76,
        matched_text="被告人夜间进入店铺盗窃现金5000元。",
        metadata={"case_cause": "盗窃罪"},
    )
    weak_fact_with_signals = _candidate(
        case_id="weak-signals",
        vector_score=0.74,
        matched_text="本院认为，围绕其他争议进行说明。",
        metadata={
            "case_cause": "合同纠纷",
            "court_level": "高级人民法院",
            "trial_level": "二审",
            "judgment_date": "2023-01-01",
            "chunk_type": "本院认为",
        },
    )

    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        _plan(legal_elements=[], case_cause_hint=""),
        [weak_fact_with_signals, fact_match],
    )

    by_case = {item.candidate.case_id: item for item in ranked}
    assert ranked[0].candidate.case_id == "fact-match"
    assert by_case["weak-signals"].score_breakdown["key_paragraph_match"] == 1.0
    assert by_case["weak-signals"].score_breakdown["authority_signal"] > 0.0
    assert by_case["weak-signals"].score_breakdown["effective_key_paragraph_match"] == 0.0
    assert by_case["weak-signals"].score_breakdown["effective_authority_signal"] == 0.0
    assert by_case["weak-signals"].score_breakdown["final_score_source"] == "base_retrieval_guard"
    assert "key_paragraph_without_fact_support" in by_case["weak-signals"].score_breakdown["fusion_guards"]
    assert "authority_without_fact_support" in by_case["weak-signals"].score_breakdown["fusion_guards"]


def test_case_cause_only_signal_is_capped_when_vector_similarity_is_low():
    reranker = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS)

    ranked = reranker.rerank(
        _plan(legal_elements=[], case_cause_hint="盗窃罪"),
        [
            _candidate(
                case_id="case-cause-only",
                vector_score=0.62,
                matched_text="普通程序性段落。",
                metadata={"case_cause": "盗窃罪"},
            )
        ],
    )[0]

    assert ranked.score_breakdown["case_cause_match"] == 1.0
    assert ranked.score_breakdown["effective_case_cause_match"] == 0.25
    assert "case_cause_low_fact_similarity_cap" in ranked.score_breakdown["fusion_guards"]


def test_no_fact_guard_relaxes_only_for_strong_vector_or_source_consensus():
    reranker = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS)
    plan = _plan(legal_elements=[], case_cause_hint="")

    weak_single_source = _candidate(
        case_id="weak-single-source",
        vector_score=0.70,
        matched_text="本院认为,虚构事实片段。",
        retrieval_source=[ORIGINAL_VECTOR_SOURCE],
    )
    strong_single_source = _candidate(
        case_id="strong-single-source",
        vector_score=0.76,
        matched_text="本院认为,虚构事实片段。",
        retrieval_source=[ORIGINAL_VECTOR_SOURCE],
    )
    multi_source_boundary = _candidate(
        case_id="multi-source-boundary",
        vector_score=0.66,
        matched_text="本院认为,虚构事实片段。",
        retrieval_source=[ORIGINAL_VECTOR_SOURCE, "variant_vector"],
        matched_by_rewrite=True,
    )

    ranked = reranker.rerank(plan, [weak_single_source, strong_single_source, multi_source_boundary])
    by_case = {item.candidate.case_id: item for item in ranked}

    assert by_case["weak-single-source"].score_breakdown["final_score_source"] == "base_retrieval_guard"
    assert "no_fact_guard_relaxed_strong_vector" not in by_case["weak-single-source"].score_breakdown["fusion_guards"]
    assert by_case["strong-single-source"].score_breakdown["final_score_source"] == "guarded_vector_bucket"
    assert "no_fact_guard_relaxed_strong_vector" in by_case["strong-single-source"].score_breakdown["fusion_guards"]
    assert by_case["multi-source-boundary"].score_breakdown["final_score_source"] == "guarded_vector_bucket"
    assert "no_fact_guard_relaxed_multi_source" in by_case["multi-source-boundary"].score_breakdown["fusion_guards"]


def test_weak_signal_only_candidate_cannot_jump_across_vector_bucket():
    reranker = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS)
    plan = _plan(legal_elements=[], case_cause_hint="")
    higher_vector_plain = _candidate(
        case_id="higher-vector-plain",
        vector_score=0.76,
        matched_text="普通段落,虚构事实片段。",
        metadata={},
    )
    lower_vector_with_weak_signals = _candidate(
        case_id="lower-vector-weak-signals",
        vector_score=0.64,
        matched_text="本院认为,虚构事实片段。",
        metadata={
            "court_level": "最高人民法院",
            "trial_level": "再审",
            "judgment_date": "2024-01-01",
            "chunk_type": "本院认为",
        },
        retrieval_source=[ORIGINAL_VECTOR_SOURCE, "variant_vector"],
        matched_by_rewrite=True,
    )

    ranked = reranker.rerank(plan, [lower_vector_with_weak_signals, higher_vector_plain])

    assert ranked[0].candidate.case_id == "higher-vector-plain"
    weak_breakdown = ranked[1].score_breakdown
    assert weak_breakdown["key_paragraph_match"] == 1.0
    assert weak_breakdown["authority_signal"] > 0.0
    assert weak_breakdown["effective_key_paragraph_match"] == 0.0
    assert weak_breakdown["effective_authority_signal"] == 0.0
    assert "weak_signal_tiebreak_limited_to_vector_bucket" in weak_breakdown["fusion_guards"]


def test_weak_signal_only_tiebreak_stays_within_same_guard_bucket():
    reranker = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS)
    plan = _plan(legal_elements=[], case_cause_hint="")
    weak_high_raw = _candidate(
        case_id="weak-high-raw",
        vector_score=0.756,
        matched_text="本院认为,虚构事实片段。",
        metadata={
            "court_level": "最高人民法院",
            "trial_level": "再审",
            "judgment_date": "2024-01-01",
            "chunk_type": "本院认为",
        },
    )
    weak_lower_raw_same_bucket = _candidate(
        case_id="weak-lower-raw-same-bucket",
        vector_score=0.758,
        matched_text="普通段落,虚构事实片段。",
        metadata={},
    )

    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        plan,
        [weak_lower_raw_same_bucket, weak_high_raw],
    )

    by_case = {item.candidate.case_id: item for item in ranked}
    assert ranked[0].candidate.case_id == "weak-high-raw"
    assert by_case["weak-high-raw"].final_score - by_case["weak-lower-raw-same-bucket"].final_score < 0.002
    assert by_case["weak-high-raw"].score_breakdown["effective_key_paragraph_match"] == 0.0
    assert by_case["weak-high-raw"].score_breakdown["effective_authority_signal"] == 0.0


def test_vector_bucket_tie_break_is_stable_for_equal_guard_scores():
    plan = _plan(legal_elements=[], case_cause_hint="")
    first = _candidate(
        case_id="first-equal",
        vector_score=0.756,
        retrieval_score=0.756,
        matched_text="普通段落,虚构事实片段。",
        metadata={},
    )
    second = _candidate(
        case_id="second-equal",
        vector_score=0.756,
        retrieval_score=0.756,
        matched_text="普通段落,虚构事实片段。",
        metadata={},
    )

    ranked = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        plan,
        [first, second],
    )

    assert [item.candidate.case_id for item in ranked] == ["first-equal", "second-equal"]
    assert ranked[0].final_score == ranked[1].final_score
    assert ranked[0].score_breakdown["input_rank"] == 0
    assert ranked[1].score_breakdown["input_rank"] == 1


def test_boundary_rerank_suppression_is_repaired_without_case_or_rank_ids():
    reranker = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS)
    plan = _plan(legal_elements=[], case_cause_hint="")
    boundary_relevant_like = _candidate(
        case_id="boundary-relevant-like",
        vector_score=0.755,
        retrieval_score=0.775,
        matched_text="本院认为,虚构事实片段。",
        metadata={
            "court_level": "高级人民法院",
            "trial_level": "再审",
            "judgment_date": "2024-01-01",
            "chunk_type": "本院认为",
        },
        retrieval_source=[ORIGINAL_VECTOR_SOURCE, "variant_vector"],
        matched_by_rewrite=True,
    )
    slightly_higher_base_weaker_tiebreak = _candidate(
        case_id="slightly-higher-base-weaker-tiebreak",
        vector_score=0.777,
        retrieval_score=0.797,
        matched_text="本院认为,虚构事实片段。",
        metadata={
            "court_level": "基层人民法院",
            "trial_level": "一审",
            "judgment_date": "2018-01-01",
            "chunk_type": "本院认为",
        },
        retrieval_source=[ORIGINAL_VECTOR_SOURCE, "variant_vector"],
        matched_by_rewrite=True,
    )

    ranked = reranker.rerank(plan, [slightly_higher_base_weaker_tiebreak, boundary_relevant_like])

    assert ranked[0].candidate.case_id == "boundary-relevant-like"
    assert ranked[0].score_breakdown["final_score_source"] == "guarded_vector_bucket"
    assert ranked[0].score_breakdown["m1_2_guarded_score"] < ranked[1].score_breakdown["m1_2_guarded_score"]


def test_typical_fact_supported_success_stays_ahead_of_no_fact_guard_adjustment():
    reranker = FactSimilarityReranker(enabled=True, weights=DEFAULT_RERANK_WEIGHTS)
    fact_supported = _candidate(
        case_id="fact-supported",
        vector_score=0.95,
        matched_text="被告人夜间进入店铺盗窃现金5000元。",
        metadata={"case_cause": "盗窃罪"},
    )
    no_fact_boundary = _candidate(
        case_id="no-fact-boundary",
        vector_score=0.76,
        matched_text="本院认为,虚构事实片段。",
        metadata={
            "court_level": "高级人民法院",
            "trial_level": "二审",
            "judgment_date": "2024-01-01",
            "chunk_type": "本院认为",
        },
        retrieval_source=[ORIGINAL_VECTOR_SOURCE, "variant_vector"],
        matched_by_rewrite=True,
    )

    ranked = reranker.rerank(_plan(), [no_fact_boundary, fact_supported])

    assert ranked[0].candidate.case_id == "fact-supported"
    assert ranked[0].score_breakdown["legal_element_overlap"] > 0.0
    assert ranked[1].score_breakdown["final_score_source"] == "guarded_vector_bucket"


def test_weighted_rerank_disabled_ignores_guard_bucket_adjustment():
    strong_no_fact = _candidate(
        case_id="strong-no-fact",
        vector_score=0.76,
        retrieval_score=0.76,
        matched_text="本院认为,虚构事实片段。",
        retrieval_source=[ORIGINAL_VECTOR_SOURCE, "variant_vector"],
        matched_by_rewrite=True,
    )
    higher_base = _candidate(
        case_id="higher-base",
        vector_score=0.74,
        retrieval_score=0.79,
        matched_text="普通段落,虚构事实片段。",
        metadata={},
    )

    ranked = FactSimilarityReranker(enabled=False, weights=DEFAULT_RERANK_WEIGHTS).rerank(
        _plan(legal_elements=[], case_cause_hint=""),
        [strong_no_fact, higher_base],
    )

    assert ranked[0].candidate.case_id == "higher-base"
    assert all(item.score_breakdown["score_mode"] == "base_retrieval" for item in ranked)
