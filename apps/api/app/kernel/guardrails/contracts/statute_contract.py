"""E5-1 法条法规检索入口合同（冻结口径，纯数据 + 纯函数，零业务实现、零接线）。

本模块只冻结「法条检索端 statute」对外的**结果契约对象 StatuteRef** 与法条↔类案
互跳口径，使其成为机器可校验的常量 + 纯函数。**不建 statute 产品包、不建法条语料管道、
不建内核法条检索服务、不接任何端点**（E5-2 建语料/索引，E5-3 建内核服务，E5-4 建端点）。

法条检索契约方向（文档 16 §4.1 第 5 契约对象 / 文档 17 §3.5 / 文档 20 §1）：

    查询文本（已脱敏 SearchProfile.query_text）或 一条 CandidateRef（类案）
      -> 内核法条检索能力（E5-3 才实现，经 app.kernel.rag 公开面）
      -> StatuteRef[]（law_name / article_no / statute_anchors[text_id]）  ← statute 唯一**产出**的契约对象
      -> 法条 <-> 类案互跳：StatuteRef.related_case_refs = CandidateRef[]（无正文）

第一性约束（E5-1 红线，本模块严格遵守）：
- 法条条文是**公开法律文本**，允许在结果中展示，但有两条硬约束：
  (a) 展示的条文（article_text，若有）必须**来自法条语料库、带 text_id 锚点**；
  (b) 模型**不得杜撰 / 改写 / 续写**任何条文；命中无锚点则降级不展示。
  契约层用「statute_anchors 必填非空、每条至少 text_id」表达「无锚点不展示」。
- 法条 ↔ 类案互跳只走契约对象：StatuteRef 关联 CandidateRef[]（无裁判正文），
  CandidateRef 侧**不改字段**；互跳由服务层关联，契约不互相内嵌正文。
- 拒绝任何裁判正文型键（full_text/content/chunk_text/summary_text/highlight_text/
  matched_text/...）、PII 型键，以及**模型生成条文型键**（generated_article/llm_text/
  paraphrased_article/...）——fail-closed，不静默放行。
- 纯数据 + 纯函数：本模块**不 import 检索 / rerank / retrieval / summary / 内核 rag 服务**，
  只依赖同包 whitelist（裁判正文黑名单）与 intake_contract（PII 黑名单）；
  不接任何端点，不依赖 ENABLE_STATUTE_SEARCH 的 on 路径。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .whitelist import (
    CANDIDATE_REF_FIELDS,
    FORBIDDEN_BODY_KEYS,
    ContractViolationError,
    is_forbidden_body_key,
)
from .intake_contract import is_forbidden_pii_key

# --- 法条检索契约方向冻结（StatuteRef 是 E5 引入的第 5 个跨产品契约对象）---

# statute 唯一产出的契约对象 = StatuteRef（法条检索结果，带 text_id 锚点）。
STATUTE_PRODUCES_CONTRACT: str = "StatuteRef"
# statute 互跳消费/关联的契约对象 = CandidateRef（白名单七字段，无正文，由 E3 服务产出）。
STATUTE_RELATES_CONTRACT: str = "CandidateRef"

# StatuteRef 字段白名单（与文档 16 §4.1 / 文档 17 §3.5 / 文档 20 §4 逐字段一致）。
# 红线：不含裁判正文 / chunk 正文 / 全文 / summary / highlight；article_text 若有必带锚点。
STATUTE_REF_FIELDS: frozenset[str] = frozenset(
    {
        "statute_id",         # 法条标识（短）
        "law_name",           # 法名（短）
        "article_no",         # 条号（短）
        "statute_anchors",    # list[{text_id, law_name?, article_no?, anchor_type?}]，非空
        "article_text",       # 条文文本（可选）：只来自法条语料、带 text_id 锚点，不得由模型生成
        "source_corpus",      # 法条语料来源（短，如 "judge_law_corpus"）
        "effective_status",   # 时效状态（短，如 "current"），结构化非正文
        "related_case_refs",  # list[CandidateRef]（无正文），法条→类案互跳
    }
)

# 法条来源锚点字段白名单：指向法条语料的最小结构化引用（非条文正文）。
STATUTE_ANCHOR_FIELDS: frozenset[str] = frozenset(
    {
        "text_id",      # 指向法条语料的条目标识（必填非空）
        "law_name",     # 法名（短，可选）
        "article_no",   # 条号（短，可选）
        "anchor_type",  # 锚点类型（短，可选）
    }
)

# E5 追加的「模型生成条文型」禁止键：这些键意味着条文由模型杜撰/改写/续写，
# 与「条文必锚定语料、不得由模型生成」红线冲突，出现即 fail-closed 拒绝。
# 注意：这些键本就不在 StatuteRef 白名单内，黑名单是「显式拒绝 + 告警」的双保险。
STATUTE_FORBIDDEN_GENERATED_KEYS: frozenset[str] = frozenset(
    {
        "generated_article",
        "generated_text",
        "generated_statute",
        "llm_text",
        "llm_article",
        "ai_text",
        "ai_article",
        "ai_generated_article",
        "model_generated_text",
        "paraphrased_article",
        "paraphrase",
        "article_paraphrase",
        "rewritten_article",
        "synthesized_article",
        "synthesized_text",
        "drafted_article",
        "hallucinated_text",
    }
)

# E5 追加的「富展示/裁判正文型」禁止键：与 E3 富展示禁止键同口径，
# 防止把裁判文书 summary/highlight/matched_text 借法条结果或互跳泄露。
# （本模块不 import rag，故在此本地冻结同口径键集，单点维护在测试中比对一致。）
STATUTE_FORBIDDEN_DISPLAY_KEYS: frozenset[str] = frozenset(
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


def is_forbidden_generated_statute_key(key: str) -> bool:
    """判断某个键是否为「模型生成条文型」键（大小写不敏感）。"""
    return str(key).strip().lower() in STATUTE_FORBIDDEN_GENERATED_KEYS


def is_statute_rejected_key(key: str) -> bool:
    """判断某个键是否应被 StatuteRef 入口拒绝（裁判正文 / 富展示 / PII / 模型生成条文型）。"""
    k = str(key).strip().lower()
    return (
        is_forbidden_body_key(k)
        or k in STATUTE_FORBIDDEN_DISPLAY_KEYS
        or is_forbidden_pii_key(k)
        or k in STATUTE_FORBIDDEN_GENERATED_KEYS
    )


def is_valid_statute_anchor(anchor: object) -> bool:
    """单条法条锚点是否合法：必须是 dict 且 text_id 非空字符串（指向法条语料）。"""
    if not isinstance(anchor, dict):
        return False
    text_id = anchor.get("text_id")
    return bool(text_id) and isinstance(text_id, str) and bool(text_id.strip())


def _is_valid_case_anchor(anchor: object) -> bool:
    """单条类案锚点是否合法：case_id / source_chunk_id 均为非空字符串（互跳 CandidateRef 用）。"""
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


class StatuteAnchorRef(BaseModel):
    """法条来源锚点（结构化引用，非条文正文）。

    最小合法锚点 = text_id 非空，指向法条语料库的具体条目；
    law_name / article_no / anchor_type 可选。绝不承载由模型生成的条文文本。
    """

    model_config = ConfigDict(extra="forbid")

    text_id: str = Field(min_length=1)
    law_name: str | None = None
    article_no: str | None = None
    anchor_type: str | None = None

    @field_validator("text_id")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("statute anchor text_id 不能为空")
        return v


class StatuteRelatedCaseRef(BaseModel):
    """法条→类案互跳携带的类案引用（= CandidateRef 同款字段，零裁判正文）。

    互跳红线：法条侧关联类案只回引用与元数据 + 案件来源锚点，**绝不内嵌裁判正文**。
    字段集与 E-1 CandidateRef 白名单逐字段一致（不增删，不因互跳被加字段）；
    source_anchors 必须非空，每条至少 case_id + source_chunk_id。
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
            raise ValueError("互跳 CandidateRef 必须有非空 source_anchors")
        for anchor in v:
            if not _is_valid_case_anchor(anchor):
                raise ValueError(
                    "互跳 CandidateRef 含不完整锚点（缺 case_id 或 source_chunk_id）"
                )
        return v


