"""E3-1 检索内部服务契约（冻结口径，纯模型 + 纯函数，不接线检索链路）。

E 系列多产品生态第三步 E-3 的本质是把「检索助手」沉淀为可复用的**内部检索服务接口**：

    SearchProfile（脱敏、白名单输入）
      -> 检索内部服务（E3-2 才实现）
      -> CandidateRef[]（只含元数据与 source_anchors，不含正文）

本模块只冻结上述输入 / 输出契约的 Python 形态、白名单约束与 sanitize 规则；
**不接入真实检索链路、不改 /api/search、不注册任何 HTTP 端点**（E3-2/E3-3 才做）。

第一性约束（与文档 18 §3 / E-1 白名单逐字段一致）：
- SearchProfile 只接受 E-1 SearchProfile 白名单字段，经 sanitize_contract("SearchProfile") 清洗。
- CandidateRef 只接受 E-1 CandidateRef 白名单字段，经 sanitize_contract("CandidateRef") 清洗。
- CandidateRef 必须有非空 source_anchors；每个 anchor 至少有 case_id + source_chunk_id。
- 内部服务对外只暴露 Python import 面，不是 HTTP API。
- 正文型字段一律 fail-closed 拒绝，不静默放行：
  * E-1 已冻结的 FORBIDDEN_BODY_KEYS（raw_*/full_text/content/chunk_text/body/...）。
  * E3 追加显式拒绝 SearchResultItem 的富展示字段 summary/highlights/matched_text 等
    （文档 18 §10 止损线明确将其列为 NO_GO 级正文泄露），在 E-1 黑名单之上加一层，
    不改 E-1 白名单 / 黑名单本身。

护栏单点复用：白名单 sanitize 走 app.kernel.guardrails.contracts.sanitize_contract，
锚点合法性走 app.kernel.guardrails 的 is_valid_anchor（即 sharing.anchors 单点实现），
本模块不另写第二套白名单 / 锚点校验。
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.kernel.guardrails.contracts import (
    CANDIDATE_REF_FIELDS,
    SEARCH_PROFILE_FIELDS,
    ContractViolationError,
    sanitize_contract,
)
from app.kernel.identity.sharing.anchors import is_valid_anchor

# 内部检索模式：standard（默认）/ expanded（放宽召回）。
# 仅作为结构化参数透传，本步不改任何召回 / 排序策略。
InternalSearchMode = Literal["standard", "expanded"]

# E3 追加的「富展示型」禁止键：这些是 SearchResultItem 的展示字段，不在 E-1 CandidateRef
# 白名单内，且承载/指向正文型内容。E-1 黑名单未收录它们（会被静默丢弃），E3 在此显式
# 拒绝以满足文档 18 §10 止损线（summary/highlight 文本、matched_text 出现即 NO_GO）。
# 注意：metadata 等非正文承载键不在此列，仍按白名单静默丢弃。
E3_FORBIDDEN_CANDIDATE_KEYS: frozenset[str] = frozenset(
    {
        "summary",
        "summary_text",
        "highlights",
        "highlight",
        "highlight_text",
        "matched_text",
        "holding_summary",
    }
)


class SourceAnchorRef(BaseModel):
    """来源锚点（结构化引用，非正文）。

    最小合法锚点 = case_id + source_chunk_id 均非空；anchor_type 可选。
    只承载 id / 类型等元数据，绝不含 chunk 正文 / 裁判文书全文。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    source_chunk_id: str = Field(min_length=1)
    anchor_type: str | None = None

    @field_validator("case_id", "source_chunk_id")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("anchor case_id / source_chunk_id 不能为空")
        return v


class SearchProfile(BaseModel):
    """E3 内部检索服务的**输入**契约（脱敏、白名单）。

    字段集与 E-1 SearchProfile 白名单逐字段一致：
    case_cause / region / trial_level_preference / dispute_focus_keywords / query_text。
    原始口语化案情不在内（仅浏览器本地）；query_text 视为已脱敏短查询。
    extra="forbid"：任何非白名单 / 正文型键在模型层即被拒绝（fail-closed）。
    """

    model_config = ConfigDict(extra="forbid")

    case_cause: str | None = None
    region: str | None = None
    trial_level_preference: str | None = None
    dispute_focus_keywords: list[str] = Field(default_factory=list)
    query_text: str | None = None


