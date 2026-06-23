"""E6-1 文书工作台入口合同（冻结口径，纯数据 + 纯函数，零业务实现、零接线）。

本模块只冻结「文书工作台 drafting」对外的**契约对象 DraftDescriptor** 与其 sanitize
纯函数，使其成为机器可校验的常量 + 纯函数。**不建 drafting 产品包、不建端点、不写库、
不接任何运行时**（产品包/端点/持久化在 E6-2 才建）。

文书工作台契约方向（文档 16 §4.1 第 3 契约对象 / 文档 17 §3.3 / 文档 21 §1）：

    一组 CandidateRef（类案，来自检索/清单）+ 可选 StatuteRef（法条，经 E5 互跳）
      -> 用户在工作台编排 structure_skeleton（段落标题，非正文）
      -> 每个引用继承 source_anchors（case_id+source_chunk_id）/ statute_anchors（text_id）
      -> 用户自填短字段（note / tag），**不起草正文、不下结论**
      -> 沉淀为 DraftDescriptor（只存骨架 + 引用 + 短字段，零起草正文）

第一性约束（E6-1 红线，本模块严格遵守）：
- **只组装、不起草**：DraftDescriptor 只承载「结构骨架（标题）+ 锚定引用 + 用户短字段」，
  绝不含起草正文 / 段落正文 / 结论 / 胜负判断。structure_skeleton 是**段落标题清单**，
  每项做长度上限校验（标题非正文）；超限即 fail-closed 拒绝并记 reason code。
- **引用必带锚点、无锚点不进交付物**：candidate_refs / statute_refs 逐项收敛，
  缺锚点的引用 fail-closed **丢弃**（不抛错、不暴露不可溯源引用），保留项 100% 有锚点。
- **DraftDescriptor 持久层零正文**：拒绝四类键——①起草正文型 ②裁判正文型 ③PII 型
  ④胜负/结论型；fail-closed，异常消息只暴露键名 / reason code，绝不回显原始值。
- 纯数据 + 纯函数：本模块**不 import 检索 / rerank / retrieval / summary / 内核 rag 服务、
  不 import 任何产品包（intake/statute/drafting/casebook）**，只依赖同包 whitelist
  （正文黑名单 + CandidateRef 白名单）、intake_contract（PII 黑名单）、statute_contract
  （StatuteRef 复用 + 互跳锚点校验）。不接任何端点，不依赖 ENABLE_DRAFTING 的 on 路径。

合同变更登记（2026-06-18，E6-1）：E-1 §3.3 冻结的 DraftDescriptor 五字段
（draft_id / structure_skeleton / candidate_refs / note / tag）保持不变（仍由
whitelist.DRAFT_DESCRIPTOR_FIELDS 与 test_e1_contracts 守门）。E6 文书工作台需要引用
经 E5 互跳而来的法条 StatuteRef，故经文档 16 §4 / 文档 17 §3.3 登记，为 DraftDescriptor
追加**可选** statute_refs 字段（沿 StatuteRef 登记范式，非擅自加字段）；持久层另补
created_at/updated_at/owner_user_id/team_id/visibility（同 CaseFolder，由后端补，
默认 visibility=private）。本模块冻结 E6 权威白名单为独立常量
DRAFT_DESCRIPTOR_E6_FIELDS（不污染 E-1 CONTRACT_FIELD_WHITELIST）。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .whitelist import (
    CANDIDATE_REF_FIELDS,
    DRAFT_DESCRIPTOR_FIELDS,
    ContractViolationError,
    is_forbidden_body_key,
)
from .intake_contract import is_forbidden_pii_key
from .statute_contract import (
    STATUTE_FORBIDDEN_DISPLAY_KEYS,
    StatuteRef,
    is_forbidden_generated_statute_key,
    sanitize_statute_ref,
)

# --- 文书工作台契约方向冻结（DraftDescriptor 是 E-1 已冻结的第 3 个契约对象）---

# drafting 唯一产出/沉淀的契约对象 = DraftDescriptor（文书结构骨架 + 锚定引用 + 短字段）。
DRAFTING_PRODUCES_CONTRACT: str = "DraftDescriptor"
# drafting 消费的契约对象 = CandidateRef（E3 产出）+ 可选 StatuteRef（E5 互跳）。
DRAFTING_CONSUMES_CONTRACTS: tuple[str, ...] = ("CandidateRef", "StatuteRef")

# structure_skeleton 单项长度上限：标题非正文，超限视为塞入正文，fail-closed 拒绝。
STRUCTURE_SKELETON_ITEM_MAX_LEN: int = 60
# structure_skeleton 列表项数上限：防止异常膨胀（结构骨架是有限标题清单）。
STRUCTURE_SKELETON_MAX_ITEMS: int = 64
# note / tag 短字段长度上限（用户自填短备注/短标签，非正文）。
NOTE_MAX_LEN: int = 200
TAG_MAX_LEN: int = 40

# E-1 §3.3 冻结的 DraftDescriptor 核心五字段（单点复用 whitelist，不重写）。
DRAFT_DESCRIPTOR_CORE_FIELDS = DRAFT_DESCRIPTOR_FIELDS

# E6 权威白名单 = 核心五字段 + 可选 statute_refs（合同变更登记）+ 持久层元数据字段。
# 注意：本常量**不进** CONTRACT_FIELD_WHITELIST（E-1 四对象口径不动），与 STATUTE_REF_FIELDS
# 独立冻结于 statute_contract 同理；test_e1_contracts 守门的 E-1 五字段保持逐位一致。
DRAFT_DESCRIPTOR_E6_FIELDS: frozenset[str] = frozenset(
    DRAFT_DESCRIPTOR_FIELDS
    | {
        "statute_refs",     # 可选：引用的法条 StatuteRef（经 E5 互跳），合同变更登记 2026-06-18
        # --- 持久层元数据（由后端补，同 CaseFolder；非起草正文）---
        "created_at",
        "updated_at",
        "owner_user_id",
        "team_id",
        "visibility",       # enum(private/team)，默认 private
    }
)


# --- 四类禁止键集合（DraftDescriptor 持久层零正文红线）-----------------------------

# ① 起草正文型键：意味着工作台「起草」了段落正文 / 结论，与「只组装不起草」红线冲突。
DRAFT_FORBIDDEN_BODY_KEYS: frozenset[str] = frozenset(
    {
        "draft_body",
        "draft_content",
        "draft_text",
        "generated_text",
        "generated_draft",
        "opinion_text",
        "legal_opinion",
        "paragraph_body",
        "paragraph_text",
        "paragraph_content",
        "section_body",
        "full_text",
        "content",
        "body",
        "conclusion_text",
        "conclusion",
        "argument_text",
        "reasoning_text",
        "llm_text",
        "ai_text",
        "model_generated_text",
        "auto_drafted_text",
    }
)

# ② 裁判正文型键：把裁判文书 / 候选 / chunk 正文借文书骨架或引用泄露。
DRAFT_FORBIDDEN_JUDGMENT_KEYS: frozenset[str] = frozenset(
    {
        "chunk_text",
        "chunk_content",
        "judgment_text",
        "judgment_full_text",
        "summary_text",
        "highlight_text",
        "matched_text",
        "holding_summary",
        "case_body",
        "document_text",
    }
)

# ④ 胜负 / 结论型键：诉讼结果预测 / 胜负概率 / 裁判结果判断，结构性红线，不可用 flag 放开。
DRAFT_FORBIDDEN_OUTCOME_KEYS: frozenset[str] = frozenset(
    {
        "win_probability",
        "winning_probability",
        "win_rate",
        "success_probability",
        "outcome_prediction",
        "predicted_outcome",
        "verdict",
        "verdict_prediction",
        "judgment_prediction",
        "result_prediction",
        "litigation_outcome",
        "case_outcome",
        "probability_of_winning",
    }
)


def is_forbidden_draft_body_key(key: str) -> bool:
    """判断某个键是否为起草正文型键（大小写不敏感）。"""
    return str(key).strip().lower() in DRAFT_FORBIDDEN_BODY_KEYS


def is_forbidden_outcome_key(key: str) -> bool:
    """判断某个键是否为胜负 / 结论型键（大小写不敏感）。"""
    return str(key).strip().lower() in DRAFT_FORBIDDEN_OUTCOME_KEYS


def is_draft_rejected_key(key: str) -> bool:
    """判断某个键是否应被 DraftDescriptor 入口拒绝。

    拒绝四类：①起草正文型 ②裁判正文型（含通用正文黑名单 + 富展示型）③PII 型
    ④胜负/结论型。任一命中即应 fail-closed 拒绝。
    """
    k = str(key).strip().lower()
    return (
        is_forbidden_body_key(k)              # 通用正文黑名单（whitelist）
        or k in DRAFT_FORBIDDEN_BODY_KEYS     # 起草正文型
        or k in DRAFT_FORBIDDEN_JUDGMENT_KEYS  # 裁判正文型
        or k in STATUTE_FORBIDDEN_DISPLAY_KEYS  # 富展示/摘要型（与 E5 同口径）
        or is_forbidden_pii_key(k)            # PII 型
        or k in DRAFT_FORBIDDEN_OUTCOME_KEYS  # 胜负/结论型
        or is_forbidden_generated_statute_key(k)  # 模型生成条文型（引用法条时兜底）
    )


def _reject_draft_forbidden_keys(payload: Mapping[str, Any]) -> None:
    """显式拒绝四类禁止键（fail-closed）。异常消息只暴露**键名**，绝不回显键值。"""
    for key in payload:
        k = str(key).strip().lower()
        if is_forbidden_body_key(k) or k in DRAFT_FORBIDDEN_BODY_KEYS:
            raise ContractViolationError(
                f"forbidden draft-body key {key!r} not allowed in DraftDescriptor "
                "(只组装不起草红线)"
            )
        if k in DRAFT_FORBIDDEN_JUDGMENT_KEYS or k in STATUTE_FORBIDDEN_DISPLAY_KEYS:
            raise ContractViolationError(
                f"forbidden judgment-body key {key!r} not allowed in DraftDescriptor "
                "(持久层零裁判正文红线)"
            )
        if is_forbidden_pii_key(k):
            raise ContractViolationError(
                f"forbidden PII-type key {key!r} not allowed in DraftDescriptor"
            )
        if k in DRAFT_FORBIDDEN_OUTCOME_KEYS:
            raise ContractViolationError(
                f"forbidden outcome/verdict key {key!r} not allowed in DraftDescriptor "
                "(不输出胜负/结论红线)"
            )
        if is_forbidden_generated_statute_key(k):
            raise ContractViolationError(
                f"forbidden model-generated key {key!r} not allowed in DraftDescriptor"
            )


def _is_valid_case_anchor(anchor: object) -> bool:
    """单条类案锚点是否合法：case_id / source_chunk_id 均为非空字符串。"""
    if not isinstance(anchor, dict):
        return False
    case_id = anchor.get("case_id")
    chunk_id = anchor.get("source_chunk_id")
    return (
        bool(case_id)
        and isinstance(case_id, str)
        and bool(chunk_id)
        and isinstance(chunk_id, str)
    )


def _is_valid_skeleton_item(item: object) -> bool:
    """单条结构骨架是否为合法标题：非空字符串且不超过单项长度上限（标题非正文）。"""
    return (
        isinstance(item, str)
        and bool(item.strip())
        and len(item) <= STRUCTURE_SKELETON_ITEM_MAX_LEN
    )


# --- reason code 常量（异常只回 reason code / 键名，绝不回显原始值）-----------------

REASON_FORBIDDEN_KEY: str = "FORBIDDEN_KEY"
REASON_SKELETON_ITEM_TOO_LONG: str = "SKELETON_ITEM_TOO_LONG"
REASON_SKELETON_EMPTY: str = "SKELETON_EMPTY"
REASON_SKELETON_TOO_MANY: str = "SKELETON_TOO_MANY_ITEMS"
REASON_SKELETON_NOT_TITLE: str = "SKELETON_ITEM_NOT_TITLE"
REASON_REF_DROPPED_NO_ANCHOR: str = "REF_DROPPED_NO_ANCHOR"
REASON_NOTE_TOO_LONG: str = "NOTE_TOO_LONG"
REASON_TAG_TOO_LONG: str = "TAG_TOO_LONG"


# --- 契约模型 ---------------------------------------------------------------------

class DraftCandidateRef(BaseModel):
    """文书骨架引用的类案（= E-1 CandidateRef 白名单七字段，零裁判正文）。

    字段集与 E-1 CANDIDATE_REF_FIELDS 逐字段一致（不增删，不因被 drafting 引用而加字段）；
    source_anchors 必须非空，每条至少 case_id + source_chunk_id（无锚点不进交付物）。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    source_anchors: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("source_anchors")
    @classmethod
    def _anchors_non_empty_and_valid(
        cls, v: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not v:
            raise ValueError("DraftDescriptor 引用的 CandidateRef 必须有非空 source_anchors")
        for anchor in v:
            if not _is_valid_case_anchor(anchor):
                raise ValueError(
                    "DraftDescriptor 引用的 CandidateRef 含不完整锚点（缺 case_id 或 source_chunk_id）"
                )
        return v


class DraftDescriptor(BaseModel):
    """E6 文书工作台的**沉淀**契约对象（E-1 已冻结的第 3 个跨产品契约对象）。

    核心字段集与文档 16 §4.1 / 文档 17 §3.3 逐字段一致：
    draft_id / structure_skeleton / candidate_refs / note? / tag?。
    E6-1 合同变更登记追加可选 statute_refs（经 E5 互跳）+ 持久层元数据
    （created_at/updated_at/owner_user_id/team_id/visibility，由后端补，默认 private）。

    红线：
    - **只组装不起草**：只含结构骨架（标题）+ 锚定引用 + 用户短字段；
      绝不含起草正文 / 段落正文 / 结论 / 胜负判断。
    - structure_skeleton 是**段落标题清单**，每项 ≤ STRUCTURE_SKELETON_ITEM_MAX_LEN 字（标题非正文）。
    - candidate_refs / statute_refs 引用 100% 有锚点；缺锚点引用在 sanitize 阶段 fail-closed 丢弃。
    extra="forbid"：起草正文型 / 裁判正文型 / PII 型 / 胜负结论型 / 非白名单键在模型层即被拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=1)
    structure_skeleton: list[str] = Field(min_length=1, max_length=STRUCTURE_SKELETON_MAX_ITEMS)
    candidate_refs: list[DraftCandidateRef] = Field(default_factory=list)
    statute_refs: list[StatuteRef] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=NOTE_MAX_LEN)
    tag: str | None = Field(default=None, max_length=TAG_MAX_LEN)
    # 持久层元数据（由后端补；契约层只声明类型，默认 private）。
    created_at: Any | None = None
    updated_at: Any | None = None
    owner_user_id: str | None = None
    team_id: str | None = None
    visibility: str | None = "private"

    @field_validator("structure_skeleton")
    @classmethod
    def _skeleton_titles_only(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("structure_skeleton 不能为空")
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("structure_skeleton 每项必须是非空标题字符串")
            if len(item) > STRUCTURE_SKELETON_ITEM_MAX_LEN:
                # 不回显正文内容，只暴露长度与上限（reason code 口径）。
                raise ValueError(
                    f"structure_skeleton 单项超长（{len(item)} > "
                    f"{STRUCTURE_SKELETON_ITEM_MAX_LEN}，疑似正文非标题）"
                )
        return v

    @field_validator("visibility")
    @classmethod
    def _visibility_enum(cls, v: str | None) -> str | None:
        if v is None:
            return "private"
        if v not in {"private", "team"}:
            raise ValueError("visibility 只能是 private / team（默认 private）")
        return v


# --- sanitize 纯函数（白名单清洗 + fail-closed 校验 + 缺锚点丢弃）-------------------

def _sanitize_candidate_ref(payload: Mapping[str, Any]) -> DraftCandidateRef | None:
    """清洗单条类案引用为合法 DraftCandidateRef（= CandidateRef 白名单七字段，无正文）。

    1. 先显式拒绝四类禁止键（fail-closed，正文/PII/结论出现即抛错）。
    2. 仅保留 E-1 CandidateRef 白名单七字段，其余非白名单键主动丢弃。
    3. 缺非空有效 source_anchors → 返回 None（fail-closed **丢弃**，不暴露不可溯源引用）。
    """
    if not isinstance(payload, Mapping):
        return None
    _reject_draft_forbidden_keys(payload)
    cleaned = {k: v for k, v in payload.items() if k in CANDIDATE_REF_FIELDS}

    raw_anchors = cleaned.get("source_anchors")
    if not raw_anchors:
        return None
    if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
        return None

    normalized: list[dict[str, Any]] = []
    for anchor in raw_anchors:
        if not _is_valid_case_anchor(anchor):
            return None  # 含不完整锚点 → 整条丢弃（不进交付物）
        normalized.append(
            {
                "case_id": anchor["case_id"],
                "source_chunk_id": anchor["source_chunk_id"],
                "anchor_type": anchor.get("anchor_type"),
            }
        )
    cleaned["source_anchors"] = normalized
    try:
        return DraftCandidateRef(**cleaned)
    except (ValueError, TypeError):
        return None


def _sanitize_statute_ref_or_drop(payload: Mapping[str, Any]) -> StatuteRef | None:
    """清洗单条法条引用为合法 StatuteRef（复用 E5 sanitize_statute_ref）；缺锚点丢弃。

    - 模型生成条文型 / 裁判正文 / PII 型键 → sanitize_statute_ref 内 fail-closed 抛错（保留抛错语义）。
    - 缺非空 statute_anchors（无锚点不展示）→ 捕获后返回 None（丢弃，不进交付物）。
    """
    if not isinstance(payload, Mapping):
        return None
    try:
        return sanitize_statute_ref(payload)
    except ContractViolationError:
        # 缺锚点 / 无效锚点是「丢弃」语义；但禁止键应继续抛出（见下方 re-raise 判定）。
        if _statute_payload_has_forbidden_key(payload):
            raise
        return None


def _statute_payload_has_forbidden_key(payload: Mapping[str, Any]) -> bool:
    """判断法条引用 payload 是否含「必须抛错」的禁止键（正文/PII/胜负/模型生成条文）。"""
    for key in payload:
        k = str(key).strip().lower()
        if (
            is_forbidden_body_key(k)
            or k in DRAFT_FORBIDDEN_BODY_KEYS
            or k in DRAFT_FORBIDDEN_JUDGMENT_KEYS
            or is_forbidden_pii_key(k)
            or k in DRAFT_FORBIDDEN_OUTCOME_KEYS
            or is_forbidden_generated_statute_key(k)
        ):
            return True
    return False


def sanitize_draft_descriptor(payload: Mapping[str, Any]) -> DraftDescriptor:
    """清洗任意 payload 为合法 DraftDescriptor（纯函数，无副作用，fail-closed）。

    1. 先显式拒绝四类禁止键（起草正文 / 裁判正文 / PII / 胜负结论），NO_GO 级不静默丢弃。
    2. 仅保留 DRAFT_DESCRIPTOR_E6_FIELDS 白名单键，其余非白名单键主动丢弃。
    3. structure_skeleton 每项做标题校验（非空 + ≤ 长度上限）；超限/非标题即抛错（reason code）。
    4. candidate_refs / statute_refs 逐项收敛；缺锚点引用 fail-closed **丢弃**（保留项 100% 有锚点）。
    5. 用白名单子集 + 校验过的骨架/引用构造 DraftDescriptor（extra="forbid" 再兜一层）。

    异常只回字段名 / reason code / 结构性原因，绝不回显起草正文 / 裁判正文 / PII 原始值。
    """
    _reject_draft_forbidden_keys(payload)
    cleaned = {k: v for k, v in payload.items() if k in DRAFT_DESCRIPTOR_E6_FIELDS}

    # structure_skeleton：标题清单校验（交由模型 validator 兜底，这里做结构性预检）。
    raw_skeleton = cleaned.get("structure_skeleton")
    if raw_skeleton is None or not isinstance(raw_skeleton, Sequence) or isinstance(
        raw_skeleton, (str, bytes)
    ):
        raise ContractViolationError(
            f"structure_skeleton 必须是标题列表（{REASON_SKELETON_EMPTY}）"
        )
    if len(raw_skeleton) == 0:
        raise ContractViolationError(f"structure_skeleton 不能为空（{REASON_SKELETON_EMPTY}）")
    if len(raw_skeleton) > STRUCTURE_SKELETON_MAX_ITEMS:
        raise ContractViolationError(
            f"structure_skeleton 项数超限（{REASON_SKELETON_TOO_MANY}）"
        )
    for item in raw_skeleton:
        if not isinstance(item, str) or not item.strip():
            raise ContractViolationError(
                f"structure_skeleton 含非标题项（{REASON_SKELETON_NOT_TITLE}）"
            )
        if len(item) > STRUCTURE_SKELETON_ITEM_MAX_LEN:
            raise ContractViolationError(
                f"structure_skeleton 单项超长，疑似正文非标题（{REASON_SKELETON_ITEM_TOO_LONG}）"
            )

    # candidate_refs：逐项收敛，缺锚点丢弃（保留项 100% 有锚点）。
    raw_candidates = cleaned.get("candidate_refs")
    kept_candidates: list[dict[str, Any]] = []
    if raw_candidates:
        if not isinstance(raw_candidates, Sequence) or isinstance(
            raw_candidates, (str, bytes)
        ):
            raise ContractViolationError("candidate_refs 必须是 CandidateRef 列表")
        for ref in raw_candidates:
            sanitized = _sanitize_candidate_ref(ref)
            if sanitized is not None:
                kept_candidates.append(sanitized.model_dump(exclude_none=True))
    cleaned["candidate_refs"] = kept_candidates

    # statute_refs（可选）：逐项收敛，缺锚点丢弃；禁止键仍 fail-closed 抛错。
    raw_statutes = cleaned.get("statute_refs")
    kept_statutes: list[dict[str, Any]] = []
    if raw_statutes:
        if not isinstance(raw_statutes, Sequence) or isinstance(
            raw_statutes, (str, bytes)
        ):
            raise ContractViolationError("statute_refs 必须是 StatuteRef 列表")
        for ref in raw_statutes:
            sanitized_st = _sanitize_statute_ref_or_drop(ref)
            if sanitized_st is not None:
                kept_statutes.append(sanitized_st.model_dump(exclude_none=True))
    cleaned["statute_refs"] = kept_statutes

    return DraftDescriptor(**cleaned)


def assert_no_draft_body(payload: Mapping[str, Any]) -> None:
    """「只组装不起草、持久层零正文」可校验断言（纯函数，fail-closed）。

    任何 DraftDescriptor 型 payload 出现起草正文型 / 裁判正文型 / 胜负结论型 / 模型生成型键，
    即抛 ContractViolationError——起草正文 / 裁判正文 / 胜负结论出现是 NO_GO 级事件，必须显式失败。
    异常消息只暴露键名 / reason code，绝不回显正文/结论值。同时递归检查嵌套引用。
    """
    _reject_draft_forbidden_keys(payload)
    # 递归检查嵌套引用（candidate_refs / statute_refs）内不夹带正文/结论键。
    for list_key in ("candidate_refs", "statute_refs", "related_case_refs"):
        nested = payload.get(list_key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            for ref in nested:
                if isinstance(ref, Mapping):
                    _reject_draft_forbidden_keys(ref)


__all__ = [
    "DRAFTING_PRODUCES_CONTRACT",
    "DRAFTING_CONSUMES_CONTRACTS",
    "STRUCTURE_SKELETON_ITEM_MAX_LEN",
    "STRUCTURE_SKELETON_MAX_ITEMS",
    "NOTE_MAX_LEN",
    "TAG_MAX_LEN",
    "DRAFT_DESCRIPTOR_CORE_FIELDS",
    "DRAFT_DESCRIPTOR_E6_FIELDS",
    "DRAFT_FORBIDDEN_BODY_KEYS",
    "DRAFT_FORBIDDEN_JUDGMENT_KEYS",
    "DRAFT_FORBIDDEN_OUTCOME_KEYS",
    "REASON_FORBIDDEN_KEY",
    "REASON_SKELETON_ITEM_TOO_LONG",
    "REASON_SKELETON_EMPTY",
    "REASON_SKELETON_TOO_MANY",
    "REASON_SKELETON_NOT_TITLE",
    "REASON_REF_DROPPED_NO_ANCHOR",
    "REASON_NOTE_TOO_LONG",
    "REASON_TAG_TOO_LONG",
    "is_forbidden_draft_body_key",
    "is_forbidden_outcome_key",
    "is_draft_rejected_key",
    "DraftCandidateRef",
    "DraftDescriptor",
    "sanitize_draft_descriptor",
    "assert_no_draft_body",
]
