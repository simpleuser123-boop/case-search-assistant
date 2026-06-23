"""Shared retrieval adapter models and sanitized errors."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievalDependencyError(RuntimeError):
    """External retrieval dependency failed without exposing query text."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class RetrievalConfigMismatchError(RetrievalDependencyError):
    """Provider/model/dimension/distance metadata does not match Day 0 index."""


@dataclass(frozen=True)
class EmbeddingResult:
    provider: str
    model: str
    dimension: int
    vector: list[float]


@dataclass(frozen=True)
class RetrievedChunk:
    """Stable raw chunk shape returned by vector recall adapters."""

    case_id: str
    chunk_id: str
    score: float
    metadata: dict[str, Any]
    text: str
    source: str
    vector_score: float | None = None
    distance: float | None = None
    retrieval_source: str = "chroma_vector"


@dataclass(frozen=True)
class VectorCandidate:
    """Raw vector candidate plus conservative retrieval-stage soft signals."""

    case_id: str
    chunk_id: str
    vector_score: float
    retrieval_source: str
    metadata: dict[str, Any]
    matched_text: str
    source: str
    distance: float | None = None
    soft_filter_score: float = 0.0
    retrieval_score: float = 0.0
    soft_filter_breakdown: dict[str, float] = field(default_factory=dict)
    candidate_source: str = ""
    recall_stage: str = ""
    matched_by_vector: bool = False
    matched_by_bm25: bool = False
    matched_by_rewrite: bool = False
    filtered_reason: str | None = None
    dedup_reason: str | None = None


@dataclass(frozen=True)
class CaseCandidate:
    """Case-level candidate merged from one or more hit chunks."""

    case_id: str
    top_chunk_id: str
    source_chunk_ids: list[str]
    hit_chunk_ids: list[str]
    retrieval_source: list[str]
    metadata: dict[str, Any]
    matched_text: str
    source: str
    vector_score: float | None = None
    fallback_score: float | None = None
    top_chunk_score: float = 0.0
    retrieval_score: float = 0.0
    soft_filter_score: float = 0.0
    soft_filter_breakdown: dict[str, float] = field(default_factory=dict)
    distance: float | None = None
    candidate_source: str = ""
    recall_stage: list[str] = field(default_factory=list)
    matched_by_vector: bool = False
    matched_by_bm25: bool = False
    matched_by_rewrite: bool = False
    filtered_reason: str | None = None
    dedup_reason: str | None = None


@dataclass(frozen=True)
class VectorRetrievalResult:
    candidates: list[VectorCandidate]
    retrieval_duration_ms: int
    embedding_duration_ms: int = 0
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChromaProbeResult:
    collection: str
    persist_dir: str
    queryable: bool
    chunk_count: int
    metadata_valid: bool
    degraded_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
