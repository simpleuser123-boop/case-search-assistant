# -*- coding: utf-8 -*-
"""Day0 4.5: prepare a repeatable LeCaRDv2 evaluation set.

Input is the official LeCaRDv2 GitHub layout:
  LeCaRDv2-main/
    query/{query,train_query,test_query,common_query,controversial_query,procedural_query}.json
    label/{relevence,test_relevence}.trec
    candidate/...

The GitHub archive includes query/qrels, while candidate case texts are a
separate download. This script standardizes the available query/qrels and
records candidate-corpus readiness explicitly instead of producing fake metrics.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.query_processing.term_mapping import DEFAULT_TERM_MAPPINGS, TERM_MAPPING_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_LECARD_ROOT = PROJECT_ROOT / "data/external/LeCaRDv2-main"
VERSION = "lecardv2_day0_v1"
TERM_MAPPINGS = DEFAULT_TERM_MAPPINGS


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} is not valid JSONL") from exc
    return rows


def read_query_type_map(root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    files = {
        "common": root / "query" / "common_query.json",
        "controversial": root / "query" / "controversial_query.json",
        "procedural": root / "query" / "procedural_query.json",
    }
    for query_type, path in files.items():
        if not path.exists():
            continue
        for row in read_jsonl(path):
            mapping[str(row["id"])] = query_type
    return mapping


def read_qrels(path: Path, allowed_qids: set[str] | None = None) -> list[dict]:
    qrels = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 4:
                raise ValueError(f"{path}:{line_no} must be qid 0 pid label")
            qid, _, pid, label_raw = parts
            if allowed_qids is not None and qid not in allowed_qids:
                continue
            label = int(label_raw)
            qrels.append({
                "eval_query_id": f"lecardv2_q{qid}",
                "source_query_id": qid,
                "candidate_case_id": pid,
                "relevance": label,
                "is_relevant": label >= 2,
                "version": VERSION,
            })
    return qrels


def find_candidate_corpus(root: Path) -> dict:
    candidate_dir = root / "candidate"
    if not candidate_dir.exists():
        return {"found": False, "path": str(candidate_dir), "reason": "candidate directory missing"}
    files = [
        p for p in candidate_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".json", ".jsonl", ".tar", ".tgz", ".gz"}
    ]
    if not files:
        return {
            "found": False,
            "path": str(candidate_dir),
            "reason": "candidate directory has no .json/.jsonl/.tar case texts",
            "download_hint": "LeCaRDv2 README: candidate cases are a separate Google Drive download.",
        }
    return {
        "found": True,
        "path": str(candidate_dir),
        "file_count": len(files),
        "formats": sorted({p.suffix.lower() for p in files}),
    }


def write_jsonl(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare(root: Path, out_dir: Path, split: str = "test", limit: int = 0) -> dict:
    split_file = {
        "all": "query.json",
        "train": "train_query.json",
        "test": "test_query.json",
    }[split]
    qrels_file = "relevence.trec" if split in {"all", "train"} else "test_relevence.trec"
    queries_raw = read_jsonl(root / "query" / split_file)
    if limit:
        queries_raw = queries_raw[:limit]
    query_type_map = read_query_type_map(root)

    queries = []
    for row in queries_raw:
        qid = str(row["id"])
        queries.append({
            "eval_query_id": f"lecardv2_q{qid}",
            "source_query_id": qid,
            "query_text": row.get("fact") or row.get("query", ""),
            "full_query_text": row.get("query", ""),
            "fact": row.get("fact", ""),
            "query_type": query_type_map.get(qid, "unknown"),
            "source": "LeCaRDv2",
            "split": split,
            "version": VERSION,
        })

    allowed_qids = {q["source_query_id"] for q in queries}
    qrels = read_qrels(root / "label" / qrels_file, allowed_qids=allowed_qids)
    labels = Counter(str(q["relevance"]) for q in qrels)
    rel_by_query = Counter(q["eval_query_id"] for q in qrels if q["is_relevant"])
    candidate_status = find_candidate_corpus(root)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "lecardv2_queries.jsonl", queries)
    write_jsonl(out_dir / "lecardv2_qrels.jsonl", qrels)
    with (out_dir / "term_mappings.json").open("w", encoding="utf-8") as f:
        json.dump({"version": TERM_MAPPING_VERSION, "mappings": TERM_MAPPINGS}, f, ensure_ascii=False, indent=2)

    report = {
        "version": VERSION,
        "source_root": str(root),
        "split": split,
        "query_count": len(queries),
        "qrels_count": len(qrels),
        "queries_with_relevant_labels": len(rel_by_query),
        "label_distribution": dict(sorted(labels.items())),
        "term_mapping_count": len(TERM_MAPPINGS),
        "candidate_corpus": candidate_status,
        "day0_45_gate": {
            "has_at_least_20_queries": len(queries) >= 20,
            "has_at_least_10_labeled_queries": len(rel_by_query) >= 10,
            "has_at_least_15_term_mappings": len(TERM_MAPPINGS) >= 15,
            "can_run_retrieval_baseline": bool(candidate_status.get("found")),
        },
    }
    with (out_dir / "lecardv2_eval_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lecard-root", default=str(DEFAULT_LECARD_ROOT))
    ap.add_argument("--out", default="data/eval")
    ap.add_argument("--split", choices=["all", "train", "test"], default="test")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    report = prepare(Path(args.lecard_root), Path(args.out), args.split, args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
