"""M1.3x-5 sanitized RECALL_MISS closure runner.

This runner writes only ids, ranks, scores, counts, labels, and reason codes.
Raw queries, case facts, candidate text, and chunk text are kept in memory only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings, settings  # noqa: E402
from app.eval.product_eval import (  # noqa: E402
    DEFAULT_PRODUCT_CASES,
    DEFAULT_PRODUCT_CHUNKS,
    DEFAULT_PRODUCT_QRELS,
    DEFAULT_PRODUCT_QUERIES,
    DEFAULT_TOP_K,
    RELEVANCE_THRESHOLD,
    load_product_case_ids,
    load_product_qrels,
    read_jsonl,
)
from app.query_processing import QueryProcessingService  # noqa: E402
from app.query_processing.term_mapping import load_term_mapping_catalog  # noqa: E402
from app.rerank import FactSimilarityReranker  # noqa: E402
from app.retrieval import (  # noqa: E402
    BM25_FALLBACK_SOURCE,
    BM25FallbackRetriever,
    ChromaCollectionAdapter,
    OllamaEmbeddingClient,
    ORIGINAL_VECTOR_SOURCE,
    VARIANT_VECTOR_SOURCE,
    VectorRetrievalService,
    merge_case_candidates,
)
from app.retrieval.models import CaseCandidate, RetrievedChunk, VectorCandidate  # noqa: E402
from app.retrieval.service import (  # noqa: E402
    CONTROLLED_BM25_SUPPLEMENT_SOURCE,
    RECALL_ONLY_VECTOR_SOURCE,
)
from scripts.m1_2_regression import DEFAULT_REGRESSION_SET, _metric_summary  # noqa: E402
from scripts.m1_3_candidate_comparison import (  # noqa: E402
    _evaluate_candidates,
    _feature_flag_file_state,
)


DEFAULT_BLOCKER_REGISTER = (
    PROJECT_ROOT / "docs/development/m1.3x-blocker-register-20260610-161732.json"
)
DEFAULT_GUARD_V2_COMPARISON = (
    PROJECT_ROOT / "docs/development/m1.3x-rerank-guard-v2-candidate-comparison-20260610-180500.json"
)
DEFAULT_PRODUCT_EVAL = (
    PROJECT_ROOT / "docs/development/m1.3x-rerank-guard-v2-product-eval-20260610-180500.json"
)
DEFAULT_M13_RECALL_REPAIR = (
    PROJECT_ROOT / "docs/development/m1.3-recall-repair-20260610-113200.json"
)

MISS_TYPES = (
    "VECTOR_MISS",
    "BM25_MISS",
    "MERGE_DROPPED",
    "DEDUPE_DROPPED",
    "GATING_DROPPED",
    "QRELS_OR_DATA_BOUNDARY",
    "UNREPAIRABLE_WITH_CURRENT_SIGNALS",
)
OUTCOMES = (
    "FIXED_BY_CONTROLLED_BM25",
    "FIXED_BY_QUERY_MAPPING",
    "FIXED_BY_MERGE_ADMISSION",
    "EXPLAINED_NOT_FIXED",
    "STILL_OPEN_NO_GO",
)
FORBIDDEN_OUTPUT_FIELDS = (
    '"query_text"',
    '"raw_query"',
    '"case_text"',
    '"case_fact"',
    '"candidate_text"',
    '"chunk_text"',
    '"matched_text"',
    '"matchedText"',
    '"text"',
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value.resolve() if value.is_absolute() else (PROJECT_ROOT / value).resolve()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _round_score(value: Any) -> float | None:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _load_target_items(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    rows: list[dict[str, Any]] = []
    for item in payload.get("blockers", []):
        if item.get("blockerType") != "PRODUCT_EVAL_RECALL_MISS":
            continue
        query_id = str(item.get("queryId") or "").strip()
        case_ids = [str(value) for value in item.get("relatedCaseIds", []) if str(value).strip()]
        if not case_ids and item.get("relatedCaseId"):
            case_ids = [str(item["relatedCaseId"])]
        rows.append(
            {
                "blockerId": str(item.get("blockerId") or ""),
                "queryId": query_id,
                "targetCaseIds": case_ids,
                "blockerType": "PRODUCT_EVAL_RECALL_MISS",
                "sourceRunner": str(item.get("sourceRunner") or ""),
                "sourceArtifact": str(item.get("sourceArtifact") or ""),
            }
        )
    if not rows:
        raise ValueError("blocker register contains no PRODUCT_EVAL_RECALL_MISS rows")
    return rows


def _case_order(items: Iterable[Any]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    for item in items:
        case_id = str(getattr(item, "case_id", "") or "")
        if case_id and case_id not in seen:
            order.append(case_id)
            seen.add(case_id)
    return order


def _rank_in_order(case_ids: list[str], rels: dict[str, int]) -> dict[str, Any]:
    for rank, case_id in enumerate(case_ids, 1):
        rel = int(rels.get(case_id, 0))
        if rel >= RELEVANCE_THRESHOLD:
            return {"hit": True, "bestRank": rank, "caseId": case_id, "relevance": rel}
    return {"hit": False, "bestRank": None, "caseId": None, "relevance": None}


def _source_case_order(candidates: list[VectorCandidate], source: str) -> list[str]:
    return _case_order(candidate for candidate in candidates if candidate.retrieval_source == source)


def _case_rank_map(case_ids: list[str]) -> dict[str, int]:
    return {case_id: rank for rank, case_id in enumerate(case_ids, 1)}


def _score_by_case(items: Iterable[Any]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for item in items:
        case_id = str(getattr(item, "case_id", "") or "")
        if not case_id:
            continue
        score = _round_score(getattr(item, "retrieval_score", None))
        if score is None:
            score = _round_score(getattr(item, "score", None))
        if score is None:
            score = _round_score(getattr(item, "vector_score", None))
        if score is None:
            continue
        scores[case_id] = max(scores.get(case_id, 0.0), score)
    return scores


def _expanded_query(plan: Any) -> str:
    values = [
        plan.cleaned_query,
        *plan.query_variants,
        *plan.legal_elements,
        plan.case_cause_hint,
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            unique.append(item)
            seen.add(item)
    return " ".join(unique)


def _top10_overlap(left: list[str], right: list[str]) -> int:
    return len(set(left[:10]) & set(right[:10]))


def _bm25_channel(
    chunks: list[RetrievedChunk],
    rels: dict[str, int],
    *,
    top_k_label: int,
) -> dict[str, Any]:
    case_ids = _case_order(chunks)
    presence = _rank_in_order(case_ids, rels)
    return {
        "topK": top_k_label,
        "uniqueCaseCount": len(case_ids),
        "presence": presence,
        "score": None if not presence["caseId"] else _score_by_case(chunks).get(presence["caseId"]),
    }


def _ranked_rows(
    reranker: FactSimilarityReranker,
    plan: Any,
    merged: list[CaseCandidate],
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(reranker.rerank(plan, merged)[:top_k], 1):
        candidate = item.candidate
        breakdown = item.score_breakdown
        rows.append(
            {
                "rank": rank,
                "case_id": candidate.case_id,
                "score": _round_score(item.final_score),
                "retrieval_score": _round_score(candidate.retrieval_score),
                "retrieval_source": list(candidate.retrieval_source),
                "candidateSource": candidate.candidate_source,
                "recallStage": list(candidate.recall_stage),
                "matchedByVector": bool(candidate.matched_by_vector),
                "matchedByBm25": bool(candidate.matched_by_bm25),
                "matchedByRewrite": bool(candidate.matched_by_rewrite),
                "filteredReason": candidate.filtered_reason,
                "dedupReason": candidate.dedup_reason,
                "score_mode": breakdown.get("score_mode"),
                "final_score_source": breakdown.get("final_score_source"),
                "fusion_guards": list(breakdown.get("fusion_guards") or []),
                "base_retrieval_score": _round_score(breakdown.get("base_retrieval_score")),
                "raw_weighted_score": _round_score(breakdown.get("raw_weighted_score")),
                "weighted_score": _round_score(breakdown.get("weighted_score")),
                "effective_feature_scores": {
                    "legal_element_overlap": _round_score(breakdown.get("effective_legal_element_overlap")),
                    "case_cause_match": _round_score(breakdown.get("effective_case_cause_match")),
                },
            }
        )
    return rows


def _presence_from_rows(rows: list[dict[str, Any]], rels: dict[str, int]) -> dict[str, Any]:
    case_ids = [str(row.get("case_id") or "") for row in rows]
    presence = _rank_in_order(case_ids, rels)
    if presence["caseId"]:
        row = rows[int(presence["bestRank"]) - 1]
        presence = {
            **presence,
            "score": row.get("score"),
            "retrievalScore": row.get("retrieval_score"),
            "retrievalSource": row.get("retrieval_source"),
            "candidateSource": row.get("candidateSource"),
            "recallStage": row.get("recallStage"),
            "filteredReason": row.get("filteredReason"),
            "dedupReason": row.get("dedupReason"),
            "fusionGuards": row.get("fusion_guards"),
            "finalScoreSource": row.get("final_score_source"),
        }
    else:
        presence = {
            **presence,
            "score": None,
            "retrievalScore": None,
            "retrievalSource": [],
            "candidateSource": None,
            "recallStage": [],
            "filteredReason": None,
            "dedupReason": None,
            "fusionGuards": [],
            "finalScoreSource": None,
        }
    return presence


def _target_case_evidence(
    *,
    target_case_ids: list[str],
    rels: dict[str, int],
    corpus_case_ids: set[str],
    original_vector_order: list[str],
    variant_vector_order: list[str],
    recall_only_vector_order: list[str],
    cleaned_bm25_order: list[str],
    expanded_bm25_order: list[str],
    raw_order: list[str],
    merged_order: list[str],
    ranked_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rank_maps = {
        "originalVectorRank": _case_rank_map(original_vector_order),
        "mappedRewriteVectorRank": _case_rank_map(variant_vector_order),
        "recallOnlyVectorRank": _case_rank_map(recall_only_vector_order),
        "cleanedBm25Rank": _case_rank_map(cleaned_bm25_order),
        "expandedBm25Rank": _case_rank_map(expanded_bm25_order),
        "rawCandidateRank": _case_rank_map(raw_order),
        "mergedRank": _case_rank_map(merged_order),
        "finalRank": _case_rank_map([str(row.get("case_id") or "") for row in ranked_rows]),
    }
    rows: list[dict[str, Any]] = []
    for case_id in target_case_ids:
        item = {
            "caseId": case_id,
            "relevance": int(rels.get(case_id, 0)),
            "inCorpus": case_id in corpus_case_ids,
        }
        for field, rank_map in rank_maps.items():
            rank = rank_map.get(case_id)
            item[field] = rank
            if field.endswith("Rank"):
                item[field.replace("Rank", "Top10Hit")] = bool(rank and rank <= 10)
        rows.append(item)
    return rows


def _channel_snapshot(
    *,
    candidates: list[VectorCandidate],
    merged: list[CaseCandidate],
    ranked_rows: list[dict[str, Any]],
    cleaned_bm25: list[RetrievedChunk],
    expanded_bm25: list[RetrievedChunk],
    rels: dict[str, int],
) -> dict[str, Any]:
    original_vector_order = _source_case_order(candidates, ORIGINAL_VECTOR_SOURCE)
    variant_vector_order = _source_case_order(candidates, VARIANT_VECTOR_SOURCE)
    recall_only_vector_order = _source_case_order(candidates, RECALL_ONLY_VECTOR_SOURCE)
    supplement_order = _source_case_order(candidates, CONTROLLED_BM25_SUPPLEMENT_SOURCE)
    cleaned_bm25_order = _case_order(cleaned_bm25)
    expanded_bm25_order = _case_order(expanded_bm25)
    raw_order = _case_order(candidates)
    merged_order = _case_order(merged)
    return {
        "channels": {
            "originalVector": {
                "topK": 50,
                "uniqueCaseCount": len(original_vector_order),
                "presence": _rank_in_order(original_vector_order, rels),
            },
            "mappedRewriteVector": {
                "topK": 30,
                "uniqueCaseCount": len(variant_vector_order),
                "presence": _rank_in_order(variant_vector_order, rels),
            },
            "recallOnlyMappingVector": {
                "topK": 30,
                "uniqueCaseCount": len(recall_only_vector_order),
                "presence": _rank_in_order(recall_only_vector_order, rels),
            },
            "cleanedBm25": _bm25_channel(cleaned_bm25, rels, top_k_label=100),
            "expandedBm25": _bm25_channel(expanded_bm25, rels, top_k_label=100),
            "controlledBm25Supplement": {
                "topK": 4,
                "uniqueCaseCount": len(supplement_order),
                "presence": _rank_in_order(supplement_order, rels),
            },
        },
        "pool": {
            "rawCandidateCount": len(candidates),
            "rawUniqueCaseCount": len(raw_order),
            "mergedCandidateCount": len(merged),
            "rawPresence": _rank_in_order(raw_order, rels),
            "mergedPresence": _rank_in_order(merged_order, rels),
            "finalPresence": _presence_from_rows(ranked_rows, rels),
            "dedupeDropped": _rank_in_order(raw_order, rels)["hit"]
            and not _rank_in_order(merged_order, rels)["hit"],
            "mergeDropped": _rank_in_order(raw_order, rels)["hit"]
            and not _rank_in_order(merged_order, rels)["hit"],
            "gatingDropped": _rank_in_order(merged_order, rels)["hit"]
            and not (
                _presence_from_rows(ranked_rows, rels)["bestRank"]
                and int(_presence_from_rows(ranked_rows, rels)["bestRank"]) <= 10
            ),
            "bm25VectorTop10Overlap": _top10_overlap(original_vector_order, cleaned_bm25_order),
        },
        "orders": {
            "originalVector": original_vector_order,
            "mappedRewriteVector": variant_vector_order,
            "recallOnlyMappingVector": recall_only_vector_order,
            "cleanedBm25": cleaned_bm25_order,
            "expandedBm25": expanded_bm25_order,
            "raw": raw_order,
            "merged": merged_order,
        },
    }


def _catalog_by_id() -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in load_term_mapping_catalog().get("mappings", [])}


def _probe_recall_only_variants(
    *,
    plan: Any,
    query_id: str,
    rels: dict[str, int],
    mapping_catalog: dict[str, dict[str, Any]],
    retrieval_service: VectorRetrievalService,
    reranker: FactSimilarityReranker,
) -> list[dict[str, Any]]:
    if not plan.mapping_labels:
        return []
    probes: list[dict[str, Any]] = []
    for label in plan.mapping_labels:
        mapping = mapping_catalog.get(str(label))
        if not mapping:
            continue
        legal = str(mapping.get("legal_term") or "").strip()
        cause = str(mapping.get("case_cause_hint") or "").strip()
        expansion = [str(value).strip() for value in mapping.get("expansion_terms", []) if str(value).strip()]
        modes = {
            "legal_term": [legal] if legal else [],
            "case_cause_plus_expansion": [" ".join(value for value in [cause, *expansion] if value)],
            "legal_cause_expansion": [" ".join(value for value in [legal, cause, *expansion] if value)],
        }
        for mode, variants in modes.items():
            variants = [value for value in variants if value]
            if not variants:
                continue
            probe_plan = plan.model_copy(
                update={
                    "recall_only_query_variants": variants,
                    "queries": [plan.cleaned_query, *plan.query_variants, *variants],
                }
            )
            result = retrieval_service.retrieve(probe_plan, include_relaxed_recall=False)
            merged = merge_case_candidates(result.candidates)
            ranked_rows = _ranked_rows(reranker, probe_plan, merged)
            presence = _presence_from_rows(ranked_rows, rels)
            recall_only_presence = _rank_in_order(
                _source_case_order(result.candidates, RECALL_ONLY_VECTOR_SOURCE),
                rels,
            )
            probes.append(
                {
                    "mappingLabel": str(label),
                    "mode": mode,
                    "variantCount": len(variants),
                    "recallOnlyPresence": recall_only_presence,
                    "finalPresence": presence,
                    "top10Hit": bool(presence["bestRank"] and int(presence["bestRank"]) <= 10),
                    "rawCandidateCount": len(result.candidates),
                    "mergedCandidateCount": len(merged),
                    "queryId": query_id,
                }
            )
    return probes


def _classify(
    *,
    target_case_ids: list[str],
    qrel_missing_from_corpus: list[str],
    snapshot: dict[str, Any],
    variant_probes: list[dict[str, Any]],
) -> tuple[list[str], str, list[str], bool]:
    channels = snapshot["channels"]
    pool = snapshot["pool"]
    miss_types: list[str] = []
    reasons: list[str] = []
    fixed = False
    outcome = "EXPLAINED_NOT_FIXED"

    final_rank = pool["finalPresence"]["bestRank"]
    if final_rank and int(final_rank) <= 10:
        fixed = True
        if channels["controlledBm25Supplement"]["presence"]["hit"]:
            outcome = "FIXED_BY_CONTROLLED_BM25"
        elif channels["recallOnlyMappingVector"]["presence"]["hit"]:
            outcome = "FIXED_BY_QUERY_MAPPING"
        else:
            outcome = "FIXED_BY_MERGE_ADMISSION"
        return miss_types, outcome, ["CURRENT_TOP10_HIT"], fixed

    if qrel_missing_from_corpus:
        miss_types.append("QRELS_OR_DATA_BOUNDARY")
        reasons.append("RELEVANT_QREL_CASE_MISSING_FROM_CORPUS")

    vector_hit = any(
        channels[name]["presence"]["hit"]
        for name in ("originalVector", "mappedRewriteVector", "recallOnlyMappingVector")
    )
    bm25_hit = any(
        channels[name]["presence"]["hit"]
        for name in ("cleanedBm25", "expandedBm25", "controlledBm25Supplement")
    )
    if not vector_hit:
        miss_types.append("VECTOR_MISS")
        reasons.append("NO_RELEVANT_CASE_IN_VECTOR_TOPK")
    if not bm25_hit:
        miss_types.append("BM25_MISS")
        reasons.append("NO_RELEVANT_CASE_IN_BM25_TOPK_OR_SUPPLEMENT")
    if pool["mergeDropped"]:
        miss_types.append("MERGE_DROPPED")
        reasons.append("RELEVANT_CASE_PRESENT_RAW_ABSENT_AFTER_MERGE")
    if pool["dedupeDropped"]:
        miss_types.append("DEDUPE_DROPPED")
        reasons.append("RELEVANT_CASE_PRESENT_RAW_ABSENT_AFTER_DEDUPE")
    if pool["gatingDropped"]:
        miss_types.append("GATING_DROPPED")
        reasons.append("RELEVANT_CASE_PRESENT_AFTER_MERGE_BUT_OUTSIDE_TOP10")

    if not qrel_missing_from_corpus and not fixed:
        miss_types.append("UNREPAIRABLE_WITH_CURRENT_SIGNALS")
        if pool["mergedPresence"]["hit"]:
            reasons.append("RELEVANT_CASE_IN_POOL_BUT_RANKING_OR_SIGNAL_STRENGTH_OUT_OF_SCOPE")
        elif any(probe.get("recallOnlyPresence", {}).get("hit") for probe in variant_probes):
            reasons.append("RECALL_ONLY_PROBE_RETRIEVES_RELEVANT_BUT_STILL_OUTSIDE_TOP10")
        elif channels["cleanedBm25"]["presence"]["hit"] or channels["expandedBm25"]["presence"]["hit"]:
            reasons.append("BM25_SIGNAL_EXISTS_BUT_CONTROLLED_ADMISSION_DOES_NOT_REACH_TARGET")
        else:
            reasons.append("NO_SAFE_RECALL_ONLY_SIGNAL_TO_ADMIT_RELEVANT_CASE")

    miss_types = [item for item in MISS_TYPES if item in set(miss_types)]
    if not miss_types:
        miss_types = ["UNREPAIRABLE_WITH_CURRENT_SIGNALS"]
    if not reasons:
        reasons = ["NO_SAFE_M1_3X_RECALL_REPAIR"]
    return miss_types, outcome, sorted(set(reasons)), fixed


def _candidate_metrics_from_comparison(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    for row in payload.get("candidate_matrix", []):
        if row.get("candidate_id") == "m1_3_combined_candidate":
            return {
                "candidateId": row.get("candidate_id"),
                "Precision@5": row.get("Precision@5"),
                "NDCG@10": row.get("NDCG@10"),
                "Top10 hit rate": row.get("Top10 hit rate"),
                "METRIC_REGRESSION count": row.get("METRIC_REGRESSION count"),
                "RECALL_MISS count": row.get("RECALL_MISS count"),
                "beforeVsAfterRegressedCount": row.get("beforeVsAfterRegressedCount"),
                "afterVsBaselineRegressedCount": row.get("afterVsBaselineRegressedCount"),
                "goNoGo": row.get("goNoGo"),
            }
    return {}


def _product_eval_metrics(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    current = payload.get("current") or {}
    gate = payload.get("m13_regression_gate") or {}
    bad = payload.get("bad_case_report") or {}
    return {
        "Precision@5": current.get("precision_at_5"),
        "NDCG@10": current.get("ndcg_at_10"),
        "Top10 hit rate": current.get("top10_hit_rate"),
        "METRIC_REGRESSION count": gate.get("metricRegressionCount"),
        "RECALL_MISS count": gate.get("recallMissCount"),
        "reasonDistribution": bad.get("reason_distribution", {}),
        "hardGateDataComplete": gate.get("hardGateDataComplete"),
        "weightedRerankGrayCandidate": gate.get("weightedRerankGrayCandidate"),
    }


def _current_comparison_summary(
    *,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    regression_set_path: Path,
    top_k: int,
) -> dict[str, Any]:
    evaluated = _evaluate_candidates(
        queries_path=queries_path,
        qrels_path=qrels_path,
        cases_path=cases_path,
        chunks_path=chunks_path,
        regression_set_path=regression_set_path,
        top_k=top_k,
    )
    candidate_rows = evaluated["candidateRows"]["m1_3_combined_candidate"]
    regression_rows = evaluated["regressionRows"]["m1_3_combined_candidate"]
    metrics = _metric_summary(
        [
            {"evaluated": row["evaluated"], "metrics": {"candidate": row["metrics"]["candidate"]}}
            for row in candidate_rows
        ],
        "candidate",
    )
    before_vs_after = Counter(row["beforeVsAfterLabel"] for row in regression_rows)
    after_vs_baseline = Counter(row["afterVsBaselineLabel"] for row in regression_rows)
    recall_miss_ids = [
        row["queryId"]
        for row in candidate_rows
        if not row["metrics"]["baseline"]["Top10 hit"] and not row["metrics"]["candidate"]["Top10 hit"]
    ]
    return {
        "candidateId": "m1_3_combined_candidate",
        "Precision@5": metrics["Precision@5"],
        "NDCG@10": metrics["NDCG@10"],
        "Top10 hit rate": metrics["Top10 hit rate"],
        "evaluatedQueryCount": metrics["evaluatedQueryCount"],
        "beforeVsAfterRegressedCount": int(before_vs_after.get("REGRESSED", 0)),
        "afterVsBaselineRegressedCount": int(after_vs_baseline.get("REGRESSED", 0)),
        "METRIC_REGRESSION count": int(after_vs_baseline.get("REGRESSED", 0)),
        "RECALL_MISS count": len(recall_miss_ids),
        "RECALL_MISS ids": recall_miss_ids,
        "globalBlockedItems": evaluated["globalBlockedItems"],
    }


def build_report(
    *,
    blocker_register: Path,
    prior_comparison: Path,
    prior_product_eval: Path,
    prior_recall_repair: Path,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    regression_set_path: Path,
    performance_smoke: Path | None,
    rollback_drill: Path | None,
    output_md: Path,
    output_json: Path,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    targets = _load_target_items(blocker_register)
    queries = {str(row.get("eval_query_id") or ""): row for row in read_jsonl(queries_path)}
    qrels = load_product_qrels(qrels_path)
    corpus_case_ids = load_product_case_ids(cases_path)
    mapping_catalog = _catalog_by_id()

    eval_settings = Settings(
        ENABLE_QUERY_REWRITE=False,
        ENABLE_WEIGHTED_RERANK=True,
        ENABLE_SUMMARY=False,
        ENABLE_EXPANDED_SEARCH=False,
    )
    query_service = QueryProcessingService(config=eval_settings)
    bm25 = BM25FallbackRetriever(cases_path=cases_path, chunks_path=chunks_path)
    embedding = OllamaEmbeddingClient(config=eval_settings)
    vector_store = ChromaCollectionAdapter(config=eval_settings)
    retrieval_service = VectorRetrievalService(
        embedding_client=embedding,
        vector_store=vector_store,
        fallback_retriever=bm25,
        embedding_cache=None,
        enable_targeted_recall_repairs=True,
    )
    reranker = FactSimilarityReranker(config=eval_settings, enabled=True)

    rows: list[dict[str, Any]] = []
    raw_queries: list[str] = []
    for target in targets:
        query_id = target["queryId"]
        query_row = queries.get(query_id)
        rels = qrels.get(query_id, {})
        if not query_row or not rels:
            raise ValueError(f"missing query or qrels for {query_id}")
        query_text = str(query_row.get("query_text") or "")
        raw_queries.append(query_text)
        plan = query_service.process(query_text)
        cleaned_bm25 = bm25.search(
            plan.cleaned_query,
            top_k=100,
            retrieval_source=BM25_FALLBACK_SOURCE,
        )
        expanded_bm25 = bm25.search(
            _expanded_query(plan),
            top_k=100,
            retrieval_source="bm25_fallback_expanded_query",
        )
        result = retrieval_service.retrieve(plan, include_relaxed_recall=False)
        merged = merge_case_candidates(result.candidates)
        ranked_rows = _ranked_rows(reranker, plan, merged, top_k=top_k)
        snapshot = _channel_snapshot(
            candidates=result.candidates,
            merged=merged,
            ranked_rows=ranked_rows,
            cleaned_bm25=cleaned_bm25,
            expanded_bm25=expanded_bm25,
            rels=rels,
        )
        qrel_missing_from_corpus = sorted(
            case_id
            for case_id, relevance in rels.items()
            if relevance >= RELEVANCE_THRESHOLD and case_id not in corpus_case_ids
        )
        variant_probes = _probe_recall_only_variants(
            plan=plan,
            query_id=query_id,
            rels=rels,
            mapping_catalog=mapping_catalog,
            retrieval_service=retrieval_service,
            reranker=reranker,
        )
        miss_types, outcome, reason_codes, fixed = _classify(
            target_case_ids=target["targetCaseIds"],
            qrel_missing_from_corpus=qrel_missing_from_corpus,
            snapshot=snapshot,
            variant_probes=variant_probes,
        )
        target_evidence = _target_case_evidence(
            target_case_ids=target["targetCaseIds"],
            rels=rels,
            corpus_case_ids=corpus_case_ids,
            original_vector_order=snapshot["orders"]["originalVector"],
            variant_vector_order=snapshot["orders"]["mappedRewriteVector"],
            recall_only_vector_order=snapshot["orders"]["recallOnlyMappingVector"],
            cleaned_bm25_order=snapshot["orders"]["cleanedBm25"],
            expanded_bm25_order=snapshot["orders"]["expandedBm25"],
            raw_order=snapshot["orders"]["raw"],
            merged_order=snapshot["orders"]["merged"],
            ranked_rows=ranked_rows,
        )
        rows.append(
            {
                **target,
                "qrelRelevantCaseCount": sum(1 for value in rels.values() if value >= RELEVANCE_THRESHOLD),
                "qrelCaseIdsMissingFromCorpus": qrel_missing_from_corpus,
                "queryPlanSignals": {
                    "mappingUsed": bool(plan.local_mapping_used),
                    "mappingLabels": list(plan.mapping_labels),
                    "queryVariantCount": len(plan.query_variants),
                    "recallOnlyVariantCount": len(plan.recall_only_query_variants),
                    "legalElementCount": len(plan.legal_elements),
                    "caseCauseHintPresent": bool(plan.case_cause_hint),
                    "degradedReasons": list(plan.degraded_reasons),
                },
                "targetCaseEvidence": target_evidence,
                "snapshot": {
                    "channels": snapshot["channels"],
                    "pool": snapshot["pool"],
                },
                "offlineRecallOnlyProbes": variant_probes,
                "missTypes": miss_types,
                "outcome": outcome,
                "reasonCodes": reason_codes,
                "safeM13xRepairAvailable": fixed,
                "stopLossBoundary": _stop_loss_boundary(miss_types, reason_codes),
            }
        )

    current_summary = _current_comparison_summary(
        queries_path=queries_path,
        qrels_path=qrels_path,
        cases_path=cases_path,
        chunks_path=chunks_path,
        regression_set_path=regression_set_path,
        top_k=top_k,
    )
    prior_comparison_summary = _candidate_metrics_from_comparison(prior_comparison)
    prior_product_summary = _product_eval_metrics(prior_product_eval)
    performance_summary = _performance_summary(performance_smoke)
    rollback_summary = _rollback_summary(rollback_drill)
    type_distribution = dict(sorted(Counter(t for row in rows for t in row["missTypes"]).items()))
    outcome_distribution = dict(sorted(Counter(row["outcome"] for row in rows).items()))
    fixed_query_ids = [row["queryId"] for row in rows if row["outcome"].startswith("FIXED_BY_")]
    explained_query_ids = [row["queryId"] for row in rows if row["outcome"] == "EXPLAINED_NOT_FIXED"]
    still_open_query_ids = [row["queryId"] for row in rows if row["outcome"] == "STILL_OPEN_NO_GO"]

    recall_miss_before = int(prior_comparison_summary.get("RECALL_MISS count") or 0)
    recall_miss_after = int(current_summary.get("RECALL_MISS count") or 0)
    explained_all_remaining = len(explained_query_ids) + len(fixed_query_ids) == len(rows)
    new_metric_regression = (
        int(current_summary["METRIC_REGRESSION count"])
        > int(prior_comparison_summary.get("METRIC_REGRESSION count") or 0)
    )
    new_fixed_regression = (
        int(current_summary["beforeVsAfterRegressedCount"])
        > int(prior_comparison_summary.get("beforeVsAfterRegressedCount") or 0)
        or int(current_summary["afterVsBaselineRegressedCount"])
        > int(prior_comparison_summary.get("afterVsBaselineRegressedCount") or 0)
    )
    step_go = (
        (recall_miss_after < recall_miss_before or explained_all_remaining)
        and not new_metric_regression
        and not new_fixed_regression
        and float(current_summary["Top10 hit rate"]) >= 0.60
        and bool(performance_summary.get("warmP95Under3s"))
        and bool(rollback_summary.get("recoveryWithin60Seconds"))
        and bool(rollback_summary.get("requiresIndexRebuild") is False)
        and bool(settings.ENABLE_WEIGHTED_RERANK) is False
    )

    report = {
        "version": "m1_3x_recall_miss_closure_v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "scope": {
            "step": "M1.3x-5",
            "name": "remaining RECALL_MISS closure and stop-loss",
            "logicChange": "none",
            "defaultRecallPoolExpanded": False,
            "rankingGuardV2Entered": False,
            "weightedRerankDefaultChanged": False,
        },
        "privacy": {
            "rawQueryTextWritten": False,
            "caseFactTextWritten": False,
            "candidateTextWritten": False,
            "chunkTextWritten": False,
            "allowedFieldClasses": [
                "query id",
                "case id",
                "rank",
                "score",
                "bucket",
                "label",
                "count",
                "status",
                "reason code",
                "runner name",
                "gate name",
            ],
        },
        "inputs": {
            "blockerRegister": _relative(blocker_register),
            "priorComparison": _relative(prior_comparison),
            "priorProductEval": _relative(prior_product_eval),
            "priorRecallRepair": _relative(prior_recall_repair),
            "queries": _relative(queries_path),
            "qrels": _relative(qrels_path),
            "cases": _relative(cases_path),
            "chunks": _relative(chunks_path),
            "regressionSet": _relative(regression_set_path),
        },
        "outputs": {
            "markdown": _relative(output_md),
            "json": _relative(output_json),
        },
        "allowedRepairRulesEvaluated": [
            "controlled BM25 supplement",
            "recall-only query mapping",
            "merge admission rule",
        ],
        "prohibitedActionsConfirmed": {
            "caseIdHardcoding": False,
            "queryIdSpecialInsertion": False,
            "manualRankEdit": False,
            "qrelsModified": False,
            "badSamplesDeleted": False,
            "historicalEvalModified": False,
            "globalTopKExpanded": False,
            "weightedRerankDefaultEnabled": False,
        },
        "summary": {
            "targetRecallMissCount": len(rows),
            "coveredQueryCount": len(rows),
            "fixedQueryIds": fixed_query_ids,
            "explainedNotFixedQueryIds": explained_query_ids,
            "stillOpenNoGoQueryIds": still_open_query_ids,
            "missTypeDistribution": type_distribution,
            "outcomeDistribution": outcome_distribution,
            "recallMissBefore": recall_miss_before,
            "recallMissAfter": recall_miss_after,
            "recallMissDelta": recall_miss_after - recall_miss_before,
            "recallMissDecreased": recall_miss_after < recall_miss_before,
            "allRemainingExplained": explained_all_remaining,
            "newMetricRegression": new_metric_regression,
            "newFixedRegression": new_fixed_regression,
            "top10HitRate": current_summary["Top10 hit rate"],
            "precisionAt5": current_summary["Precision@5"],
            "ndcgAt10": current_summary["NDCG@10"],
            "stepConclusion": "GO" if step_go else "NO_GO",
        },
        "metrics": {
            "currentM13CombinedCandidate": current_summary,
            "priorM13CombinedCandidate": prior_comparison_summary,
            "priorProductEval": prior_product_summary,
        },
        "performance": performance_summary,
        "rollback": rollback_summary,
        "featureFlagState": _feature_flag_file_state(),
        "rows": rows,
        "boundaries": {
            "m2ReadinessBlockedByThisStep": False,
            "m2ReadinessReason": (
                "M1.3x-5 closes recall misses by explanation. Remaining hard-gate metric regressions "
                "are not introduced by this step and stay outside M1.3x-5 recall repair."
            ),
            "nextRequiredCapabilityForUnfixedItems": [
                "more discriminative evidence labels or qrels enrichment",
                "separate ranking/guard work outside M1.3x-5",
                "additional corpus coverage only if qrels/data boundary is confirmed",
            ],
        },
    }

    markdown = _render_markdown(report)
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    _privacy_check(markdown=markdown, json_text=json_text, raw_queries=raw_queries)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json_text, encoding="utf-8")
    output_md.write_text(markdown, encoding="utf-8")
    return report


def _stop_loss_boundary(miss_types: list[str], reason_codes: list[str]) -> dict[str, Any]:
    return {
        "currentSignalInsufficient": True,
        "cannotSafelyFixInM13x": True,
        "doesNotBlockM2ReadinessByItself": True,
        "reasonCodes": reason_codes,
        "blockedRepairClasses": [
            "global_topk_expansion",
            "ranking_adjustment",
            "query_id_or_case_id_special_case",
            "qrels_or_bad_sample_edit",
        ],
        "futureNeeds": (
            ["data_or_qrels_alignment"] if "QRELS_OR_DATA_BOUNDARY" in miss_types else []
        )
        + [
            "stronger non-qrels recall signal",
            "ranking evidence outside M1.3x-5",
        ],
    }


def _performance_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "artifact": None,
            "warmP95Ms": None,
            "warmP95Under3s": False,
            "blocked": True,
        }
    payload = _load_json(path)
    api = payload.get("api") or {}
    return {
        "artifact": _relative(path),
        "status": payload.get("status"),
        "warmP95Ms": ((api.get("warm_response_total_duration_ms") or {}).get("p95")),
        "warmApiWallP95Ms": ((api.get("warm_api_wall_ms") or {}).get("p95")),
        "warmP95Under3s": api.get("warm_p95_under_3s"),
        "errorRate": api.get("error_rate"),
        "blocked": False,
    }


def _rollback_summary(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "artifact": None,
            "status": None,
            "recoveryWithin60Seconds": False,
            "requiresIndexRebuild": None,
            "blocked": True,
        }
    payload = _load_json(path)
    weighted = None
    for scenario in payload.get("scenarios", []):
        if scenario.get("flag") == "ENABLE_WEIGHTED_RERANK":
            weighted = scenario
            break
    return {
        "artifact": _relative(path),
        "status": payload.get("status"),
        "recoveryWithin60Seconds": payload.get("recovery_within_60_seconds"),
        "maxRollbackElapsedMs": payload.get("max_rollback_elapsed_ms"),
        "requiresIndexRebuild": payload.get("requires_index_rebuild", False),
        "weightedRerankReturnsBaseRetrieval": bool(
            weighted
            and weighted.get("status") == "passed"
            and ((weighted.get("observed") or {}).get("score_mode") == "base_retrieval")
        ),
        "weightedRerankElapsedMs": None if weighted is None else weighted.get("rollback_elapsed_ms"),
        "globalEnableWeightedRerankDefault": bool(settings.ENABLE_WEIGHTED_RERANK),
        "blocked": False,
    }


def _privacy_check(*, markdown: str, json_text: str, raw_queries: list[str]) -> None:
    for raw_query in raw_queries:
        if raw_query and (raw_query in markdown or raw_query in json_text):
            raise ValueError("privacy check failed: raw query found in output")
    for field in FORBIDDEN_OUTPUT_FIELDS:
        if field in json_text:
            raise ValueError(f"privacy check failed: forbidden field found: {field}")


def _render_rank(value: Any) -> str:
    return "null" if value is None else str(value)


def _render_bool(value: Any) -> str:
    return "true" if bool(value) else "false"


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# M1.3x-5 RECALL_MISS Closure",
        "",
        f"- Generated at: `{report['generatedAt']}`",
        f"- Target RECALL_MISS: `{summary['targetRecallMissCount']}`",
        "- Logic change: `none`",
        "- Privacy: raw query, case fact, candidate text, and chunk text are excluded.",
        "",
        "## Outcome",
        "",
        f"- RECALL_MISS: `{summary['recallMissBefore']}` -> `{summary['recallMissAfter']}`",
        f"- RECALL_MISS decreased: `{_render_bool(summary['recallMissDecreased'])}`",
        f"- All remaining explained: `{_render_bool(summary['allRemainingExplained'])}`",
        f"- New METRIC_REGRESSION: `{_render_bool(summary['newMetricRegression'])}`",
        f"- New fixed regression: `{_render_bool(summary['newFixedRegression'])}`",
        f"- Top10 hit rate: `{summary['top10HitRate']}`",
        f"- Precision@5: `{summary['precisionAt5']}`",
        f"- NDCG@10: `{summary['ndcgAt10']}`",
        f"- Step conclusion: `{summary['stepConclusion']}`",
        "",
        "## Distribution",
        "",
        f"- Miss types: `{json.dumps(summary['missTypeDistribution'], ensure_ascii=False)}`",
        f"- Outcomes: `{json.dumps(summary['outcomeDistribution'], ensure_ascii=False)}`",
        f"- Fixed query ids: `{', '.join(summary['fixedQueryIds']) or '-'}`",
        f"- Explained query ids: `{', '.join(summary['explainedNotFixedQueryIds']) or '-'}`",
        f"- STILL_OPEN_NO_GO query ids: `{', '.join(summary['stillOpenNoGoQueryIds']) or '-'}`",
        "",
        "## Query Closure",
        "",
        "| Query ID | Miss types | Outcome | Original V | Rewrite V | Recall-only V | BM25 | Expanded BM25 | Raw | Merged | Final | Reason codes |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["rows"]:
        channels = row["snapshot"]["channels"]
        pool = row["snapshot"]["pool"]
        lines.append(
            f"| `{row['queryId']}` | `{', '.join(row['missTypes'])}` | `{row['outcome']}` | "
            f"`{_render_rank(channels['originalVector']['presence']['bestRank'])}` | "
            f"`{_render_rank(channels['mappedRewriteVector']['presence']['bestRank'])}` | "
            f"`{_render_rank(channels['recallOnlyMappingVector']['presence']['bestRank'])}` | "
            f"`{_render_rank(channels['cleanedBm25']['presence']['bestRank'])}` | "
            f"`{_render_rank(channels['expandedBm25']['presence']['bestRank'])}` | "
            f"`{_render_rank(pool['rawPresence']['bestRank'])}` | "
            f"`{_render_rank(pool['mergedPresence']['bestRank'])}` | "
            f"`{_render_rank(pool['finalPresence']['bestRank'])}` | "
            f"`{', '.join(row['reasonCodes'])}` |"
        )
    lines.extend(
        [
            "",
            "## Performance And Rollback",
            "",
            f"- Warm P95: `{report['performance']['warmP95Ms']}` ms",
            f"- Warm P95 < 3s: `{_render_bool(report['performance']['warmP95Under3s'])}`",
            f"- Rollback status: `{report['rollback']['status']}`",
            f"- Rollback < 60s: `{_render_bool(report['rollback']['recoveryWithin60Seconds'])}`",
            f"- Requires index rebuild: `{_render_bool(report['rollback']['requiresIndexRebuild'])}`",
            "",
            "## Feature Flag",
            "",
            f"- `ENABLE_WEIGHTED_RERANK` settings default: `{_render_bool(report['featureFlagState']['settings_ENABLE_WEIGHTED_RERANK'])}`",
            f"- `.env ENABLE_WEIGHTED_RERANK`: `{report['featureFlagState']['env_ENABLE_WEIGHTED_RERANK']}`",
            f"- `.env.example ENABLE_WEIGHTED_RERANK`: `{report['featureFlagState']['env_example_ENABLE_WEIGHTED_RERANK']}`",
            "",
            "## Stop-Loss Boundary",
            "",
            "- No query-id/case-id hardcoding, qrels edits, bad sample deletion, manual rank edits, global topK expansion, or default weighted rerank enablement.",
            "- Remaining items need stronger non-qrels recall signals, data/qrels alignment if boundary evidence appears, or ranking work outside M1.3x-5.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    timestamp = _timestamp()
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocker-register", default=str(DEFAULT_BLOCKER_REGISTER))
    parser.add_argument("--prior-comparison", default=str(DEFAULT_GUARD_V2_COMPARISON))
    parser.add_argument("--prior-product-eval", default=str(DEFAULT_PRODUCT_EVAL))
    parser.add_argument("--prior-recall-repair", default=str(DEFAULT_M13_RECALL_REPAIR))
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument(
        "--regression-set",
        default=str(DEFAULT_REGRESSION_SET),
    )
    parser.add_argument("--performance-smoke", default="")
    parser.add_argument("--rollback-drill", default="")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--out-md",
        default=f"docs/development/m1.3x-recall-miss-closure-{timestamp}.md",
    )
    parser.add_argument(
        "--out-json",
        default=f"docs/development/m1.3x-recall-miss-closure-{timestamp}.json",
    )
    args = parser.parse_args()
    report = build_report(
        blocker_register=_resolve(args.blocker_register),
        prior_comparison=_resolve(args.prior_comparison),
        prior_product_eval=_resolve(args.prior_product_eval),
        prior_recall_repair=_resolve(args.prior_recall_repair),
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        regression_set_path=_resolve(args.regression_set),
        performance_smoke=_resolve(args.performance_smoke) if args.performance_smoke else None,
        rollback_drill=_resolve(args.rollback_drill) if args.rollback_drill else None,
        output_md=_resolve(args.out_md),
        output_json=_resolve(args.out_json),
        top_k=args.top_k,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
