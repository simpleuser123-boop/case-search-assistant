"""Models for the explainable fact-similarity rerank stage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.kernel.rag.retrieval.models import CaseCandidate


@dataclass(frozen=True)
class RerankWeights:
    """Configurable weights for Day 1 fact-similarity rerank."""

    vector_similarity: float = 0.55
    legal_element_overlap: float = 0.20
    case_cause_match: float = 0.10
    key_paragraph_match: float = 0.10
    authority_signal: float = 0.05

    def as_dict(self) -> dict[str, float]:
        return {
            "vector_similarity": self.vector_similarity,
            "legal_element_overlap": self.legal_element_overlap,
            "case_cause_match": self.case_cause_match,
            "key_paragraph_match": self.key_paragraph_match,
            "authority_signal": self.authority_signal,
        }


@dataclass(frozen=True)
class RankedCaseCandidate:
    """Case candidate plus the score explanation used by the API response."""

    candidate: CaseCandidate
    final_score: float
    score_breakdown: dict[str, Any]
