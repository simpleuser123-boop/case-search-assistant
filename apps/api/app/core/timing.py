"""Timing helpers for the search API skeleton."""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

SEARCH_TIMING_FIELDS = (
    "rewrite_duration_ms",
    "embedding_duration_ms",
    "retrieval_duration_ms",
    "rerank_duration_ms",
    "summary_duration_ms",
    "total_duration_ms",
)


@dataclass
class SearchTimings:
    rewrite_duration_ms: int = 0
    embedding_duration_ms: int = 0
    retrieval_duration_ms: int = 0
    rerank_duration_ms: int = 0
    summary_duration_ms: int = 0
    total_duration_ms: int = 0


@dataclass
class TimingRecorder:
    _start: float = field(default_factory=perf_counter)
    timings: SearchTimings = field(default_factory=SearchTimings)

    def finish(self) -> SearchTimings:
        self.timings.total_duration_ms = int((perf_counter() - self._start) * 1000)
        return self.timings