# 输入别名：文档建议 SearchProfileInput 或 SearchProfile，统一指向同一冻结模型。
SearchProfileInput = SearchProfile


class CandidateRef(BaseModel):
    """E3 内部检索服务的**输出**契约（跨产品可复用引用，零正文）。

    字段集与 E-1 CandidateRef 白名单逐字段一致：
    case_id / case_number / court / trial_level / case_cause / judgment_date / source_anchors。
    红线：
    - 不含 summary / highlights / matched_text / chunk_text / full_text / content / body。
    - source_anchors 必须非空，且每条至少有 case_id + source_chunk_id。
    extra="forbid"：正文型 / 非白名单键在模型层即被拒绝（fail-closed）。
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    case_number: str | None = None
    court: str | None = None
    trial_level: str | None = None
    case_cause: str | None = None
    judgment_date: str | None = None
    source_anchors: list[SourceAnchorRef] = Field(min_length=1)

    @field_validator("source_anchors")
    @classmethod
    def _anchors_non_empty(cls, v: list[SourceAnchorRef]) -> list[SourceAnchorRef]:
        if not v:
            raise ValueError("CandidateRef 必须有非空 source_anchors")
        return v


class InternalSearchRequest(BaseModel):
    """内部检索服务请求（仅结构化参数，绝不含原始案情 / 原始 query）。

    - profile：脱敏后的 SearchProfile。
    - mode：standard / expanded（仅透传，不改召回策略）。
    - limit：返回候选上限。
    - include_relaxed_recall：是否允许放宽召回（内部参数，E3-2 才消费）。
    红线：不得含 raw_case / raw_query 等正文型字段（extra="forbid" 兜底）。
    """

    model_config = ConfigDict(extra="forbid")

    profile: SearchProfile
    mode: InternalSearchMode = "standard"
    limit: int = Field(default=10, ge=1, le=50)
    include_relaxed_recall: bool = False


class InternalSearchResult(BaseModel):
    """内部检索服务结果（只含引用与元信息，零正文）。

    - candidate_refs：CandidateRef[]，跨产品唯一可复用的检索输出。
    - degraded / degraded_reasons：降级标记与原因码（不含正文）。
    - coverage / timings：可选元信息（结构化，不含正文）。
    红线：不得携带候选正文 / summary / highlight 文本。
    """

    model_config = ConfigDict(extra="forbid")

    candidate_refs: list[CandidateRef] = Field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] | None = None
    timings: dict[str, Any] | None = None


# --- sanitize 纯函数（白名单清洗 + fail-closed 校验）------------------------------

def _reject_e3_body_keys(payload: Mapping[str, Any]) -> None:
    """在 E-1 黑名单之上，显式拒绝 E3 富展示型正文键（fail-closed）。"""
    for key in payload:
        if str(key).strip().lower() in E3_FORBIDDEN_CANDIDATE_KEYS:
            raise ContractViolationError(
                f"forbidden display/body key {key!r} not allowed in CandidateRef"
            )


def sanitize_search_profile(payload: Mapping[str, Any]) -> SearchProfile:
    """清洗任意 payload 为合法 SearchProfile。

    1. 经 sanitize_contract("SearchProfile") 丢弃非白名单键、拒绝正文型键（fail-closed）。
    2. 用清洗后的白名单子集构造 SearchProfile（extra="forbid" 再兜一层）。
    """
    cleaned = sanitize_contract("SearchProfile", dict(payload))
    return SearchProfile(**cleaned)


def sanitize_candidate_ref(payload: Mapping[str, Any]) -> CandidateRef:
    """清洗任意 payload 为合法 CandidateRef。

    1. 先在 E-1 黑名单之上显式拒绝 E3 富展示型正文键（summary/highlights/matched_text...）。
    2. 经 sanitize_contract("CandidateRef") 丢弃非白名单键、拒绝 E-1 正文型键（fail-closed）。
    3. source_anchors 必须存在、非空，且每条至少有 case_id + source_chunk_id；
       任一不满足即抛 ContractViolationError（不静默暴露不可溯源候选）。
    4. 用白名单子集 + 校验过的锚点构造 CandidateRef。
    """
    _reject_e3_body_keys(payload)
    cleaned = sanitize_contract("CandidateRef", dict(payload))

    raw_anchors = cleaned.get("source_anchors")
    if not raw_anchors:
        raise ContractViolationError(
            "CandidateRef 缺少非空 source_anchors，拒绝暴露不可溯源候选"
        )
    if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
        raise ContractViolationError("source_anchors 必须是锚点列表")

    normalized_anchors: list[dict[str, Any]] = []
    for anchor in raw_anchors:
        if not is_valid_anchor(anchor):
            raise ContractViolationError(
                "source_anchors 含不完整锚点（缺 case_id 或 source_chunk_id）"
            )
        normalized_anchors.append(
            {
                "case_id": anchor["case_id"],
                "source_chunk_id": anchor["source_chunk_id"],
                "anchor_type": anchor.get("anchor_type"),
            }
        )

    cleaned["source_anchors"] = normalized_anchors
    return CandidateRef(**cleaned)


def search_result_item_to_candidate_ref(item: Any) -> CandidateRef:
    """把检索结果项（schemas.SearchResultItem 或等价 Mapping）转为 CandidateRef。

    只取 E-1 白名单允许字段与 source_anchors，**绝不**搬运
    summary / highlights / matched_text / metadata 等富展示 / 正文型字段：
    - case_number 由 SearchResultItem.case_no 映射（输出字段名固定为 case_number）。
    - source_anchors 从 item 的锚点列表抽取最小元数据（case_id + source_chunk_id [+ anchor_type]）。
    - 锚点为空 / 不完整 → 经 sanitize_candidate_ref fail-closed 抛错，不暴露该候选。
    """

    def _get(name: str) -> Any:
        if isinstance(item, Mapping):
            return item.get(name)
        return getattr(item, name, None)

    raw_anchors = _get("source_anchors") or []
    anchors: list[dict[str, Any]] = []
    for anchor in raw_anchors:
        if isinstance(anchor, Mapping):
            a_case_id = anchor.get("case_id")
            a_chunk_id = anchor.get("source_chunk_id")
            a_type = anchor.get("anchor_type")
        else:
            a_case_id = getattr(anchor, "case_id", None)
            a_chunk_id = getattr(anchor, "source_chunk_id", None)
            a_type = getattr(anchor, "anchor_type", None)
        anchors.append(
            {"case_id": a_case_id, "source_chunk_id": a_chunk_id, "anchor_type": a_type}
        )

    payload = {
        "case_id": _get("case_id"),
        "case_number": _get("case_no"),  # case_no -> case_number 映射
        "court": _get("court"),
        "trial_level": _get("trial_level"),
        "case_cause": _get("case_cause"),
        "judgment_date": _get("judgment_date"),
        "source_anchors": anchors,
    }
    # 丢弃 None 值，交由 sanitize_candidate_ref 做白名单 + 锚点 fail-closed 校验。
    payload = {k: v for k, v in payload.items() if v is not None}
    return sanitize_candidate_ref(payload)


__all__ = [
    "InternalSearchMode",
    "E3_FORBIDDEN_CANDIDATE_KEYS",
    "SourceAnchorRef",
    "SearchProfile",
    "SearchProfileInput",
    "CandidateRef",
    "InternalSearchRequest",
    "InternalSearchResult",
    "sanitize_search_profile",
    "sanitize_candidate_ref",
    "search_result_item_to_candidate_ref",
    # 复用的护栏符号（再导出，便于消费方单点引用）。
    "ContractViolationError",
    "SEARCH_PROFILE_FIELDS",
    "CANDIDATE_REF_FIELDS",
]
