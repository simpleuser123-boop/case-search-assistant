"""R4 evaluation corpus preflight.

Checks both evaluation lines without fabricating readiness:
- LeCaRDv2 query/qrels/candidate corpus readability and candidate-id overlap.
- Product-local query/qrels/case corpus readiness and case-id overlap.

The report intentionally writes counts, paths, case ids, and status enums only.
It does not write raw query text or document/chunk text.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.eval.bm25_baseline import iter_corpus_docs, read_jsonl


DEFAULT_LECARD_QUERIES = PROJECT_ROOT / "data/eval/lecardv2_queries.jsonl"
DEFAULT_LECARD_QRELS = PROJECT_ROOT / "data/eval/lecardv2_qrels.jsonl"
DEFAULT_LECARD_CORPUS = Path(r"C:\Users\yyl\Downloads\LeCaRDv2-main\candidate")
DEFAULT_PRODUCT_QUERIES = PROJECT_ROOT / "data/eval/product_eval_queries.jsonl"
DEFAULT_PRODUCT_QRELS = PROJECT_ROOT / "data/eval/product_eval_qrels.jsonl"
DEFAULT_PRODUCT_CASES = PROJECT_ROOT / "data/processed/cases.jsonl"
DEFAULT_PRODUCT_CHUNKS = PROJECT_ROOT / "data/processed/chunks.jsonl"


@dataclass(frozen=True)
class JsonlReadResult:
    rows: list[dict[str, Any]]
    error: str | None = None


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_jsonl_if_present(path: Path) -> JsonlReadResult:
    if not path.is_file():
        return JsonlReadResult(rows=[], error="file_missing")
    try:
        return JsonlReadResult(rows=read_jsonl(path), error=None)
    except Exception as exc:  # noqa: BLE001 - preflight must explain bad input
        return JsonlReadResult(rows=[], error=f"read_failed:{exc.__class__.__name__}")


def _query_ids(rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(row.get("eval_query_id") or "").strip()
        for row in rows
        if str(row.get("eval_query_id") or "").strip()
    }


def _qrels_by_query(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        query_id = str(row.get("eval_query_id") or "").strip()
        candidate_id = str(row.get("candidate_case_id") or row.get("case_id") or "").strip()
        if not query_id or not candidate_id:
            continue
        try:
            relevance = int(row.get("relevance", 0))
        except (TypeError, ValueError):
            relevance = 0
        qrels[query_id][candidate_id] = max(qrels[query_id].get(candidate_id, 0), relevance)
    return qrels


def _qrel_candidate_ids(qrels: dict[str, dict[str, int]]) -> set[str]:
    return {candidate_id for rels in qrels.values() for candidate_id in rels}


def _relevant_query_count(qrels: dict[str, dict[str, int]], *, threshold: int = 2) -> int:
    return sum(1 for rels in qrels.values() if any(score >= threshold for score in rels.values()))


def _count_product_chunks(chunks_path: Path) -> dict[str, Any]:
    if not chunks_path.is_file():
        return {"path": str(chunks_path), "found": False, "chunk_count": 0, "case_id_count": 0}
    chunk_count = 0
    case_ids: set[str] = set()
    try:
        with chunks_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                chunk_count += 1
                row = json.loads(line)
                case_id = str(row.get("case_id") or "").strip()
                if case_id:
                    case_ids.add(case_id)
    except Exception as exc:  # noqa: BLE001 - preflight must return reason
        return {
            "path": str(chunks_path),
            "found": True,
            "readable": False,
            "reason": f"read_failed:{exc.__class__.__name__}",
            "chunk_count": chunk_count,
            "case_id_count": len(case_ids),
        }
    return {
        "path": str(chunks_path),
        "found": True,
        "readable": True,
        "chunk_count": chunk_count,
        "case_id_count": len(case_ids),
    }


def _load_product_case_ids(cases_path: Path) -> tuple[set[str], dict[str, Any]]:
    if not cases_path.is_file():
        return set(), {"path": str(cases_path), "found": False, "doc_count": 0}
    case_ids: set[str] = set()
    duplicate_counter: Counter[str] = Counter()
    try:
        with cases_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                row = json.loads(line)
                case_id = str(row.get("case_id") or "").strip()
                if case_id:
                    duplicate_counter[case_id] += 1
                    case_ids.add(case_id)
    except Exception as exc:  # noqa: BLE001
        return case_ids, {
            "path": str(cases_path),
            "found": True,
            "readable": False,
            "reason": f"read_failed:{exc.__class__.__name__}",
            "doc_count": len(case_ids),
        }
    duplicate_count = sum(count - 1 for count in duplicate_counter.values() if count > 1)
    return case_ids, {
        "path": str(cases_path),
        "found": True,
        "readable": True,
        "doc_count": len(case_ids),
        "duplicate_case_id_count": duplicate_count,
    }


def _load_lecard_corpus_ids(corpus_path: Path) -> tuple[set[str], dict[str, Any]]:
    if not corpus_path.exists():
        return set(), {
            "path": str(corpus_path),
            "found": False,
            "readable": False,
            "doc_count": 0,
            "reason": "candidate_corpus_path_missing",
        }
    candidate_ids: set[str] = set()
    try:
        for candidate_id, _text in iter_corpus_docs(corpus_path):
            if candidate_id:
                candidate_ids.add(str(candidate_id))
    except Exception as exc:  # noqa: BLE001
        return candidate_ids, {
            "path": str(corpus_path),
            "found": True,
            "readable": False,
            "doc_count": len(candidate_ids),
            "reason": f"candidate_corpus_read_failed:{exc.__class__.__name__}",
        }
    if not candidate_ids:
        return candidate_ids, {
            "path": str(corpus_path),
            "found": True,
            "readable": True,
            "doc_count": 0,
            "reason": "candidate_corpus_empty_or_unrecognized",
        }
    return candidate_ids, {
        "path": str(corpus_path),
        "found": True,
        "readable": True,
        "doc_count": len(candidate_ids),
    }


def check_lecardv2(
    *,
    queries_path: Path,
    qrels_path: Path,
    corpus_path: Path,
) -> dict[str, Any]:
    queries_result = _read_jsonl_if_present(queries_path)
    qrels_result = _read_jsonl_if_present(qrels_path)
    query_ids = _query_ids(queries_result.rows)
    qrels = _qrels_by_query(qrels_result.rows)
    qrel_query_ids = set(qrels)
    qrel_candidate_ids = _qrel_candidate_ids(qrels)
    corpus_ids, corpus_report = _load_lecard_corpus_ids(corpus_path)
    overlap_ids = corpus_ids & qrel_candidate_ids
    errors: list[str] = []

    if queries_result.error:
        errors.append(f"queries_{queries_result.error}")
    if qrels_result.error:
        errors.append(f"qrels_{qrels_result.error}")
    if not query_ids:
        errors.append("queries_empty_or_missing_eval_query_id")
    if not qrels:
        errors.append("qrels_empty_or_missing_candidate_case_id")
    if query_ids and qrel_query_ids and not (query_ids & qrel_query_ids):
        errors.append("query_qrels_eval_query_id_overlap_zero")
    if not corpus_report.get("readable"):
        errors.append(str(corpus_report.get("reason") or "candidate_corpus_unreadable"))
    elif int(corpus_report.get("doc_count") or 0) <= 0:
        errors.append("candidate_corpus_doc_count_zero")
    elif not overlap_ids:
        errors.append("candidate_qrels_id_overlap_zero")

    status = "ok" if not errors else "blocked"
    return {
        "status": status,
        "errors": errors,
        "queries": {
            "path": str(queries_path),
            "read_error": queries_result.error,
            "query_count": len(queries_result.rows),
            "eval_query_id_count": len(query_ids),
        },
        "qrels": {
            "path": str(qrels_path),
            "read_error": qrels_result.error,
            "qrels_count": sum(len(rels) for rels in qrels.values()),
            "qrels_query_count": len(qrels),
            "relevant_query_count": _relevant_query_count(qrels),
            "candidate_id_count": len(qrel_candidate_ids),
            "query_overlap_count": len(query_ids & qrel_query_ids),
        },
        "candidate_corpus": corpus_report,
        "id_overlap": {
            "qrels_candidate_id_count": len(qrel_candidate_ids),
            "candidate_doc_id_count": len(corpus_ids),
            "overlap_count": len(overlap_ids),
            "sample_overlap_ids": sorted(overlap_ids)[:10],
            "sample_qrels_ids_without_doc": sorted(qrel_candidate_ids - corpus_ids)[:10],
        },
    }


def check_product_eval(
    *,
    queries_path: Path,
    qrels_path: Path,
    cases_path: Path,
    chunks_path: Path,
) -> dict[str, Any]:
    queries_result = _read_jsonl_if_present(queries_path)
    qrels_result = _read_jsonl_if_present(qrels_path)
    query_ids = _query_ids(queries_result.rows)
    qrels = _qrels_by_query(qrels_result.rows)
    qrel_query_ids = set(qrels)
    qrel_candidate_ids = _qrel_candidate_ids(qrels)
    case_ids, cases_report = _load_product_case_ids(cases_path)
    chunks_report = _count_product_chunks(chunks_path)
    overlap_ids = case_ids & qrel_candidate_ids
    labeled_query_count = _relevant_query_count(qrels)
    errors: list[str] = []

    if queries_result.error:
        errors.append(f"queries_{queries_result.error}")
    if qrels_result.error:
        errors.append(f"qrels_{qrels_result.error}")
    if len(queries_result.rows) < 20:
        errors.append("product_query_count_below_20")
    if len(queries_result.rows) > 50:
        errors.append("product_query_count_above_50")
    if labeled_query_count < 10:
        errors.append("product_labeled_query_count_below_10")
    if not cases_report.get("readable"):
        errors.append(str(cases_report.get("reason") or "product_cases_unreadable"))
    elif int(cases_report.get("doc_count") or 0) <= 0:
        errors.append("product_case_doc_count_zero")
    if not chunks_report.get("readable"):
        errors.append(str(chunks_report.get("reason") or "product_chunks_unreadable"))
    if query_ids and qrel_query_ids and not (query_ids & qrel_query_ids):
        errors.append("product_query_qrels_overlap_zero")
    if qrel_candidate_ids and not overlap_ids:
        errors.append("product_qrels_case_id_overlap_zero")

    status = "ok" if not errors else "blocked"
    return {
        "status": status,
        "errors": errors,
        "queries": {
            "path": str(queries_path),
            "read_error": queries_result.error,
            "query_count": len(queries_result.rows),
            "eval_query_id_count": len(query_ids),
        },
        "qrels": {
            "path": str(qrels_path),
            "read_error": qrels_result.error,
            "qrels_count": sum(len(rels) for rels in qrels.values()),
            "qrels_query_count": len(qrels),
            "relevant_query_count": labeled_query_count,
            "candidate_id_count": len(qrel_candidate_ids),
            "query_overlap_count": len(query_ids & qrel_query_ids),
        },
        "candidate_corpus": {
            "cases": cases_report,
            "chunks": chunks_report,
        },
        "id_overlap": {
            "qrels_candidate_id_count": len(qrel_candidate_ids),
            "product_case_id_count": len(case_ids),
            "overlap_count": len(overlap_ids),
            "sample_overlap_ids": sorted(overlap_ids)[:10],
            "sample_qrels_ids_without_case": sorted(qrel_candidate_ids - case_ids)[:10],
        },
    }


def build_report(
    *,
    lecard_queries: Path = DEFAULT_LECARD_QUERIES,
    lecard_qrels: Path = DEFAULT_LECARD_QRELS,
    lecard_corpus: Path = DEFAULT_LECARD_CORPUS,
    product_queries: Path = DEFAULT_PRODUCT_QUERIES,
    product_qrels: Path = DEFAULT_PRODUCT_QRELS,
    product_cases: Path = DEFAULT_PRODUCT_CASES,
    product_chunks: Path = DEFAULT_PRODUCT_CHUNKS,
) -> dict[str, Any]:
    lecard = check_lecardv2(
        queries_path=lecard_queries,
        qrels_path=lecard_qrels,
        corpus_path=lecard_corpus,
    )
    product = check_product_eval(
        queries_path=product_queries,
        qrels_path=product_qrels,
        cases_path=product_cases,
        chunks_path=product_chunks,
    )
    usable_eval_lines = [
        line
        for line, line_report in {
            "standard_lecardv2": lecard,
            "product_local": product,
        }.items()
        if line_report.get("status") == "ok"
    ]
    status = "ok" if lecard["status"] == "ok" and product["status"] == "ok" else "blocked"
    return {
        "version": "m1_1_r4_eval_corpus_preflight_v1",
        "status": status,
        "m1_2_minimum_status": "ok" if usable_eval_lines else "blocked",
        "usable_eval_lines": usable_eval_lines,
        "line_roles": {
            "standard_lecardv2": "standard academic/retrieval benchmark reference; not a product release decision line",
            "product_local": "product-local JuDGE corpus evaluation; release decision basis for current product corpus",
        },
        "privacy": {
            "raw_query_text_written": False,
            "candidate_full_text_written": False,
            "chunk_text_written": False,
        },
        "lecardv2": lecard,
        "product_local": product,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lecard-queries", default=str(DEFAULT_LECARD_QUERIES))
    parser.add_argument("--lecard-qrels", default=str(DEFAULT_LECARD_QRELS))
    parser.add_argument("--lecard-corpus", default=str(DEFAULT_LECARD_CORPUS))
    parser.add_argument("--product-queries", default=str(DEFAULT_PRODUCT_QUERIES))
    parser.add_argument("--product-qrels", default=str(DEFAULT_PRODUCT_QRELS))
    parser.add_argument("--product-cases", default=str(DEFAULT_PRODUCT_CASES))
    parser.add_argument("--product-chunks", default=str(DEFAULT_PRODUCT_CHUNKS))
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="Return exit code 0 even when a line is blocked; report status remains blocked.",
    )
    args = parser.parse_args()

    report = build_report(
        lecard_queries=_resolve(args.lecard_queries),
        lecard_qrels=_resolve(args.lecard_qrels),
        lecard_corpus=_resolve(args.lecard_corpus),
        product_queries=_resolve(args.product_queries),
        product_qrels=_resolve(args.product_qrels),
        product_cases=_resolve(args.product_cases),
        product_chunks=_resolve(args.product_chunks),
    )
    if args.out:
        output_path = _resolve(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "ok" and not args.allow_blocked:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
