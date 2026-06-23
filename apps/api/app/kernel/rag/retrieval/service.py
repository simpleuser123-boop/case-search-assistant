"""Day 1 step 5.3 multi-vector recall with degraded BM25 fallback."""
from __future__ import annotations

import re
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from app.core.config import settings
from app.core.logging import logger
from app.kernel.rag.query_processing.models import QueryPlan
from app.kernel.rag.retrieval.bm25_fallback import (
    BM25_FALLBACK_SOURCE,
    BM25_RELAXED_RECALL_SOURCE,
    BM25FallbackRetriever,
)
from app.kernel.rag.retrieval.chroma_adapter import ChromaCollectionAdapter
from app.kernel.rag.retrieval.embedding import OllamaEmbeddingClient
from app.kernel.rag.retrieval.embedding_cache import QueryEmbeddingCache, get_default_embedding_cache
from app.kernel.rag.retrieval.models import (
    RetrievalConfigMismatchError,
    RetrievalDependencyError,
    RetrievedChunk,
    VectorCandidate,
    VectorRetrievalResult,
)

ORIGINAL_VECTOR_TOP_K = 50
VARIANT_VECTOR_TOP_K = 30
ORIGINAL_VECTOR_SOURCE = "original_vector"
VARIANT_VECTOR_SOURCE = "variant_vector"
RECALL_ONLY_VECTOR_SOURCE = "recall_only_mapping_vector"
BM25_FALLBACK_TOP_K = 50
RELAXED_RECALL_TOP_K = 30
MIN_CANDIDATES_BEFORE_RELAXED_RECALL = 5
CONTROLLED_BM25_SUPPLEMENT_SOURCE = "bm25_fallback_controlled_supplement"
CONTROLLED_BM25_SUPPLEMENT_CASE_LIMIT = 4
CONTROLLED_BM25_MAX_VECTOR_CASES = 40
CONTROLLED_BM25_MAX_TOP10_OVERLAP = 4
CONTROLLED_BM25_ADMISSION_ANCHOR_RANK = 5

EMBEDDING_UNAVAILABLE = "EMBEDDING_UNAVAILABLE"
EMBEDDING_TIMEOUT = "EMBEDDING_TIMEOUT"
EMBEDDING_MODEL_MISMATCH = "EMBEDDING_MODEL_MISMATCH"
CHROMA_UNAVAILABLE = "CHROMA_UNAVAILABLE"
CHROMA_QUERY_FAILED = "CHROMA_QUERY_FAILED"
CHROMA_QUERY_TIMEOUT = "CHROMA_QUERY_TIMEOUT"
CHROMA_EMPTY = "CHROMA_EMPTY"
BM25_FALLBACK_USED = "BM25_FALLBACK_USED"
BM25_FALLBACK_FAILED = "BM25_FALLBACK_FAILED"

KEY_PARAGRAPH_TYPES = {
    "court_found",
    "court_opinion",
    "本院查明",
    "经审理查明",
    "本院认为",
}


@dataclass(frozen=True)
class SoftFilterWeights:
    """Small retrieval-stage nudges, centralized for rollback and tuning."""

    case_cause_match: float = 0.03
    exact_year_match: float = 0.02
    close_year_match: float = 0.01
    key_paragraph_match: float = 0.02
    close_year_window: int = 2


DEFAULT_SOFT_FILTER_WEIGHTS = SoftFilterWeights()


