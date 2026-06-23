"""Sanitized M1.2-4 recall-path replay and repair report.

This script never writes raw query text, candidate text, or chunk text.
It focuses only on candidate recall stages.
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

from app.core.config import Settings  # noqa: E402
from app.eval.product_eval import (  # noqa: E402
    DEFAULT_PRODUCT_CASES,
    DEFAULT_PRODUCT_CHUNKS,
    DEFAULT_PRODUCT_QRELS,
    DEFAULT_PRODUCT_QUERIES,
    RELEVANCE_THRESHOLD,
    load_product_qrels,
    read_jsonl,
)
from app.query_processing import QueryProcessingService  # noqa: E402
from app.retrieval import (  # noqa: E402
    BM25_FALLBACK_SOURCE,
    BM25_RELAXED_RECALL_SOURCE,
    BM25FallbackRetriever,
    ChromaCollectionAdapter,
    OllamaEmbeddingClient,
    ORIGINAL_VECTOR_SOURCE,
    VARIANT_VECTOR_SOURCE,
    VectorRetrievalService,
    merge_case_candidates,
)
from app.retrieval.models import VectorCandidate  # noqa: E402


DEFAULT_BAD_CASES = PROJECT_ROOT / "docs/development/m1.2-product-bad-cases-bm25-pool-step5.3.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs/development" / (
    f"m1.2-recall-repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
)
DEFAULT_REPORT_JSON = PROJECT_ROOT / "docs/development" / (
    f"m1.2-recall-repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
)


def _hash_query(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT / value


def _load_recall_miss_query_ids(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids: list[str] = []
    for bad_case in payload.get("bad_cases", []):
        labels = set(bad_case.get("reason_labels", []))
        if "RECALL_MISS" in labels:
            query_id = str(bad_case.get("eval_query_id") or "").strip()
            if query_id:
                ids.append(query_id)
    return ids


def _expanded_query(plan) -> str:
    parts = [plan.cleaned_query, *plan.query_variants, *plan.legal_elements, plan.case_cause_hint]
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = str(part or "").strip()
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return " ".join(unique)


def _dedupe_case_ids(chunks: list[Any]) -> list[str]:
    case_ids: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        case_id = str(getattr(chunk, "case_id", "") or "")
        if case_id and case_id not in seen:
            case_ids.append(case_id)
            seen.add(case_id)
    return case_ids


def _best_relevant_rank(case_ids: list[str], rels: dict[str, int]) -> int | None:
    for index, case_id in enumerate(case_ids, 1):
        if rels.get(case_id, 0) >= RELEVANCE_THRESHOLD:
            return index
    return None


def _top10_hit(case_ids: list[str], rels: dict[str, int]) -> bool:
    return any(rels.get(case_id, 0) >= RELEVANCE_THRESHOLD for case_id in case_ids[:10])


def _to_candidate(chunk: Any, *, stage: str, matched_by_rewrite: bool) -> VectorCandidate:
    score = float(getattr(chunk, "vector_score", None) if getattr(chunk, "vector_score", None) is not None else chunk.score)
    return VectorCandidate(
        case_id=chunk.case_id,
        chunk_id=chunk.chunk_id,
        vector_score=score,
        retrieval_source=chunk.retrieval_source,
        metadata=dict(chunk.metadata),
        matched_text=chunk.text,
        source=chunk.source,
        distance=chunk.distance,
        retrieval_score=score,
        candidate_source=chunk.retrieval_source,
        recall_stage=stage,
        matched_by_vector=chunk.retrieval_source in {ORIGINAL_VECTOR_SOURCE, VARIANT_VECTOR_SOURCE},
        matched_by_bm25=chunk.retrieval_source.startswith(BM25_FALLBACK_SOURCE),
        matched_by_rewrite=matched_by_rewrite,
        filtered_reason="not_filtered",
        dedup_reason="case_level_merge_pending",
    )


def build_report(
    *,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    bad_cases_path: Path,
    output_md: Path,
    output_json: Path,
) -> dict[str, Any]:
    queries = {str(row.get("eval_query_id") or ""): row for row in read_jsonl(queries_path)}
    qrels = load_product_qrels(qrels_path)
    recall_ids = _load_recall_miss_query_ids(bad_cases_path)

    repaired_settings = Settings(
        ENABLE_QUERY_REWRITE=False,
        ENABLE_WEIGHTED_RERANK=False,
        ENABLE_SUMMARY=False,
        ENABLE_EXPANDED_SEARCH=False,
        EMBEDDING_TIMEOUT_SECONDS=6.0,
        EMBEDDING_WARMUP_TIMEOUT_SECONDS=6.0,
        CHROMA_QUERY_TIMEOUT_SECONDS=6,
    )
    query_service = QueryProcessingService(config=repaired_settings)
    bm25 = BM25FallbackRetriever(cases_path=cases_path, chunks_path=chunks_path)
    repaired_retrieval = VectorRetrievalService(
        embedding_client=OllamaEmbeddingClient(config=repaired_settings),
        vector_store=ChromaCollectionAdapter(config=repaired_settings),
        fallback_retriever=bm25,
    )

    rows: list[dict[str, Any]] = []
    loss_counter: Counter[str] = Counter()
    repaired_counter: Counter[str] = Counter()

    for query_id in recall_ids:
        query = queries.get(query_id)
        rels = qrels.get(query_id, {})
        if not query or not rels:
            continue
        query_text = str(query.get("query_text") or "")
        plan = query_service.process(query_text)
        original_bm25_chunks = bm25.search(
            plan.cleaned_query,
            top_k=50,
            retrieval_source=BM25_FALLBACK_SOURCE,
        )
        expanded_bm25_chunks = bm25.search(
            _expanded_query(plan),
            top_k=50,
            retrieval_source="bm25_fallback_expanded_query",
        )
        repaired_result = repaired_retrieval.retrieve(plan)
        merged = merge_case_candidates(repaired_result.candidates)
        merged_case_ids = [candidate.case_id for candidate in merged]

        original_vector_ids = _dedupe_case_ids(
            [chunk for chunk in repaired_result.candidates if chunk.retrieval_source == ORIGINAL_VECTOR_SOURCE]
        )
        variant_vector_ids = _dedupe_case_ids(
            [chunk for chunk in repaired_result.candidates if chunk.retrieval_source == VARIANT_VECTOR_SOURCE]
        )
        original_bm25_ids = _dedupe_case_ids(original_bm25_chunks)
        expanded_bm25_ids = _dedupe_case_ids(expanded_bm25_chunks)

        merged_top = merged[0] if merged else None
        before_rank = _best_relevant_rank(original_bm25_ids, rels)
        after_rank = _best_relevant_rank(merged_case_ids, rels)

        if before_rank is None:
            if after_rank is not None:
                repaired_counter["vector_recovered_from_total_miss"] += 1
            else:
                loss_counter["still_missing_after_repair"] += 1
        elif before_rank > 10:
            if after_rank is not None and after_rank <= 10:
                repaired_counter["entered_top10_after_repair"] += 1
            else:
                loss_counter["still_outside_top10_after_repair"] += 1

        if _best_relevant_rank(original_vector_ids, rels) is None:
            loss_counter["original_vector_miss"] += 1
        if _best_relevant_rank(variant_vector_ids, rels) is None:
            loss_counter["rewrite_vector_miss"] += 1
        if _best_relevant_rank(original_bm25_ids, rels) is None:
            loss_counter["bm25_cleaned_miss"] += 1
        if _best_relevant_rank(expanded_bm25_ids, rels) is None:
            loss_counter["bm25_expanded_miss"] += 1

        repaired_path = "none"
        if after_rank is not None:
            if _best_relevant_rank(original_vector_ids, rels) is not None or _best_relevant_rank(variant_vector_ids, rels) is not None:
                repaired_path = "vector"
            elif _best_relevant_rank(expanded_bm25_ids, rels) is not None:
                repaired_path = "bm25_expanded"
            elif _best_relevant_rank(original_bm25_ids, rels) is not None:
                repaired_path = "bm25_cleaned"

        rows.append(
            {
                "eval_query_id": query_id,
                "input_hash": _hash_query(query_text),
                "input_length": len(query_text),
                "mapping_used": plan.local_mapping_used,
                "query_variant_count": len(plan.query_variants),
                "legal_element_count": len(plan.legal_elements),
                "repaired_retrieval_degraded": repaired_result.degraded,
                "repaired_retrieval_degraded_reasons": repaired_result.degraded_reasons,
                "original_vector_best_rank": _best_relevant_rank(original_vector_ids, rels),
                "rewrite_vector_best_rank": _best_relevant_rank(variant_vector_ids, rels),
                "bm25_cleaned_best_rank": before_rank,
                "bm25_expanded_best_rank": _best_relevant_rank(expanded_bm25_ids, rels),
                "merged_best_rank_after_repair": after_rank,
                "before_top10_hit": _top10_hit(original_bm25_ids, rels),
                "after_top10_hit": _top10_hit(merged_case_ids, rels),
                "candidate_count_after_repair": len(merged_case_ids),
                "candidate_source_after_repair": merged_top.candidate_source if merged_top else None,
                "recall_stage_after_repair": merged_top.recall_stage if merged_top else [],
                "matched_by_vector_after_repair": bool(merged_top.matched_by_vector) if merged_top else False,
                "matched_by_bm25_after_repair": bool(merged_top.matched_by_bm25) if merged_top else False,
                "matched_by_rewrite_after_repair": bool(merged_top.matched_by_rewrite) if merged_top else False,
                "dedup_reason_after_repair": merged_top.dedup_reason if merged_top else None,
                "repair_path": repaired_path,
                "relevant_case_ids": sorted(
                    case_id for case_id, score in rels.items() if score >= RELEVANCE_THRESHOLD
                )[:10],
            }
        )

    summary = {
        "version": "m1_2_recall_repair_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "privacy": {
            "raw_query_text_written": False,
            "candidate_full_text_written": False,
            "chunk_text_written": False,
        },
        "inputs": {
            "queries": str(queries_path),
            "qrels": str(qrels_path),
            "cases": str(cases_path),
            "chunks": str(chunks_path),
            "bad_cases": str(bad_cases_path),
        },
        "outputs": {
            "report_md": str(output_md),
            "report_json": str(output_json),
        },
        "sample_count": len(rows),
        "loss_distribution": dict(sorted(loss_counter.items())),
        "repair_distribution": dict(sorted(repaired_counter.items())),
        "rows": rows,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_render_markdown(summary), encoding="utf-8")
    return summary


def _render_markdown(summary: dict[str, Any]) -> str:
    rows = summary["rows"]
    repaired_rows = [row for row in rows if row["after_top10_hit"]]
    report_md = summary["outputs"]["report_md"]
    report_json = summary["outputs"]["report_json"]
    lines = [
        "# M1.2-4 Recall Repair Report",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        "- Scope: `M1.2-4 候选召回修复`",
        "- Privacy: raw query text, candidate text, and chunk text are excluded.",
        "",
        "## Inputs Read",
        "",
        f"- `{summary['inputs']['queries']}`",
        f"- `{summary['inputs']['qrels']}`",
        f"- `{summary['inputs']['cases']}`",
        f"- `{summary['inputs']['chunks']}`",
        f"- `{summary['inputs']['bad_cases']}`",
        "- `apps/api/app/query_processing/service.py`",
        "- `apps/api/app/query_processing/term_mapping.py`",
        "- `apps/api/app/retrieval/service.py`",
        "- `apps/api/app/retrieval/merge.py`",
        "- `apps/api/app/retrieval/bm25_fallback.py`",
        "- `apps/api/app/eval/product_eval.py`",
        "",
        "## Repair Summary",
        "",
        f"- Sampled `RECALL_MISS` queries: `{summary['sample_count']}`",
        f"- Loss distribution: `{json.dumps(summary['loss_distribution'], ensure_ascii=False)}`",
        f"- Repair distribution: `{json.dumps(summary['repair_distribution'], ensure_ascii=False)}`",
        "",
        "## Stage Definitions",
        "",
        "- `candidateSource`: 候选最终代表来源，按合并后的候选来源组合输出。",
        "- `recallStage`: 候选命中的召回阶段列表，例如 `original_query_vector`、`rewrite_or_mapped_query_vector`、`bm25_fallback`。",
        "- `matchedByVector`: 是否被任一向量召回路径命中。",
        "- `matchedByBm25`: 是否被 BM25 fallback / relaxed recall 命中。",
        "- `matchedByRewrite`: 是否依赖 query variant / legal element / mapped expansion 才命中。",
        "- `filteredReason`: 本步骤固定写 `not_filtered`，用于后续 5.5 之前继续区分过滤截断。",
        "- `dedupReason`: case 级去重保留哪个 top chunk 的说明。",
        "",
        "## Typical Before / After",
        "",
        "| Query ID | Before BM25 best rank | Original vector best rank | Rewrite vector best rank | After merged best rank | Before Top10 | After Top10 | Repair path |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows[:12]:
        lines.append(
            f"| `{row['eval_query_id']}` | `{row['bm25_cleaned_best_rank']}` | `{row['original_vector_best_rank']}` | "
            f"`{row['rewrite_vector_best_rank']}` | `{row['merged_best_rank_after_repair']}` | "
            f"`{row['before_top10_hit']}` | `{row['after_top10_hit']}` | `{row['repair_path']}` |"
        )
    lines.extend(
        [
            "",
            "## Candidate Repair Interpretation",
            "",
            f"- After repair, Top10-hit samples in this replay: `{len(repaired_rows)}/{len(rows)}`.",
            "- Default repair keeps current TopK values. No rerank score formula, fusion weight, or feature flag default was changed.",
            "- Expanded BM25 hybrid supplement was tested offline and left as controlled experiment only because it increased candidate count but hurt ranking stability.",
            "",
            "## Repro Commands",
            "",
            "```text",
            "cd apps/api",
            f"python scripts/m1_2_recall_repair.py --out-md \"{report_md}\" --out-json \"{report_json}\"",
            "python -m app.eval.product_eval --out \"../../docs/development/m1.2-product-eval-product-chain-post54-no-rerank.json\" --bad-cases-out \"../../docs/development/m1.2-product-bad-cases-product-chain-post54-no-rerank.json\" --comparison-mode product_chain --no-current-weighted-rerank",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--bad-cases", default=str(DEFAULT_BAD_CASES))
    parser.add_argument("--out-md", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--out-json", default=str(DEFAULT_REPORT_JSON))
    args = parser.parse_args()

    summary = build_report(
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        bad_cases_path=_resolve(args.bad_cases),
        output_md=_resolve(args.out_md),
        output_json=_resolve(args.out_json),
    )
    print(json.dumps(
        {
            "status": "ok",
            "sample_count": summary["sample_count"],
            "loss_distribution": summary["loss_distribution"],
            "repair_distribution": summary["repair_distribution"],
            "out_md": str(_resolve(args.out_md)),
            "out_json": str(_resolve(args.out_json)),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
