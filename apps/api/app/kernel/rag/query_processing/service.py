"""Query cleaning, validation, hashing, rewrite, and degradation logic."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from pydantic import ValidationError

from app.core.config import Settings, settings
from app.kernel.rag.query_processing.client import (
    DeepSeekClient,
    DeepSeekClientError,
    DeepSeekTimeoutError,
)
from app.kernel.rag.query_processing.models import QueryPlan, QueryRewriteLLMOutput
from app.kernel.rag.query_processing.term_mapping import TermMappingApplication, apply_term_mappings

QUERY_REWRITE_DISABLED = "QUERY_REWRITE_DISABLED"
LLM_TIMEOUT = "LLM_TIMEOUT"
LLM_INVALID_JSON = "LLM_INVALID_JSON"
LLM_SCHEMA_INVALID = "LLM_SCHEMA_INVALID"
LLM_UNAVAILABLE = "LLM_UNAVAILABLE"

_PUNCT_TRANSLATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "、": ",",
        "；": ";",
        "：": ":",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "《": "<",
        "》": ">",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "「": '"',
        "」": '"',
        "『": '"',
        "』": '"',
    }
)

_SHORT_QUERY_KEYWORDS = (
    "盗窃",
    "偷",
    "抢劫",
    "抢",
    "诈骗",
    "骗",
    "故意伤害",
    "伤害",
    "伤",
    "杀人",
    "贩毒",
    "毒品",
    "醉驾",
    "酒驾",
    "交通",
    "借款",
    "欠款",
    "合同",
    "离婚",
    "赌博",
    "强奸",
    "受贿",
    "职务侵占",
    "寻衅滋事",
)

_FACT_CONSTRAINT_KEYWORDS = (
    "自首",
    "坦白",
    "缓刑",
    "从犯",
    "主犯",
    "未遂",
    "既遂",
    "逃逸",
    "轻伤",
    "重伤",
    "死亡",
    "赔偿",
    "谅解",
    "认罪认罚",
    "持刀",
    "持械",
    "入户",
    "入室",
    "未成年",
    "幼女",
)


class RewriteClient(Protocol):
    def rewrite_query(self, cleaned_query: str) -> str:
        """Return raw JSON text from the model response."""


@dataclass
class QueryValidationError(Exception):
    code: str
    message: str
    status_code: int = 400


def clean_query(raw_query: str) -> str:
    """Normalize whitespace and common Chinese/English punctuation.

    This function intentionally does not remove facts, names, amounts, dates,
    relationships, case causes, or crime keywords.
    """
    normalized = unicodedata.normalize("NFKC", raw_query)
    normalized = normalized.translate(_PUNCT_TRANSLATION)
    return re.sub(r"\s+", " ", normalized).strip()


def input_hash_for_query(cleaned_query: str) -> str:
    return hashlib.sha256(cleaned_query.encode("utf-8")).hexdigest()


def _semantic_char_count(value: str) -> int:
    count = 0
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("L") or category.startswith("N"):
            count += 1
    return count


def _has_semantic_char(value: str) -> bool:
    return _semantic_char_count(value) > 0


def _has_short_query_signal(value: str) -> bool:
    return any(keyword in value for keyword in _SHORT_QUERY_KEYWORDS)


def validate_cleaned_query(cleaned_query: str, config: Settings = settings) -> None:
    if not cleaned_query:
        raise QueryValidationError(
            code="QUERY_EMPTY",
            message="query 不能为空，请输入需要检索的案情或关键事实。",
        )
    if len(cleaned_query) > config.QUERY_MAX_LENGTH:
        raise QueryValidationError(
            code="QUERY_TOO_LONG",
            message=f"query 超过长度上限 {config.QUERY_MAX_LENGTH} 字，请压缩到核心案情后再检索。",
            status_code=413,
        )
    if not _has_semantic_char(cleaned_query):
        raise QueryValidationError(
            code="QUERY_PUNCTUATION_ONLY",
            message="query 不能只包含标点或符号，请补充案件事实、主体关系、时间、金额或案由关键词。",
        )
    semantic_count = _semantic_char_count(cleaned_query)
    if semantic_count < config.QUERY_MIN_SEMANTIC_LENGTH and not _has_short_query_signal(cleaned_query):
        raise QueryValidationError(
            code="QUERY_TOO_SHORT",
            message="query 过短且缺少可检索语义，请补充案件事实、金额、时间、主体关系或案由关键词。",
        )


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)


def _safe_queries(cleaned_query: str, variants: list[str]) -> list[str]:
    queries = [cleaned_query]
    seen = {cleaned_query}
    for variant in variants:
        if variant not in seen:
            queries.append(variant)
            seen.add(variant)
    return queries


def _dedupe(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        if item and item not in seen:
            unique.append(item)
            seen.add(item)
    return unique


def _safe_variants(*groups: list[str], limit: int = 3) -> list[str]:
    return _dedupe([value for group in groups for value in group])[:limit]


def _strict_fact_signals(cleaned_query: str) -> set[str]:
    signals = set(re.findall(r"\d+(?:\.\d+)?(?:万|元|年|月|日|岁|克|次)?", cleaned_query))
    for keyword in [*_SHORT_QUERY_KEYWORDS, *_FACT_CONSTRAINT_KEYWORDS]:
        if keyword in cleaned_query:
            signals.add(keyword)
    return {signal for signal in signals if signal}


def _core_fact_signals(cleaned_query: str) -> set[str]:
    signals = set(re.findall(r"\d+(?:\.\d+)?(?:万|元|年|月|日|岁|克|次)?", cleaned_query))
    for keyword in _SHORT_QUERY_KEYWORDS:
        if keyword in cleaned_query:
            signals.add(keyword)
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned_query):
        for index in range(len(segment) - 1):
            signals.add(segment[index : index + 2])
    return {signal for signal in signals if signal}


def _variants_preserve_core_signals(cleaned_query: str, variants: list[str]) -> bool:
    strict_signals = _strict_fact_signals(cleaned_query)
    if strict_signals and not all(all(signal in variant for signal in strict_signals) for variant in variants):
        return False
    signals = _core_fact_signals(cleaned_query)
    if not signals:
        return True
    return all(any(signal in variant for signal in signals) for variant in variants)


def _mapping_labels(mapping: TermMappingApplication) -> list[str]:
    return [*mapping.high_confidence_labels, *mapping.low_confidence_labels]


def _mapped_plan_fields(mapping: TermMappingApplication) -> dict:
    return {
        "local_mapping_used": mapping.used,
        "mapping_version": mapping.version if mapping.used else None,
        "mapping_labels": _mapping_labels(mapping),
        "high_confidence_mappings": list(mapping.high_confidence_labels),
        "low_confidence_mappings": list(mapping.low_confidence_labels),
        "recall_only_query_variants": list(mapping.recall_only_query_variants),
    }


class QueryProcessingService:
    def __init__(
        self,
        *,
        config: Settings = settings,
        rewrite_client: RewriteClient | None = None,
    ) -> None:
        self.config = config
        self._rewrite_client = rewrite_client

    def process(self, raw_query: str) -> QueryPlan:
        cleaned_query = clean_query(raw_query)
        validate_cleaned_query(cleaned_query, self.config)
        input_hash = input_hash_for_query(cleaned_query)
        local_mapping = apply_term_mappings(cleaned_query)

        rewrite_start = perf_counter()
        if not self.config.ENABLE_QUERY_REWRITE:
            return self._fallback_plan(
                cleaned_query=cleaned_query,
                input_hash=input_hash,
                rewrite_start=rewrite_start,
                reason=QUERY_REWRITE_DISABLED,
                rewrite_enabled=False,
                local_mapping=local_mapping,
            )
        if not self.config.DEEPSEEK_API_KEY.strip():
            return self._fallback_plan(
                cleaned_query=cleaned_query,
                input_hash=input_hash,
                rewrite_start=rewrite_start,
                reason=LLM_UNAVAILABLE,
                rewrite_enabled=True,
                local_mapping=local_mapping,
            )

        try:
            raw_llm_output = self._client().rewrite_query(cleaned_query)
        except (DeepSeekTimeoutError, TimeoutError):
            return self._fallback_plan(
                cleaned_query=cleaned_query,
                input_hash=input_hash,
                rewrite_start=rewrite_start,
                reason=LLM_TIMEOUT,
                rewrite_enabled=True,
                local_mapping=local_mapping,
            )
        except DeepSeekClientError:
            return self._fallback_plan(
                cleaned_query=cleaned_query,
                input_hash=input_hash,
                rewrite_start=rewrite_start,
                reason=LLM_UNAVAILABLE,
                rewrite_enabled=True,
                local_mapping=local_mapping,
            )

        try:
            parsed = json.loads(raw_llm_output)
        except json.JSONDecodeError:
            return self._fallback_plan(
                cleaned_query=cleaned_query,
                input_hash=input_hash,
                rewrite_start=rewrite_start,
                reason=LLM_INVALID_JSON,
                rewrite_enabled=True,
                local_mapping=local_mapping,
            )

        try:
            rewrite = QueryRewriteLLMOutput.model_validate(parsed)
        except ValidationError:
            return self._fallback_plan(
                cleaned_query=cleaned_query,
                input_hash=input_hash,
                rewrite_start=rewrite_start,
                reason=LLM_SCHEMA_INVALID,
                rewrite_enabled=True,
                local_mapping=local_mapping,
            )

        cleaned_variants = [clean_query(variant) for variant in rewrite.query_variants]
        merged_variants = _safe_variants(cleaned_variants, list(local_mapping.query_variants))
        if not _variants_preserve_core_signals(cleaned_query, merged_variants):
            return self._fallback_plan(
                cleaned_query=cleaned_query,
                input_hash=input_hash,
                rewrite_start=rewrite_start,
                reason=LLM_SCHEMA_INVALID,
                rewrite_enabled=True,
                local_mapping=local_mapping,
            )

        return QueryPlan(
            cleaned_query=cleaned_query,
            input_hash=input_hash,
            queries=_safe_queries(
                cleaned_query,
                [*merged_variants, *local_mapping.recall_only_query_variants],
            ),
            legal_elements=_dedupe([*rewrite.legal_elements, *local_mapping.legal_elements]),
            query_variants=merged_variants,
            case_cause_hint=rewrite.case_cause_hint or local_mapping.case_cause_hint,
            confidence=rewrite.confidence if rewrite.confidence is not None else local_mapping.weighted_confidence,
            notes=rewrite.notes,
            rewrite_enabled=True,
            rewrite_used=True,
            **_mapped_plan_fields(local_mapping),
            degraded=False,
            degraded_reasons=[],
            rewrite_duration_ms=_elapsed_ms(rewrite_start),
        )

    def _client(self) -> RewriteClient:
        if self._rewrite_client is not None:
            return self._rewrite_client
        return DeepSeekClient(
            api_key=self.config.DEEPSEEK_API_KEY,
            base_url=self.config.DEEPSEEK_BASE_URL,
            chat_completions_path=self.config.DEEPSEEK_CHAT_COMPLETIONS_PATH,
            model=self.config.DEEPSEEK_MODEL,
            timeout_seconds=self.config.QUERY_REWRITE_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _fallback_plan(
        *,
        cleaned_query: str,
        input_hash: str,
        rewrite_start: float,
        reason: str,
        rewrite_enabled: bool,
        local_mapping: TermMappingApplication,
    ) -> QueryPlan:
        return QueryPlan(
            cleaned_query=cleaned_query,
            input_hash=input_hash,
            queries=_safe_queries(
                cleaned_query,
                [*local_mapping.query_variants, *local_mapping.recall_only_query_variants],
            ),
            legal_elements=list(local_mapping.legal_elements),
            query_variants=list(local_mapping.query_variants),
            case_cause_hint=local_mapping.case_cause_hint,
            confidence=local_mapping.weighted_confidence,
            rewrite_enabled=rewrite_enabled,
            rewrite_used=False,
            **_mapped_plan_fields(local_mapping),
            degraded=True,
            degraded_reasons=[reason],
            rewrite_duration_ms=_elapsed_ms(rewrite_start),
        )
