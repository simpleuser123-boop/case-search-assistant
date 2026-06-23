# -*- coding: utf-8 -*-
"""R4 product-local evaluation runner.

This evaluates the current product corpus with product-local qrels. It writes
only sanitized evidence: query ids, input hashes/lengths, case ids, scores,
metrics, and enum reasons. Raw query text and candidate text are intentionally
kept out of reports and logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

API_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings, settings
from app.eval.bm25_baseline import dcg
from app.eval.result_format import (
    UNIFIED_EVAL_RESULT_VERSION,
    build_m13_regression_gate_summary,
    build_unified_eval_result,
    count_recall_misses_from_per_query,
    count_top10_misses_from_per_query,
)
from app.query_processing import QueryProcessingService, QueryValidationError
from app.query_processing.models import QueryPlan
from app.rerank import FactSimilarityReranker
from app.retrieval import (
    BM25FallbackRetriever,
    VectorRetrievalService,
    merge_case_candidates,
)
from app.retrieval.bm25_fallback import BM25_FALLBACK_SOURCE
from app.retrieval.models import VectorCandidate, VectorRetrievalResult


REPORT_VERSION = "m1_1_r4_product_eval_v1"
RELEVANCE_THRESHOLD = 2
DEFAULT_TOP_K = 100
DEFAULT_PRODUCT_QUERIES = PROJECT_ROOT / "data/eval/product_eval_queries.jsonl"
DEFAULT_PRODUCT_QRELS = PROJECT_ROOT / "data/eval/product_eval_qrels.jsonl"
DEFAULT_PRODUCT_CASES = PROJECT_ROOT / "data/processed/cases.jsonl"
DEFAULT_PRODUCT_CHUNKS = PROJECT_ROOT / "data/processed/chunks.jsonl"
COMPARISON_MODE_PRODUCT_CHAIN = "product_chain"
COMPARISON_MODE_BM25_POOL_RERANK = "bm25_pool_rerank"

BAD_CASE_REASON_LABELS = [
    "RECALL_MISS",
    "QUERY_REWRITE_ERROR",
    "CASE_CAUSE_OVERWEIGHT",
    "KEY_PARAGRAPH_MISMATCH",
    "ID_MISMATCH",
    "DEPENDENCY_UNAVAILABLE",
    "QUERY_VALIDATION_ERROR",
    "NO_QRELS",
    "METRIC_REGRESSION",
]

BAD_CASE_REASON_ZH = {
    "RECALL_MISS": "召回缺失",
    "QUERY_REWRITE_ERROR": "改写错误",
    "CASE_CAUSE_OVERWEIGHT": "案由误加权",
    "KEY_PARAGRAPH_MISMATCH": "段落匹配错误",
    "ID_MISMATCH": "ID 不匹配",
    "DEPENDENCY_UNAVAILABLE": "依赖不可用",
    "QUERY_VALIDATION_ERROR": "query 校验失败",
    "NO_QRELS": "缺少人工相关性标注",
    "METRIC_REGRESSION": "指标回退",
}


class ProductRetrievalService(Protocol):
    def retrieve(self, query_plan: QueryPlan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        """Return current product retrieval candidates."""


@dataclass(frozen=True)
class MetricSummary:
    precision_at_5: float
    ndcg_at_10: float
    top10_hit_rate: float
    evaluated_query_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "precision_at_5": round(self.precision_at_5, 4),
            "ndcg_at_10": round(self.ndcg_at_10, 4),
            "top10_hit_rate": round(self.top10_hit_rate, 4),
            "evaluated_query_count": self.evaluated_query_count,
        }


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_id(prefix: str, generated_at: str) -> str:
    safe_timestamp = generated_at.replace("-", "").replace(":", "").replace("T", "_")
    return f"{prefix}_{safe_timestamp}"


def _default_report_path() -> Path:
    return PROJECT_ROOT / f"data/eval/product_eval_report_{_timestamp()}.json"


def _default_bad_cases_path() -> Path:
    return PROJECT_ROOT / f"data/eval/bad_cases_product_eval_{_timestamp()}.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} is not valid JSONL") from exc
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_product_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in read_jsonl(path):
        query_id = str(row.get("eval_query_id") or "").strip()
        candidate_id = str(row.get("candidate_case_id") or row.get("case_id") or "").strip()
        if not query_id or not candidate_id:
            continue
        relevance = int(row.get("relevance", 0))
        qrels[query_id][candidate_id] = max(qrels[query_id].get(candidate_id, 0), relevance)
    return dict(qrels)


def load_product_case_ids(path: Path) -> set[str]:
    case_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = str(row.get("case_id") or "").strip()
            if case_id:
                case_ids.add(case_id)
    return case_ids


def _hash_query(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def precision_at(ranked_ids: list[str], rels: dict[str, int], *, k: int = 5) -> float:
    if k <= 0:
        return 0.0
    hits = sum(1 for case_id in ranked_ids[:k] if rels.get(case_id, 0) >= RELEVANCE_THRESHOLD)
    return hits / k


def ndcg_at(ranked_ids: list[str], rels: dict[str, int], *, k: int = 10) -> float:
    gains = [rels.get(case_id, 0) for case_id in ranked_ids[:k]]
    ideal = sorted(rels.values(), reverse=True)[:k]
    ideal_dcg = dcg(ideal, k)
    if ideal_dcg <= 0:
        return 0.0
    return dcg(gains, k) / ideal_dcg


def top_k_has_hit(ranked_ids: list[str], rels: dict[str, int], *, k: int = 10) -> bool:
    return any(rels.get(case_id, 0) >= RELEVANCE_THRESHOLD for case_id in ranked_ids[:k])


def _metrics_from_query_rows(rows: list[dict[str, Any]], prefix: str) -> MetricSummary:
    evaluated = [row for row in rows if row.get("evaluated")]
    if not evaluated:
        return MetricSummary(0.0, 0.0, 0.0, 0)
    return MetricSummary(
        precision_at_5=sum(float(row[f"{prefix}_precision_at_5"]) for row in evaluated) / len(evaluated),
        ndcg_at_10=sum(float(row[f"{prefix}_ndcg_at_10"]) for row in evaluated) / len(evaluated),
        top10_hit_rate=sum(1 for row in evaluated if row[f"{prefix}_top10_has_hit"]) / len(evaluated),
        evaluated_query_count=len(evaluated),
    )


def _dedupe(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique


def _bm25_pool_rows_and_candidates(
    *,
    query_plan: QueryPlan,
    fallback_retriever: BM25FallbackRetriever,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[VectorCandidate]]:
    chunks = fallback_retriever.search(
        query_plan.cleaned_query,
        top_k=top_k,
        retrieval_source=BM25_FALLBACK_SOURCE,
    )
    best_by_case: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        if not chunk.case_id:
            continue
        existing = best_by_case.get(chunk.case_id)
        score = float(chunk.score)
        if existing is None or score > float(existing["row"]["score"]):
            best_by_case[chunk.case_id] = {
                "row": {
                    "case_id": chunk.case_id,
                    "score": round(score, 6),
                "retrieval_score": round(score, 6),
                "retrieval_source": [chunk.retrieval_source],
                "candidateSource": "bm25",
                "recallStage": ["bm25_product_baseline"],
                "matchedByVector": False,
                "matchedByBm25": True,
                "matchedByRewrite": bool(query_plan.query_variants or query_plan.legal_elements),
                "filteredReason": "not_filtered",
                "dedupReason": "bm25_case_dedup_keep_best_chunk",
                "score_mode": "bm25_product_baseline",
                },
                "candidate": VectorCandidate(
                    case_id=chunk.case_id,
                    chunk_id=chunk.chunk_id,
                    vector_score=float(chunk.vector_score if chunk.vector_score is not None else score),
                    retrieval_source=chunk.retrieval_source,
                    metadata=dict(chunk.metadata),
                    matched_text=chunk.text,
                    source=chunk.source,
                    distance=chunk.distance,
                    retrieval_score=score,
                    candidate_source=chunk.retrieval_source,
                    recall_stage="bm25_product_baseline",
                    matched_by_vector=False,
                    matched_by_bm25=True,
                    matched_by_rewrite=bool(query_plan.query_variants or query_plan.legal_elements),
                    filtered_reason="not_filtered",
                    dedup_reason="case_level_merge_pending",
                ),
            }
    ranked = sorted(best_by_case.values(), key=lambda item: float(item["row"]["score"]), reverse=True)[:top_k]
    rows: list[dict[str, Any]] = []
    candidates: list[VectorCandidate] = []
    for rank, item in enumerate(ranked, 1):
        row = dict(item["row"])
        row["rank"] = rank
        rows.append(row)
        candidates.append(item["candidate"])
    return rows, candidates


def _baseline_rows(
    *,
    query_plan: QueryPlan,
    fallback_retriever: BM25FallbackRetriever,
    top_k: int,
) -> list[dict[str, Any]]:
    rows, _candidates = _bm25_pool_rows_and_candidates(
        query_plan=query_plan,
        fallback_retriever=fallback_retriever,
        top_k=top_k,
    )
    return rows


def _current_rows(
    *,
    query_plan: QueryPlan,
    retrieval_service: ProductRetrievalService,
    reranker: FactSimilarityReranker,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    retrieval_result = retrieval_service.retrieve(query_plan, include_relaxed_recall=False)
    merged = merge_case_candidates(retrieval_result.candidates)
    ranked = reranker.rerank(query_plan, merged)[:top_k]
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(ranked, 1):
        candidate = item.candidate
        breakdown = item.score_breakdown
        rows.append(
            {
                "rank": rank,
                "case_id": candidate.case_id,
                "score": round(float(item.final_score), 6),
                "retrieval_score": round(float(candidate.retrieval_score), 6),
                "retrieval_source": candidate.retrieval_source,
                "candidateSource": candidate.candidate_source,
                "recallStage": candidate.recall_stage,
                "matchedByVector": candidate.matched_by_vector,
                "matchedByBm25": candidate.matched_by_bm25,
                "matchedByRewrite": candidate.matched_by_rewrite,
                "filteredReason": candidate.filtered_reason,
                "dedupReason": candidate.dedup_reason,
                "score_mode": breakdown.get("score_mode"),
                "final_score_source": breakdown.get("final_score_source"),
                "fusion_guards": breakdown.get("fusion_guards", []),
                "base_retrieval_score": breakdown.get("base_retrieval_score"),
                "raw_weighted_score": breakdown.get("raw_weighted_score"),
                "weighted_score": breakdown.get("weighted_score"),
                "feature_scores": {
                    "vector_similarity": breakdown.get("vector_similarity"),
                    "legal_element_overlap": breakdown.get("legal_element_overlap"),
                    "case_cause_match": breakdown.get("case_cause_match"),
                    "key_paragraph_match": breakdown.get("key_paragraph_match"),
                    "authority_signal": breakdown.get("authority_signal"),
                },
                "effective_feature_scores": {
                    "vector_similarity": breakdown.get("vector_similarity"),
                    "legal_element_overlap": breakdown.get("effective_legal_element_overlap"),
                    "case_cause_match": breakdown.get("effective_case_cause_match"),
                    "key_paragraph_match": breakdown.get("effective_key_paragraph_match"),
                    "authority_signal": breakdown.get("effective_authority_signal"),
                },
            }
        )
    return rows, list(retrieval_result.degraded_reasons)


def _current_rows_from_bm25_pool(
    *,
    query_plan: QueryPlan,
    bm25_pool_candidates: list[VectorCandidate],
    reranker: FactSimilarityReranker,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    merged = merge_case_candidates(bm25_pool_candidates)
    ranked = reranker.rerank(query_plan, merged)[:top_k]
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(ranked, 1):
        candidate = item.candidate
        breakdown = item.score_breakdown
        rows.append(
            {
                "rank": rank,
                "case_id": candidate.case_id,
                "score": round(float(item.final_score), 6),
                "retrieval_score": round(float(candidate.retrieval_score), 6),
                "retrieval_source": candidate.retrieval_source,
                "candidateSource": candidate.candidate_source,
                "recallStage": candidate.recall_stage,
                "matchedByVector": candidate.matched_by_vector,
                "matchedByBm25": candidate.matched_by_bm25,
                "matchedByRewrite": candidate.matched_by_rewrite,
                "filteredReason": candidate.filtered_reason,
                "dedupReason": candidate.dedup_reason,
                "score_mode": breakdown.get("score_mode"),
                "final_score_source": breakdown.get("final_score_source"),
                "fusion_guards": breakdown.get("fusion_guards", []),
                "base_retrieval_score": breakdown.get("base_retrieval_score"),
                "raw_weighted_score": breakdown.get("raw_weighted_score"),
                "weighted_score": breakdown.get("weighted_score"),
                "feature_scores": {
                    "vector_similarity": breakdown.get("vector_similarity"),
                    "legal_element_overlap": breakdown.get("legal_element_overlap"),
                    "case_cause_match": breakdown.get("case_cause_match"),
                    "key_paragraph_match": breakdown.get("key_paragraph_match"),
                    "authority_signal": breakdown.get("authority_signal"),
                },
                "effective_feature_scores": {
                    "vector_similarity": breakdown.get("vector_similarity"),
                    "legal_element_overlap": breakdown.get("effective_legal_element_overlap"),
                    "case_cause_match": breakdown.get("effective_case_cause_match"),
                    "key_paragraph_match": breakdown.get("effective_key_paragraph_match"),
                    "authority_signal": breakdown.get("effective_authority_signal"),
                },
            }
        )
    return rows, []


def _rows_with_relevance(rows: list[dict[str, Any]], rels: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "relevance": rels.get(str(row.get("case_id") or ""), 0),
        }
        for row in rows[:10]
    ]


def _bad_case_note(labels: list[str]) -> str:
    if "ID_MISMATCH" in labels:
        return "Qrels contain product case ids that do not exist in the candidate corpus."
    if "QUERY_REWRITE_ERROR" in labels:
        return "Query rewrite degraded; rewrite remains disabled for this R4 run."
    if "DEPENDENCY_UNAVAILABLE" in labels:
        return "Current retrieval dependency degraded or failed; rerun after dependency health is restored."
    if "CASE_CAUSE_OVERWEIGHT" in labels:
        return "A non-relevant current top result received a case-cause feature score."
    if "KEY_PARAGRAPH_MISMATCH" in labels:
        return "A non-relevant current top result received a key-paragraph feature score."
    if "RECALL_MISS" in labels:
        return "Relevant product qrels are missing from top10 results."
    if "METRIC_REGRESSION" in labels:
        return "Current metrics are below the BM25 product baseline."
    return "Needs manual review."


def _bad_case_labels(
    *,
    rels: dict[str, int],
    product_case_ids: set[str],
    baseline_rows: list[dict[str, Any]],
    current_rows: list[dict[str, Any]],
    query_degraded_reasons: list[str],
    current_degraded_reasons: list[str],
    baseline_p5: float,
    current_p5: float,
    baseline_ndcg10: float,
    current_ndcg10: float,
) -> list[str]:
    labels: list[str] = []
    qrel_ids = set(rels)
    if qrel_ids - product_case_ids:
        labels.append("ID_MISMATCH")

    baseline_ids = [str(row["case_id"]) for row in baseline_rows]
    current_ids = [str(row["case_id"]) for row in current_rows]
    if not top_k_has_hit(baseline_ids, rels) and not top_k_has_hit(current_ids, rels):
        labels.append("RECALL_MISS")
    if current_p5 < baseline_p5 or current_ndcg10 < baseline_ndcg10:
        labels.append("METRIC_REGRESSION")

    if any(reason.startswith("LLM_") for reason in query_degraded_reasons):
        labels.append("QUERY_REWRITE_ERROR")
    if any(
        "UNAVAILABLE" in reason or "TIMEOUT" in reason or "FAILED" in reason
        for reason in [*query_degraded_reasons, *current_degraded_reasons]
    ):
        labels.append("DEPENDENCY_UNAVAILABLE")

    top_non_relevant = next(
        (row for row in current_rows[:5] if rels.get(str(row.get("case_id") or ""), 0) < RELEVANCE_THRESHOLD),
        None,
    )
    if top_non_relevant is not None:
        feature_scores = top_non_relevant.get("feature_scores") or {}
        effective_feature_scores = top_non_relevant.get("effective_feature_scores") or feature_scores
        if float(effective_feature_scores.get("case_cause_match") or 0.0) > 0:
            labels.append("CASE_CAUSE_OVERWEIGHT")
        if float(effective_feature_scores.get("key_paragraph_match") or 0.0) > 0:
            labels.append("KEY_PARAGRAPH_MISMATCH")

    return _dedupe(labels)


def _gray_candidate_decision(
    *,
    baseline: MetricSummary,
    current: MetricSummary,
    current_weighted_rerank: bool,
    blocked_reasons: list[str],
    m13_gate: dict[str, Any],
) -> dict[str, Any]:
    thresholds = {
        "precision_at_5": "current >= baseline",
        "ndcg_at_10": "current > baseline",
        "top10_hit_rate": "current >= 0.6",
        "m13_hard_gate": m13_gate["hardGateFormula"],
    }
    gate_fields = {
        key: m13_gate[key]
        for key in (
            "beforeVsAfterRegressedCount",
            "afterVsBaselineRegressedCount",
            "top10MissCount",
            "metricRegressionCount",
            "recallMissCount",
            "grayCandidateHardGatePassed",
            "weightedRerankGrayCandidate",
            "hardGateDataComplete",
            "missingInputs",
            "hardGateFailedReasons",
        )
    }
    if blocked_reasons:
        return {
            **gate_fields,
            "eligible": False,
            "reason": "Evaluation is blocked or degraded; keep ENABLE_WEIGHTED_RERANK closed.",
            "thresholds": thresholds,
            "blocked_reasons": blocked_reasons,
            "feature_flag_changed": False,
        }
    if not current_weighted_rerank:
        return {
            **gate_fields,
            "eligible": False,
            "reason": "Weighted rerank candidate mode was not evaluated in this run.",
            "thresholds": thresholds,
            "feature_flag_changed": False,
        }
    if current.precision_at_5 < baseline.precision_at_5:
        return {
            **gate_fields,
            "eligible": False,
            "reason": "Precision@5 decreased versus the BM25 product baseline.",
            "thresholds": thresholds,
            "feature_flag_changed": False,
        }
    if current.ndcg_at_10 <= baseline.ndcg_at_10:
        return {
            **gate_fields,
            "eligible": False,
            "reason": "NDCG@10 did not improve versus the BM25 product baseline.",
            "thresholds": thresholds,
            "feature_flag_changed": False,
        }
    if current.top10_hit_rate < 0.6:
        return {
            **gate_fields,
            "eligible": False,
            "reason": "Top10 hit rate is below the 60% candidate threshold.",
            "thresholds": thresholds,
            "feature_flag_changed": False,
        }
    if not m13_gate["grayCandidateHardGatePassed"]:
        return {
            **gate_fields,
            "eligible": False,
            "reason": "M1.3 hard gate failed; aggregate metrics cannot override per-query regressions.",
            "thresholds": thresholds,
            "feature_flag_changed": False,
        }
    return {
        **gate_fields,
        "eligible": True,
        "reason": "Metrics meet the offline gray-candidate thresholds; this is only a candidate suggestion.",
        "thresholds": thresholds,
        "feature_flag_changed": False,
    }


def _dataset_descriptor(
    *,
    queries: list[dict[str, Any]],
    queries_path: Path,
    qrels_path: Path,
) -> dict[str, Any]:
    versions = sorted(
        {
            str(row.get("version") or "").strip()
            for row in queries
            if str(row.get("version") or "").strip()
        }
    )
    return {
        "name": "product_local_eval",
        "queries": str(queries_path),
        "qrels": str(qrels_path),
        "versions": versions,
        "queryCount": len(queries),
    }


def _candidate_corpus_descriptor(
    *,
    cases_path: Path,
    chunks_path: Path,
    comparison_mode: str,
    top_k: int,
) -> dict[str, Any]:
    candidate_set = (
        f"per-query BM25 case-dedup pool, top_k={top_k}"
        if comparison_mode == COMPARISON_MODE_BM25_POOL_RERANK
        else "current product retrieval chain candidate pool"
    )
    return {
        "type": "product_local_cases_chunks",
        "cases": str(cases_path),
        "chunks": str(chunks_path),
        "candidateSet": candidate_set,
    }


def _dependency_blocked_items(per_query: list[dict[str, Any]]) -> list[str]:
    dependency_markers = ("UNAVAILABLE", "TIMEOUT", "FAILED")
    blocked = {
        reason
        for row in per_query
        for reason in row.get("current_degraded_reasons", [])
        if any(marker in str(reason) for marker in dependency_markers)
    }
    return sorted(blocked)


def _candidate_set_summary(per_query: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [row for row in per_query if row.get("evaluated")]
    comparable = [
        row.get("candidate_set", {})
        for row in evaluated
        if row.get("candidate_set", {}).get("comparison_mode") == COMPARISON_MODE_BM25_POOL_RERANK
    ]
    return {
        "evaluated_query_count": len(evaluated),
        "same_candidate_pool_query_count": sum(1 for row in comparable if row.get("same_candidate_ids") is True),
        "different_candidate_pool_query_count": sum(1 for row in comparable if row.get("same_candidate_ids") is False),
        "comparison_mode": comparable[0].get("comparison_mode") if comparable else COMPARISON_MODE_PRODUCT_CHAIN,
    }


def evaluate_product(
    *,
    queries_path: Path = DEFAULT_PRODUCT_QUERIES,
    qrels_path: Path = DEFAULT_PRODUCT_QRELS,
    cases_path: Path = DEFAULT_PRODUCT_CASES,
    chunks_path: Path = DEFAULT_PRODUCT_CHUNKS,
    output_path: Path | None = None,
    bad_cases_path: Path | None = None,
    limit_queries: int = 0,
    top_k: int = DEFAULT_TOP_K,
    current_weighted_rerank: bool = True,
    query_service: QueryProcessingService | None = None,
    fallback_retriever: BM25FallbackRetriever | None = None,
    retrieval_service: ProductRetrievalService | None = None,
    reranker: FactSimilarityReranker | None = None,
    comparison_mode: str = COMPARISON_MODE_PRODUCT_CHAIN,
) -> dict[str, Any]:
    if comparison_mode not in {COMPARISON_MODE_PRODUCT_CHAIN, COMPARISON_MODE_BM25_POOL_RERANK}:
        raise ValueError(f"Unsupported product eval comparison_mode: {comparison_mode}")
    generated_at = datetime.now().isoformat(timespec="seconds")
    run_id = _run_id("product_eval", generated_at)
    queries = read_jsonl(queries_path)
    if limit_queries:
        queries = queries[:limit_queries]
    qrels = load_product_qrels(qrels_path)
    product_case_ids = load_product_case_ids(cases_path)

    eval_config = Settings(
        ENABLE_QUERY_REWRITE=False,
        ENABLE_SUMMARY=False,
        ENABLE_EXPANDED_SEARCH=False,
        ENABLE_WEIGHTED_RERANK=current_weighted_rerank,
    )
    query_service = query_service or QueryProcessingService(config=eval_config)
    fallback_retriever = fallback_retriever or BM25FallbackRetriever(
        cases_path=cases_path,
        chunks_path=chunks_path,
    )
    retrieval_service = retrieval_service or VectorRetrievalService(
        fallback_retriever=fallback_retriever,
    )
    reranker = reranker or FactSimilarityReranker(config=eval_config, enabled=current_weighted_rerank)

    per_query: list[dict[str, Any]] = []
    bad_cases: list[dict[str, Any]] = []
    blocked_reasons: list[str] = []

    qrel_candidate_ids = {candidate_id for rels in qrels.values() for candidate_id in rels}
    missing_qrel_ids = sorted(qrel_candidate_ids - product_case_ids)
    if missing_qrel_ids:
        blocked_reasons.append("product_qrels_case_id_missing_from_candidate_corpus")
    if len(queries) < 20:
        blocked_reasons.append("product_query_count_below_20")
    if _relevant_query_count(qrels) < 10:
        blocked_reasons.append("product_labeled_query_count_below_10")

    for query in queries:
        eval_query_id = str(query.get("eval_query_id") or "").strip()
        query_text = str(query.get("query_text") or "")
        rels = qrels.get(eval_query_id, {})
        query_hash = _hash_query(query_text)
        row_base: dict[str, Any] = {
            "eval_query_id": eval_query_id,
            "input_hash": query_hash,
            "input_length": len(query_text),
            "evaluated": bool(rels),
        }
        if not rels:
            row = {
                **row_base,
                "status": "skipped_no_qrels",
                "baseline_precision_at_5": 0.0,
                "baseline_ndcg_at_10": 0.0,
                "baseline_top10_has_hit": False,
                "current_precision_at_5": 0.0,
                "current_ndcg_at_10": 0.0,
                "current_top10_has_hit": False,
            }
            per_query.append(row)
            bad_cases.append(
                {
                    "eval_query_id": eval_query_id,
                    "input_hash": query_hash,
                    "input_length": len(query_text),
                    "reason_labels": ["NO_QRELS"],
                    "reason_zh": [BAD_CASE_REASON_ZH["NO_QRELS"]],
                    "short_note": _bad_case_note(["NO_QRELS"]),
                }
            )
            continue

        try:
            query_plan = query_service.process(query_text)
            query_degraded_reasons = list(query_plan.degraded_reasons)
        except QueryValidationError as exc:
            row = {
                **row_base,
                "status": "failed_query_validation",
                "error_code": exc.code,
                "baseline_precision_at_5": 0.0,
                "baseline_ndcg_at_10": 0.0,
                "baseline_top10_has_hit": False,
                "current_precision_at_5": 0.0,
                "current_ndcg_at_10": 0.0,
                "current_top10_has_hit": False,
            }
            per_query.append(row)
            bad_cases.append(
                {
                    "eval_query_id": eval_query_id,
                    "input_hash": query_hash,
                    "input_length": len(query_text),
                    "reason_labels": ["QUERY_VALIDATION_ERROR"],
                    "reason_zh": [BAD_CASE_REASON_ZH["QUERY_VALIDATION_ERROR"]],
                    "short_note": "Query validation failed; no retrieval metrics were computed.",
                }
            )
            continue

        try:
            baseline, bm25_pool_candidates = _bm25_pool_rows_and_candidates(
                query_plan=query_plan,
                fallback_retriever=fallback_retriever,
                top_k=top_k,
            )
            baseline_error = None
        except Exception as exc:  # noqa: BLE001 - sanitized report
            baseline = []
            bm25_pool_candidates = []
            baseline_error = exc.__class__.__name__

        try:
            if comparison_mode == COMPARISON_MODE_BM25_POOL_RERANK:
                current, current_degraded_reasons = _current_rows_from_bm25_pool(
                    query_plan=query_plan,
                    bm25_pool_candidates=bm25_pool_candidates,
                    reranker=reranker,
                    top_k=top_k,
                )
            else:
                current, current_degraded_reasons = _current_rows(
                    query_plan=query_plan,
                    retrieval_service=retrieval_service,
                    reranker=reranker,
                    top_k=top_k,
                )
            current_error = None
        except Exception as exc:  # noqa: BLE001 - sanitized report
            current = []
            current_degraded_reasons = ["DEPENDENCY_UNAVAILABLE"]
            current_error = exc.__class__.__name__

        baseline_ids = [str(item["case_id"]) for item in baseline]
        current_ids = [str(item["case_id"]) for item in current]
        baseline_p5 = precision_at(baseline_ids, rels, k=5)
        baseline_ndcg10 = ndcg_at(baseline_ids, rels, k=10)
        current_p5 = precision_at(current_ids, rels, k=5)
        current_ndcg10 = ndcg_at(current_ids, rels, k=10)
        baseline_hit = top_k_has_hit(baseline_ids, rels, k=10)
        current_hit = top_k_has_hit(current_ids, rels, k=10)
        missing_relevant_ids = sorted(
            case_id for case_id, score in rels.items()
            if score >= RELEVANCE_THRESHOLD and case_id not in product_case_ids
        )

        labels = _bad_case_labels(
            rels=rels,
            product_case_ids=product_case_ids,
            baseline_rows=baseline,
            current_rows=current,
            query_degraded_reasons=query_degraded_reasons,
            current_degraded_reasons=current_degraded_reasons,
            baseline_p5=baseline_p5,
            current_p5=current_p5,
            baseline_ndcg10=baseline_ndcg10,
            current_ndcg10=current_ndcg10,
        )
        if baseline_error or current_error:
            labels = _dedupe([*labels, "DEPENDENCY_UNAVAILABLE"])

        row = {
            **row_base,
            "status": "ok" if not current_error and not baseline_error else "partial",
            "baseline_precision_at_5": round(baseline_p5, 4),
            "baseline_ndcg_at_10": round(baseline_ndcg10, 4),
            "baseline_top10_has_hit": baseline_hit,
            "current_precision_at_5": round(current_p5, 4),
            "current_ndcg_at_10": round(current_ndcg10, 4),
            "current_top10_has_hit": current_hit,
            "baseline_error_type": baseline_error,
            "current_error_type": current_error,
            "query_degraded_reasons": query_degraded_reasons,
            "current_degraded_reasons": current_degraded_reasons,
            "qrel_relevant_count": sum(1 for score in rels.values() if score >= RELEVANCE_THRESHOLD),
            "qrel_case_ids_missing_from_corpus": missing_relevant_ids[:10],
            "candidate_set": {
                "comparison_mode": comparison_mode,
                "same_candidate_ids": (
                    sorted(set(baseline_ids)) == sorted(set(current_ids))
                    if comparison_mode == COMPARISON_MODE_BM25_POOL_RERANK
                    else None
                ),
                "baseline_candidate_count": len(set(baseline_ids)),
                "current_candidate_count": len(set(current_ids)),
            },
            "baseline_top10": _rows_with_relevance(baseline, rels),
            "current_top10": _rows_with_relevance(current, rels),
        }
        per_query.append(row)

        is_bad_case = (
            bool(labels)
            or current_p5 < baseline_p5
            or current_ndcg10 < baseline_ndcg10
            or not current_hit
        )
        if is_bad_case:
            bad_cases.append(
                {
                    "eval_query_id": eval_query_id,
                    "input_hash": query_hash,
                    "input_length": len(query_text),
                    "reason_labels": labels or ["METRIC_REGRESSION"],
                    "reason_zh": [BAD_CASE_REASON_ZH[label] for label in (labels or ["METRIC_REGRESSION"])],
                    "baseline_precision_at_5": round(baseline_p5, 4),
                    "current_precision_at_5": round(current_p5, 4),
                    "baseline_ndcg_at_10": round(baseline_ndcg10, 4),
                    "current_ndcg_at_10": round(current_ndcg10, 4),
                    "current_top10_has_hit": current_hit,
                    "relevant_case_ids": sorted(
                        case_id for case_id, score in rels.items() if score >= RELEVANCE_THRESHOLD
                    )[:10],
                    "baseline_top10_case_ids": baseline_ids[:10],
                    "current_top10_case_ids": current_ids[:10],
                    "short_note": _bad_case_note(labels),
                }
            )

    baseline_metrics = _metrics_from_query_rows(per_query, "baseline")
    current_metrics = _metrics_from_query_rows(per_query, "current")
    dependency_blocked_items = _dependency_blocked_items(per_query)
    reason_distribution = Counter(
        label
        for bad_case in bad_cases
        for label in bad_case.get("reason_labels", [])
    )
    reason_distribution_dict = dict(sorted(reason_distribution.items()))
    m13_regression_gate = build_m13_regression_gate_summary(
        top10_hit_rate=current_metrics.top10_hit_rate,
        evaluated_query_count=current_metrics.evaluated_query_count,
        before_vs_after_label_distribution=None,
        after_vs_baseline_label_distribution=None,
        metric_regression_count=int(reason_distribution.get("METRIC_REGRESSION", 0)),
        recall_miss_count=count_recall_misses_from_per_query(per_query, current_mode="current"),
        top10_miss_count=count_top10_misses_from_per_query(per_query, mode="current"),
        blocked_items=[*blocked_reasons, *dependency_blocked_items],
    )
    bad_cases_path = bad_cases_path or _default_bad_cases_path()
    output_path = output_path or _default_report_path()
    bad_case_report = {
        "version": REPORT_VERSION,
        "generated_at": generated_at,
        "privacy": {
            "raw_query_text_written": False,
            "candidate_full_text_written": False,
            "chunk_text_written": False,
        },
        "reason_taxonomy": BAD_CASE_REASON_LABELS,
        "reason_labels_zh": BAD_CASE_REASON_ZH,
        "bad_case_count": len(bad_cases),
        "reason_distribution": reason_distribution_dict,
        "bad_cases": bad_cases,
    }
    write_json(bad_cases_path, bad_case_report)

    qrels_query_count = len(qrels)
    labeled_query_count = _relevant_query_count(qrels)
    dataset = _dataset_descriptor(queries=queries, queries_path=queries_path, qrels_path=qrels_path)
    candidate_corpus = _candidate_corpus_descriptor(
        cases_path=cases_path,
        chunks_path=chunks_path,
        comparison_mode=comparison_mode,
        top_k=top_k,
    )
    current_mode = (
        "current_weighted_rerank_over_bm25_product_pool"
        if comparison_mode == COMPARISON_MODE_BM25_POOL_RERANK and current_weighted_rerank
        else "current_product_retrieval_plus_weighted_rerank_candidate"
        if current_weighted_rerank
        else "current_product_retrieval_base_score"
    )
    current_eval_note = (
        "Offline weighted rerank candidate; global ENABLE_WEIGHTED_RERANK remains unchanged."
        if current_weighted_rerank
        else "Current run keeps weighted rerank disabled and uses base retrieval score order."
    )
    unified_results = [
        build_unified_eval_result(
            run_id=run_id,
            generated_at=generated_at,
            eval_line="product_local",
            dataset=dataset,
            candidate_corpus=candidate_corpus,
            mode="baseline",
            precision_at_5=baseline_metrics.precision_at_5,
            ndcg_at_10=baseline_metrics.ndcg_at_10,
            top10_hit_rate=baseline_metrics.top10_hit_rate,
            blocked_items=blocked_reasons,
            notes=["BM25 case-dedup baseline over the product-local corpus."],
        ),
        build_unified_eval_result(
            run_id=run_id,
            generated_at=generated_at,
            eval_line="product_local",
            dataset=dataset,
            candidate_corpus=candidate_corpus,
            mode="current",
            precision_at_5=current_metrics.precision_at_5,
            ndcg_at_10=current_metrics.ndcg_at_10,
            top10_hit_rate=current_metrics.top10_hit_rate,
            blocked_items=[*blocked_reasons, *dependency_blocked_items],
            notes=[
                current_eval_note,
                (
                    "Current rerank uses the same per-query BM25 candidate pool as baseline."
                    if comparison_mode == COMPARISON_MODE_BM25_POOL_RERANK
                    else "Current product chain candidate set may differ from the BM25 baseline."
                ),
            ],
        ),
    ]
    report = {
        "version": REPORT_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "unified_result_version": UNIFIED_EVAL_RESULT_VERSION,
        "unified_results": unified_results,
        "inputs": {
            "queries": str(queries_path),
            "qrels": str(qrels_path),
            "cases": str(cases_path),
            "chunks": str(chunks_path),
        },
        "privacy": {
            "raw_query_text_written": False,
            "candidate_full_text_written": False,
            "chunk_text_written": False,
        },
        "eval_set": {
            "query_count": len(queries),
            "qrels_query_count": qrels_query_count,
            "qrels_count": sum(len(rels) for rels in qrels.values()),
            "labeled_query_count": labeled_query_count,
            "product_case_count": len(product_case_ids),
            "qrels_product_case_id_overlap": len(qrel_candidate_ids & product_case_ids),
            "missing_qrels_case_id_count": len(missing_qrel_ids),
        },
        "modes": {
            "baseline": "bm25_product_case_dedup",
            "current": current_mode,
            "comparison_mode": comparison_mode,
            "query_rewrite_enabled": False,
            "summary_enabled": False,
            "expanded_search_enabled": False,
            "current_weighted_rerank_eval_only": current_weighted_rerank,
            "same_candidate_pool_required": comparison_mode == COMPARISON_MODE_BM25_POOL_RERANK,
        },
        "feature_flags": {
            "global_ENABLE_WEIGHTED_RERANK": bool(settings.ENABLE_WEIGHTED_RERANK),
            "eval_current_weighted_rerank": current_weighted_rerank,
            "feature_flag_changed": False,
        },
        "baseline": baseline_metrics.as_dict(),
        "current": current_metrics.as_dict(),
        "metric_delta": {
            "precision_at_5": round(current_metrics.precision_at_5 - baseline_metrics.precision_at_5, 4),
            "ndcg_at_10": round(current_metrics.ndcg_at_10 - baseline_metrics.ndcg_at_10, 4),
            "top10_hit_rate": round(current_metrics.top10_hit_rate - baseline_metrics.top10_hit_rate, 4),
        },
        "m13_regression_gate": m13_regression_gate,
        "gray_candidate": _gray_candidate_decision(
            baseline=baseline_metrics,
            current=current_metrics,
            current_weighted_rerank=current_weighted_rerank,
            blocked_reasons=blocked_reasons,
            m13_gate=m13_regression_gate,
        ),
        "bad_case_report": {
            "path": str(bad_cases_path),
            "bad_case_count": len(bad_cases),
            "reason_distribution": reason_distribution_dict,
        },
        "candidate_set_summary": _candidate_set_summary(per_query),
        "report_path": str(output_path),
        "per_query": per_query,
    }
    write_json(output_path, report)
    return report


def _relevant_query_count(qrels: dict[str, dict[str, int]]) -> int:
    return sum(1 for rels in qrels.values() if any(score >= RELEVANCE_THRESHOLD for score in rels.values()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--out", default="")
    parser.add_argument("--bad-cases-out", default="")
    parser.add_argument("--limit-queries", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--current-weighted-rerank",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Offline candidate scoring only; does not mutate ENABLE_WEIGHTED_RERANK.",
    )
    parser.add_argument(
        "--comparison-mode",
        choices=[COMPARISON_MODE_PRODUCT_CHAIN, COMPARISON_MODE_BM25_POOL_RERANK],
        default=COMPARISON_MODE_PRODUCT_CHAIN,
        help="Use product_chain for the current retrieval chain or bm25_pool_rerank for same-pool rerank comparison.",
    )
    args = parser.parse_args()

    report = evaluate_product(
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        output_path=_resolve(args.out) if args.out else None,
        bad_cases_path=_resolve(args.bad_cases_out) if args.bad_cases_out else None,
        limit_queries=args.limit_queries,
        top_k=args.top_k,
        current_weighted_rerank=args.current_weighted_rerank,
        comparison_mode=args.comparison_mode,
    )
    print(json.dumps(
        {
            "status": "ok" if not report["gray_candidate"].get("blocked_reasons") else "partial",
            "run_id": report["run_id"],
            "report_path": report["report_path"],
            "bad_case_report": report["bad_case_report"],
            "comparison_mode": report["modes"]["comparison_mode"],
            "candidate_set_summary": report["candidate_set_summary"],
            "unified_results": report["unified_results"],
            "eval_set": report["eval_set"],
            "baseline": report["baseline"],
            "current": report["current"],
            "metric_delta": report["metric_delta"],
            "gray_candidate": report["gray_candidate"],
            "feature_flags": report["feature_flags"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
