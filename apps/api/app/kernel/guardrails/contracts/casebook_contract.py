"""E7-1 案件协作工作台入口合同（确认口径，纯数据 + 纯函数，零业务实现、零接线）。

本模块只确认「案件协作工作台 casebook」对外的**归集契约对象 CaseFolder**（E-1 已冻结
的第 4 个跨产品契约对象）与其 sanitize 纯函数，使其成为机器可校验的常量 + 纯函数。
**不建 casebook 产品包、不建端点、不写库、不接任何运行时**（产品包/端点/持久化在 E7-2 才建）。

协作台契约方向（文档 16 §4.1 第 4 契约对象 / 文档 17 §3.4 / 文档 22 §1）：

    录入端 SearchProfile 摘要(脱敏) + 检索助手 CandidateRef[] + 文书工作台 DraftDescriptor[]
      -> 用户在协作台归集进 CaseFolder（默认 visibility=private）
      -> 每个引用继承上游白名单 + 锚点边界（CandidateRef 零裁判正文且 100% 有 source_anchors；
         DraftDescriptor 零起草正文且 structure_skeleton 仅标题；SearchProfile 摘要只取脱敏子集）
      -> 用户自填短字段（title / note / tag），**不归集正文、不起草、不下结论、不输出胜负**
      -> 沉淀为 CaseFolder（只存元数据 + 多租户字段 + 引用 + 短字段，零正文）

第一性约束（E7-1 红线，本模块严格遵守）：
- **只归集、不复制正文、不起草、不下结论**：CaseFolder 只承载「元数据 + 多租户字段 +
  脱敏 SearchProfile 摘要 + 锚定 CandidateRef/DraftDescriptor 引用 + 用户短字段」，
  绝不含裁判正文 / 候选/chunk 正文 / 起草正文 / 原始案情 / 胜负结论。
- **引用必带锚点、无锚点不进交付物**：candidate_refs 逐项收敛，缺锚点的引用
  fail-closed **丢弃**；draft_descriptors 逐项走 E6 sanitize_draft_descriptor（其内部
  引用缺锚点亦 fail-closed 丢弃），保留项 100% 有锚点。
- **search_profile_summary 是脱敏白名单子集**：只保留 SearchProfile 已定义的脱敏摘要键
  （走 E4 sanitize_intake_search_profile 同口径，原始案情/正文/PII 键 fail-closed 拒绝），
  绝不含原始口语化案情。
- **CaseFolder 持久层零正文**：拒绝四类键——①裁判正文型 ②起草正文型 ③PII 型
  ④胜负/结论型；fail-closed，异常消息只暴露键名 / reason code，绝不回显原始值。
- **默认私有 + 对象级鉴权语义**：visibility 缺省补 private、非法值拒绝；本步只写边界语义，
  端点/多租户落库在 E7-2 才接（owner_user_id/team_id/visibility 沿用 M5 默认私有）。
- 纯数据 + 纯函数：本模块**不 import 检索 / rerank / retrieval / summary / 内核 rag 服务、
  不 import 任何产品包（intake/statute/drafting/casebook）**，只依赖同包 whitelist
  （正文黑名单 + CandidateRef/CaseFolder 白名单）、intake_contract（PII 黑名单 + 脱敏摘要
  收敛）、statute_contract（富展示黑名单）、drafting_contract（起草正文/胜负黑名单 +
  sanitize_draft_descriptor 复用）。不接任何端点，不依赖 ENABLE_CASEBOOK 的 on 路径。

合同口径说明（2026-06-22，E7-1）：CaseFolder 在 E-1 §3.4 已冻结核心九字段
（case_folder_id / owner_user_id / team_id / visibility / search_profile_summary /
candidate_refs / draft_descriptors / created_at / updated_at，仍由 whitelist.CASE_FOLDER_FIELDS
与 test_e1_contracts 守门）。文档 16 §4.1 / 17 §3.4 红线 + 文档 22 §1/§3.5 已列「用户
自填短字段（title/note/tag）」为持久层短字段（同 DraftDescriptor note/tag 范式），故本模块
冻结 E7 权威白名单为独立常量 CASE_FOLDER_E7_FIELDS（= 核心九字段 + title/note/tag），
**不进** CONTRACT_FIELD_WHITELIST（E-1 四对象核心口径不动，与 STATUTE_REF_FIELDS /
DRAFT_DESCRIPTOR_E6_FIELDS 独立冻结同理）。本步不擅自增删核心白名单字段。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .whitelist import (
    CANDIDATE_REF_FIELDS,
    CASE_FOLDER_FIELDS,
    SEARCH_PROFILE_FIELDS,
    ContractViolationError,
    is_forbidden_body_key,
)
from .intake_contract import (
    is_forbidden_pii_key,
    sanitize_intake_search_profile,
)
from .statute_contract import STATUTE_FORBIDDEN_DISPLAY_KEYS
from .drafting_contract import (
    DRAFT_FORBIDDEN_BODY_KEYS,
    DRAFT_FORBIDDEN_JUDGMENT_KEYS,
    DRAFT_FORBIDDEN_OUTCOME_KEYS,
    DraftDescriptor,
    sanitize_draft_descriptor,
)

# --- 协作台契约方向冻结（CaseFolder 是 E-1 已冻结的第 4 个契约对象）---

# casebook 唯一沉淀/产出的契约对象 = CaseFolder（归集元数据 + 引用 + 短字段）。
CASEBOOK_PRODUCES_CONTRACT: str = "CaseFolder"
# casebook 归集（消费）的契约对象 = SearchProfile 摘要 + CandidateRef + DraftDescriptor。
CASEBOOK_AGGREGATES_CONTRACTS: tuple[str, ...] = (
    "SearchProfile",
    "CandidateRef",
    "DraftDescriptor",
)

# 用户自填短字段长度上限（title / note / tag 是短标识，非正文）。
TITLE_MAX_LEN: int = 120
NOTE_MAX_LEN: int = 200
TAG_MAX_LEN: int = 40

# 合法 visibility 取值（默认 private；非法值 fail-closed 拒绝）。
VALID_VISIBILITY: frozenset[str] = frozenset({"private", "team"})
DEFAULT_VISIBILITY: str = "private"

# E-1 §3.4 冻结的 CaseFolder 核心九字段（单点复用 whitelist，不重写）。
CASE_FOLDER_CORE_FIELDS = CASE_FOLDER_FIELDS

# E7 权威白名单 = 核心九字段 + 用户自填短字段 title/note/tag。
# 注意：本常量**不进** CONTRACT_FIELD_WHITELIST（E-1 四对象核心口径不动），与
# STATUTE_REF_FIELDS / DRAFT_DESCRIPTOR_E6_FIELDS 独立冻结同理；test_e1_contracts
# 守门的 E-1 九字段保持逐位一致。
CASE_FOLDER_E7_FIELDS: frozenset[str] = frozenset(
    CASE_FOLDER_FIELDS
    | {
        "title",  # 用户自填短标题
        "note",   # 用户自填短备注
        "tag",    # 用户自填短标签
    }
)


# --- 四类禁止键集合（CaseFolder 持久层零正文红线）---------------------------------

# ① 裁判正文型键：把裁判文书 / 候选 / chunk 正文借归集或引用泄露进协作台。
CASEBOOK_FORBIDDEN_JUDGMENT_KEYS: frozenset[str] = frozenset(
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
        "full_text",
        "content",
    }
    | DRAFT_FORBIDDEN_JUDGMENT_KEYS
    | STATUTE_FORBIDDEN_DISPLAY_KEYS
)

# ② 起草正文型键：把工作台起草的段落正文 / 结论借归集泄露进协作台。
CASEBOOK_FORBIDDEN_DRAFT_KEYS: frozenset[str] = frozenset(
    {
        "draft_body",
        "draft_content",
        "draft_text",
        "generated_text",
        "opinion_text",
        "legal_opinion",
        "paragraph_body",
        "paragraph_text",
        "conclusion_text",
        "conclusion",
    }
    | DRAFT_FORBIDDEN_BODY_KEYS
)

# ④ 胜负 / 结论型键：诉讼结果预测 / 胜负概率 / 案件综述正文，结构性红线，不可用 flag 放开。
# 注：case_summary_text 是「自动生成的案件综述正文」，与「不起草不下结论」红线冲突，纳入此集。
CASEBOOK_FORBIDDEN_OUTCOME_KEYS: frozenset[str] = frozenset(
    {
        "win_probability",
        "outcome_prediction",
        "verdict",
        "case_summary_text",
        "case_summary",
        "summary_conclusion",
    }
    | DRAFT_FORBIDDEN_OUTCOME_KEYS
)


# --- reason code 常量（异常只回 reason code / 键名，绝不回显原始值）-----------------

REASON_FORBIDDEN_KEY: str = "FORBIDDEN_KEY"
REASON_REF_DROPPED_NO_ANCHOR: str = "REF_DROPPED_NO_ANCHOR"
REASON_INVALID_VISIBILITY: str = "INVALID_VISIBILITY"
REASON_TITLE_TOO_LONG: str = "TITLE_TOO_LONG"
REASON_NOTE_TOO_LONG: str = "NOTE_TOO_LONG"
REASON_TAG_TOO_LONG: str = "TAG_TOO_LONG"
REASON_SUMMARY_NOT_DICT: str = "SUMMARY_NOT_DICT"


def is_forbidden_case_body_key(key: str) -> bool:
    """判断某个键是否为裁判正文型 / 起草正文型键（大小写不敏感）。"""
    k = str(key).strip().lower()
    return (
        is_forbidden_body_key(k)
        or k in CASEBOOK_FORBIDDEN_JUDGMENT_KEYS
        or k in CASEBOOK_FORBIDDEN_DRAFT_KEYS
    )


def is_forbidden_case_outcome_key(key: str) -> bool:
    """判断某个键是否为胜负 / 结论型键（大小写不敏感）。"""
    return str(key).strip().lower() in CASEBOOK_FORBIDDEN_OUTCOME_KEYS


def is_case_folder_rejected_key(key: str) -> bool:
    """判断某个键是否应被 CaseFolder 入口拒绝。

    拒绝四类：①裁判正文型（含通用正文黑名单 + 富展示型）②起草正文型 ③PII 型
    ④胜负/结论型。任一命中即应 fail-closed 拒绝。
    """
    k = str(key).strip().lower()
    return (
        is_forbidden_body_key(k)
        or k in CASEBOOK_FORBIDDEN_JUDGMENT_KEYS
        or k in CASEBOOK_FORBIDDEN_DRAFT_KEYS
        or is_forbidden_pii_key(k)
        or k in CASEBOOK_FORBIDDEN_OUTCOME_KEYS
    )


def _reject_case_forbidden_keys(payload: Mapping[str, Any]) -> None:
    """显式拒绝四类禁止键（fail-closed）。异常消息只暴露**键名**，绝不回显键值。"""
    for key in payload:
        k = str(key).strip().lower()
        if is_forbidden_body_key(k) or k in CASEBOOK_FORBIDDEN_JUDGMENT_KEYS:
            raise ContractViolationError(
                f"forbidden judgment-body key {key!r} not allowed in CaseFolder "
                "(持久层零裁判正文红线)"
            )
        if k in CASEBOOK_FORBIDDEN_DRAFT_KEYS:
            raise ContractViolationError(
                f"forbidden draft-body key {key!r} not allowed in CaseFolder "
                "(只归集不起草红线)"
            )
        if is_forbidden_pii_key(k):
            raise ContractViolationError(
                f"forbidden PII-type key {key!r} not allowed in CaseFolder "
                "(原始案情零归集红线)"
            )
        if k in CASEBOOK_FORBIDDEN_OUTCOME_KEYS:
            raise ContractViolationError(
                f"forbidden outcome/verdict key {key!r} not allowed in CaseFolder "
                "(不下结论/不输出胜负红线)"
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


# --- 契约模型 ---------------------------------------------------------------------

class CaseFolderCandidateRef(BaseModel):
    """协作台归集的类案引用（= E-1 CandidateRef 白名单七字段，零裁判正文）。

    字段集与 E-1 CANDIDATE_REF_FIELDS 逐字段一致（不增删，不因被 casebook 归集而加字段）；
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
            raise ValueError("CaseFolder 归集的 CandidateRef 必须有非空 source_anchors")
        for anchor in v:
            if not _is_valid_case_anchor(anchor):
                raise ValueError(
                    "CaseFolder 归集的 CandidateRef 含不完整锚点（缺 case_id 或 source_chunk_id）"
                )
        return v


