"""M1.3-3 sanitized targeted recall repair comparison.

Raw queries are read only in memory. Outputs contain IDs, ranks, booleans,
metrics, and structured reason labels only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
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
    RELEVANCE_THRESHOLD,
    load_product_case_ids,
    load_product_qrels,
    read_jsonl,
)
from app.query_processing import QueryProcessingService  # noqa: E402
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


DEFAULT_TRIAGE = PROJECT_ROOT / "docs/development/m1.3-regression-triage-20260609-205353.json"


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


def _query_hash(query_text: str) -> str:
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def _load_target_items(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in payload.get("triage_items", []):
        recall = item.get("recall_miss") or {}
        if not bool(recall.get("in_final_bad_case")):
            continue
        rows.append(
            {
                "queryId": str(item.get("query_id") or ""),
                "targetCaseIds": [str(value) for value in item.get("target_case_ids", [])],
                "primaryCause": str(item.get("primary_cause") or ""),
                "priority": str(item.get("priority") or ""),
            }
        )
    if not rows:
        raise ValueError("triage artifact contains no final RECALL_MISS targets")
    return rows


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


def _case_order(items: Iterable[Any]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    for item in items:
        case_id = str(getattr(item, "case_id", "") or "")
        if case_id and case_id not in seen:
            order.append(case_id)
            seen.add(case_id)
    return order


def _source_case_order(candidates: list[VectorCandidate], source: str) -> list[str]:
    return _case_order(
        candidate
        for candidate in candidates
        if candidate.retrieval_source == source
    )


def _presence(case_ids: list[str], rels: dict[str, int]) -> dict[str, Any]:
    for rank, case_id in enumerate(case_ids, 1):
        if rels.get(case_id, 0) >= RELEVANCE_THRESHOLD:
            return {"hit": True, "bestRank": rank, "caseId": case_id}
    return {"hit": False, "bestRank": None, "caseId": None}


def _relevant_occurrences(
    candidates: list[VectorCandidate],
    rels: dict[str, int],
) -> int:
    return sum(
        1
        for candidate in candidates
        if rels.get(candidate.case_id, 0) >= RELEVANCE_THRESHOLD
    )


def _candidate_snapshot(
    *,
    candidates: list[VectorCandidate],
    merged: list[CaseCandidate],
    final_case_ids: list[str],
    cleaned_bm25: list[RetrievedChunk],
    expanded_bm25: list[RetrievedChunk],
    rels: dict[str, int],
) -> dict[str, Any]:
    raw_presence = _presence(_case_order(candidates), rels)
    merged_presence = _presence(_case_order(merged), rels)
    final_presence = _presence(final_case_ids, rels)
    relevant_occurrences = _relevant_occurrences(candidates, rels)
    final_top10_hit = bool(
        final_presence["hit"]
        and int(final_presence["bestRank"] or 999999) <= 10
    )
    return {
        "channels": {
            "originalVector": _presence(
                _source_case_order(candidates, ORIGINAL_VECTOR_SOURCE),
                rels,
            ),
            "mappedRewriteVector": _presence(
                _source_case_order(candidates, VARIANT_VECTOR_SOURCE),
                rels,
            ),
            "recallOnlyMappingVector": _presence(
                _source_case_order(candidates, RECALL_ONLY_VECTOR_SOURCE),
                rels,
            ),
            "cleanedBm25": _presence(_case_order(cleaned_bm25), rels),
            "expandedBm25": _presence(_case_order(expanded_bm25), rels),
            "controlledBm25Supplement": _presence(
                _source_case_order(candidates, CONTROLLED_BM25_SUPPLEMENT_SOURCE),
                rels,
            ),
        },
        "candidatePool": {
            "rawCandidateCount": len(candidates),
            "mergedCandidateCount": len(merged),
            "rawRelevantPresence": raw_presence,
            "mergedRelevantPresence": merged_presence,
            "relevantCandidateOccurrencesBeforeMerge": relevant_occurrences,
            "dedupeAppliedToRelevant": relevant_occurrences > 1,
            "droppedByDedupe": raw_presence["hit"] and not merged_presence["hit"],
            "droppedByTop10OrGating": merged_presence["hit"] and not final_top10_hit,
        },
        "final": {
            "relevantPresence": final_presence,
            "top10Hit": final_top10_hit,
        },
    }


def _repair_path(before: dict[str, Any], after: dict[str, Any]) -> str:
    if after["channels"]["controlledBm25Supplement"]["hit"]:
        return "CONTROLLED_BM25_SUPPLEMENT"
    if after["channels"]["recallOnlyMappingVector"]["hit"]:
        return "RECALL_ONLY_QUERY_MAPPING"
    if after["final"]["top10Hit"] and not before["final"]["top10Hit"]:
        return "TARGETED_CANDIDATE_ADMISSION"
    return "NONE"


def _final_reason(
    *,
    primary_cause: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> str:
    if after["final"]["top10Hit"] and not before["final"]["top10Hit"]:
        return "TARGETED_RECALL_RECOVERED"
    if after["candidatePool"]["droppedByDedupe"]:
        return "CANDIDATE_MERGE_GATING"
    if (
        after["candidatePool"]["mergedRelevantPresence"]["hit"]
        and not after["final"]["top10Hit"]
    ):
        return "RANKING_SUPPRESSION_DEFER_M1_3_4"
    if primary_cause == "QRELS_OR_DATA_MISMATCH":
        return "QRELS_OR_DATA_MISMATCH_EVIDENCE_UNRESOLVED"
    return "RECALL_POOL_MISS"


def _ranked_case_ids(
    reranker: FactSimilarityReranker,
    plan: Any,
    merged: list[CaseCandidate],
) -> list[str]:
    return [row.candidate.case_id for row in reranker.rerank(plan, merged)]


def _runner_summary(
    before_path: Path,
    after_path: Path,
) -> dict[str, Any]:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    before_by_id = {row["queryId"]: row for row in before.get("perQuery", [])}
    after_by_id = {row["queryId"]: row for row in after.get("perQuery", [])}
    new_regressions: set[str] = set()
    typical_success_changes: list[dict[str, Any]] = []
    for query_id, before_row in before_by_id.items():
        after_row = after_by_id.get(query_id)
        if not after_row:
            continue
        if (
            before_row.get("changeLabel") != "REGRESSED"
            and after_row.get("changeLabel") == "REGRESSED"
        ):
            new_regressions.add(query_id)
        if (
            before_row.get("afterVsBaselineLabel") != "REGRESSED"
            and after_row.get("afterVsBaselineLabel") == "REGRESSED"
        ):
            new_regressions.add(query_id)
        if before_row.get("sampleType") == "typical_success":
            typical_success_changes.append(
                {
                    "queryId": query_id,
                    "beforeTop10Hit": before_row["metrics"]["currentAfter"]["Top10 hit"],
                    "afterTop10Hit": after_row["metrics"]["currentAfter"]["Top10 hit"],
                    "beforeNdcgAt10": before_row["metrics"]["currentAfter"]["NDCG@10"],
                    "afterNdcgAt10": after_row["metrics"]["currentAfter"]["NDCG@10"],
                    "beforeChangeLabel": before_row.get("changeLabel"),
                    "afterChangeLabel": after_row.get("changeLabel"),
                }
            )
    return {
        "beforeArtifact": _relative(before_path),
        "afterArtifact": _relative(after_path),
        "before": {
            "top10HitRate": before["overallMetrics"]["currentAfter"]["Top10 hit rate"],
            "recallMissCount": before["recallMissCount"],
            "beforeVsAfterRegressedCount": before["beforeVsAfterRegressedCount"],
            "afterVsBaselineRegressedCount": before["afterVsBaselineRegressedCount"],
            "metricRegressionCount": before["metricRegressionCount"],
        },
        "after": {
            "top10HitRate": after["overallMetrics"]["currentAfter"]["Top10 hit rate"],
            "recallMissCount": after["recallMissCount"],
            "beforeVsAfterRegressedCount": after["beforeVsAfterRegressedCount"],
            "afterVsBaselineRegressedCount": after["afterVsBaselineRegressedCount"],
            "metricRegressionCount": after["metricRegressionCount"],
            "top10MissCount": after["top10MissCount"],
            "grayCandidateHardGatePassed": after["grayCandidateHardGatePassed"],
            "weightedRerankGrayCandidate": after["weightedRerankGrayCandidate"],
            "hardGateDataComplete": after["m13RegressionGate"]["hardGateDataComplete"],
            "missingInputs": after["m13RegressionGate"]["missingInputs"],
            "blockedItems": after["blockedItems"],
        },
        "newFixedRegressions": sorted(new_regressions),
        "typicalSuccess": typical_success_changes,
    }


def _performance_summary(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "artifact": None,
            "warmP95Ms": None,
            "warmP95Under3s": False,
            "blocked": True,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "artifact": _relative(path),
        "warmP95Ms": payload["api"]["warm_response_total_duration_ms"]["p95"],
        "warmApiWallP95Ms": payload["api"]["warm_api_wall_ms"]["p95"],
        "warmP95Under3s": payload["api"]["warm_p95_under_3s"],
        "blocked": False,
    }


def build_report(
    *,
    triage_path: Path,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
    regression_before_path: Path,
    regression_after_path: Path,
    performance_path: Path | None,
    output_md: Path,
    output_json: Path,
) -> dict[str, Any]:
    targets = _load_target_items(triage_path)
    queries = {
        str(row.get("eval_query_id") or ""): row
        for row in read_jsonl(queries_path)
    }
    qrels = load_product_qrels(qrels_path)
    corpus_case_ids = load_product_case_ids(cases_path)
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
    before_service = VectorRetrievalService(
        embedding_client=embedding,
        vector_store=vector_store,
        fallback_retriever=bm25,
        embedding_cache=None,
        enable_targeted_recall_repairs=False,
    )
    after_service = VectorRetrievalService(
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
            top_k=50,
            retrieval_source=BM25_FALLBACK_SOURCE,
        )
        expanded_bm25 = bm25.search(
            _expanded_query(plan),
            top_k=50,
            retrieval_source="bm25_fallback_expanded_query",
        )
        before_result = before_service.retrieve(plan)
        after_result = after_service.retrieve(plan)
        before_merged = merge_case_candidates(before_result.candidates)
        after_merged = merge_case_candidates(after_result.candidates)
        before_snapshot = _candidate_snapshot(
            candidates=before_result.candidates,
            merged=before_merged,
            final_case_ids=_ranked_case_ids(reranker, plan, before_merged),
            cleaned_bm25=cleaned_bm25,
            expanded_bm25=expanded_bm25,
            rels=rels,
        )
        after_snapshot = _candidate_snapshot(
            candidates=after_result.candidates,
            merged=after_merged,
            final_case_ids=_ranked_case_ids(reranker, plan, after_merged),
            cleaned_bm25=cleaned_bm25,
            expanded_bm25=expanded_bm25,
            rels=rels,
        )
        relevant_case_ids = sorted(
            case_id
            for case_id, score in rels.items()
            if score >= RELEVANCE_THRESHOLD
        )
        rows.append(
            {
                **target,
                "inputHash": _query_hash(query_text),
                "inputLength": len(query_text),
                "qrelCaseIdsMissingFromCorpus": sorted(
                    set(relevant_case_ids) - corpus_case_ids
                ),
                "queryPlanSignals": {
                    "mappingUsed": plan.local_mapping_used,
                    "mappingLabels": list(plan.mapping_labels),
                    "queryVariantCount": len(plan.query_variants),
                    "recallOnlyVariantCount": len(plan.recall_only_query_variants),
                    "legalElementCount": len(plan.legal_elements),
                    "caseCauseHintPresent": bool(plan.case_cause_hint),
                },
                "before": before_snapshot,
                "after": after_snapshot,
                "repairPath": _repair_path(before_snapshot, after_snapshot),
                "finalReason": _final_reason(
                    primary_cause=target["primaryCause"],
                    before=before_snapshot,
                    after=after_snapshot,
                ),
            }
        )

    runner = _runner_summary(regression_before_path, regression_after_path)
    performance = _performance_summary(performance_path)
    report = {
        "version": "m1_3_recall_repair_v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "scope": "M1.3-3 remaining RECALL_MISS targeted repair only",
        "privacy": {
            "rawQueryTextWritten": False,
            "caseFactTextWritten": False,
            "candidateTextWritten": False,
            "chunkTextWritten": False,
            "fixtureContent": "sanitized_or_fictional",
        },
        "inputs": {
            "triage": _relative(triage_path),
            "queries": _relative(queries_path),
            "qrels": _relative(qrels_path),
            "cases": _relative(cases_path),
            "chunks": _relative(chunks_path),
        },
        "outputs": {
            "markdown": _relative(output_md),
            "json": _relative(output_json),
        },
        "repairRules": [
            {
                "id": "RECALL_ONLY_QUERY_MAPPING",
                "boundary": (
                    "Only the two confirmed mapping types emit a separate recall-only "
                    "vector query; no legal/cause weighting signal is added."
                ),
            },
            {
                "id": "CONTROLLED_BM25_SUPPLEMENT",
                "boundary": (
                    "Only unmapped queries with 5-40 vector cases and at most four "
                    "Top10 overlaps admit at most four BM25-only cases at the rank-5 anchor."
                ),
            },
        ],
        "runnerComparison": runner,
        "performance": performance,
        "targetQueryCount": len(rows),
        "recoveredQueryIds": [
            row["queryId"]
            for row in rows
            if row["after"]["final"]["top10Hit"]
            and not row["before"]["final"]["top10Hit"]
        ],
        "rows": rows,
        "boundaries": {
            "rerankModified": False,
            "guardModified": False,
            "qrelsModified": False,
            "evalSamplesModified": False,
            "featureFlagDefaultsModified": False,
            "enteredM13Step4": False,
            "globalEnableWeightedRerank": bool(settings.ENABLE_WEIGHTED_RERANK),
        },
    }
    markdown = _render_markdown(report)
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for raw_query in raw_queries:
        if raw_query and (raw_query in markdown or raw_query in json_text):
            raise ValueError("privacy check failed: raw query found in output")
    forbidden_fields = (
        '"query_text"',
        '"raw_query"',
        '"case_text"',
        '"candidate_text"',
        '"chunk_text"',
        '"matched_text"',
    )
    if any(field in json_text for field in forbidden_fields):
        raise ValueError("privacy check failed: forbidden text field found")

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json_text, encoding="utf-8")
    output_md.write_text(markdown, encoding="utf-8")
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    runner = report["runnerComparison"]
    lines = [
        "# M1.3-3 Remaining RECALL_MISS Targeted Repair",
        "",
        f"- Generated at: `{report['generatedAt']}`",
        f"- Target queries: `{report['targetQueryCount']}`",
        "- Privacy: raw query, case fact, candidate, and chunk text are excluded.",
        "",
        "## Outcome",
        "",
        f"- RECALL_MISS: `{runner['before']['recallMissCount']}` -> `{runner['after']['recallMissCount']}`",
        f"- Top10 hit rate: `{runner['before']['top10HitRate']}` -> `{runner['after']['top10HitRate']}`",
        f"- Recovered query ids: `{', '.join(report['recoveredQueryIds'])}`",
        f"- New fixed regressions: `{len(runner['newFixedRegressions'])}`",
        f"- Warm P95: `{report['performance']['warmP95Ms']}` ms",
        "",
        "## Candidate Presence",
        "",
        "| Query ID | Cause | Original V | Rewrite V | Recall-only V | Clean BM25 | Expanded BM25 | Supplement | Merge before -> after | Final before -> after | Result |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["rows"]:
        before = row["before"]
        after = row["after"]
        channel = after["channels"]
        lines.append(
            f"| `{row['queryId']}` | `{row['primaryCause']}` | "
            f"`{channel['originalVector']['bestRank']}` | "
            f"`{channel['mappedRewriteVector']['bestRank']}` | "
            f"`{channel['recallOnlyMappingVector']['bestRank']}` | "
            f"`{channel['cleanedBm25']['bestRank']}` | "
            f"`{channel['expandedBm25']['bestRank']}` | "
            f"`{channel['controlledBm25Supplement']['bestRank']}` | "
            f"`{before['candidatePool']['mergedRelevantPresence']['bestRank']} -> "
            f"{after['candidatePool']['mergedRelevantPresence']['bestRank']}` | "
            f"`{before['final']['relevantPresence']['bestRank']} -> "
            f"{after['final']['relevantPresence']['bestRank']}` | "
            f"`{row['finalReason']}` |"
        )
    lines.extend(
        [
            "",
            "## Repair Boundaries",
            "",
        ]
    )
    for rule in report["repairRules"]:
        lines.append(f"- `{rule['id']}`: {rule['boundary']}")
    lines.extend(
        [
            "",
            "## Hard Gate",
            "",
            f"- `beforeVsAfterRegressedCount`: `{runner['after']['beforeVsAfterRegressedCount']}`",
            f"- `afterVsBaselineRegressedCount`: `{runner['after']['afterVsBaselineRegressedCount']}`",
            f"- `metricRegressionCount`: `{runner['after']['metricRegressionCount']}`",
            f"- `top10MissCount`: `{runner['after']['top10MissCount']}`",
            f"- `recallMissCount`: `{runner['after']['recallMissCount']}`",
            f"- `hardGateDataComplete`: `{str(runner['after']['hardGateDataComplete']).lower()}`",
            f"- `grayCandidateHardGatePassed`: `{str(runner['after']['grayCandidateHardGatePassed']).lower()}`",
            f"- `weightedRerankGrayCandidate`: `{str(runner['after']['weightedRerankGrayCandidate']).lower()}`",
            "",
            "## Scope Confirmation",
            "",
            "- Rerank, guard, qrels, evaluation samples, and feature flag defaults were not modified.",
            "- M1.3-4 was not entered; this report covers M1.3-3 only.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    timestamp = _timestamp()
    parser = argparse.ArgumentParser()
    parser.add_argument("--triage", default=str(DEFAULT_TRIAGE))
    parser.add_argument("--queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--regression-before", required=True)
    parser.add_argument("--regression-after", required=True)
    parser.add_argument("--performance-smoke", default="")
    parser.add_argument(
        "--out-md",
        default=f"docs/development/m1.3-recall-repair-{timestamp}.md",
    )
    parser.add_argument(
        "--out-json",
        default=f"docs/development/m1.3-recall-repair-{timestamp}.json",
    )
    args = parser.parse_args()
    report = build_report(
        triage_path=_resolve(args.triage),
        queries_path=_resolve(args.queries),
        qrels_path=_resolve(args.qrels),
        cases_path=_resolve(args.cases),
        chunks_path=_resolve(args.chunks),
        regression_before_path=_resolve(args.regression_before),
        regression_after_path=_resolve(args.regression_after),
        performance_path=(
            _resolve(args.performance_smoke)
            if args.performance_smoke
            else None
        ),
        output_md=_resolve(args.out_md),
        output_json=_resolve(args.out_json),
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "targetQueryCount": report["targetQueryCount"],
                "recoveredQueryIds": report["recoveredQueryIds"],
                "recallMissBefore": report["runnerComparison"]["before"]["recallMissCount"],
                "recallMissAfter": report["runnerComparison"]["after"]["recallMissCount"],
                "top10HitRateBefore": report["runnerComparison"]["before"]["top10HitRate"],
                "top10HitRateAfter": report["runnerComparison"]["after"]["top10HitRate"],
                "newFixedRegressionCount": len(
                    report["runnerComparison"]["newFixedRegressions"]
                ),
                "outputMarkdown": report["outputs"]["markdown"],
                "outputJson": report["outputs"]["json"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
