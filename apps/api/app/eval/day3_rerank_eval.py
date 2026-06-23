# -*- coding: utf-8 -*-
"""Day 3 step 7.2 offline evaluation for rerank readiness.

The report deliberately separates three scopes:
- the reproducible Day0 LeCaRDv2 BM25 baseline;
- a comparable rerank-only proxy over the same LeCaRDv2 BM25 candidate pool;
- a real product smoke over the current JuDGE/Chroma/BM25 retrieval chain.

It never rewrites the existing baseline report and never writes raw query text
or candidate document text to the Day3 report.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, Settings
from app.eval.bm25_baseline import (
    bm25_rank,
    build_index,
    evaluate as evaluate_bm25_baseline,
    iter_corpus_docs,
    load_qrels,
    ndcg_at,
    precision_at,
    read_jsonl,
    tokenize,
    write_json,
)
from app.eval.result_format import UNIFIED_EVAL_RESULT_VERSION, build_unified_eval_result
from app.query_processing import QueryProcessingService, QueryValidationError
from app.rerank import FactSimilarityReranker
from app.retrieval import (
    BM25FallbackRetriever,
    CaseCandidate,
    ChromaCollectionAdapter,
    OllamaEmbeddingClient,
    VectorRetrievalService,
    merge_case_candidates,
)


REPORT_VERSION = "day3_7_2_rerank_eval_v1"
LECARDV2_BM25_POOL_SOURCE = "lecardv2_bm25_candidate_pool"
RELEVANCE_THRESHOLD = 2

BAD_CASE_REASON_LABELS = [
    "RECALL_MISS",
    "QUERY_REWRITE_ERROR",
    "CASE_CAUSE_OVERWEIGHT",
    "KEY_PARAGRAPH_MISMATCH",
    "SUMMARY_MISLEADING",
    "DATASET_MISMATCH",
    "DEPENDENCY_UNAVAILABLE",
]


@dataclass(frozen=True)
class MetricSummary:
    precision_at_5: float
    ndcg_at_10: float
    top10_qrels_hit_rate: float
    evaluated_query_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "precision_at_5": round(self.precision_at_5, 4),
            "ndcg_at_10": round(self.ndcg_at_10, 4),
            "top10_qrels_hit_rate": round(self.top10_qrels_hit_rate, 4),
            "evaluated_query_count": self.evaluated_query_count,
        }


def _resolve_project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _round(value: float | int | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _run_id(prefix: str, generated_at: str) -> str:
    safe_timestamp = generated_at.replace("-", "").replace(":", "").replace("T", "_")
    return f"{prefix}_{safe_timestamp}"


def _top10_hit_rate_from_baseline_report(report: dict[str, Any]) -> float | None:
    per_query = report.get("per_query") or []
    if not per_query:
        return None
    hits = 0
    evaluated = 0
    for row in per_query:
        top10 = row.get("top10") or []
        if not isinstance(top10, list):
            continue
        evaluated += 1
        if any(int(item.get("relevance") or 0) >= RELEVANCE_THRESHOLD for item in top10):
            hits += 1
    if evaluated <= 0:
        return None
    return round(hits / evaluated, 4)


def _load_corpus_texts(corpus_path: Path) -> dict[str, str]:
    return {pid: text for pid, text in iter_corpus_docs(corpus_path)}


def _query_vocab(queries: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for query in queries:
        values.update(tokenize(str(query.get("query_text") or "")))
    return values


def _query_plan_service(enable_query_rewrite: bool) -> QueryProcessingService:
    config = Settings(
        ENABLE_QUERY_REWRITE=enable_query_rewrite,
        ENABLE_WEIGHTED_RERANK=True,
    )
    return QueryProcessingService(config=config)


def _reranker() -> FactSimilarityReranker:
    config = Settings(ENABLE_WEIGHTED_RERANK=True)
    return FactSimilarityReranker(config=config, enabled=True)


def _case_candidate_from_bm25(
    *,
    candidate_case_id: str,
    score: float,
    normalized_score: float,
    text: str,
    corpus_path: Path,
) -> CaseCandidate:
    chunk_id = f"lecardv2:{candidate_case_id}"
    return CaseCandidate(
        case_id=candidate_case_id,
        top_chunk_id=chunk_id,
        source_chunk_ids=[chunk_id],
        hit_chunk_ids=[chunk_id],
        retrieval_source=[LECARDV2_BM25_POOL_SOURCE],
        metadata={
            "case_id": candidate_case_id,
            "chunk_id": chunk_id,
            "source": "LeCaRDv2_candidate",
        },
        matched_text=text,
        source=str(corpus_path),
        vector_score=None,
        fallback_score=normalized_score,
        top_chunk_score=normalized_score,
        retrieval_score=normalized_score,
        soft_filter_score=0.0,
        soft_filter_breakdown={},
        distance=None,
    )


def _top10_rows_from_pairs(pairs: list[tuple[str, float]], rels: dict[str, int]) -> list[dict[str, Any]]:
    rows = []
    for rank, (candidate_case_id, score) in enumerate(pairs[:10], 1):
        rows.append(
            {
                "rank": rank,
                "candidate_case_id": candidate_case_id,
                "score": _round(score, 6),
                "relevance": rels.get(candidate_case_id, 0),
            }
        )
    return rows


def _top10_rows_from_ranked(ranked: list[Any], rels: dict[str, int]) -> list[dict[str, Any]]:
    rows = []
    for rank, item in enumerate(ranked[:10], 1):
        candidate = item.candidate
        breakdown = item.score_breakdown
        rows.append(
            {
                "rank": rank,
                "candidate_case_id": candidate.case_id,
                "final_score": _round(item.final_score, 6),
                "retrieval_score": _round(candidate.retrieval_score, 6),
                "relevance": rels.get(candidate.case_id, 0),
                "score_mode": breakdown.get("score_mode"),
                "feature_scores": {
                    "vector_similarity": breakdown.get("vector_similarity"),
                    "legal_element_overlap": breakdown.get("legal_element_overlap"),
                    "case_cause_match": breakdown.get("case_cause_match"),
                    "key_paragraph_match": breakdown.get("key_paragraph_match"),
                    "authority_signal": breakdown.get("authority_signal"),
                },
            }
        )
    return rows


def _has_top10_hit(ranked_ids: list[str], rels: dict[str, int]) -> bool:
    return any(rels.get(candidate_id, 0) >= RELEVANCE_THRESHOLD for candidate_id in ranked_ids[:10])


def _metrics_from_per_query(per_query: list[dict[str, Any]], prefix: str) -> MetricSummary:
    evaluated = len(per_query)
    if evaluated == 0:
        return MetricSummary(0.0, 0.0, 0.0, 0)
    p5 = sum(float(row[f"{prefix}_precision_at_5"]) for row in per_query) / evaluated
    ndcg10 = sum(float(row[f"{prefix}_ndcg_at_10"]) for row in per_query) / evaluated
    top10_hit = sum(1 for row in per_query if row[f"{prefix}_top10_has_qrels_hit"]) / evaluated
    return MetricSummary(p5, ndcg10, top10_hit, evaluated)


def _bad_case_labels(
    *,
    baseline_ranked_ids: list[str],
    current_ranked: list[Any],
    candidate_pool_ids: list[str],
    rels: dict[str, int],
    query_degraded_reasons: list[str],
) -> list[str]:
    labels: list[str] = []
    if not any(rels.get(candidate_id, 0) >= RELEVANCE_THRESHOLD for candidate_id in candidate_pool_ids):
        labels.append("RECALL_MISS")
    elif not _has_top10_hit([item.candidate.case_id for item in current_ranked], rels):
        labels.append("RECALL_MISS")

    if any(reason.startswith("LLM_") for reason in query_degraded_reasons):
        labels.append("QUERY_REWRITE_ERROR")
    if any("UNAVAILABLE" in reason or "TIMEOUT" in reason for reason in query_degraded_reasons):
        labels.append("DEPENDENCY_UNAVAILABLE")

    top_non_relevant = next(
        (item for item in current_ranked[:5] if rels.get(item.candidate.case_id, 0) < RELEVANCE_THRESHOLD),
        None,
    )
    if top_non_relevant is not None:
        breakdown = top_non_relevant.score_breakdown
        if float(breakdown.get("case_cause_match") or 0.0) > 0:
            labels.append("CASE_CAUSE_OVERWEIGHT")
        if float(breakdown.get("key_paragraph_match") or 0.0) > 0:
            labels.append("KEY_PARAGRAPH_MISMATCH")

    if baseline_ranked_ids and not labels:
        labels.append("RECALL_MISS")
    return _dedupe(labels)


def _dedupe(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value and value not in unique:
            unique.append(value)
    return unique


def evaluate_rerank_over_lecardv2_bm25_pool(
    *,
    queries_path: Path,
    qrels_path: Path,
    corpus_path: Path,
    limit_queries: int = 0,
    candidate_pool_k: int = 100,
    enable_query_rewrite: bool = False,
    max_bad_cases: int = 50,
) -> dict[str, Any]:
    queries = read_jsonl(queries_path)
    if limit_queries:
        queries = queries[:limit_queries]
    qrels = load_qrels(qrels_path)
    corpus_texts = _load_corpus_texts(corpus_path)
    if not corpus_texts:
        return {
            "status": "blocked_missing_candidate_corpus",
            "reason": "LeCaRDv2 candidate case texts were not found; current rerank cannot be evaluated against qrels.",
            "corpus_path": str(corpus_path),
            "query_count": len(queries),
            "bad_cases": [
                {
                    "eval_query_id": "ALL",
                    "source_query_id": None,
                    "reason_labels": ["DEPENDENCY_UNAVAILABLE"],
                    "short_note": "LeCaRDv2 candidate corpus is missing on this machine; rerank-vs-qrels evaluation is blocked.",
                }
            ],
        }

    index = build_index(corpus_path, query_vocab=_query_vocab(queries))
    query_service = _query_plan_service(enable_query_rewrite)
    reranker = _reranker()

    per_query: list[dict[str, Any]] = []
    bad_cases: list[dict[str, Any]] = []
    query_processing_failures = 0

    for query in queries:
        eval_query_id = str(query["eval_query_id"])
        rels = qrels.get(eval_query_id, {})
        query_text = str(query.get("query_text") or "")
        ranked_pairs = bm25_rank(
            query_text,
            index["seen_docs"],
            index["doc_len"],
            index["df"],
            index["postings"],
            index["avg_len"],
            top_k=candidate_pool_k,
        )
        baseline_ranked_ids = [candidate_case_id for candidate_case_id, _ in ranked_pairs]
        baseline_p5 = precision_at(baseline_ranked_ids, rels, 5, threshold=RELEVANCE_THRESHOLD)
        baseline_ndcg10 = ndcg_at(baseline_ranked_ids, rels, 10)

        try:
            query_plan = query_service.process(query_text)
            query_degraded_reasons = list(query_plan.degraded_reasons)
        except QueryValidationError as exc:
            query_processing_failures += 1
            query_plan = None
            query_degraded_reasons = [exc.code]

        max_score = max((score for _, score in ranked_pairs), default=1.0) or 1.0
        candidates = [
            _case_candidate_from_bm25(
                candidate_case_id=candidate_case_id,
                score=score,
                normalized_score=max(0.0, min(1.0, score / max_score)),
                text=corpus_texts.get(candidate_case_id, ""),
                corpus_path=corpus_path,
            )
            for candidate_case_id, score in ranked_pairs
        ]
        current_ranked = reranker.rerank(query_plan, candidates) if query_plan is not None else []
        current_ranked_ids = [item.candidate.case_id for item in current_ranked]
        current_p5 = precision_at(current_ranked_ids, rels, 5, threshold=RELEVANCE_THRESHOLD)
        current_ndcg10 = ndcg_at(current_ranked_ids, rels, 10)

        row = {
            "eval_query_id": eval_query_id,
            "source_query_id": query.get("source_query_id"),
            "baseline_precision_at_5": round(baseline_p5, 4),
            "baseline_ndcg_at_10": round(baseline_ndcg10, 4),
            "baseline_top10_has_qrels_hit": _has_top10_hit(baseline_ranked_ids, rels),
            "current_precision_at_5": round(current_p5, 4),
            "current_ndcg_at_10": round(current_ndcg10, 4),
            "current_top10_has_qrels_hit": _has_top10_hit(current_ranked_ids, rels),
            "candidate_pool_size": len(ranked_pairs),
            "relevant_in_candidate_pool": sum(
                1 for candidate_id in baseline_ranked_ids if rels.get(candidate_id, 0) >= RELEVANCE_THRESHOLD
            ),
            "query_rewrite_used": bool(query_plan and query_plan.rewrite_used),
            "query_degraded_reasons": query_degraded_reasons,
            "baseline_top10": _top10_rows_from_pairs(ranked_pairs, rels),
            "current_top10": _top10_rows_from_ranked(current_ranked, rels),
        }
        per_query.append(row)

        is_bad_case = (
            current_p5 < baseline_p5
            or current_ndcg10 < baseline_ndcg10
            or current_p5 == 0
            or not row["current_top10_has_qrels_hit"]
        )
        if is_bad_case and len(bad_cases) < max_bad_cases:
            labels = _bad_case_labels(
                baseline_ranked_ids=baseline_ranked_ids,
                current_ranked=current_ranked,
                candidate_pool_ids=baseline_ranked_ids,
                rels=rels,
                query_degraded_reasons=query_degraded_reasons,
            )
            bad_cases.append(
                {
                    "eval_query_id": eval_query_id,
                    "source_query_id": query.get("source_query_id"),
                    "reason_labels": labels,
                    "baseline_precision_at_5": round(baseline_p5, 4),
                    "current_precision_at_5": round(current_p5, 4),
                    "baseline_ndcg_at_10": round(baseline_ndcg10, 4),
                    "current_ndcg_at_10": round(current_ndcg10, 4),
                    "current_top10_has_qrels_hit": row["current_top10_has_qrels_hit"],
                    "short_note": _bad_case_note(labels, query_degraded_reasons),
                    "current_top10_case_ids": [item["candidate_case_id"] for item in row["current_top10"]],
                }
            )

    baseline_metrics = _metrics_from_per_query(per_query, "baseline")
    current_metrics = _metrics_from_per_query(per_query, "current")
    return {
        "status": "ok",
        "scope": "lecardv2_bm25_candidate_pool_plus_current_weighted_rerank",
        "comparable_with_lecardv2_qrels": True,
        "limitations": [
            "This is a rerank-only proxy over the BM25 candidate pool, not a full Chroma/vector product evaluation.",
            "Query rewrite is disabled by default unless --enable-query-rewrite is passed.",
        ],
        "query_count": len(queries),
        "candidate_pool_k": candidate_pool_k,
        "corpus_doc_count": index["seen_docs"],
        "indexed_doc_count": index["indexed_docs"],
        "query_processing_failure_count": query_processing_failures,
        "baseline_pool_metrics": baseline_metrics.as_dict(),
        "current_rerank_metrics": current_metrics.as_dict(),
        "metric_delta": {
            "precision_at_5": round(current_metrics.precision_at_5 - baseline_metrics.precision_at_5, 4),
            "ndcg_at_10": round(current_metrics.ndcg_at_10 - baseline_metrics.ndcg_at_10, 4),
            "top10_qrels_hit_rate": round(
                current_metrics.top10_qrels_hit_rate - baseline_metrics.top10_qrels_hit_rate,
                4,
            ),
        },
        "per_query": per_query,
        "bad_cases": bad_cases,
    }


def _bad_case_note(labels: list[str], query_degraded_reasons: list[str]) -> str:
    if "QUERY_REWRITE_ERROR" in labels:
        return "Query rewrite degraded; review rewritten plan before enabling weighted rerank."
    if "DEPENDENCY_UNAVAILABLE" in labels:
        return "Dependency unavailable during query processing; result should be rechecked on the target machine."
    if "RECALL_MISS" in labels:
        return "Relevant qrels are absent from the top candidate pool or current top10."
    if "CASE_CAUSE_OVERWEIGHT" in labels:
        return "Non-relevant top result received a case-cause feature boost."
    if "KEY_PARAGRAPH_MISMATCH" in labels:
        return "Non-relevant top result received a key-paragraph feature boost."
    if query_degraded_reasons:
        return "Query processing degraded; review manually."
    return "Needs manual review."


def run_product_smoke(
    *,
    queries_path: Path,
    qrels_path: Path,
    limit_queries: int,
    enable_query_rewrite: bool,
) -> dict[str, Any]:
    if limit_queries <= 0:
        return {
            "status": "skipped",
            "reason": "product smoke disabled by --product-smoke-queries 0",
            "comparable_with_lecardv2_qrels": False,
        }

    queries = read_jsonl(queries_path)[:limit_queries]
    qrels = load_qrels(qrels_path)
    qrel_candidate_ids = {
        candidate_id
        for rels in qrels.values()
        for candidate_id in rels
    }

    config = Settings(
        ENABLE_QUERY_REWRITE=enable_query_rewrite,
        ENABLE_WEIGHTED_RERANK=True,
    )
    query_service = QueryProcessingService(config=config)
    retrieval_service = VectorRetrievalService(
        embedding_client=OllamaEmbeddingClient(config=config),
        vector_store=ChromaCollectionAdapter(config=config),
        fallback_retriever=BM25FallbackRetriever(),
    )
    reranker = FactSimilarityReranker(config=config, enabled=True)

    smoke_rows: list[dict[str, Any]] = []
    smoke_bad_cases: list[dict[str, Any]] = []
    all_case_ids: set[str] = set()
    dependency_unavailable = False
    failures = 0

    for query in queries:
        eval_query_id = str(query["eval_query_id"])
        try:
            query_plan = query_service.process(str(query.get("query_text") or ""))
            retrieval_result = retrieval_service.retrieve(query_plan, include_relaxed_recall=False)
            merged = merge_case_candidates(retrieval_result.candidates)
            ranked = reranker.rerank(query_plan, merged)[:10]
            top10 = []
            for rank, item in enumerate(ranked, 1):
                candidate = item.candidate
                all_case_ids.add(candidate.case_id)
                top10.append(
                    {
                        "rank": rank,
                        "case_id": candidate.case_id,
                        "final_score": _round(item.final_score, 6),
                        "retrieval_score": _round(candidate.retrieval_score, 6),
                        "retrieval_source": candidate.retrieval_source,
                        "score_mode": item.score_breakdown.get("score_mode"),
                    }
                )
            degraded_reasons = _dedupe([*query_plan.degraded_reasons, *retrieval_result.degraded_reasons])
            dependency_label_needed = any(
                "UNAVAILABLE" in reason or "TIMEOUT" in reason for reason in degraded_reasons
            )
            if dependency_label_needed:
                dependency_unavailable = True
            smoke_labels = ["DATASET_MISMATCH"]
            if dependency_label_needed:
                smoke_labels.append("DEPENDENCY_UNAVAILABLE")
            smoke_bad_cases.append(
                {
                    "eval_query_id": eval_query_id,
                    "source_query_id": query.get("source_query_id"),
                    "reason_labels": smoke_labels,
                    "short_note": "Product smoke ran on JuDGE case_ids, which cannot be directly judged by LeCaRDv2 qrels.",
                }
            )
            smoke_rows.append(
                {
                    "eval_query_id": eval_query_id,
                    "source_query_id": query.get("source_query_id"),
                    "status": "ok",
                    "result_count": len(ranked),
                    "degraded": bool(query_plan.degraded or retrieval_result.degraded),
                    "degraded_reasons": degraded_reasons,
                    "top10": top10,
                }
            )
        except Exception as exc:  # noqa: BLE001 - smoke report must be sanitized
            failures += 1
            dependency_unavailable = True
            smoke_rows.append(
                {
                    "eval_query_id": eval_query_id,
                    "source_query_id": query.get("source_query_id"),
                    "status": "failed",
                    "reason_labels": ["DEPENDENCY_UNAVAILABLE"],
                    "error_type": exc.__class__.__name__,
                }
            )
            smoke_bad_cases.append(
                {
                    "eval_query_id": eval_query_id,
                    "source_query_id": query.get("source_query_id"),
                    "reason_labels": ["DATASET_MISMATCH", "DEPENDENCY_UNAVAILABLE"],
                    "short_note": "Product smoke failed and the result remains incomparable with LeCaRDv2 qrels.",
                }
            )

    overlap_count = len(all_case_ids & qrel_candidate_ids)
    status = "partial" if failures else "ok"
    return {
        "status": status,
        "scope": "current_product_retrieval_chain_smoke",
        "query_count": len(queries),
        "failure_count": failures,
        "comparable_with_lecardv2_qrels": False,
        "metric_status": "blocked_dataset_mismatch",
        "blocked_reason": (
            "Current product retrieval returns JuDGE/Chroma/BM25 product case_ids; "
            "LeCaRDv2 qrels use LeCaRDv2 candidate ids and no verified mapping exists."
        ),
        "observed_overlap_with_lecardv2_qrels": overlap_count,
        "bad_case_reason_labels": (
            ["DATASET_MISMATCH", "DEPENDENCY_UNAVAILABLE"] if dependency_unavailable else ["DATASET_MISMATCH"]
        ),
        "bad_cases": smoke_bad_cases,
        "queries": smoke_rows,
    }


def _baseline_summary(
    *,
    baseline_report_path: Path,
    queries_path: Path,
    qrels_path: Path,
    corpus_path: Path,
    rerun_baseline: bool,
    baseline_check_out: Path,
) -> dict[str, Any]:
    if rerun_baseline or not baseline_report_path.exists():
        report = evaluate_bm25_baseline(
            queries_path,
            qrels_path,
            corpus_path,
            baseline_check_out,
        )
        source = "rerun_to_day3_check_file"
        path = baseline_check_out
    else:
        report = _read_json(baseline_report_path)
        source = "read_existing_baseline_report"
        path = baseline_report_path
    return {
        "source": source,
        "path": str(path),
        "status": report.get("status"),
        "method": report.get("method"),
        "query_count": report.get("query_count"),
        "evaluated_query_count": report.get("evaluated_query_count"),
        "corpus_doc_count": report.get("corpus_doc_count"),
        "indexed_doc_count": report.get("indexed_doc_count"),
        "precision_at_5": report.get("precision_at_5"),
        "ndcg_at_10": report.get("ndcg_at_10"),
        "top10_hit_rate": _top10_hit_rate_from_baseline_report(report),
    }


def _decision(
    *,
    baseline: dict[str, Any],
    current_eval: dict[str, Any],
    product_smoke: dict[str, Any],
) -> dict[str, Any]:
    if baseline.get("status") != "ok":
        return {
            "decision": "NO_GO",
            "enable_new_rerank": False,
            "reason": "Baseline metrics are unavailable, so Day3 7.2 cannot make a release decision.",
            "can_enter_7_3": False,
        }
    if current_eval.get("status") != "ok":
        return {
            "decision": "PARTIAL_BLOCKED",
            "enable_new_rerank": False,
            "reason": current_eval.get("reason") or "Current rerank evaluation is blocked.",
            "can_enter_7_3": True,
        }

    baseline_p5 = float(baseline.get("precision_at_5") or 0.0)
    baseline_ndcg = float(baseline.get("ndcg_at_10") or 0.0)
    current_metrics = current_eval["current_rerank_metrics"]
    current_p5 = float(current_metrics["precision_at_5"])
    current_ndcg = float(current_metrics["ndcg_at_10"])
    current_top10 = float(current_metrics["top10_qrels_hit_rate"])

    if current_p5 < baseline_p5:
        return {
            "decision": "NO_GO",
            "enable_new_rerank": False,
            "reason": "Precision@5 decreased against the Day0 BM25 baseline.",
            "can_enter_7_3": True,
        }
    if product_smoke.get("metric_status") == "blocked_dataset_mismatch":
        return {
            "decision": "PARTIAL",
            "enable_new_rerank": False,
            "reason": (
                "Comparable rerank proxy is available, but full current product vector results "
                "cannot be judged with LeCaRDv2 qrels because the datasets are not mapped."
            ),
            "can_enter_7_3": True,
        }
    if current_ndcg > baseline_ndcg and current_top10 >= 0.6:
        return {
            "decision": "GO_FOR_GRAY_PREP",
            "enable_new_rerank": True,
            "reason": "Precision@5 did not decrease, NDCG@10 improved, and Top10 executable hit rate is >= 60%.",
            "can_enter_7_3": True,
        }
    return {
        "decision": "PARTIAL",
        "enable_new_rerank": False,
        "reason": "Metrics are not strong enough for gray enablement; keep only logs and evaluation pipeline.",
        "can_enter_7_3": True,
    }


def build_report(
    *,
    queries_path: Path,
    qrels_path: Path,
    corpus_path: Path,
    baseline_report_path: Path,
    output_path: Path,
    rerun_baseline: bool = False,
    limit_queries: int = 0,
    candidate_pool_k: int = 100,
    product_smoke_queries: int = 5,
    enable_query_rewrite: bool = False,
) -> dict[str, Any]:
    generated_at = datetime.now().isoformat(timespec="seconds")
    run_id = _run_id("lecardv2_rerank_eval", generated_at)
    baseline_check_out = output_path.with_name(f"day3_bm25_baseline_check_{date.today():%Y%m%d}.json")
    baseline = _baseline_summary(
        baseline_report_path=baseline_report_path,
        queries_path=queries_path,
        qrels_path=qrels_path,
        corpus_path=corpus_path,
        rerun_baseline=rerun_baseline,
        baseline_check_out=baseline_check_out,
    )
    current_eval = evaluate_rerank_over_lecardv2_bm25_pool(
        queries_path=queries_path,
        qrels_path=qrels_path,
        corpus_path=corpus_path,
        limit_queries=limit_queries,
        candidate_pool_k=candidate_pool_k,
        enable_query_rewrite=enable_query_rewrite,
    )
    product_smoke = run_product_smoke(
        queries_path=queries_path,
        qrels_path=qrels_path,
        limit_queries=product_smoke_queries,
        enable_query_rewrite=enable_query_rewrite,
    )
    dataset = {
        "name": "LeCaRDv2",
        "queries": str(queries_path),
        "qrels": str(qrels_path),
    }
    candidate_corpus = {
        "type": "lecardv2_candidate_corpus",
        "path": str(corpus_path),
        "candidateSet": f"per-query BM25 pool, top_k={candidate_pool_k}",
    }
    current_metrics = current_eval.get("current_rerank_metrics") or {}
    baseline_blocked = [] if baseline.get("status") == "ok" else [str(baseline.get("status") or "baseline_unavailable")]
    current_blocked = (
        []
        if current_eval.get("status") == "ok"
        else [
            str(current_eval.get("status") or "current_eval_blocked"),
            str(current_eval.get("reason") or "current rerank evaluation is blocked"),
        ]
    )
    unified_results = [
        build_unified_eval_result(
            run_id=run_id,
            generated_at=generated_at,
            eval_line="standard_lecardv2",
            dataset=dataset,
            candidate_corpus=candidate_corpus,
            mode="baseline",
            precision_at_5=baseline.get("precision_at_5"),
            ndcg_at_10=baseline.get("ndcg_at_10"),
            top10_hit_rate=baseline.get("top10_hit_rate"),
            blocked_items=baseline_blocked,
            notes=["BM25 baseline over LeCaRDv2 qrels and candidate corpus."],
        ),
        build_unified_eval_result(
            run_id=run_id,
            generated_at=generated_at,
            eval_line="standard_lecardv2",
            dataset=dataset,
            candidate_corpus=candidate_corpus,
            mode="current",
            precision_at_5=current_metrics.get("precision_at_5"),
            ndcg_at_10=current_metrics.get("ndcg_at_10"),
            top10_hit_rate=current_metrics.get("top10_qrels_hit_rate"),
            blocked_items=current_blocked,
            notes=[
                "Current rerank is evaluated only over the same LeCaRDv2 BM25 candidate pool when corpus exists.",
                "This standard line is not the release decision line for product-local JuDGE cases.",
            ],
        ),
    ]
    report = {
        "version": REPORT_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "generated_date": f"{date.today():%Y-%m-%d}",
        "unified_result_version": UNIFIED_EVAL_RESULT_VERSION,
        "unified_results": unified_results,
        "inputs": {
            "queries": str(queries_path),
            "qrels": str(qrels_path),
            "corpus": str(corpus_path),
            "baseline_report": str(baseline_report_path),
        },
        "privacy": {
            "raw_query_text_written": False,
            "candidate_full_text_written": False,
        },
        "bad_case_reason_taxonomy": BAD_CASE_REASON_LABELS,
        "baseline": baseline,
        "current_rerank_eval": current_eval,
        "current_product_smoke": product_smoke,
        "top10_subjective_hit_rate": {
            "status": "requires_manual_review",
            "measured": False,
            "reason": "No human subjective Top10 labels were available in this offline run.",
            "executable_alternative": "top10_qrels_hit_rate",
        },
    }
    report["release_decision"] = _decision(
        baseline=baseline,
        current_eval=current_eval,
        product_smoke=product_smoke,
    )
    write_json(output_path, report)
    return report


def main() -> None:
    today = f"{date.today():%Y%m%d}"
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default="data/eval/lecardv2_queries.jsonl")
    parser.add_argument("--qrels", default="data/eval/lecardv2_qrels.jsonl")
    parser.add_argument("--corpus", default=r"C:\Users\yyl\Downloads\LeCaRDv2-main\candidate")
    parser.add_argument("--baseline-report", default="data/eval/bm25_baseline_report.json")
    parser.add_argument("--out", default=f"data/eval/day3_rerank_eval_{today}.json")
    parser.add_argument("--rerun-baseline", action="store_true")
    parser.add_argument("--limit-queries", type=int, default=0)
    parser.add_argument("--candidate-pool-k", type=int, default=100)
    parser.add_argument("--product-smoke-queries", type=int, default=5)
    parser.add_argument("--enable-query-rewrite", action="store_true")
    args = parser.parse_args()

    output_path = _resolve_project_path(args.out)
    report = build_report(
        queries_path=_resolve_project_path(args.queries),
        qrels_path=_resolve_project_path(args.qrels),
        corpus_path=_resolve_project_path(args.corpus),
        baseline_report_path=_resolve_project_path(args.baseline_report),
        output_path=output_path,
        rerun_baseline=args.rerun_baseline,
        limit_queries=args.limit_queries,
        candidate_pool_k=args.candidate_pool_k,
        product_smoke_queries=args.product_smoke_queries,
        enable_query_rewrite=args.enable_query_rewrite,
    )
    print(json.dumps({
        "status": report["current_rerank_eval"].get("status"),
        "run_id": report["run_id"],
        "out": str(output_path),
        "baseline": report["baseline"],
        "current_rerank_metrics": report["current_rerank_eval"].get("current_rerank_metrics"),
        "metric_delta": report["current_rerank_eval"].get("metric_delta"),
        "product_smoke_status": report["current_product_smoke"].get("status"),
        "release_decision": report["release_decision"],
        "unified_results": report["unified_results"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
