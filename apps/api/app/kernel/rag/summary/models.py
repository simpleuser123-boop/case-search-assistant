"""Structured summary/highlight models for result presentation."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceChunk:
    case_id: str
    chunk_id: str
    text: str


@dataclass(frozen=True)
class SummaryItem:
    text: str
    source_chunk_id: str
    source_case_id: str
    method: str
    degraded_reason: str | None = None


@dataclass(frozen=True)
class HighlightItem:
    text: str
    source_chunk_id: str
    start_offset: int | None = None
    end_offset: int | None = None
    matched_terms: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class ResultPresentation:
    summary: SummaryItem | None
    highlights: list[HighlightItem]
    degraded_reasons: list[str] = field(default_factory=list)

