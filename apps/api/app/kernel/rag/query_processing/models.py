"""Schemas used by the Day 1 query processing stage."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class QueryRewriteLLMOutput(BaseModel):
    """Strict JSON contract expected from DeepSeek query rewrite."""

    model_config = ConfigDict(extra="forbid")

    legal_elements: list[StrictStr] = Field(...)
    query_variants: list[StrictStr] = Field(..., min_length=2, max_length=3)
    case_cause_hint: StrictStr = Field(..., max_length=80)
    confidence: float = Field(..., ge=0, le=1)
    notes: StrictStr | None = Field(default=None, max_length=160)

    @field_validator("legal_elements", "query_variants")
    @classmethod
    def strings_must_not_be_blank(cls, value: list[str]) -> list[str]:
        stripped = [item.strip() for item in value]
        if any(not item for item in stripped):
            raise ValueError("list items must not be blank")
        return stripped

    @field_validator("case_cause_hint", "notes")
    @classmethod
    def optional_strings_are_stripped(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("confidence", mode="before")
    @classmethod
    def confidence_must_be_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("confidence must be a number")
        return value


class QueryPlan(BaseModel):
    """Safe internal plan for later retrieval stages.

    The plan intentionally stores only the cleaned query, rewrite metadata, and
    input hash. Routes must not log the query text or variants.
    """

    cleaned_query: str
    input_hash: str
    queries: list[str]
    legal_elements: list[str] = Field(default_factory=list)
    query_variants: list[str] = Field(default_factory=list)
    recall_only_query_variants: list[str] = Field(default_factory=list)
    case_cause_hint: str = ""
    confidence: float | None = None
    notes: str | None = None
    rewrite_enabled: bool = False
    rewrite_used: bool = False
    local_mapping_used: bool = False
    mapping_version: str | None = None
    mapping_labels: list[str] = Field(default_factory=list)
    high_confidence_mappings: list[str] = Field(default_factory=list)
    low_confidence_mappings: list[str] = Field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    rewrite_duration_ms: int = 0
