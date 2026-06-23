"""Extractive summaries, highlights, and optional bounded LLM enhancement."""
from __future__ import annotations

import json
import re
from html import escape
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError, field_validator

from app.kernel.data.case_store.jsonl_store import CaseStoreNotReadyError, get_chunk_by_id
from app.core.config import Settings, settings
from app.kernel.rag.query_processing.models import QueryPlan
from app.kernel.rag.retrieval.models import CaseCandidate
from app.kernel.rag.summary.client import (
    DeepSeekSummaryClient,
    SummaryLLMError,
    SummaryLLMTimeoutError,
)
from app.kernel.rag.summary.models import HighlightItem, ResultPresentation, SourceChunk, SummaryItem

SUMMARY_DISABLED = "SUMMARY_DISABLED"
SUMMARY_LLM_TIMEOUT = "SUMMARY_LLM_TIMEOUT"
SUMMARY_LLM_INVALID_JSON = "SUMMARY_LLM_INVALID_JSON"
SUMMARY_LLM_SCHEMA_INVALID = "SUMMARY_LLM_SCHEMA_INVALID"
SUMMARY_LLM_UNAVAILABLE = "SUMMARY_LLM_UNAVAILABLE"
SUMMARY_SOURCE_MISSING = "SUMMARY_SOURCE_MISSING"

HOLDING_MISSING_SOURCE_ANCHOR = "missing_source_anchor"
HOLDING_INSUFFICIENT_SOURCE = "insufficient_source"
HOLDING_MODEL_FAILED = "model_failed"
HOLDING_SOURCE_MISMATCH = "source_mismatch"

READING_ALLOWED_CATEGORIES = (
    "争议焦点",
    "裁判理由中的关键事实",
    "法院认定的关键要素",
    "与用户阅读相关的程序或证据节点",
)
READING_CHUNK_TYPES = ("fact", "court_found", "court_opinion")
READING_DISPUTE_MARKERS = (
    "争议",
    "是否",
    "主张",
    "抗辩",
    "辩称",
    "诉称",
    "围绕",
    "分歧",
    "异议",
)
READING_ELEMENT_TERMS = (
    "产品缺陷",
    "缺陷",
    "损害原因",
    "因果关系",
    "举证责任",
    "举证",
    "证据",
    "鉴定",
    "过错",
    "责任范围",
    "赔偿范围",
    "合同履行",
    "迟延履行",
    "合同目的",
    "解除合同",
    "服务瑕疵",
    "损害发生时间",
    "证据链",
    "金额",
    "转账",
    "借款",
)
READING_PROCEDURE_EVIDENCE_TERMS = (
    "证据",
    "举证",
    "鉴定",
    "质证",
    "催告",
    "报警",
    "记录",
    "票据",
    "合同",
    "转账",
)
READING_FORBIDDEN_TERMS = (
    "胜诉",
    "败诉",
    "概率",
    "诉讼结果",
    "确定性法律结论",
    "风险评级",
    "本案应当如何判",
    "已查全",
    "保证无遗漏",
    "必然支持",
)

MAX_SENTENCE_CHARS = 120
MAX_SUMMARY_CHARS = 280
MAX_FALLBACK_CHARS = 180
MAX_HIGHLIGHT_CHARS = 96
MAX_LLM_CHUNK_CHARS = 900
MAX_LLM_TERMS = 12
MAX_HOLDING_ITEMS = 2
MAX_HOLDING_CHARS = 220
MAX_ISSUE_FOCUS_ITEMS = 3
MAX_KEY_ELEMENT_ITEMS = 4
HOLDING_CHUNK_TYPES = ("court_opinion", "judgment_result")

FACT_HINT_TERMS = (
    "本院查明",
    "经审理查明",
    "法院查明",
    "认定",
    "被告人",
    "被害人",
    "原告",
    "被告",
    "签订",
    "借款",
    "转账",
    "盗窃",
    "诈骗",
    "抢劫",
    "故意伤害",
    "交通事故",
    "醉酒",
    "逃逸",
    "毒品",
    "赌博",
    "金额",
    "现金",
    "财物",
)

