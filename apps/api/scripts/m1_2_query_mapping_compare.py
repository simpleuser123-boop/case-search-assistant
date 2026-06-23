# -*- coding: utf-8 -*-
"""Sanitized M1.2-3 comparison for original query vs local mapped query.

The report never writes raw query text, candidate text, or chunk text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings
from app.eval.product_eval import (  # noqa: E402
    DEFAULT_PRODUCT_CHUNKS,
    DEFAULT_PRODUCT_QRELS,
    DEFAULT_PRODUCT_QUERIES,
    RELEVANCE_THRESHOLD,
    load_product_qrels,
    ndcg_at,
    precision_at,
    read_jsonl,
    top_k_has_hit,
)
from app.query_processing import QueryProcessingService  # noqa: E402
from app.query_processing.service import clean_query  # noqa: E402
from app.retrieval import BM25FallbackRetriever  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "docs/development" / (
    f"m1.2-query-mapping-compare-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
)
DEFAULT_BAD_CASES = PROJECT_ROOT / "docs/development/m1.2-product-bad-cases-bm25-pool-20260609-144147-run1.json"


def _hash_query(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def _dedupe_case_ids(chunks: list[Any]) -> list[str]:
    case_ids: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        case_id = str(getattr(chunk, "case_id", "") or "")
        if case_id and case_id not in seen:
            case_ids.append(case_id)
            seen.add(case_id)
    return case_ids


def _top10_case_cause_counts(chunks: list[Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for chunk in chunks:
        case_id = str(getattr(chunk, "case_id", "") or "")
        if not case_id or case_id in seen:
            continue
        seen.add(case_id)
        metadata = dict(getattr(chunk, "metadata", {}) or {})
        cause = str(metadata.get("case_cause") or metadata.get("crime_type") or "unknown").strip()
        counts[cause[:40] or "unknown"] += 1
        if len(seen) >= 10:
            break
    return dict(sorted(counts.items()))


def _best_relevant_rank(case_ids: list[str], rels: dict[str, int]) -> int | None:
    for index, case_id in enumerate(case_ids, 1):
        if rels.get(case_id, 0) >= RELEVANCE_THRESHOLD:
            return index
    return None


def _expanded_query_for_plan(plan) -> str:
    parts = [
        plan.cleaned_query,
        *plan.query_variants,
        *plan.legal_elements,
        plan.case_cause_hint,
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = str(part or "").strip()
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return " ".join(unique)


def _load_recall_miss_query_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for bad_case in payload.get("bad_cases", []):
        labels = set(bad_case.get("reason_labels", []))
        if "RECALL_MISS" in labels:
            ids.add(str(bad_case.get("eval_query_id") or ""))
    return ids


def run_compare(
    *,
    queries_path: Path,
    qrels_path: Path,
    chunks_path: Path,
    bad_cases_path: Path,
    output_path: Path,
    top_k: int,
    limit_queries: int,
) -> dict[str, Any]:
    queries = read_jsonl(queries_path)
    if limit_queries:
        queries = queries[:limit_queries]
    qrels = load_product_qrels(qrels_path)
    recall_miss_ids = _load_recall_miss_query_ids(bad_cases_path)
    query_service = QueryProcessingService(
        config=Settings(
            ENABLE_QUERY_REWRITE=False,
            ENABLE_SUMMARY=False,
            ENABLE_EXPANDED_SEARCH=False,
            ENABLE_WEIGHTED_RERANK=False,
        )
    )
    retriever = BM25FallbackRetriever(chunks_path=chunks_path)

    rows: list[dict[str, Any]] = []
    for query in queries:
        eval_query_id = str(query.get("eval_query_id") or "").strip()
        query_text = str(query.get("query_text") or "")
        rels = qrels.get(eval_query_id, {})
        if not eval_query_id or not rels:
            continue

        cleaned_query = clean_query(query_text)
        plan = query_service.process(query_text)
        original_chunks = retriever.search(cleaned_query, top_k=top_k, retrieval_source="m1_2_original_bm25")
        mapped_chunks = retriever.search(
            _expanded_query_for_plan(plan),
            top_k=top_k,
            retrieval_source="m1_2_mapped_bm25",
        )
        original_ids = _dedupe_case_ids(original_chunks)
        mapped_ids = _dedupe_case_ids(mapped_chunks)
        original_rank = _best_relevant_rank(original_ids, rels)
        mapped_rank = _best_relevant_rank(mapped_ids, rels)
        original_hit = top_k_has_hit(original_ids, rels, k=10)
        mapped_hit = top_k_has_hit(mapped_ids, rels, k=10)
        cause_counts = _top10_case_cause_counts(mapped_chunks)
        high_hint = bool(plan.high_confidence_mappings and plan.case_cause_hint)
        hint_top10_count = cause_counts.get(plan.case_cause_hint, 0) if high_hint else 0
        cause_noise_warning = bool(high_hint and hint_top10_count == 0 and len(cause_counts) >= 5)

        rows.append(
            {
                "eval_query_id": eval_query_id,
                "input_hash": _hash_query(query_text),
                "input_length": len(query_text),
                "mapping_used": plan.local_mapping_used,
                "mapping_version": plan.mapping_version,
                "high_confidence_mappings": plan.high_confidence_mappings,
                "low_confidence_mappings": plan.low_confidence_mappings,
                "inferred_query_understanding_gap": bool(eval_query_id in recall_miss_ids and plan.local_mapping_used),
                "original_top10_has_hit": original_hit,
                "mapped_top10_has_hit": mapped_hit,
                "original_best_relevant_rank": original_rank,
                "mapped_best_relevant_rank": mapped_rank,
                "original_precision_at_5": round(precision_at(original_ids, rels, k=5), 4),
                "mapped_precision_at_5": round(precision_at(mapped_ids, rels, k=5), 4),
                "original_ndcg_at_10": round(ndcg_at(original_ids, rels, k=10), 4),
                "mapped_ndcg_at_10": round(ndcg_at(mapped_ids, rels, k=10), 4),
                "top10_case_cause_counts": cause_counts,
                "case_cause_noise_warning": cause_noise_warning,
                "effect": _effect_label(original_hit, mapped_hit, original_rank, mapped_rank),
            }
        )

    mapped_rows = [row for row in rows if row["mapping_used"]]
    gap_rows = [row for row in mapped_rows if row["inferred_query_understanding_gap"]]
    improved = [row for row in mapped_rows if row["effect"] in {"top10_fixed", "rank_improved"}]
    regressed = [row for row in mapped_rows if row["effect"] in {"top10_lost", "rank_regressed"}]
    noise_warnings = [row for row in mapped_rows if row["case_cause_noise_warning"]]
    report = {
        "version": "m1_2_query_mapping_compare_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "privacy": {
            "raw_query_text_written": False,
            "candidate_full_text_written": False,
            "chunk_text_written": False,
        },
        "inputs": {
            "queries": str(queries_path),
            "qrels": str(qrels_path),
            "chunks": str(chunks_path),
            "bad_cases": str(bad_cases_path),
        },
        "summary": {
            "evaluated_query_count": len(rows),
            "mapped_query_count": len(mapped_rows),
            "inferred_query_understanding_gap_count": len(gap_rows),
            "mapped_improved_count": len(improved),
            "mapped_regressed_count": len(regressed),
            "case_cause_noise_warning_count": len(noise_warnings),
        },
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _effect_label(
    original_hit: bool,
    mapped_hit: bool,
    original_rank: int | None,
    mapped_rank: int | None,
) -> str:
    if not original_hit and mapped_hit:
        return "top10_fixed"
    if original_hit and not mapped_hit:
        return "top10_lost"
    if original_rank is not None and mapped_rank is not None:
        if mapped_rank < original_rank:
            return "rank_improved"
        if mapped_rank > original_rank:
            return "rank_regressed"
    if original_rank is None and mapped_rank is not None:
        return "recall_added_outside_top10"
    if original_rank is not None and mapped_rank is None:
        return "recall_lost_outside_top10"
    return "unchanged"


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT / value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--bad-cases", default=str(DEFAULT_BAD_CASES))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--top-k", type=int, default=100)
    parser.add_argument("--limit-queries", type=int, default=0)
    args = parser.parse_args()
    report = run_compare(
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        chunks_path=_resolve(args.chunks),
        bad_cases_path=_resolve(args.bad_cases),
        output_path=_resolve(args.out),
        top_k=args.top_k,
        limit_queries=args.limit_queries,
    )
    print(json.dumps({"status": "ok", "summary": report["summary"], "out": args.out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