class StatuteRef(BaseModel):
    """E5 法条检索的**输出**契约对象（跨产品可复用引用，第 5 个契约对象）。

    字段集与文档 16 §4.1 / 文档 17 §3.5 / 文档 20 §4 逐字段一致：
    statute_id / law_name / article_no / statute_anchors / article_text? /
    source_corpus? / effective_status? / related_case_refs?。

    红线：
    - statute_anchors 必须非空，每条至少有 text_id（指向法条语料）——「无锚点不展示」。
    - article_text（若有）只来自法条语料、带 text_id 锚点，**不得由模型生成**；
      不提供 article_text 时退化为「只给法名+条号+锚点」的可核验引用。
    - 不含裁判正文 / chunk 正文 / 全文 / summary / highlight / matched_text。
    - related_case_refs 只承载 CandidateRef 同款字段（无正文），互跳不内嵌对侧正文。
    extra="forbid"：正文型 / 模型生成条文型 / 非白名单键在模型层即被拒绝（fail-closed）。
    """

    model_config = ConfigDict(extra="forbid")

    statute_id: str = Field(min_length=1)
    law_name: str = Field(min_length=1)
    article_no: str | None = None
    statute_anchors: list[StatuteAnchorRef] = Field(min_length=1)
    article_text: str | None = None
    source_corpus: str | None = None
    effective_status: str | None = None
    related_case_refs: list[StatuteRelatedCaseRef] = Field(default_factory=list)

    @field_validator("statute_anchors")
    @classmethod
    def _anchors_non_empty(
        cls, v: list[StatuteAnchorRef]
    ) -> list[StatuteAnchorRef]:
        if not v:
            raise ValueError("StatuteRef 必须有非空 statute_anchors（无锚点不展示）")
        return v