LEGAL_HINT_TERMS = (
    "盗窃",
    "诈骗",
    "抢劫",
    "故意伤害",
    "交通肇事",
    "危险驾驶",
    "借款",
    "合同",
    "离婚",
    "职务侵占",
    "开设赌场",
    "贩卖毒品",
    "非法吸收公众存款",
)


class SummaryClient(Protocol):
    def summarize_chunk(
        self,
        *,
        chunk_excerpt: str,
        source_chunk_id: str,
        query_terms: list[str],
        case_cause_hint: str,
    ) -> str:
        """Return raw JSON text from the model response."""


class ChunkResolver(Protocol):
    def __call__(self, chunk_id: str, *, case_id: str) -> SourceChunk | None:
        """Return a bounded source chunk for the supplied anchor."""


class SummaryLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: StrictStr = Field(..., min_length=1, max_length=MAX_SUMMARY_CHARS)

    @field_validator("text")
    @classmethod
    def text_must_be_short_summary(cls, value: str) -> str:
        cleaned = _normalize_space(value)
        if not cleaned:
            raise ValueError("summary text must not be blank")
        if len(_split_sentences(cleaned)) > 3:
            raise ValueError("summary text must contain at most 3 sentences")
        return cleaned


class SummaryService:
    def __init__(
        self,
        *,
        config: Settings = settings,
        summary_client: SummaryClient | None = None,
        chunk_resolver: ChunkResolver | None = None,
    ) -> None:
        self.config = config
        self._summary_client = summary_client
        self._chunk_resolver = chunk_resolver or _default_chunk_resolver

    def build_presentations(
        self,
        query_plan: QueryPlan,
        candidates: list[CaseCandidate],
    ) -> list[ResultPresentation]:
        return [self.build_presentation(query_plan, candidate) for candidate in candidates]

    def build_holding_summary(
        self,
        *,
        case_id: str,
        case_cause_hint: str = "",
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build a detail-page holding summary only from anchored source chunks."""

        records: list[tuple[str, dict[str, Any]]] = []
        invalid_reasons: list[str] = []
        for chunk in chunks:
            if _clean_optional(chunk.get("chunk_type")) not in HOLDING_CHUNK_TYPES:
                continue
            text = _normalize_space(str(chunk.get("text") or ""))
            if not text:
                invalid_reasons.append(HOLDING_INSUFFICIENT_SOURCE)
                continue
            anchor, reason = _validated_holding_anchor(case_id=case_id, chunk=chunk)
            if anchor is None:
                invalid_reasons.append(reason)
                continue
            records.append((text, anchor))

        if not records:
            return _degraded_holding_summary(_dominant_holding_reason(invalid_reasons))

        summary_items: list[dict[str, Any]] = []
        source_anchors: list[dict[str, Any]] = []
        for text, anchor in records[:MAX_HOLDING_ITEMS]:
            item_text, reason = self._holding_summary_text(
                text=text,
                source_chunk_id=str(anchor["source_chunk_id"]),
                case_cause_hint=case_cause_hint,
            )
            if reason:
                return _degraded_holding_summary(reason)
            if not item_text:
                continue
            summary_items.append(
                {
                    "text": item_text,
                    "source_anchors": [anchor],
                    "confidence": "medium",
                }
            )
            source_anchors.append(anchor)

        if not summary_items:
            return _degraded_holding_summary(HOLDING_INSUFFICIENT_SOURCE)

        return {
            "summary_items": summary_items,
            "source_anchors": _dedupe_holding_anchors(source_anchors),
            "confidence": "medium",
            "generation_status": "generated",
            "degrade_reason": None,
        }

    def build_issue_focus_and_key_elements(
        self,
        *,
        case_id: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build anchored detail-page reading navigation without affecting retrieval."""

        records: list[dict[str, Any]] = []
        invalid_reasons: list[str] = []
        for chunk in chunks:
            chunk_type = _clean_optional(chunk.get("chunk_type"))
            if chunk_type not in READING_CHUNK_TYPES:
                continue
            text = _normalize_space(str(chunk.get("text") or ""))
            if not text:
                invalid_reasons.append(HOLDING_INSUFFICIENT_SOURCE)
                continue
            anchor, reason = _validated_holding_anchor(case_id=case_id, chunk=chunk)
            if anchor is None:
                invalid_reasons.append(reason)
                continue
            records.append(
                {
                    "text": text,
                    "chunk_type": chunk_type,
                    "anchor": anchor,
                }
            )

        if not records:
            reason = _dominant_holding_reason(invalid_reasons)
            return {
                "issue_focus": _degraded_reading_section(reason),
                "key_elements": _degraded_reading_section(reason),
            }

        issue_items: list[dict[str, Any]] = []
        key_items: list[dict[str, Any]] = []
        for record in records:
            if len(issue_items) < MAX_ISSUE_FOCUS_ITEMS:
                issue_item = _build_issue_focus_item(record)
                if issue_item is not None:
                    issue_items.append(issue_item)

            if len(key_items) < MAX_KEY_ELEMENT_ITEMS:
                key_items.extend(
                    _build_key_element_items(
                        record,
                        remaining=MAX_KEY_ELEMENT_ITEMS - len(key_items),
                    )
                )

            if (
                len(issue_items) >= MAX_ISSUE_FOCUS_ITEMS
                and len(key_items) >= MAX_KEY_ELEMENT_ITEMS
            ):
                break

        return {
            "issue_focus": _reading_section(issue_items, fallback_reason=HOLDING_INSUFFICIENT_SOURCE),
            "key_elements": _reading_section(key_items, fallback_reason=HOLDING_INSUFFICIENT_SOURCE),
        }

    def build_presentation(self, query_plan: QueryPlan, candidate: CaseCandidate) -> ResultPresentation:
        source_chunk = self._select_source_chunk(candidate)
        if source_chunk is None:
            return ResultPresentation(
                summary=None,
                highlights=[],
                degraded_reasons=[SUMMARY_SOURCE_MISSING],
            )

        terms = _collect_terms(
            cleaned_query=query_plan.cleaned_query,
            legal_elements=query_plan.legal_elements,
            case_cause_hint=query_plan.case_cause_hint,
        )
        rule_summary = _extractive_summary(
            source_chunk=source_chunk,
            terms=terms,
            case_cause_hint=query_plan.case_cause_hint,
        )
        highlights = _build_highlights(source_chunk=source_chunk, terms=terms)

        if not self.config.ENABLE_SUMMARY:
            return ResultPresentation(
                summary=_source_snippet_summary(source_chunk),
                highlights=highlights,
                degraded_reasons=[SUMMARY_DISABLED],
            )

        llm_summary, reason = self._try_llm_summary(
            query_plan=query_plan,
            source_chunk=source_chunk,
            terms=terms,
        )
        if llm_summary is not None:
            return ResultPresentation(summary=llm_summary, highlights=highlights)

        fallback_summary = SummaryItem(
            text=rule_summary.text,
            source_chunk_id=rule_summary.source_chunk_id,
            source_case_id=rule_summary.source_case_id,
            method=rule_summary.method,
            degraded_reason=reason,
        )
        return ResultPresentation(
            summary=fallback_summary,
            highlights=highlights,
            degraded_reasons=[reason] if reason else [],
        )

    def _select_source_chunk(self, candidate: CaseCandidate) -> SourceChunk | None:
        chunk_ids = _ordered_source_ids(candidate)
        if not chunk_ids:
            return None

        first_chunk_id = chunk_ids[0]
        if candidate.matched_text and candidate.matched_text.strip():
            return SourceChunk(
                case_id=candidate.case_id,
                chunk_id=first_chunk_id,
                text=_bounded_text(candidate.matched_text, MAX_LLM_CHUNK_CHARS),
            )

        for chunk_id in chunk_ids:
            chunk = self._chunk_resolver(chunk_id, case_id=candidate.case_id)
            if chunk and chunk.text.strip() and chunk.chunk_id:
                return SourceChunk(
                    case_id=chunk.case_id or candidate.case_id,
                    chunk_id=chunk.chunk_id,
                    text=_bounded_text(chunk.text, MAX_LLM_CHUNK_CHARS),
                )
        return None

    def _try_llm_summary(
        self,
        *,
        query_plan: QueryPlan,
        source_chunk: SourceChunk,
        terms: list[str],
    ) -> tuple[SummaryItem | None, str]:
        if not self.config.DEEPSEEK_API_KEY.strip():
            return None, SUMMARY_LLM_UNAVAILABLE

        try:
            raw_output = self._client().summarize_chunk(
                chunk_excerpt=_bounded_text(source_chunk.text, MAX_LLM_CHUNK_CHARS),
                source_chunk_id=source_chunk.chunk_id,
                query_terms=_llm_query_terms(query_plan=query_plan, terms=terms),
                case_cause_hint=query_plan.case_cause_hint,
            )
        except (SummaryLLMTimeoutError, TimeoutError):
            return None, SUMMARY_LLM_TIMEOUT
        except SummaryLLMError:
            return None, SUMMARY_LLM_UNAVAILABLE

        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            return None, SUMMARY_LLM_INVALID_JSON

        try:
            output = SummaryLLMOutput.model_validate(parsed)
        except ValidationError:
            return None, SUMMARY_LLM_SCHEMA_INVALID

        if not source_chunk.chunk_id:
            return None, SUMMARY_SOURCE_MISSING
        return (
            SummaryItem(
                text=_safe_output_text(output.text),
                source_chunk_id=source_chunk.chunk_id,
                source_case_id=source_chunk.case_id,
                method="llm_deepseek",
            ),
            "",
        )

    def _holding_summary_text(
        self,
        *,
        text: str,
        source_chunk_id: str,
        case_cause_hint: str,
    ) -> tuple[str | None, str | None]:
        if not self.config.ENABLE_SUMMARY:
            return _extract_holding_summary_text(text), None
        if not self.config.DEEPSEEK_API_KEY.strip():
            return None, HOLDING_MODEL_FAILED

        try:
            raw_output = self._client().summarize_chunk(
                chunk_excerpt=_bounded_text(text, MAX_LLM_CHUNK_CHARS),
                source_chunk_id=source_chunk_id,
                query_terms=[],
                case_cause_hint=case_cause_hint,
            )
        except (SummaryLLMError, TimeoutError):
            return None, HOLDING_MODEL_FAILED

        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            return None, HOLDING_MODEL_FAILED

        try:
            output = SummaryLLMOutput.model_validate(parsed)
        except ValidationError:
            return None, HOLDING_MODEL_FAILED

        return _safe_output_text(_fit_summary(output.text)), None

    def _client(self) -> SummaryClient:
        if self._summary_client is not None:
            return self._summary_client
        return DeepSeekSummaryClient(
            api_key=self.config.DEEPSEEK_API_KEY,
            base_url=self.config.DEEPSEEK_BASE_URL,
            chat_completions_path=self.config.DEEPSEEK_CHAT_COMPLETIONS_PATH,
            model=self.config.DEEPSEEK_MODEL,
            timeout_seconds=self.config.SUMMARY_TIMEOUT_SECONDS,
        )


def _default_chunk_resolver(chunk_id: str, *, case_id: str) -> SourceChunk | None:
    try:
        row = get_chunk_by_id(chunk_id, case_id=case_id)
    except CaseStoreNotReadyError:
        return None
    if not row:
        return None
    text = row.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    return SourceChunk(
        case_id=str(row.get("case_id") or case_id),
        chunk_id=str(row.get("chunk_id") or chunk_id),
        text=text,
    )


def _ordered_source_ids(candidate: CaseCandidate) -> list[str]:
    values = [
        candidate.top_chunk_id,
        *candidate.source_chunk_ids,
        *candidate.hit_chunk_ids,
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        chunk_id = str(value or "").strip()
        if chunk_id and chunk_id not in seen:
            ordered.append(chunk_id)
            seen.add(chunk_id)
    return ordered


def _extractive_summary(
    *,
    source_chunk: SourceChunk,
    terms: list[str],
    case_cause_hint: str,
) -> SummaryItem:
    sentences = _split_sentences(source_chunk.text)
    ranked: list[tuple[int, float, str]] = []
    for index, sentence in enumerate(sentences):
        score = _sentence_score(sentence, terms=terms, case_cause_hint=case_cause_hint)
        if score > 0:
            ranked.append((index, score, _truncate_sentence(sentence)))

    if ranked:
        selected = sorted(
            sorted(ranked, key=lambda item: (item[1], -item[0]), reverse=True)[:3],
            key=lambda item: item[0],
        )
        summary_text = _fit_summary("".join(item[2] for item in selected))
    else:
        summary_text = _safe_snippet(source_chunk.text, max_chars=MAX_FALLBACK_CHARS)

    return SummaryItem(
        text=_safe_output_text(summary_text),
        source_chunk_id=source_chunk.chunk_id,
        source_case_id=source_chunk.case_id,
        method="extractive",
    )


def _source_snippet_summary(source_chunk: SourceChunk) -> SummaryItem:
    return SummaryItem(
        text=_safe_snippet(source_chunk.text, max_chars=MAX_FALLBACK_CHARS),
        source_chunk_id=source_chunk.chunk_id,
        source_case_id=source_chunk.case_id,
        method="source_snippet",
        degraded_reason=SUMMARY_DISABLED,
    )


def _extract_holding_summary_text(text: str) -> str:
    sentences = _split_sentences(text)
    if not sentences:
        return _safe_snippet(text, max_chars=MAX_HOLDING_CHARS)

    preferred = [
        sentence
        for sentence in sentences
        if any(marker in sentence for marker in ("法院认为", "本院认为", "裁判", "判决"))
    ]
    selected = (preferred or sentences)[:2]
    fitted = _fit_summary("".join(_truncate_sentence(sentence) for sentence in selected))
    if len(fitted) > MAX_HOLDING_CHARS:
        fitted = fitted[:MAX_HOLDING_CHARS].rstrip("。！？!?；;,，") + "..."
    return _safe_output_text(fitted)


def _validated_holding_anchor(
    *,
    case_id: str,
    chunk: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    clean_case_id = _clean_optional(case_id)
    chunk_id = _clean_optional(chunk.get("chunk_id"))
    if not clean_case_id or not chunk_id:
        return None, HOLDING_INSUFFICIENT_SOURCE

    anchors = chunk.get("source_anchors")
    if not isinstance(anchors, list) or not anchors:
        return None, HOLDING_MISSING_SOURCE_ANCHOR

    saw_anchor = False
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        saw_anchor = True
        anchor_case_id = _clean_optional(anchor.get("case_id"))
        anchor_chunk_id = _clean_optional(anchor.get("source_chunk_id"))
        anchor_type = _clean_optional(anchor.get("anchor_type"))
        if (
            anchor_case_id == clean_case_id
            and anchor_chunk_id == chunk_id
            and anchor_type == "detail_chunk"
        ):
            return (
                {
                    "case_id": anchor_case_id,
                    "source_chunk_id": anchor_chunk_id,
                    "chunk_type": _clean_optional(anchor.get("chunk_type")),
                    "anchor_type": anchor_type,
                    "source_url": _clean_optional(anchor.get("source_url")),
                    "source_ref": _clean_optional(anchor.get("source_ref")) or "local_case_store",
                },
                "",
            )

    return None, HOLDING_SOURCE_MISMATCH if saw_anchor else HOLDING_MISSING_SOURCE_ANCHOR


def _degraded_holding_summary(reason: str) -> dict[str, Any]:
    return {
        "summary_items": [],
        "source_anchors": [],
        "confidence": "low",
        "generation_status": "degraded",
        "degrade_reason": reason,
    }


def _dominant_holding_reason(reasons: list[str]) -> str:
    for reason in (HOLDING_SOURCE_MISMATCH, HOLDING_MISSING_SOURCE_ANCHOR, HOLDING_MODEL_FAILED):
        if reason in reasons:
            return reason
    return HOLDING_INSUFFICIENT_SOURCE


def _dedupe_holding_anchors(anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for anchor in anchors:
        key = (
            str(anchor.get("case_id") or ""),
            str(anchor.get("source_chunk_id") or ""),
            str(anchor.get("anchor_type") or ""),
        )
        if not all(key) or key in seen:
            continue
        seen.add(key)
        unique.append(anchor)
    return unique


def _build_issue_focus_item(record: dict[str, Any]) -> dict[str, Any] | None:
    text = str(record["text"])
    if not _has_any_term(text, READING_DISPUTE_MARKERS):
        return None

    signals = _reading_signals(text, limit=2)
    if signals:
        label = f"围绕{'、'.join(signals)}的争议复核"
    else:
        label = "围绕当事人主张与抗辩的争议复核"

    return _safe_reading_item(
        label=label,
        category="争议焦点",
        anchor=record["anchor"],
        confidence=_reading_confidence(text, minimum="medium"),
    )


def _build_key_element_items(record: dict[str, Any], *, remaining: int) -> list[dict[str, Any]]:
    if remaining <= 0:
        return []

    text = str(record["text"])
    chunk_type = str(record["chunk_type"])
    anchor = record["anchor"]
    signals = _reading_signals(text, limit=2)
    items: list[dict[str, Any]] = []

    if chunk_type in ("fact", "court_found"):
        label = (
            f"关键事实：{'、'.join(signals)}相关事实"
            if signals
            else "关键事实：事实经过与当事人主张"
        )
        item = _safe_reading_item(
            label=label,
            category="裁判理由中的关键事实",
            anchor=anchor,
            confidence=_reading_confidence(text),
        )
        if item is not None:
            items.append(item)

    if len(items) < remaining and chunk_type == "court_opinion":
        label = (
            f"关键要素：{'、'.join(signals)}相关说理"
            if signals
            else "关键要素：裁判说理中的认定要点"
        )
        item = _safe_reading_item(
            label=label,
            category="法院认定的关键要素",
            anchor=anchor,
            confidence=_reading_confidence(text, minimum="medium"),
        )
        if item is not None:
            items.append(item)

    if len(items) < remaining and _has_any_term(text, READING_PROCEDURE_EVIDENCE_TERMS):
        evidence_signals = _reading_signals(
            text,
            limit=2,
            terms=READING_PROCEDURE_EVIDENCE_TERMS,
        )
        label = (
            f"程序或证据节点：{'、'.join(evidence_signals)}相关材料"
            if evidence_signals
            else "程序或证据节点：证据材料来源片段"
        )
        item = _safe_reading_item(
            label=label,
            category="与用户阅读相关的程序或证据节点",
            anchor=anchor,
            confidence=_reading_confidence(text),
        )
        if item is not None:
            items.append(item)

    return _dedupe_reading_items(items)[:remaining]


def _safe_reading_item(
    *,
    label: str,
    category: str,
    anchor: dict[str, Any],
    confidence: str,
) -> dict[str, Any] | None:
    label = _safe_output_text(_normalize_space(label))
    if (
        category not in READING_ALLOWED_CATEGORIES
        or not label
        or _has_any_term(label, READING_FORBIDDEN_TERMS)
    ):
        return None
    if not _anchor_has_minimum_fields(anchor):
        return None
    return {
        "label": label,
        "category": category,
        "source_anchors": [anchor],
        "confidence": confidence,
        "degrade_reason": None,
    }


def _reading_section(items: list[dict[str, Any]], *, fallback_reason: str) -> dict[str, Any]:
    safe_items = _dedupe_reading_items(
        [
            item
            for item in items
            if item.get("category") in READING_ALLOWED_CATEGORIES
            and item.get("source_anchors")
            and not _has_any_term(str(item.get("label") or ""), READING_FORBIDDEN_TERMS)
        ]
    )
    if not safe_items:
        return _degraded_reading_section(fallback_reason)

    anchors: list[dict[str, Any]] = []
    for item in safe_items:
        anchors.extend(item.get("source_anchors") or [])

    return {
        "items": safe_items,
        "source_anchors": _dedupe_holding_anchors(anchors),
        "generation_status": "generated",
        "degrade_reason": None,
    }


def _degraded_reading_section(reason: str) -> dict[str, Any]:
    return {
        "items": [],
        "source_anchors": [],
        "generation_status": "degraded",
        "degrade_reason": reason,
    }


def _dedupe_reading_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        anchors = item.get("source_anchors") or []
        primary = anchors[0] if anchors else {}
        key = (
            str(item.get("category") or ""),
            str(item.get("label") or ""),
            str(primary.get("case_id") or ""),
            str(primary.get("source_chunk_id") or ""),
        )
        if not all(key) or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _reading_signals(
    text: str,
    *,
    limit: int,
    terms: tuple[str, ...] = READING_ELEMENT_TERMS,
) -> list[str]:
    signals: list[str] = []
    for term in terms:
        if term in text and term not in signals:
            signals.append(term)
        if len(signals) >= limit:
            return signals
    return signals


def _reading_confidence(text: str, *, minimum: str = "low") -> str:
    signal_count = len(_reading_signals(text, limit=3))
    if signal_count >= 2 and _has_any_term(text, READING_DISPUTE_MARKERS):
        return "medium"
    return "medium" if minimum == "medium" else "low"


def _anchor_has_minimum_fields(anchor: dict[str, Any]) -> bool:
    return bool(_clean_optional(anchor.get("case_id")) and _clean_optional(anchor.get("source_chunk_id")))


def _has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _sentence_score(sentence: str, *, terms: list[str], case_cause_hint: str) -> float:
    compact_sentence = _compact_text(sentence)
    if not compact_sentence:
        return 0.0

    score = 0.0
    for term in terms:
        compact_term = _compact_text(term)
        if compact_term and compact_term in compact_sentence:
            score += 2.0 if len(compact_term) >= 4 else 1.0

    compact_cause = _compact_text(case_cause_hint)
    if compact_cause and compact_cause in compact_sentence:
        score += 1.5

    for hint in FACT_HINT_TERMS:
        if hint in sentence:
            score += 0.5

    if len(sentence) > MAX_SENTENCE_CHARS * 2:
        score -= 0.5
    return score


def _build_highlights(*, source_chunk: SourceChunk, terms: list[str]) -> list[HighlightItem]:
    text = source_chunk.text
    highlights: list[HighlightItem] = []
    seen_spans: set[tuple[int, int]] = set()
    for term in terms:
        compact_term = _compact_text(term)
        if not compact_term:
            continue
        for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            start, end = _snippet_window(text, match.start(), match.end(), MAX_HIGHLIGHT_CHARS)
            span = (start, end)
            if span in seen_spans:
                continue
            seen_spans.add(span)
            highlights.append(
                HighlightItem(
                    text=_safe_snippet(text[start:end], max_chars=MAX_HIGHLIGHT_CHARS),
                    source_chunk_id=source_chunk.chunk_id,
                    start_offset=start,
                    end_offset=end,
                    matched_terms=[term],
                    reason="term_overlap",
                )
            )
            if len(highlights) >= 3:
                return highlights

    if not highlights:
        highlights.append(
            HighlightItem(
                text=_safe_snippet(text, max_chars=MAX_HIGHLIGHT_CHARS),
                source_chunk_id=source_chunk.chunk_id,
                reason="matched_chunk_fallback",
            )
        )
    return highlights


def _collect_terms(
    *,
    cleaned_query: str,
    legal_elements: list[str],
    case_cause_hint: str,
) -> list[str]:
    terms: list[str] = []
    for value in [*legal_elements, case_cause_hint]:
        _append_term(terms, value, max_len=24)

    for hint in [*LEGAL_HINT_TERMS, *FACT_HINT_TERMS]:
        if hint and hint in cleaned_query:
            _append_term(terms, hint, max_len=24)

    for match in re.finditer(r"\d+(?:\.\d+)?(?:万|元|年|月|日|岁|克|次)?", cleaned_query):
        _append_term(terms, match.group(0), max_len=20)

    for match in re.finditer(r"[A-Za-z0-9]{2,}", cleaned_query):
        _append_term(terms, match.group(0), max_len=20)

    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned_query):
        if len(segment) <= 8:
            _append_term(terms, segment, max_len=8)
        for size in (4, 3, 2):
            for index in range(max(0, len(segment) - size + 1)):
                _append_term(terms, segment[index : index + size], max_len=size)
                if len(terms) >= 80:
                    return terms
    return terms


def _append_term(terms: list[str], value: str, *, max_len: int) -> None:
    term = _normalize_space(str(value or ""))
    if not term:
        return
    compact = _compact_text(term)
    if len(compact) < 2:
        return
    if len(term) > max_len:
        term = term[:max_len]
    if term not in terms:
        terms.append(term)


def _llm_query_terms(*, query_plan: QueryPlan, terms: list[str]) -> list[str]:
    allowed: list[str] = []
    for value in [*query_plan.legal_elements, query_plan.case_cause_hint]:
        _append_term(allowed, value, max_len=24)
    for term in terms:
        if term in LEGAL_HINT_TERMS or term in FACT_HINT_TERMS or re.fullmatch(
            r"\d+(?:\.\d+)?(?:万|元|年|月|日|岁|克|次)?",
            term,
        ):
            _append_term(allowed, term, max_len=24)
    return allowed[:MAX_LLM_TERMS]


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize_space(text)
    if not normalized:
        return []
    matches = re.finditer(r"[^。！？!?；;\n]+[。！？!?；;]?", normalized)
    sentences = [_normalize_space(match.group(0)) for match in matches]
    return [sentence for sentence in sentences if sentence]


def _truncate_sentence(sentence: str) -> str:
    sentence = _normalize_space(sentence)
    if len(sentence) <= MAX_SENTENCE_CHARS:
        return sentence
    tail = sentence[-1] if sentence[-1] in "。！？!?；;" else ""
    return sentence[:MAX_SENTENCE_CHARS].rstrip("。！？!?；;") + "..." + tail


def _fit_summary(text: str) -> str:
    text = _normalize_space(text)
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[:MAX_SUMMARY_CHARS].rstrip("。！？!?；;,，") + "..."


def _safe_snippet(text: str, *, max_chars: int) -> str:
    snippet = _normalize_space(text)
    if len(snippet) <= max_chars:
        return _safe_output_text(snippet)
    return _safe_output_text(snippet[:max_chars].rstrip("。！？!?；;,，") + "...")


def _safe_output_text(text: str) -> str:
    return escape(str(text or ""), quote=False)


def _bounded_text(text: str, max_chars: int) -> str:
    text = _normalize_space(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _snippet_window(text: str, start: int, end: int, max_chars: int) -> tuple[int, int]:
    term_len = max(1, end - start)
    padding = max(8, (max_chars - term_len) // 2)
    window_start = max(0, start - padding)
    window_end = min(len(text), end + padding)
    if window_end - window_start > max_chars:
        window_end = min(len(text), window_start + max_chars)
    return window_start, window_end


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _clean_optional(value: Any) -> str | None:
    clean = str(value or "").strip()
    return clean or None
