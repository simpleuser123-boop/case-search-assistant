"""Read-only BM25 fallback over Day 0 processed JuDGE chunks.

This is a degraded retrieval path for unavailable vector dependencies. It reads
only ``data/processed`` and never writes Chroma or processed corpus files.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any

from app.kernel.data.case_store.jsonl_store import CASES_PATH, CHUNKS_PATH
from app.kernel.rag.retrieval.models import RetrievedChunk

BM25_FALLBACK_SOURCE = "bm25_fallback"
RELAXED_RECALL_SUFFIX = "relaxed_recall"
BM25_RELAXED_RECALL_SOURCE = f"{BM25_FALLBACK_SOURCE}_{RELAXED_RECALL_SUFFIX}"

RE_LATIN = re.compile(r"[a-zA-Z0-9]+")
RE_CJK = re.compile(r"[\u4e00-\u9fff]")

LEGAL_TERMS = (
    "交通肇事",
    "肇事逃逸",
    "危险驾驶",
    "非法吸收公众存款",
    "组织领导传销活动",
    "开设赌场",
    "贩卖毒品",
    "容留他人吸毒",
    "污染环境",
    "危险废物",
    "盗窃",
    "抢劫",
    "敲诈勒索",
    "故意毁坏财物",
    "非法占用农用地",
    "故意伤害",
    "诈骗",
    "掩饰隐瞒犯罪所得",
    "自首",
    "坦白",
    "缓刑",
    "从犯",
    "未遂",
)


@dataclass(frozen=True)
class BM25Document:
    case_id: str
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    token_count: int


@dataclass(frozen=True)
class BM25Index:
    documents: dict[str, BM25Document]
    doc_len: dict[str, int]
    df: Counter[str]
    postings: dict[str, list[tuple[str, int]]]
    avg_len: float


@dataclass(frozen=True)
class BM25WarmupResult:
    ok: bool
    duration_ms: int
    document_count: int
    degraded_reason: str | None = None


class BM25FallbackRetriever:
    """Small in-memory BM25 retriever for processed JuDGE chunks."""

    def __init__(
        self,
        *,
        cases_path: Path = CASES_PATH,
        chunks_path: Path = CHUNKS_PATH,
    ) -> None:
        self.cases_path = cases_path
        self.chunks_path = chunks_path

    def search(
        self,
        query_text: str,
        *,
        top_k: int,
        retrieval_source: str = BM25_FALLBACK_SOURCE,
    ) -> list[RetrievedChunk]:
        index = _load_index(str(self.cases_path), str(self.chunks_path))
        return _rank(index, query_text, top_k=top_k, retrieval_source=retrieval_source)


def warmup_bm25_fallback(
    *,
    cases_path: Path = CASES_PATH,
    chunks_path: Path = CHUNKS_PATH,
) -> BM25WarmupResult:
    """Load the read-only process cache before warm search traffic."""

    started = perf_counter()
    try:
        index = _load_index(str(cases_path), str(chunks_path))
    except Exception:  # noqa: BLE001 - startup warmup reports only a sanitized code
        return BM25WarmupResult(
            ok=False,
            duration_ms=int((perf_counter() - started) * 1000),
            document_count=0,
            degraded_reason="BM25_INDEX_UNAVAILABLE",
        )
    document_count = len(index.documents)
    return BM25WarmupResult(
        ok=document_count > 0,
        duration_ms=int((perf_counter() - started) * 1000),
        document_count=document_count,
        degraded_reason=None if document_count > 0 else "BM25_INDEX_EMPTY",
    )


def tokenize(text: str) -> list[str]:
    """Conservative local tokens: legal terms, alnum, and CJK bigrams."""

    text = text or ""
    tokens: list[str] = []
    compact = re.sub(r"\s+", "", text)
    for term in LEGAL_TERMS:
        if term in compact:
            tokens.append(term)
    tokens.extend(match.group(0).lower() for match in RE_LATIN.finditer(text))
    cjk = RE_CJK.findall(text)
    tokens.extend("".join(cjk[index:index + 2]) for index in range(max(0, len(cjk) - 1)))
    return tokens


@lru_cache(maxsize=4)
def _load_index(cases_path: str, chunks_path: str) -> BM25Index:
    case_meta = _load_case_metadata(Path(cases_path))
    documents: dict[str, BM25Document] = {}
    doc_len: dict[str, int] = {}
    df: Counter[str] = Counter()
    postings: defaultdict[str, list[tuple[str, int]]] = defaultdict(list)

    for row in _iter_jsonl(Path(chunks_path)):
        case_id = str(row.get("case_id") or "")
        chunk_id = str(row.get("chunk_id") or "")
        text = str(row.get("text") or "")
        if not case_id or not chunk_id or not text.strip():
            continue

        metadata = {
            **case_meta.get(case_id, {}),
            "case_id": case_id,
            "chunk_id": chunk_id,
            "chunk_type": row.get("chunk_type") or "",
            "start_offset": row.get("start_offset"),
            "end_offset": row.get("end_offset"),
            "quality_score": row.get("quality_score"),
        }
        searchable_text = " ".join(
            value
            for value in (
                text,
                str(metadata.get("case_cause") or ""),
                str(metadata.get("title") or ""),
            )
            if value
        )
        tokens = tokenize(searchable_text)
        if not tokens:
            continue

        token_counts = Counter(tokens)
        documents[chunk_id] = BM25Document(
            case_id=case_id,
            chunk_id=chunk_id,
            text=text,
            metadata=metadata,
            token_count=len(tokens),
        )
        doc_len[chunk_id] = len(tokens)
        df.update(token_counts.keys())
        for term, freq in token_counts.items():
            postings[term].append((chunk_id, freq))

    avg_len = sum(doc_len.values()) / max(1, len(doc_len))
    return BM25Index(
        documents=documents,
        doc_len=doc_len,
        df=df,
        postings=dict(postings),
        avg_len=avg_len,
    )


def _rank(
    index: BM25Index,
    query_text: str,
    *,
    top_k: int,
    retrieval_source: str,
) -> list[RetrievedChunk]:
    query_terms = Counter(tokenize(query_text))
    if not query_terms:
        return []

    n_docs = max(1, len(index.documents))
    k1 = 1.5
    b = 0.75
    scores: defaultdict[str, float] = defaultdict(float)
    for term, query_weight in query_terms.items():
        term_df = index.df.get(term, 0)
        if term_df <= 0:
            continue
        idf = math.log(1 + (n_docs - term_df + 0.5) / (term_df + 0.5))
        for chunk_id, freq in index.postings.get(term, []):
            denominator = freq + k1 * (1 - b + b * index.doc_len[chunk_id] / max(index.avg_len, 1e-9))
            scores[chunk_id] += query_weight * idf * (freq * (k1 + 1) / denominator)

    if not scores:
        return []

    max_score = max(scores.values()) or 1.0
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:max(top_k, 0)]
    chunks: list[RetrievedChunk] = []
    for chunk_id, score in ranked:
        document = index.documents[chunk_id]
        normalized_score = max(0.0, min(1.0, score / max_score))
        chunks.append(
            RetrievedChunk(
                case_id=document.case_id,
                chunk_id=document.chunk_id,
                score=normalized_score,
                vector_score=normalized_score,
                distance=None,
                metadata=dict(document.metadata),
                text=document.text,
                source="data/processed/chunks.jsonl",
                retrieval_source=retrieval_source,
            )
        )
    return chunks


def _load_case_metadata(path: Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for row in _iter_jsonl(path):
        case_id = str(row.get("case_id") or "")
        if not case_id:
            continue
        metadata[case_id] = {
            "case_no": row.get("case_no"),
            "title": row.get("title"),
            "court": row.get("court"),
            "court_level": row.get("court_level"),
            "trial_level": row.get("trial_level"),
            "case_cause": row.get("case_cause"),
            "crime_type": row.get("crime_type"),
            "law_articles": row.get("law_articles"),
            "judgment_date": row.get("judgment_date"),
            "judgment_year": _year_from_date(row.get("judgment_date")),
            "region": row.get("region"),
            "source_url": row.get("source_url"),
            "source_name": row.get("source_name"),
            "text_hash": row.get("text_hash"),
        }
    return metadata


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                yield json.loads(line)


def _year_from_date(value: object) -> int | None:
    if not value:
        return None
    match = re.search(r"(?:19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None
