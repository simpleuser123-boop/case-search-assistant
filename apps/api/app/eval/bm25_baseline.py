# -*- coding: utf-8 -*-
"""BM25 baseline for LeCaRDv2-style case retrieval evaluation.

This intentionally uses only the Python standard library. It is a baseline and
fallback path, not a production retriever.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import tarfile
from collections import Counter, defaultdict
from pathlib import Path


RE_LATIN = re.compile(r"[a-zA-Z0-9]+")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")


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


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def tokenize(text: str) -> list[str]:
    """Conservative Chinese baseline tokens: legal phrases + CJK bigrams + alnum."""
    text = text or ""
    tokens: list[str] = []
    legal_terms = [
        "交通肇事", "肇事逃逸", "非法吸收公众存款", "组织领导传销活动", "开设赌场",
        "贩卖毒品", "容留他人吸毒", "污染环境", "危险废物", "盗窃", "抢劫",
        "敲诈勒索", "故意毁坏财物", "非法占用农用地", "故意伤害", "诈骗",
        "掩饰隐瞒犯罪所得", "自首", "坦白", "缓刑", "从犯", "未遂",
    ]
    compact = re.sub(r"\s+", "", text)
    for term in legal_terms:
        if term in compact:
            tokens.append(term)
    tokens.extend(m.group(0).lower() for m in RE_LATIN.finditer(text))
    cjk = RE_CJK.findall(text)
    tokens.extend("".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1)))
    return tokens


def normalize_official_doc(obj: dict) -> tuple[str, str] | None:
    """Support official LeCaRDv2 candidates and MTEB corpus.jsonl variants."""
    pid = None
    for key in ("pid", "_id", "id", "docid"):
        if obj.get(key) is not None:
            pid = obj[key]
            break
    if pid is None:
        return None
    structured_parts = [
        obj.get("fact", ""),
        obj.get("reason", ""),
        obj.get("result", ""),
        obj.get("title", ""),
    ]
    fallback_parts = [
        obj.get("text", ""),
        obj.get("qw", ""),
        obj.get("title", ""),
    ]
    parts = structured_parts if any(structured_parts) else fallback_parts
    text = "\n".join(str(p) for p in parts if p)
    if not text.strip():
        return None
    return str(pid), text


def iter_tar_json_objects(path: Path):
    with tarfile.open(path) as tf:
        for member in tf:
            if not member.isfile() or not member.name.lower().endswith((".json", ".jsonl")):
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            if member.name.lower().endswith(".jsonl"):
                for raw in extracted:
                    line = raw.decode("utf-8").strip()
                    if line:
                        yield json.loads(line)
            else:
                yield json.loads(extracted.read().decode("utf-8"))


def iter_json_objects(path: Path):
    if path.suffix.lower() in {".tar", ".tgz", ".gz"} and tarfile.is_tarfile(path):
        yield from iter_tar_json_objects(path)
        return
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        yield from payload
    elif isinstance(payload, dict):
        if any(k in payload for k in ("pid", "_id", "id", "docid")):
            yield payload
        else:
            for key, value in payload.items():
                if isinstance(value, dict):
                    value = {"pid": key, **value}
                    yield value


def iter_corpus_docs(corpus_path: Path):
    if not corpus_path.exists():
        return
    if corpus_path.is_file():
        files = [corpus_path]
    else:
        files = [
            p for p in corpus_path.rglob("*")
            if p.is_file() and (
                p.suffix.lower() in {".json", ".jsonl", ".tar", ".tgz", ".gz"}
                or tarfile.is_tarfile(p)
            )
        ]
    for path in files:
        for obj in iter_json_objects(path):
            normalized = normalize_official_doc(obj)
            if normalized:
                yield normalized


def build_index(corpus_path: Path, query_vocab: set[str] | None = None):
    doc_len: dict[str, int] = {}
    df: Counter = Counter()
    postings: defaultdict[str, list[tuple[str, int]]] = defaultdict(list)
    seen_docs = 0
    indexed_docs = 0
    for pid, text in iter_corpus_docs(corpus_path):
        seen_docs += 1
        tokens = tokenize(text)
        if not tokens:
            continue
        doc_len[pid] = len(tokens)
        tf = Counter(tokens)
        if query_vocab is not None:
            tf = Counter({term: freq for term, freq in tf.items() if term in query_vocab})
        if not tf:
            continue
        indexed_docs += 1
        df.update(tf.keys())
        for term, freq in tf.items():
            postings[term].append((pid, freq))
    avg_len = sum(doc_len.values()) / max(1, len(doc_len))
    return {
        "seen_docs": seen_docs,
        "indexed_docs": indexed_docs,
        "doc_len": doc_len,
        "df": df,
        "postings": postings,
        "avg_len": avg_len,
    }


def bm25_rank(query: str, n_docs: int, doc_len: dict[str, int], df: Counter,
              postings: dict[str, list[tuple[str, int]]], avg_len: float,
              top_k: int = 100) -> list[tuple[str, float]]:
    q_terms = Counter(tokenize(query))
    if not q_terms:
        return []
    n_docs = max(1, n_docs)
    k1 = 1.5
    b = 0.75
    scores: defaultdict[str, float] = defaultdict(float)
    for term, q_weight in q_terms.items():
        n_t = df.get(term, 0)
        if n_t <= 0:
            continue
        idf = math.log(1 + (n_docs - n_t + 0.5) / (n_t + 0.5))
        for pid, freq in postings.get(term, []):
            denom = freq + k1 * (1 - b + b * doc_len[pid] / max(avg_len, 1e-9))
            scores[pid] += q_weight * idf * (freq * (k1 + 1) / denom)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in read_jsonl(path):
        qrels[row["eval_query_id"]][str(row["candidate_case_id"])] = int(row["relevance"])
    return qrels


def precision_at(ranked: list[str], rels: dict[str, int], k: int = 5, threshold: int = 2) -> float:
    if k <= 0:
        return 0.0
    hits = sum(1 for pid in ranked[:k] if rels.get(pid, 0) >= threshold)
    return hits / k


def dcg(labels: list[int], k: int) -> float:
    total = 0.0
    for idx, label in enumerate(labels[:k], 1):
        total += (2 ** label - 1) / math.log2(idx + 1)
    return total


def ndcg_at(ranked: list[str], rels: dict[str, int], k: int = 10) -> float:
    gains = [rels.get(pid, 0) for pid in ranked[:k]]
    ideal = sorted(rels.values(), reverse=True)[:k]
    ideal_dcg = dcg(ideal, k)
    if ideal_dcg == 0:
        return 0.0
    return dcg(gains, k) / ideal_dcg


def evaluate(queries_path: Path, qrels_path: Path, corpus_path: Path, out_path: Path,
             limit_queries: int = 0, top_k: int = 100) -> dict:
    queries = read_jsonl(queries_path)
    if limit_queries:
        queries = queries[:limit_queries]
    qrels = load_qrels(qrels_path)
    has_corpus = corpus_path.exists() and any(iter_corpus_docs(corpus_path))
    if not has_corpus:
        report = {
            "status": "blocked_missing_candidate_corpus",
            "reason": "LeCaRDv2 candidate case texts were not found; query/qrels are ready, but BM25 cannot rank without corpus documents.",
            "corpus_path": str(corpus_path),
            "query_count": len(queries),
            "qrels_query_count": len(qrels),
            "required_next_step": "Download/extract LeCaRDv2 candidate case texts into the candidate directory or pass --corpus to a corpus.jsonl file.",
        }
        write_json(out_path, report)
        return report

    query_vocab = set()
    for query in queries:
        query_vocab.update(tokenize(query.get("query_text", "")))
    index = build_index(corpus_path, query_vocab=query_vocab)
    doc_len = index["doc_len"]
    df = index["df"]
    postings = index["postings"]
    avg_len = index["avg_len"]
    per_query = []
    p5_sum = 0.0
    ndcg10_sum = 0.0
    evaluated = 0
    for query in queries:
        qid = query["eval_query_id"]
        rels = qrels.get(qid, {})
        ranked_pairs = bm25_rank(
            query.get("query_text", ""),
            index["seen_docs"],
            doc_len,
            df,
            postings,
            avg_len,
            top_k=top_k,
        )
        ranked = [pid for pid, _ in ranked_pairs]
        p5 = precision_at(ranked, rels, 5)
        n10 = ndcg_at(ranked, rels, 10)
        p5_sum += p5
        ndcg10_sum += n10
        evaluated += 1
        per_query.append({
            "eval_query_id": qid,
            "source_query_id": query.get("source_query_id"),
            "precision_at_5": round(p5, 4),
            "ndcg_at_10": round(n10, 4),
            "top10": [{"candidate_case_id": pid, "score": round(score, 6), "relevance": rels.get(pid, 0)}
                      for pid, score in ranked_pairs[:10]],
        })
    report = {
        "status": "ok",
        "method": "bm25_char_bigram_baseline",
        "query_count": len(queries),
        "evaluated_query_count": evaluated,
        "corpus_doc_count": index["seen_docs"],
        "indexed_doc_count": index["indexed_docs"],
        "query_vocab_size": len(query_vocab),
        "precision_at_5": round(p5_sum / max(1, evaluated), 4),
        "ndcg_at_10": round(ndcg10_sum / max(1, evaluated), 4),
        "per_query": per_query,
    }
    write_json(out_path, report)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="data/eval/lecardv2_queries.jsonl")
    ap.add_argument("--qrels", default="data/eval/lecardv2_qrels.jsonl")
    ap.add_argument("--corpus", default=r"C:\Users\yyl\Downloads\LeCaRDv2-main\candidate")
    ap.add_argument("--out", default="data/eval/bm25_baseline_report.json")
    ap.add_argument("--limit-queries", type=int, default=0)
    ap.add_argument("--top-k", type=int, default=100)
    args = ap.parse_args()
    report = evaluate(
        Path(args.queries),
        Path(args.qrels),
        Path(args.corpus),
        Path(args.out),
        args.limit_queries,
        args.top_k,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
