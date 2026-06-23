"""M3-4 similar-fact alignment.

Compares request-scoped abstracted user fact signals against anchored case
facts. Hard rules enforced here:

* The user query is only abstracted *inside the request*. We never keep the raw
  query, free text, or long context. We retain controlled dimension keys and
  short controlled tokens only.
* Every visible case-side fact must carry source anchors traceable to at least
  ``case_id`` and ``source_chunk_id``. Unanchored case facts are dropped.
* Output text expresses reading/review clues only ("相同维度" / "相近维度" /
  "需复核差异"); never deterministic legal conclusions.
* This module never touches retrieval ranking or recall, and never consumes
  offline evaluation signals or graded judgments at runtime.
"""
from __future__ import annotations

import re
from html import escape
from typing import Any

# Match types are review clues, never outcome statements.
MATCH_SAME = "same_dimension"
MATCH_SIMILAR = "similar_dimension"
MATCH_DIFFERENCE = "difference_to_review"
MATCH_TYPES = (MATCH_SAME, MATCH_SIMILAR, MATCH_DIFFERENCE)

# Degrade reason codes (sanitized; safe for logs and reports).
FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR = "missing_source_anchor"
FACT_ALIGNMENT_INSUFFICIENT_SOURCE = "insufficient_source"
FACT_ALIGNMENT_MISSING_QUERY_SIGNAL = "missing_query_signal"
FACT_ALIGNMENT_TIMEOUT = "fact_alignment_timeout"
FACT_ALIGNMENT_FAILED = "fact_alignment_failed"

FACT_ALIGNMENT_REASON_CODES = (
    FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR,
    FACT_ALIGNMENT_INSUFFICIENT_SOURCE,
    FACT_ALIGNMENT_MISSING_QUERY_SIGNAL,
    FACT_ALIGNMENT_TIMEOUT,
    FACT_ALIGNMENT_FAILED,
)

# Query-side controlled signal states. We expose only these enum-like states to
# the UI, never the raw input.
QUERY_SIGNAL_PRESENT = "input_signals_dimension"
QUERY_SIGNAL_ABSENT = "input_does_not_mention_dimension"

# Reading chunk types we are allowed to derive facts from on the case side.
FACT_ALIGNMENT_CHUNK_TYPES = ("fact", "court_found", "court_opinion")

MAX_FACT_DIMENSIONS = 6
MAX_CASE_FACTS_PER_DIMENSION = 2
MAX_LABEL_CHARS = 48

# Forbidden outcome / certainty copy. Mirrors reading-navigation guardrails.
FACT_ALIGNMENT_FORBIDDEN_TERMS = (
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
    "必然不支持",
    "足以适用",
)


class FactDimension:
    """A controlled fact dimension with detector tokens for both sides."""

    __slots__ = ("key", "display", "case_label", "tokens")

    def __init__(self, key: str, display: str, case_label: str, tokens: tuple[str, ...]):
        self.key = key
        self.display = display
        self.case_label = case_label
        self.tokens = tokens


# Controlled dimension vocabulary. Both the user side and case side are matched
# against the SAME token sets, so the dimension key is the only thing that
# crosses the boundary - never the raw user text.
FACT_DIMENSIONS: tuple[FactDimension, ...] = (
    FactDimension(
        key="act_type",
        display="行为类型",
        case_label="案件行为类型",
        tokens=(
            "盗窃",
            "诈骗",
            "抢劫",
            "抢夺",
            "故意伤害",
            "交通肇事",
            "危险驾驶",
            "职务侵占",
            "贩卖毒品",
            "开设赌场",
            "非法吸收公众存款",
            "合同诈骗",
        ),
    ),
    FactDimension(
        key="amount",
        display="涉案金额",
        case_label="涉案金额相关事实",
        tokens=("金额", "数额", "万元", "现金", "财物", "价值", "赃款", "损失"),
    ),
    FactDimension(
        key="contract",
        display="合同与履行",
        case_label="合同履行相关事实",
        tokens=(
            "合同",
            "协议",
            "借款",
            "借条",
            "欠款",
            "履行",
            "违约",
            "解除合同",
            "转账",
            "还款",
        ),
    ),
    FactDimension(
        key="injury",
        display="损害后果",
        case_label="损害后果相关事实",
        tokens=("受伤", "伤情", "轻伤", "重伤", "死亡", "损害", "残疾", "鉴定意见"),
    ),
    FactDimension(
        key="party_relation",
        display="当事人关系",
        case_label="当事人关系相关事实",
        tokens=("夫妻", "母子", "父子", "雇佣", "劳动关系", "合伙", "亲属", "邻里", "同事"),
    ),
    FactDimension(
        key="evidence",
        display="证据与举证",
        case_label="证据与举证相关事实",
        tokens=("证据", "举证", "鉴定", "质证", "证人", "票据", "转账记录", "录音", "聊天记录"),
    ),
    FactDimension(
        key="subjective",
        display="主观状态",
        case_label="主观状态相关事实",
        tokens=("故意", "过失", "明知", "预谋", "临时起意", "自首", "坦白", "退赔", "谅解"),
    ),
    FactDimension(
        key="time_place",
        display="时间与地点",
        case_label="时间或地点相关事实",
        tokens=("夜间", "凌晨", "白天", "公共场所", "住宅", "现场", "案发时间", "案发地点"),
    ),
)