class CaseFolder(BaseModel):
    """E7 案件协作工作台的**沉淀**契约对象（E-1 已冻结的第 4 个跨产品契约对象）。

    核心字段集与文档 16 §4.1 / 文档 17 §3.4 逐字段一致：
    case_folder_id / owner_user_id / team_id? / visibility / search_profile_summary? /
    candidate_refs? / draft_descriptors? / created_at / updated_at。
    文档 22 §1/§3.5 列用户自填短字段 title?/note?/tag?（持久层短字段，同 DraftDescriptor
    note/tag 范式）；E7 权威白名单 CASE_FOLDER_E7_FIELDS = 核心九字段 + title/note/tag。

    红线：
    - **只归集不起草不下结论**：只含元数据 + 多租户字段 + 脱敏 SearchProfile 摘要 +
      锚定引用 + 用户短字段；绝不含裁判正文 / 起草正文 / 原始案情 / 胜负结论。
    - search_profile_summary 只取 SearchProfile 脱敏白名单子集（原始案情绝不进入）。
    - candidate_refs / draft_descriptors 引用 100% 有锚点；缺锚点引用在 sanitize 阶段
      fail-closed 丢弃。
    - 默认 visibility=private；非法 visibility 值 fail-closed 拒绝。
    extra="forbid"：裁判正文型 / 起草正文型 / PII 型 / 胜负结论型 / 非白名单键在模型层即被拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    case_folder_id: str = Field(min_length=1)
    owner_user_id: str = Field(min_length=1)
    team_id: str | None = None
    visibility: str = DEFAULT_VISIBILITY
    search_profile_summary: dict[str, Any] | None = None
    candidate_refs: list[CaseFolderCandidateRef] = Field(default_factory=list)
    draft_descriptors: list[DraftDescriptor] = Field(default_factory=list)
    title: str | None = Field(default=None, max_length=TITLE_MAX_LEN)
    note: str | None = Field(default=None, max_length=NOTE_MAX_LEN)
    tag: str | None = Field(default=None, max_length=TAG_MAX_LEN)
    # 持久层时间戳（由后端补；契约层只声明类型）。
    created_at: Any | None = None
    updated_at: Any | None = None

    @field_validator("visibility")
    @classmethod
    def _visibility_enum(cls, v: str | None) -> str:
        if v is None:
            return DEFAULT_VISIBILITY
        if v not in VALID_VISIBILITY:
            raise ValueError("visibility 只能是 private / team（默认 private）")
        return v

    @field_validator("search_profile_summary")
    @classmethod
    def _summary_is_dehydrated_subset(
        cls, v: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("search_profile_summary 必须是脱敏白名单子集 dict")
        # 模型层兜底：只允许 SearchProfile 脱敏白名单子集键（其余 fail-closed 拒绝，
        # sanitize 阶段已主动丢弃，这里再兜一层防绕过 sanitize 直接构造）。
        for key in v:
            if key not in SEARCH_PROFILE_FIELDS:
                raise ValueError(
                    f"search_profile_summary 含非脱敏白名单键（{key!r}），疑似原始案情/正文"
                )
        return v


# --- sanitize 纯函数（白名单清洗 + fail-closed 校验 + 缺锚点丢弃）-------------------

def _sanitize_candidate_ref(payload: Mapping[str, Any]) -> CaseFolderCandidateRef | None:
    """清洗单条类案引用为合法 CaseFolderCandidateRef（= CandidateRef 白名单七字段，无正文）。

    1. 先显式拒绝四类禁止键（fail-closed，正文/PII/结论出现即抛错）。
    2. 仅保留 E-1 CandidateRef 白名单七字段，其余非白名单键主动丢弃。
    3. 缺非空有效 source_anchors → 返回 None（fail-closed **丢弃**，不暴露不可溯源引用）。
    """
    if not isinstance(payload, Mapping):
        return None
    _reject_case_forbidden_keys(payload)
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
        return CaseFolderCandidateRef(**cleaned)
    except (ValueError, TypeError):
        return None


def _sanitize_draft_descriptor_or_drop(
    payload: Mapping[str, Any],
) -> DraftDescriptor | None:
    """清洗单条文书草稿描述为合法 DraftDescriptor（复用 E6 sanitize_draft_descriptor）。

    - 起草正文 / 裁判正文 / PII / 胜负结论型键 → sanitize_draft_descriptor 内 fail-closed
      抛错（保留抛错语义，NO_GO 级不静默丢弃）。
    - structure_skeleton 缺失/为空等结构性问题 → 捕获后返回 None（丢弃，不进交付物）。
    """
    if not isinstance(payload, Mapping):
        return None
    try:
        return sanitize_draft_descriptor(payload)
    except ContractViolationError:
        # 禁止键（正文/PII/胜负）必须继续抛出；其余结构性问题（缺骨架等）丢弃。
        if _draft_payload_has_forbidden_key(payload):
            raise
        return None


def _draft_payload_has_forbidden_key(payload: Mapping[str, Any]) -> bool:
    """判断文书 payload 是否含「必须抛错」的禁止键（正文/PII/胜负/起草正文）。"""
    for key in payload:
        if is_case_folder_rejected_key(key):
            return True
    return False


def _sanitize_search_profile_summary(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """清洗 search_profile_summary 为 SearchProfile 脱敏白名单子集（走 E4 同口径）。

    复用 sanitize_intake_search_profile：先跑「原始案情零上送」断言（正文/PII 键 fail-closed
    抛错），再只保留 SearchProfile 白名单五字段子集，其余非白名单键主动丢弃。
    """
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ContractViolationError(
            f"search_profile_summary 必须是脱敏白名单子集 dict（{REASON_SUMMARY_NOT_DICT}）"
        )
    # sanitize_intake_search_profile 内含 assert_no_raw_case_payload（正文/PII fail-closed）
    # + 只保留 SEARCH_PROFILE_FIELDS 子集。
    return sanitize_intake_search_profile(payload)


def sanitize_case_folder(payload: Mapping[str, Any]) -> CaseFolder:
    """清洗任意 payload 为合法 CaseFolder（纯函数，无副作用，fail-closed）。

    1. 先显式拒绝四类禁止键（裁判正文 / 起草正文 / PII / 胜负结论），NO_GO 级不静默丢弃。
    2. 仅保留 CASE_FOLDER_E7_FIELDS 白名单键，其余非白名单键主动丢弃。
    3. search_profile_summary 收敛为 SearchProfile 脱敏白名单子集（原始案情/正文/PII fail-closed）。
    4. candidate_refs 逐项收敛；缺锚点引用 fail-closed **丢弃**（保留项 100% 有锚点）。
    5. draft_descriptors 逐项走 E6 sanitize_draft_descriptor；缺骨架丢弃、禁止键抛错。
    6. visibility 缺省补 private、非法值拒绝。
    7. 用白名单子集 + 校验过的摘要/引用构造 CaseFolder（extra="forbid" 再兜一层）。

    异常只回字段名 / reason code / 结构性原因，绝不回显裁判正文 / 起草正文 / 原始案情 / PII 原始值。
    """
    _reject_case_forbidden_keys(payload)
    cleaned = {k: v for k, v in payload.items() if k in CASE_FOLDER_E7_FIELDS}

    # search_profile_summary：脱敏白名单子集收敛（原始案情绝不进入）。
    if "search_profile_summary" in cleaned:
        cleaned["search_profile_summary"] = _sanitize_search_profile_summary(
            cleaned.get("search_profile_summary")
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

    # draft_descriptors：逐项走 E6 sanitize；缺骨架丢弃、禁止键抛错。
    raw_drafts = cleaned.get("draft_descriptors")
    kept_drafts: list[dict[str, Any]] = []
    if raw_drafts:
        if not isinstance(raw_drafts, Sequence) or isinstance(raw_drafts, (str, bytes)):
            raise ContractViolationError("draft_descriptors 必须是 DraftDescriptor 列表")
        for ref in raw_drafts:
            sanitized_d = _sanitize_draft_descriptor_or_drop(ref)
            if sanitized_d is not None:
                kept_drafts.append(sanitized_d.model_dump(exclude_none=True))
    cleaned["draft_descriptors"] = kept_drafts

    # visibility 缺省补 private（模型 validator 亦兜底；这里显式补齐语义）。
    if not cleaned.get("visibility"):
        cleaned["visibility"] = DEFAULT_VISIBILITY

    return CaseFolder(**cleaned)


def assert_no_case_body(payload: Mapping[str, Any]) -> None:
    """「只归集不起草、持久层零正文」可校验断言（纯函数，fail-closed）。

    任何 CaseFolder 型 payload 出现裁判正文型 / 起草正文型 / 原始案情(PII)型 / 胜负结论型键，
    即抛 ContractViolationError——正文/原始案情/胜负结论出现是 NO_GO 级事件，必须显式失败。
    异常消息只暴露键名 / reason code，绝不回显正文/结论值。同时递归检查嵌套引用与摘要。
    """
    _reject_case_forbidden_keys(payload)
    # 递归检查归集的引用（candidate_refs / draft_descriptors）内不夹带正文/结论键。
    for list_key in ("candidate_refs", "draft_descriptors"):
        nested = payload.get(list_key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            for ref in nested:
                if isinstance(ref, Mapping):
                    _reject_case_forbidden_keys(ref)
                    # DraftDescriptor 内层引用再下钻一层。
                    for inner_key in ("candidate_refs", "statute_refs"):
                        inner = ref.get(inner_key)
                        if isinstance(inner, Sequence) and not isinstance(
                            inner, (str, bytes)
                        ):
                            for inner_ref in inner:
                                if isinstance(inner_ref, Mapping):
                                    _reject_case_forbidden_keys(inner_ref)
    # search_profile_summary 内不得夹带正文/PII（原始案情零归集）。
    summary = payload.get("search_profile_summary")
    if isinstance(summary, Mapping):
        _reject_case_forbidden_keys(summary)


__all__ = [
    "CASEBOOK_PRODUCES_CONTRACT",
    "CASEBOOK_AGGREGATES_CONTRACTS",
    "TITLE_MAX_LEN",
    "NOTE_MAX_LEN",
    "TAG_MAX_LEN",
    "VALID_VISIBILITY",
    "DEFAULT_VISIBILITY",
    "CASE_FOLDER_CORE_FIELDS",
    "CASE_FOLDER_E7_FIELDS",
    "CASEBOOK_FORBIDDEN_JUDGMENT_KEYS",
    "CASEBOOK_FORBIDDEN_DRAFT_KEYS",
    "CASEBOOK_FORBIDDEN_OUTCOME_KEYS",
    "REASON_FORBIDDEN_KEY",
    "REASON_REF_DROPPED_NO_ANCHOR",
    "REASON_INVALID_VISIBILITY",
    "REASON_TITLE_TOO_LONG",
    "REASON_NOTE_TOO_LONG",
    "REASON_TAG_TOO_LONG",
    "REASON_SUMMARY_NOT_DICT",
    "is_forbidden_case_body_key",
    "is_forbidden_case_outcome_key",
    "is_case_folder_rejected_key",
    "CaseFolderCandidateRef",
    "CaseFolder",
    "sanitize_case_folder",
    "assert_no_case_body",
]
