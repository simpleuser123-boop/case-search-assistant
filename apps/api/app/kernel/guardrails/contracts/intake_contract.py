"""E4-1 案情录入端入口合同（冻结口径，纯数据 + 纯函数，零业务实现、零接线）。

本模块只冻结「案情录入端 intake」与生态其余产品之间的契约口径，使其成为
机器可校验的常量 + 纯函数。**不建 intake 产品包、不实现脱敏函数主体、不接任何端点**
（脱敏在 E4-2 实现，端点在 E4-3 才建）。

录入端契约方向（文档 16 §3~§6 / 文档 17 §3 / 文档 19 §1）：

    原始案情（口语化、含 PII）  ← 仅浏览器本地内存，零上送服务端
      -> 本地脱敏 + 要素抽取（E4-2 实现）
      -> SearchProfile（白名单五字段，已脱敏）   ← intake 唯一**产出**的契约对象
      -> E3 InternalSearchService（E4-3 消费）
      -> CandidateRef[]（白名单七字段，无正文）  ← intake 唯一**消费**的契约对象

第一性约束（E4-1 红线，本模块严格遵守）：
- intake 只产出 SearchProfile（白名单五字段），只消费 CandidateRef（白名单七字段）。
- 原始案情零上送：任何 intake 入参 / 模型 / 持久层 / 日志 / 报告都不得含
  raw_case / raw_query / 当事人姓名 / 身份证 / 手机号 / 住址等 PII 型或正文型键。
- 字段白名单单点复用 E-1 冻结口径（SEARCH_PROFILE_FIELDS / CANDIDATE_REF_FIELDS），
  本模块**不另写第二套白名单**，只追加「PII 型键黑名单」这一录入端特有维度。
- 纯数据 + 纯函数：不 import 检索 / rerank / retrieval / summary 等运行时，不接任何端点，
  不依赖 ENABLE_INTAKE / ENABLE_INTAKE_AI_EXTRACTION 的 on 路径（两者本步皆 off、不接线）。
"""
from __future__ import annotations

from typing import Any, Mapping

from .whitelist import (
    CANDIDATE_REF_FIELDS,
    FORBIDDEN_BODY_KEYS,
    SEARCH_PROFILE_FIELDS,
    ContractViolationError,
    is_forbidden_body_key,
)

# --- intake 契约方向冻结（复用 E-1 白名单，单点不重写）---

# intake 唯一产出的契约对象 = SearchProfile（白名单五字段，已脱敏）。
INTAKE_PRODUCES_CONTRACT: str = "SearchProfile"
# intake 唯一消费的契约对象 = CandidateRef（白名单七字段，无正文，由 E3 服务产出）。
INTAKE_CONSUMES_CONTRACT: str = "CandidateRef"

# intake 出参字段白名单 = E-1 SearchProfile 五字段（不增删）。
INTAKE_SEARCH_PROFILE_FIELDS = SEARCH_PROFILE_FIELDS
# intake 入参（消费）字段白名单 = E-1 CandidateRef 七字段（不增删，只读不重构）。
INTAKE_CANDIDATE_REF_FIELDS = CANDIDATE_REF_FIELDS


# --- PII 型键黑名单（录入端特有红线：原始案情零上送）---
# 这些是「当事人身份 / 联系方式 / 原始案情」类键。它们本就不在 SearchProfile 白名单内，
# 黑名单是「显式拒绝 + fail-closed」的双保险：防止前端漏脱敏后把 PII 塞进 intake 入参
# 被静默丢弃而无告警。出现即视为「原始案情/PII 上送」，sanitize 直接拒绝（NO_GO 级事件）。
#
# 注意：这里只冻结**键名**口径（机器可校验），不做任何对值的脱敏/正则识别——
# 值层面的脱敏（识别并移除姓名/身份证/手机号等 PII token）是 E4-2 的脱敏纯函数职责。
INTAKE_FORBIDDEN_PII_KEYS: frozenset[str] = frozenset(
    {
        # 姓名 / 身份
        "name",
        "full_name",
        "party_name",
        "defendant_name",
        "plaintiff_name",
        "litigant_name",
        "real_name",
        # 证件号
        "id_card",
        "id_card_no",
        "id_number",
        "identity_card",
        "passport_no",
        "social_credit_code",
        "uscc",
        # 联系方式
        "phone",
        "phone_no",
        "phone_number",
        "mobile",
        "mobile_no",
        "telephone",
        "email",
        "email_address",
        # 金融
        "bank_card",
        "bank_card_no",
        "bank_account",
        "card_no",
        # 地址 / 车牌
        "address",
        "home_address",
        "residence",
        "residential_address",
        "plate_no",
        "license_plate",
        "car_plate",
    }
)


def is_forbidden_pii_key(key: str) -> bool:
    """判断某个键是否为录入端 PII 型键（大小写不敏感）。"""
    return key.strip().lower() in INTAKE_FORBIDDEN_PII_KEYS


def is_intake_rejected_key(key: str) -> bool:
    """判断某个键是否应被 intake 入口拒绝（正文型 或 PII 型）。"""
    return is_forbidden_body_key(key) or is_forbidden_pii_key(key)


def assert_no_raw_case_payload(payload: Mapping[str, Any]) -> None:
    """「原始案情零上送」可校验断言（纯函数，fail-closed）。

    任何 intake 入参 / 模型出现正文型键（raw_case/raw_query/full_text/...）或
    PII 型键（name/id_card/phone/address/...）即抛 ContractViolationError，
    不静默放行——原始案情/PII 出现在 intake 入口是 NO_GO 级事件，必须显式失败。

    异常消息只暴露**键名**，绝不回显键值（避免原始 PII/正文进入异常/日志）。
    """
    for key in payload:
        if is_forbidden_body_key(key):
            raise ContractViolationError(
                f"forbidden body-type key {key!r} not allowed at intake entry "
                "(原始案情零上送红线)"
            )
        if is_forbidden_pii_key(key):
            raise ContractViolationError(
                f"forbidden PII-type key {key!r} not allowed at intake entry "
                "(原始案情零上送红线)"
            )


def sanitize_intake_search_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    """按 SearchProfile 白名单清洗 intake 出参 payload（纯函数，无副作用）。

    规则（fail-closed）：
    1. 先跑「原始案情零上送」断言：出现任何正文型键或 PII 型键立即抛 ContractViolationError，
       不静默丢弃——这是录入端最强红线。
    2. 仅保留 SearchProfile 白名单五字段（case_cause / region / trial_level_preference /
       dispute_focus_keywords / query_text），其余非白名单键被主动丢弃。

    本函数只做**键级**白名单 + 红线校验；值层面的脱敏（移除 PII token）由 E4-2 实现。
    """
    assert_no_raw_case_payload(payload)
    return {
        key: value
        for key, value in payload.items()
        if key in INTAKE_SEARCH_PROFILE_FIELDS
    }


__all__ = [
    "INTAKE_PRODUCES_CONTRACT",
    "INTAKE_CONSUMES_CONTRACT",
    "INTAKE_SEARCH_PROFILE_FIELDS",
    "INTAKE_CANDIDATE_REF_FIELDS",
    "INTAKE_FORBIDDEN_PII_KEYS",
    "is_forbidden_pii_key",
    "is_intake_rejected_key",
    "assert_no_raw_case_payload",
    "sanitize_intake_search_profile",
]