class FactAlignmentService:
    """Builds anchored fact-alignment views without persisting user input."""

    def build_fact_alignment(
        self,
        *,
        case_id: str,
        query_signal_text: str | None,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the fact-alignment payload for one case.

        ``query_signal_text`` is the raw user query. It is abstracted to
        controlled dimension keys *here* and discarded immediately. Nothing
        derived from it that can reconstruct raw text leaves this function.
        """

        clean_case_id = _clean(case_id)
        if not clean_case_id:
            return _degraded_alignment(FACT_ALIGNMENT_INSUFFICIENT_SOURCE)

        # --- user side: request-scoped abstraction only -------------------
        query_dimension_keys = _abstract_query_dimensions(query_signal_text)

        # --- case side: anchored facts only -------------------------------
        case_dimension_facts, anchor_reason = _collect_case_dimension_facts(
            case_id=clean_case_id,
            chunks=chunks,
        )

        if not case_dimension_facts:
            # No anchored case facts at all -> degrade (preserve detail page).
            return _degraded_alignment(anchor_reason or FACT_ALIGNMENT_INSUFFICIENT_SOURCE)

        items: list[dict[str, Any]] = []
        for dimension in FACT_DIMENSIONS:
            facts = case_dimension_facts.get(dimension.key)
            if not facts:
                continue
            item = _build_alignment_item(
                dimension=dimension,
                case_facts=facts,
                query_has_dimension=dimension.key in query_dimension_keys,
            )
            if item is not None:
                items.append(item)
            if len(items) >= MAX_FACT_DIMENSIONS:
                break

        if not items:
            return _degraded_alignment(FACT_ALIGNMENT_INSUFFICIENT_SOURCE)

        return {
            "items": items,
            "generation_status": "generated",
            "degrade_reason": None,
            "query_signal_present": bool(query_dimension_keys),
        }


def _abstract_query_dimensions(query_signal_text: str | None) -> set[str]:
    """Reduce raw user text to a set of controlled dimension keys.

    The raw text is consumed locally and never returned or stored.
    """

    text = _normalize_space(str(query_signal_text or ""))
    if not text:
        return set()

    matched: set[str] = set()
    for dimension in FACT_DIMENSIONS:
        if any(token in text for token in dimension.tokens):
            matched.add(dimension.key)
    # Numeric amount signal (e.g. "5万", "3000元") maps to the amount dimension.
    if re.search(r"\d+(?:\.\d+)?\s*(?:万|元|千|百)", text):
        matched.add("amount")
    return matched


def _collect_case_dimension_facts(
    *,
    case_id: str,
    chunks: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
    """Group anchored case facts by controlled dimension key."""

    facts_by_dimension: dict[str, list[dict[str, Any]]] = {}
    saw_chunk = False
    saw_anchor_problem = False
    for chunk in chunks:
        chunk_type = _clean(chunk.get("chunk_type"))
        if chunk_type not in FACT_ALIGNMENT_CHUNK_TYPES:
            continue
        text = _normalize_space(str(chunk.get("text") or ""))
        if not text:
            continue
        saw_chunk = True
        anchor = _validated_anchor(case_id=case_id, chunk=chunk)
        if anchor is None:
            saw_anchor_problem = True
            continue

        for dimension in FACT_DIMENSIONS:
            hits = [token for token in dimension.tokens if token in text]
            if not hits:
                continue
            bucket = facts_by_dimension.setdefault(dimension.key, [])
            if len(bucket) >= MAX_CASE_FACTS_PER_DIMENSION:
                continue
            bucket.append(
                {
                    "matched_tokens": hits[:3],
                    "anchor": anchor,
                }
            )

    if not facts_by_dimension:
        if saw_chunk and saw_anchor_problem:
            return {}, FACT_ALIGNMENT_MISSING_SOURCE_ANCHOR
        return {}, FACT_ALIGNMENT_INSUFFICIENT_SOURCE
    return facts_by_dimension, None


def _build_alignment_item(
    *,
    dimension: FactDimension,
    case_facts: list[dict[str, Any]],
    query_has_dimension: bool,
) -> dict[str, Any] | None:
    anchors: list[dict[str, Any]] = []
    token_pool: list[str] = []
    for fact in case_facts:
        anchor = fact.get("anchor")
        if isinstance(anchor, dict) and _anchor_has_minimum_fields(anchor):
            anchors.append(anchor)
        for token in fact.get("matched_tokens") or []:
            if token not in token_pool:
                token_pool.append(token)

    anchors = _dedupe_anchors(anchors)
    if not anchors:
        # No traceable anchor -> never display this dimension.
        return None

    case_side_label = _safe_label(
        f"{dimension.case_label}：{'、'.join(token_pool[:3])}"
        if token_pool
        else dimension.case_label
    )
    if not case_side_label:
        return None

    if query_has_dimension:
        # Both sides reference the same controlled dimension. Whether the
        # specific tokens match decides same vs. similar - still a review clue.
        match_type = MATCH_SAME if token_pool else MATCH_SIMILAR
        query_signal = QUERY_SIGNAL_PRESENT
        confidence = "medium" if len(anchors) >= 1 and token_pool else "low"
    else:
        # Case raises a dimension the user input did not mention -> review diff.
        match_type = MATCH_DIFFERENCE
        query_signal = QUERY_SIGNAL_ABSENT
        confidence = "low"

    return {
        "dimension": dimension.display,
        "dimension_key": dimension.key,
        "query_side_signal": query_signal,
        "case_side_facts": [case_side_label],
        "source_anchors": anchors,
        "match_type": match_type,
        "confidence": confidence,
        "degrade_reason": None,
    }


def _validated_anchor(*, case_id: str, chunk: dict[str, Any]) -> dict[str, Any] | None:
    chunk_id = _clean(chunk.get("chunk_id"))
    if not chunk_id:
        return None
    anchors = chunk.get("source_anchors")
    if not isinstance(anchors, list) or not anchors:
        return None
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        anchor_case_id = _clean(anchor.get("case_id"))
        anchor_chunk_id = _clean(anchor.get("source_chunk_id"))
        anchor_type = _clean(anchor.get("anchor_type"))
        if (
            anchor_case_id == case_id
            and anchor_chunk_id == chunk_id
            and anchor_type == "detail_chunk"
        ):
            return {
                "case_id": anchor_case_id,
                "source_chunk_id": anchor_chunk_id,
                "chunk_type": _clean(anchor.get("chunk_type")),
                "anchor_type": anchor_type,
                "source_url": _clean(anchor.get("source_url")),
                "source_ref": _clean(anchor.get("source_ref")) or "local_case_store",
            }
    return None


def _degraded_alignment(reason: str) -> dict[str, Any]:
    return {
        "items": [],
        "generation_status": "degraded",
        "degrade_reason": reason,
        "query_signal_present": False,
    }


def _dedupe_anchors(anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _anchor_has_minimum_fields(anchor: dict[str, Any]) -> bool:
    return bool(_clean(anchor.get("case_id")) and _clean(anchor.get("source_chunk_id")))


def _safe_label(label: str) -> str:
    label = _normalize_space(label)
    if not label:
        return ""
    if any(term in label for term in FACT_ALIGNMENT_FORBIDDEN_TERMS):
        return ""
    if len(label) > MAX_LABEL_CHARS:
        label = label[:MAX_LABEL_CHARS].rstrip("：:、，,") + "…"
    return escape(label, quote=False)


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _clean(value: Any) -> str | None:
    clean = str(value or "").strip()
    return clean or None