class QueryEmbeddingClient(Protocol):
    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Return one query embedding per input text."""


class VectorStore(Protocol):
    def query(self, embedding: list[float], *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        """Return vector candidates for a single embedding."""


class FallbackRetriever(Protocol):
    def search(self, query_text: str, *, top_k: int, retrieval_source: str) -> list[RetrievedChunk]:
        """Return BM25/keyword candidates for a sanitized query."""


class VectorRetrievalService:
    def __init__(
        self,
        *,
        embedding_client: QueryEmbeddingClient | None = None,
        vector_store: VectorStore | None = None,
        fallback_retriever: FallbackRetriever | None = None,
        embedding_cache: QueryEmbeddingCache | None = None,
        soft_filter_weights: SoftFilterWeights = DEFAULT_SOFT_FILTER_WEIGHTS,
        min_candidates_before_relaxed_recall: int = MIN_CANDIDATES_BEFORE_RELAXED_RECALL,
        enable_targeted_recall_repairs: bool = True,
    ) -> None:
        self.embedding_client = embedding_client or OllamaEmbeddingClient()
        self.vector_store = vector_store or ChromaCollectionAdapter()
        self.fallback_retriever = fallback_retriever or BM25FallbackRetriever()
        self.embedding_cache = embedding_cache if embedding_cache is not None else (
            get_default_embedding_cache() if embedding_client is None else None
        )
        self.soft_filter_weights = soft_filter_weights
        self.min_candidates_before_relaxed_recall = min_candidates_before_relaxed_recall
        self.enable_targeted_recall_repairs = enable_targeted_recall_repairs

    def retrieve(self, query_plan: QueryPlan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        """Return the raw vector candidate pool for the already-sanitized plan.

        This intentionally does not do case-level dedupe, reranking, or
        summary/highlight work. BM25 is used as a degraded fallback or a
        relaxed recall supplement when the initial pool is too small or when
        the caller explicitly requests expanded recall.
        """
        degraded_reasons: list[str] = []
        embedding_started = perf_counter()
        recall_only_queries = (
            query_plan.recall_only_query_variants
            if self.enable_targeted_recall_repairs
            else []
        )
        retrieval_queries = [
            query_plan.cleaned_query,
            *query_plan.query_variants,
            *recall_only_queries,
        ]
        regular_variant_count = len(query_plan.query_variants)
        model_version = _embedding_model_version(self.embedding_client)
        input_fingerprint = _embedding_input_fingerprint(retrieval_queries)
        embedding_cache_hit = False
        try:
            embeddings = None
            if self.embedding_cache is not None:
                embeddings = self.embedding_cache.get(
                    input_hash=query_plan.input_hash,
                    model_version=model_version,
                    input_fingerprint=input_fingerprint,
                )
                embedding_cache_hit = embeddings is not None
            if embeddings is None:
                embeddings = self.embedding_client.embed_queries(retrieval_queries)
                if self.embedding_cache is not None:
                    self.embedding_cache.set(
                        input_hash=query_plan.input_hash,
                        model_version=model_version,
                        input_fingerprint=input_fingerprint,
                        vectors=embeddings,
                        metadata={
                            "input_count": len(retrieval_queries),
                            "input_lengths": [len(value or "") for value in retrieval_queries],
                        },
                    )
        except Exception as exc:  # noqa: BLE001 - dependency boundary sanitizes logging
            embedding_duration_ms = _elapsed_ms(embedding_started)
            _append_reason(degraded_reasons, _embedding_degraded_reason(exc))
            retrieval_started = perf_counter()
            raw_chunks = self._safe_bm25_fallback_chunks(
                query_plan,
                retrieval_source=BM25_FALLBACK_SOURCE,
                top_k=BM25_FALLBACK_TOP_K,
                degraded_reasons=degraded_reasons,
            )
            retrieval_duration_ms = _elapsed_ms(retrieval_started)
            return self._build_result(
                query_plan=query_plan,
                raw_chunks=raw_chunks,
                embedding_duration_ms=embedding_duration_ms,
                retrieval_duration_ms=retrieval_duration_ms,
                degraded_reasons=degraded_reasons,
                include_relaxed_recall=include_relaxed_recall,
            )
        embedding_duration_ms = _elapsed_ms(embedding_started)
        if embedding_cache_hit:
            logger.info(
                "query_embedding_cache_hit input_hash=%s model_version=%s embedding_duration_ms=%s",
                query_plan.input_hash,
                model_version,
                embedding_duration_ms,
            )

        query_years = _extract_years([query_plan.cleaned_query, *query_plan.legal_elements])
        raw_chunks: list[RetrievedChunk] = []
        retrieval_started = perf_counter()
        try:
            for index, embedding in enumerate(embeddings):
                if index == 0:
                    top_k = ORIGINAL_VECTOR_TOP_K
                    retrieval_source = ORIGINAL_VECTOR_SOURCE
                elif index <= regular_variant_count:
                    top_k = VARIANT_VECTOR_TOP_K
                    retrieval_source = VARIANT_VECTOR_SOURCE
                else:
                    top_k = VARIANT_VECTOR_TOP_K
                    retrieval_source = RECALL_ONLY_VECTOR_SOURCE
                raw_chunks.extend(
                    self.vector_store.query(
                        embedding,
                        top_k=top_k,
                        retrieval_source=retrieval_source,
                    )
                )
        except Exception as exc:  # noqa: BLE001 - Chroma boundary may raise adapter or client errors
            _append_reason(degraded_reasons, _chroma_degraded_reason(exc))
            raw_chunks = self._safe_bm25_fallback_chunks(
                query_plan,
                retrieval_source=BM25_FALLBACK_SOURCE,
                top_k=BM25_FALLBACK_TOP_K,
                degraded_reasons=degraded_reasons,
            )
            retrieval_duration_ms = _elapsed_ms(retrieval_started)
            return self._build_result(
                query_plan=query_plan,
                raw_chunks=raw_chunks,
                embedding_duration_ms=embedding_duration_ms,
                retrieval_duration_ms=retrieval_duration_ms,
                degraded_reasons=degraded_reasons,
                include_relaxed_recall=include_relaxed_recall,
            )

        if not raw_chunks:
            _append_reason(degraded_reasons, CHROMA_EMPTY)
            raw_chunks = self._safe_bm25_fallback_chunks(
                query_plan,
                retrieval_source=BM25_FALLBACK_SOURCE,
                top_k=BM25_FALLBACK_TOP_K,
                degraded_reasons=degraded_reasons,
            )

        candidates = [
            self._to_candidate(
                chunk,
                query_plan=query_plan,
                case_cause_hint=query_plan.case_cause_hint,
                query_years=query_years,
            )
            for chunk in raw_chunks
        ]
        if self.enable_targeted_recall_repairs and not include_relaxed_recall:
            candidates.extend(
                self._controlled_bm25_supplement_candidates(
                    query_plan,
                    existing_candidates=candidates,
                )
            )
        if include_relaxed_recall or _unique_case_count(candidates) < self.min_candidates_before_relaxed_recall:
            relaxed_chunks = self._relaxed_recall_chunks(
                query_plan,
                existing_chunks=raw_chunks,
                degraded_reasons=degraded_reasons,
            )
            if relaxed_chunks:
                raw_chunks.extend(relaxed_chunks)
                candidates.extend(
                    self._to_candidate(
                        chunk,
                        query_plan=query_plan,
                        case_cause_hint=query_plan.case_cause_hint,
                        query_years=query_years,
                    )
                    for chunk in relaxed_chunks
                )
                _append_reason(degraded_reasons, BM25_FALLBACK_USED)

        retrieval_duration_ms = _elapsed_ms(retrieval_started)
        logger.info(
            "vector_retrieval_completed input_hash=%s candidate_count=%s degraded=%s "
            "degraded_reasons=%s retrieval_duration_ms=%s embedding_duration_ms=%s",
            query_plan.input_hash,
            len(candidates),
            bool(degraded_reasons),
            degraded_reasons,
            retrieval_duration_ms,
            embedding_duration_ms,
        )
        return VectorRetrievalResult(
            candidates=candidates,
            retrieval_duration_ms=retrieval_duration_ms,
            embedding_duration_ms=embedding_duration_ms,
            degraded=bool(degraded_reasons),
            degraded_reasons=degraded_reasons,
        )

    def _build_result(
        self,
        *,
        query_plan: QueryPlan,
        raw_chunks: list[RetrievedChunk],
        embedding_duration_ms: int,
        retrieval_duration_ms: int,
        degraded_reasons: list[str],
        include_relaxed_recall: bool,
    ) -> VectorRetrievalResult:
        if include_relaxed_recall or _unique_chunk_case_count(raw_chunks) < self.min_candidates_before_relaxed_recall:
            relaxed_chunks = self._relaxed_recall_chunks(
                query_plan,
                existing_chunks=raw_chunks,
                degraded_reasons=degraded_reasons,
            )
            if relaxed_chunks:
                raw_chunks.extend(relaxed_chunks)
                _append_reason(degraded_reasons, BM25_FALLBACK_USED)
        query_years = _extract_years([query_plan.cleaned_query, *query_plan.legal_elements])
        candidates = [
            self._to_candidate(
                chunk,
                query_plan=query_plan,
                case_cause_hint=query_plan.case_cause_hint,
                query_years=query_years,
            )
            for chunk in raw_chunks
        ]
        logger.info(
            "vector_retrieval_degraded input_hash=%s candidate_count=%s degraded_reasons=%s "
            "retrieval_duration_ms=%s embedding_duration_ms=%s",
            query_plan.input_hash,
            len(candidates),
            degraded_reasons,
            retrieval_duration_ms,
            embedding_duration_ms,
        )
        return VectorRetrievalResult(
            candidates=candidates,
            retrieval_duration_ms=retrieval_duration_ms,
            embedding_duration_ms=embedding_duration_ms,
            degraded=True,
            degraded_reasons=degraded_reasons,
        )

    def _bm25_fallback_chunks(
        self,
        query_plan: QueryPlan,
        *,
        retrieval_source: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        return self.fallback_retriever.search(
            _bm25_recall_query(query_plan),
            top_k=top_k,
            retrieval_source=retrieval_source,
        )

    def _safe_bm25_fallback_chunks(
        self,
        query_plan: QueryPlan,
        *,
        retrieval_source: str,
        top_k: int,
        degraded_reasons: list[str],
    ) -> list[RetrievedChunk]:
        try:
            chunks = self._bm25_fallback_chunks(
                query_plan,
                retrieval_source=retrieval_source,
                top_k=top_k,
            )
        except Exception as exc:  # noqa: BLE001 - fallback must not white-screen search
            _append_reason(degraded_reasons, BM25_FALLBACK_FAILED)
            logger.warning(
                "bm25_fallback_failed input_hash=%s retrieval_source=%s error_type=%s",
                query_plan.input_hash,
                retrieval_source,
                exc.__class__.__name__,
            )
            return []
        _append_reason(degraded_reasons, BM25_FALLBACK_USED)
        return chunks

    def _relaxed_recall_chunks(
        self,
        query_plan: QueryPlan,
        *,
        existing_chunks: list[RetrievedChunk],
        degraded_reasons: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        relaxed_query = _bm25_recall_query(query_plan)
        existing_chunk_ids = {chunk.chunk_id for chunk in existing_chunks}
        try:
            chunks = self.fallback_retriever.search(
                relaxed_query,
                top_k=RELAXED_RECALL_TOP_K,
                retrieval_source=BM25_RELAXED_RECALL_SOURCE,
            )
        except Exception as exc:  # noqa: BLE001 - relaxed fallback must not fail search
            if degraded_reasons is not None:
                _append_reason(degraded_reasons, BM25_FALLBACK_FAILED)
            logger.warning(
                "bm25_relaxed_recall_failed input_hash=%s error_type=%s",
                query_plan.input_hash,
                exc.__class__.__name__,
            )
            return []
        return [chunk for chunk in chunks if chunk.chunk_id not in existing_chunk_ids]

    def _controlled_bm25_supplement_candidates(
        self,
        query_plan: QueryPlan,
        *,
        existing_candidates: list[VectorCandidate],
    ) -> list[VectorCandidate]:
        if (
            query_plan.local_mapping_used
            or query_plan.query_variants
            or query_plan.legal_elements
            or query_plan.case_cause_hint
        ):
            return []

        vector_score_by_case: dict[str, float] = {}
        vector_case_order: list[str] = []
        for candidate in existing_candidates:
            if not candidate.matched_by_vector or not candidate.case_id:
                continue
            if candidate.case_id not in vector_score_by_case:
                vector_case_order.append(candidate.case_id)
            vector_score_by_case[candidate.case_id] = max(
                vector_score_by_case.get(candidate.case_id, 0.0),
                float(candidate.retrieval_score),
            )
        if (
            len(vector_case_order) < CONTROLLED_BM25_ADMISSION_ANCHOR_RANK
            or len(vector_case_order) > CONTROLLED_BM25_MAX_VECTOR_CASES
        ):
            return []

        try:
            bm25_chunks = self.fallback_retriever.search(
                query_plan.cleaned_query,
                top_k=BM25_FALLBACK_TOP_K,
                retrieval_source=CONTROLLED_BM25_SUPPLEMENT_SOURCE,
            )
        except Exception as exc:  # noqa: BLE001 - optional supplement must not fail search
            logger.warning(
                "controlled_bm25_supplement_failed input_hash=%s error_type=%s",
                query_plan.input_hash,
                exc.__class__.__name__,
            )
            return []

        bm25_case_order: list[str] = []
        best_chunk_by_case: dict[str, RetrievedChunk] = {}
        for chunk in bm25_chunks:
            if not chunk.case_id:
                continue
            if chunk.case_id not in best_chunk_by_case:
                bm25_case_order.append(chunk.case_id)
                best_chunk_by_case[chunk.case_id] = chunk
        top10_overlap = len(set(vector_case_order[:10]) & set(bm25_case_order[:10]))
        if top10_overlap > CONTROLLED_BM25_MAX_TOP10_OVERLAP:
            return []

        selected_case_ids = [
            case_id
            for case_id in bm25_case_order
            if case_id not in vector_score_by_case
        ][:CONTROLLED_BM25_SUPPLEMENT_CASE_LIMIT]
        if not selected_case_ids:
            return []

        anchor_score = sorted(vector_score_by_case.values(), reverse=True)[
            CONTROLLED_BM25_ADMISSION_ANCHOR_RANK - 1
        ]
        supplements = [
            VectorCandidate(
                case_id=case_id,
                chunk_id=best_chunk_by_case[case_id].chunk_id,
                vector_score=anchor_score,
                retrieval_source=CONTROLLED_BM25_SUPPLEMENT_SOURCE,
                metadata=dict(best_chunk_by_case[case_id].metadata),
                matched_text=best_chunk_by_case[case_id].text,
                source=best_chunk_by_case[case_id].source,
                distance=best_chunk_by_case[case_id].distance,
                retrieval_score=anchor_score,
                candidate_source=CONTROLLED_BM25_SUPPLEMENT_SOURCE,
                recall_stage="bm25_controlled_supplement",
                matched_by_vector=False,
                matched_by_bm25=True,
                matched_by_rewrite=False,
                filtered_reason="not_filtered",
                dedup_reason="case_level_merge_pending",
            )
            for case_id in selected_case_ids
        ]
        logger.info(
            "controlled_bm25_supplement_used input_hash=%s vector_case_count=%s "
            "top10_overlap=%s supplement_case_count=%s admission_anchor_rank=%s",
            query_plan.input_hash,
            len(vector_case_order),
            top10_overlap,
            len(supplements),
            CONTROLLED_BM25_ADMISSION_ANCHOR_RANK,
        )
        return supplements

    def _to_candidate(
        self,
        chunk: RetrievedChunk,
        *,
        query_plan: QueryPlan,
        case_cause_hint: str,
        query_years: set[int],
    ) -> VectorCandidate:
        vector_score = float(chunk.vector_score if chunk.vector_score is not None else chunk.score)
        breakdown = _soft_filter_breakdown(
            metadata=chunk.metadata,
            matched_text=chunk.text,
            case_cause_hint=case_cause_hint,
            query_years=query_years,
            weights=self.soft_filter_weights,
        )
        soft_filter_score = sum(breakdown.values())
        return VectorCandidate(
            case_id=chunk.case_id,
            chunk_id=chunk.chunk_id,
            vector_score=vector_score,
            retrieval_source=chunk.retrieval_source,
            metadata=dict(chunk.metadata),
            matched_text=chunk.text,
            source=chunk.source,
            distance=chunk.distance,
            soft_filter_score=soft_filter_score,
            retrieval_score=min(1.0, vector_score + soft_filter_score),
            soft_filter_breakdown=breakdown,
            candidate_source=chunk.retrieval_source,
            recall_stage=_recall_stage(chunk.retrieval_source),
            matched_by_vector=_is_vector_source(chunk.retrieval_source),
            matched_by_bm25=_is_bm25_source(chunk.retrieval_source),
            matched_by_rewrite=_matched_by_rewrite(chunk.retrieval_source, query_plan),
            filtered_reason="not_filtered",
            dedup_reason="case_level_merge_pending",
        )


def _soft_filter_breakdown(
    *,
    metadata: dict,
    matched_text: str,
    case_cause_hint: str,
    query_years: set[int],
    weights: SoftFilterWeights,
) -> dict[str, float]:
    breakdown: dict[str, float] = {}
    if _case_cause_matches(case_cause_hint, metadata):
        breakdown["case_cause_match"] = weights.case_cause_match

    year_bonus = _year_bonus(metadata, query_years, weights)
    if year_bonus > 0:
        breakdown["year_match"] = year_bonus

    if _is_key_paragraph(metadata, matched_text):
        breakdown["key_paragraph_match"] = weights.key_paragraph_match
    return breakdown


def _bm25_recall_query(query_plan: QueryPlan) -> str:
    parts = [
        query_plan.cleaned_query,
        *query_plan.query_variants,
        *query_plan.legal_elements,
        query_plan.case_cause_hint,
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = str(part or "").strip()
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return " ".join(unique)


def _is_vector_source(source: str) -> bool:
    return source in {
        ORIGINAL_VECTOR_SOURCE,
        VARIANT_VECTOR_SOURCE,
        RECALL_ONLY_VECTOR_SOURCE,
    }


def _is_bm25_source(source: str) -> bool:
    return source.startswith(BM25_FALLBACK_SOURCE)


def _recall_stage(source: str) -> str:
    if source == ORIGINAL_VECTOR_SOURCE:
        return "original_query_vector"
    if source == VARIANT_VECTOR_SOURCE:
        return "rewrite_or_mapped_query_vector"
    if source == RECALL_ONLY_VECTOR_SOURCE:
        return "recall_only_mapping_vector"
    if source == BM25_FALLBACK_SOURCE:
        return "bm25_fallback"
    if source == BM25_RELAXED_RECALL_SOURCE:
        return "bm25_relaxed_recall"
    return source or "unknown"


def _matched_by_rewrite(source: str, query_plan: QueryPlan) -> bool:
    if source in {VARIANT_VECTOR_SOURCE, RECALL_ONLY_VECTOR_SOURCE}:
        return True
    if _is_bm25_source(source):
        return bool(query_plan.query_variants or query_plan.legal_elements)
    return False


def _case_cause_matches(case_cause_hint: str, metadata: dict) -> bool:
    hint = _compact_text(case_cause_hint)
    if not hint:
        return False
    case_cause = _compact_text(_metadata_text(metadata.get("case_cause")))
    if not case_cause:
        return False
    return hint in case_cause or case_cause in hint


def _year_bonus(metadata: dict, query_years: set[int], weights: SoftFilterWeights) -> float:
    if not query_years:
        return 0.0
    candidate_year = _candidate_year(metadata)
    if candidate_year is None:
        return 0.0
    if candidate_year in query_years:
        return weights.exact_year_match
    closest_delta = min(abs(candidate_year - query_year) for query_year in query_years)
    if closest_delta <= weights.close_year_window:
        return weights.close_year_match
    return 0.0


def _is_key_paragraph(metadata: dict, matched_text: str) -> bool:
    paragraph_type = _compact_text(
        _metadata_text(
            metadata.get("paragraph_type")
            or metadata.get("chunk_type")
            or metadata.get("chunk_type_cn")
        )
    )
    if paragraph_type in {_compact_text(value) for value in KEY_PARAGRAPH_TYPES}:
        return True
    text = (matched_text or "").strip()
    return text.startswith(("本院查明", "经审理查明", "本院认为"))


def _extract_years(values: Sequence[str]) -> set[int]:
    years: set[int] = set()
    for value in values:
        for raw_year in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value or ""):
            year = int(raw_year)
            if 1900 <= year <= 2099:
                years.add(year)
    return years


def _candidate_year(metadata: dict) -> int | None:
    raw = metadata.get("judgment_year") or metadata.get("year") or metadata.get("judgment_date")
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if 1900 <= raw <= 2099 else None
    text = str(raw)
    match = re.search(r"(?:19|20)\d{2}", text)
    if not match:
        return None
    year = int(match.group(0))
    return year if 1900 <= year <= 2099 else None


def _metadata_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value)
    return str(value)


def _compact_text(value: str) -> str:
    return re.sub(r"[\s,，、。；;：:（）()《》<>\"'“”‘’]+", "", value or "").strip()


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def _embedding_model_version(embedding_client: QueryEmbeddingClient) -> str:
    config = getattr(embedding_client, "config", settings)
    provider = str(getattr(config, "EMBEDDING_PROVIDER", settings.EMBEDDING_PROVIDER)).strip().lower()
    model = str(getattr(config, "EMBEDDING_MODEL", settings.EMBEDDING_MODEL)).strip()
    dimension = int(getattr(config, "EMBEDDING_DIMENSION", settings.EMBEDDING_DIMENSION))
    distance = str(getattr(config, "EMBEDDING_DISTANCE_METRIC", settings.EMBEDDING_DISTANCE_METRIC)).strip().lower()
    return f"{provider}:{model}:{dimension}:{distance}"


def _embedding_input_fingerprint(values: Sequence[str]) -> str:
    input_hashes = [
        hashlib.sha256((value or "").encode("utf-8")).hexdigest()
        for value in values
    ]
    return hashlib.sha256("|".join(input_hashes).encode("ascii")).hexdigest()


def _embedding_degraded_reason(exc: Exception) -> str:
    if isinstance(exc, RetrievalConfigMismatchError):
        return EMBEDDING_MODEL_MISMATCH
    if isinstance(exc, RetrievalDependencyError):
        if exc.code == "EMBEDDING_TIMEOUT":
            return EMBEDDING_TIMEOUT
        if exc.code.startswith("EMBEDDING_"):
            return EMBEDDING_UNAVAILABLE
        return exc.code
    return EMBEDDING_UNAVAILABLE


def _chroma_degraded_reason(exc: Exception) -> str:
    if isinstance(exc, RetrievalConfigMismatchError):
        return EMBEDDING_MODEL_MISMATCH
    if isinstance(exc, RetrievalDependencyError):
        if exc.code in {"CHROMA_COLLECTION_NOT_FOUND", "CHROMA_UNAVAILABLE"}:
            return CHROMA_UNAVAILABLE
        if exc.code == "CHROMA_QUERY_TIMEOUT":
            return CHROMA_QUERY_TIMEOUT
        if exc.code.startswith("CHROMA_"):
            return CHROMA_QUERY_FAILED
        if exc.code.startswith("EMBEDDING_"):
            return EMBEDDING_MODEL_MISMATCH
        return exc.code
    return CHROMA_QUERY_FAILED


def _unique_case_count(candidates: Sequence[VectorCandidate]) -> int:
    return len({candidate.case_id for candidate in candidates if candidate.case_id})


def _unique_chunk_case_count(chunks: Sequence[RetrievedChunk]) -> int:
    return len({chunk.case_id for chunk in chunks if chunk.case_id})


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