# --- sanitize 纯函数（白名单清洗 + fail-closed 校验）------------------------------

def _reject_statute_forbidden_keys(payload: Mapping[str, Any]) -> None:
    """显式拒绝裁判正文型 / 富展示型 / PII 型 / 模型生成条文型键（fail-closed）。

    异常消息只暴露**键名**，绝不回显键值（避免正文/PII 进入异常或日志）。
    """
    for key in payload:
        k = str(key).strip().lower()
        if is_forbidden_body_key(k):
            raise ContractViolationError(
                f"forbidden body-type key {key!r} not allowed in StatuteRef"
            )
        if k in STATUTE_FORBIDDEN_DISPLAY_KEYS:
            raise ContractViolationError(
                f"forbidden display/body key {key!r} not allowed in StatuteRef"
            )
        if is_forbidden_pii_key(k):
            raise ContractViolationError(
                f"forbidden PII-type key {key!r} not allowed in StatuteRef"
            )
        if k in STATUTE_FORBIDDEN_GENERATED_KEYS:
            raise ContractViolationError(
                f"forbidden model-generated statute key {key!r} not allowed in "
                "StatuteRef（条文必锚定语料、不得由模型生成）"
            )


def _sanitize_related_case_ref(payload: Mapping[str, Any]) -> StatuteRelatedCaseRef:
    """清洗单条互跳类案引用为合法 StatuteRelatedCaseRef（= CandidateRef 同款，无正文）。

    1. 先显式拒绝裁判正文 / 富展示 / PII / 模型生成型键（fail-closed）。
    2. 仅保留 E-1 CandidateRef 白名单七字段，其余非白名单键主动丢弃。
    3. source_anchors 必须非空且每条至少 case_id + source_chunk_id（StatuteRelatedCaseRef 校验）。
    """
    _reject_statute_forbidden_keys(payload)
    cleaned = {k: v for k, v in payload.items() if k in CANDIDATE_REF_FIELDS}

    raw_anchors = cleaned.get("source_anchors")
    if not raw_anchors:
        raise ContractViolationError(
            "互跳 CandidateRef 缺少非空 source_anchors，拒绝暴露不可溯源类案"
        )
    if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
        raise ContractViolationError("source_anchors 必须是锚点列表")

    normalized: list[dict[str, Any]] = []
    for anchor in raw_anchors:
        if not _is_valid_case_anchor(anchor):
            raise ContractViolationError(
                "互跳 CandidateRef 含不完整锚点（缺 case_id 或 source_chunk_id）"
            )
        normalized.append(
            {
                "case_id": anchor["case_id"],
                "source_chunk_id": anchor["source_chunk_id"],
                "anchor_type": anchor.get("anchor_type"),
            }
        )
    cleaned["source_anchors"] = normalized
    return StatuteRelatedCaseRef(**cleaned)


def sanitize_statute_ref(payload: Mapping[str, Any]) -> StatuteRef:
    """清洗任意 payload 为合法 StatuteRef（纯函数，无副作用，fail-closed）。

    1. 先显式拒绝裁判正文型 / 富展示型 / PII 型 / 模型生成条文型键（NO_GO 级，不静默丢弃）。
    2. 仅保留 StatuteRef 白名单字段，其余非白名单键主动丢弃。
    3. statute_anchors 必须存在、非空，每条至少有 text_id（指向法条语料）；
       任一不满足即抛 ContractViolationError（无锚点不展示、不杜撰）。
    4. related_case_refs（若有）逐条经 _sanitize_related_case_ref 清洗为无正文 CandidateRef。
    5. 用白名单子集 + 校验过的锚点/互跳引用构造 StatuteRef（extra="forbid" 再兜一层）。
    """
    _reject_statute_forbidden_keys(payload)
    cleaned = {k: v for k, v in payload.items() if k in STATUTE_REF_FIELDS}

    raw_anchors = cleaned.get("statute_anchors")
    if not raw_anchors:
        raise ContractViolationError(
            "StatuteRef 缺少非空 statute_anchors，拒绝展示无来源条文（无锚点不展示）"
        )
    if not isinstance(raw_anchors, Sequence) or isinstance(raw_anchors, (str, bytes)):
        raise ContractViolationError("statute_anchors 必须是锚点列表")

    normalized_anchors: list[dict[str, Any]] = []
    for anchor in raw_anchors:
        if not is_valid_statute_anchor(anchor):
            raise ContractViolationError(
                "statute_anchors 含不完整锚点（缺 text_id，无法指向法条语料）"
            )
        normalized_anchors.append(
            {
                "text_id": anchor["text_id"],
                "law_name": anchor.get("law_name"),
                "article_no": anchor.get("article_no"),
                "anchor_type": anchor.get("anchor_type"),
            }
        )
    cleaned["statute_anchors"] = normalized_anchors

    raw_related = cleaned.get("related_case_refs")
    if raw_related:
        if not isinstance(raw_related, Sequence) or isinstance(
            raw_related, (str, bytes)
        ):
            raise ContractViolationError("related_case_refs 必须是 CandidateRef 列表")
        cleaned["related_case_refs"] = [
            _sanitize_related_case_ref(ref).model_dump(exclude_none=True)
            for ref in raw_related
        ]

    return StatuteRef(**cleaned)


def assert_statute_anchored(payload: Mapping[str, Any]) -> None:
    """「法条条文必锚定、不杜撰」可校验断言（纯函数，fail-closed）。

    任何 StatuteRef 型 payload 出现模型生成条文型键、或缺非空 statute_anchors，
    即抛 ContractViolationError——条文无锚点 / 由模型生成是 NO_GO 级事件，必须显式失败。
    异常消息只暴露键名 / 结构性原因，绝不回显条文/正文值。
    """
    for key in payload:
        if is_forbidden_generated_statute_key(key):
            raise ContractViolationError(
                f"forbidden model-generated statute key {key!r}（条文必锚定语料、不得由模型生成）"
            )
    anchors = payload.get("statute_anchors")
    if not anchors:
        raise ContractViolationError(
            "StatuteRef 缺少非空 statute_anchors（无锚点不展示，不杜撰条文）"
        )
    if not isinstance(anchors, Sequence) or isinstance(anchors, (str, bytes)):
        raise ContractViolationError("statute_anchors 必须是锚点列表")
    for anchor in anchors:
        if not is_valid_statute_anchor(anchor):
            raise ContractViolationError(
                "statute_anchors 含不完整锚点（缺 text_id，无法指向法条语料）"
            )


__all__ = [
    "STATUTE_PRODUCES_CONTRACT",
    "STATUTE_RELATES_CONTRACT",
    "STATUTE_REF_FIELDS",
    "STATUTE_ANCHOR_FIELDS",
    "STATUTE_FORBIDDEN_GENERATED_KEYS",
    "STATUTE_FORBIDDEN_DISPLAY_KEYS",
    "is_forbidden_generated_statute_key",
    "is_statute_rejected_key",
    "is_valid_statute_anchor",
    "StatuteAnchorRef",
    "StatuteRelatedCaseRef",
    "StatuteRef",
    "sanitize_statute_ref",
    "assert_statute_anchored",
]
