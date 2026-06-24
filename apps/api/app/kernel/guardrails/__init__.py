"""共享内核 · 契约与护栏组公开面（E-2a 逻辑边界，纯 re-export）。

内核成员（依据文档 17 §2.1）：
- contracts/（E-1 已建）：跨产品契约对象字段白名单 + sanitize 纯函数。
- 来源锚点校验：sharing/anchors.py（见 kernel.identity 已收敛，本处再导出护栏视角）。
- 多租户过滤：team/isolation.py（见 kernel.identity，护栏视角导出）。
- 对象级鉴权：permission/access.py（见 kernel.identity，护栏视角导出）。

本模块只把上述「现有可调用入口」收敛为稳定公开符号，**纯 re-export**：
不复制实现、不改签名、不改运行时语义。护栏只在内核实现一次，消费方调用不重写不旁路。

E4 追加：intake 入口合同（E4-1）+ 脱敏纯函数（E4-2）从 contracts 再导出，作为录入端
护栏面。纯逻辑、不接线、不依赖 ENABLE_INTAKE_AI_EXTRACTION 的 on 路径。
E5 追加：法条检索 StatuteRef 契约对象（E5-1）从 contracts 再导出，作为法条端护栏面。
纯逻辑、不接线、不依赖 ENABLE_STATUTE_SEARCH 的 on 路径。
E6 追加：文书工作台 DraftDescriptor 契约对象（E6-1，合同变更追加 statute_refs）从 contracts
再导出，作为文书端护栏面。纯逻辑、不接线、不依赖 ENABLE_DRAFTING 的 on 路径。
"""
from __future__ import annotations

# --- contracts 白名单 + sanitize（E-1 已建护栏面）---
from app.kernel.guardrails.contracts import (
    CANDIDATE_REF_FIELDS,
    CASE_FOLDER_FIELDS,
    CONTRACT_FIELD_WHITELIST,
    DRAFT_DESCRIPTOR_FIELDS,
    FORBIDDEN_BODY_KEYS,
    SEARCH_PROFILE_FIELDS,
    ContractViolationError,
    is_forbidden_body_key,
    sanitize_contract,
)

# --- E4 intake 入口合同 + 脱敏纯函数（录入端护栏面，纯逻辑不接线）---
from app.kernel.guardrails.contracts import (
    INTAKE_CANDIDATE_REF_FIELDS,
    INTAKE_CONSUMES_CONTRACT,
    INTAKE_FORBIDDEN_PII_KEYS,
    INTAKE_PRODUCES_CONTRACT,
    INTAKE_SEARCH_PROFILE_FIELDS,
    assert_no_raw_case_payload,
    build_search_profile_from_raw,
    extract_case_cause,
    extract_dispute_focus_keywords,
    extract_region,
    extract_trial_level_preference,
    is_forbidden_pii_key,
    is_intake_rejected_key,
    redact_pii,
    sanitize_intake_profile_payload,
    sanitize_intake_search_profile,
)

# --- 来源锚点校验（共享自 sharing.anchors，护栏单点）---
from app.kernel.identity.sharing.anchors import is_valid_anchor, validate_anchors_for_share

# --- 多租户过滤（共享自 team.isolation，护栏单点）---
from app.kernel.identity.team.isolation import (
    TenantContext,
    assert_write_within_tenant,
    tenant_visibility_clause,
)

# --- 对象级鉴权（共享自 permission.access，护栏单点）---
from app.kernel.identity.permission.access import (
    AccessDecision,
    ObjectAccessInput,
    authorize,
    resolve_effective_level,
)

# --- E5 法条检索 StatuteRef 契约对象（第 5 个跨产品契约对象，纯逻辑不接线）---
from app.kernel.guardrails.contracts import (
    STATUTE_ANCHOR_FIELDS,
    STATUTE_FORBIDDEN_DISPLAY_KEYS,
    STATUTE_FORBIDDEN_GENERATED_KEYS,
    STATUTE_PRODUCES_CONTRACT,
    STATUTE_REF_FIELDS,
    STATUTE_RELATES_CONTRACT,
    StatuteAnchorRef,
    StatuteRef,
    StatuteRelatedCaseRef,
    assert_statute_anchored,
    is_forbidden_generated_statute_key,
    is_statute_rejected_key,
    is_valid_statute_anchor,
    sanitize_statute_ref,
)

# --- E6 文书工作台 DraftDescriptor 契约对象（第 3 契约对象，合同变更追加 statute_refs，纯逻辑不接线）---
from app.kernel.guardrails.contracts import (
    DRAFT_DESCRIPTOR_CORE_FIELDS,
    DRAFT_DESCRIPTOR_E6_FIELDS,
    DRAFT_FORBIDDEN_BODY_KEYS,
    DRAFT_FORBIDDEN_JUDGMENT_KEYS,
    DRAFT_FORBIDDEN_OUTCOME_KEYS,
    DRAFTING_CONSUMES_CONTRACTS,
    DRAFTING_PRODUCES_CONTRACT,
    NOTE_MAX_LEN,
    STRUCTURE_SKELETON_ITEM_MAX_LEN,
    STRUCTURE_SKELETON_MAX_ITEMS,
    TAG_MAX_LEN,
    DraftCandidateRef,
    DraftDescriptor,
    assert_no_draft_body,
    is_draft_rejected_key,
    is_forbidden_draft_body_key,
    is_forbidden_outcome_key,
    sanitize_draft_descriptor,
)

# --- E7 案件协作工作台 CaseFolder 契约对象（第 4 契约对象，确认口径，纯逻辑不接线）---
from app.kernel.guardrails.contracts import (
    CASE_FOLDER_CORE_FIELDS,
    CASE_FOLDER_E7_FIELDS,
    CASEBOOK_AGGREGATES_CONTRACTS,
    CASEBOOK_FORBIDDEN_DRAFT_KEYS,
    CASEBOOK_FORBIDDEN_JUDGMENT_KEYS,
    CASEBOOK_FORBIDDEN_OUTCOME_KEYS,
    CASEBOOK_PRODUCES_CONTRACT,
    DEFAULT_VISIBILITY,
    TITLE_MAX_LEN,
    VALID_VISIBILITY,
    CaseFolder,
    CaseFolderCandidateRef,
    assert_no_case_body,
    is_case_folder_rejected_key,
    is_forbidden_case_body_key,
    is_forbidden_case_outcome_key,
    sanitize_case_folder,
)

__all__ = [
    # contracts
    "SEARCH_PROFILE_FIELDS", "CANDIDATE_REF_FIELDS", "DRAFT_DESCRIPTOR_FIELDS",
    "CASE_FOLDER_FIELDS", "CONTRACT_FIELD_WHITELIST", "FORBIDDEN_BODY_KEYS",
    "ContractViolationError", "is_forbidden_body_key", "sanitize_contract",
    # anchor validation
    "is_valid_anchor", "validate_anchors_for_share",
    # tenant filter
    "TenantContext", "assert_write_within_tenant", "tenant_visibility_clause",
    # object authz
    "AccessDecision", "ObjectAccessInput", "authorize", "resolve_effective_level",
    # --- E4 intake 入口合同（冻结口径，不接线）---
    "INTAKE_PRODUCES_CONTRACT", "INTAKE_CONSUMES_CONTRACT",
    "INTAKE_SEARCH_PROFILE_FIELDS", "INTAKE_CANDIDATE_REF_FIELDS",
    "INTAKE_FORBIDDEN_PII_KEYS", "is_forbidden_pii_key", "is_intake_rejected_key",
    "assert_no_raw_case_payload", "sanitize_intake_search_profile",
    # --- E4-2 intake 脱敏纯函数（本地脱敏 + 要素抽取 + 后端防御层，不接线）---
    "redact_pii", "extract_case_cause", "extract_region",
    "extract_trial_level_preference", "extract_dispute_focus_keywords",
    "build_search_profile_from_raw", "sanitize_intake_profile_payload",
    # --- E5-1 法条检索 StatuteRef 契约对象（第 5 个跨产品契约对象，不接线）---
    "STATUTE_PRODUCES_CONTRACT", "STATUTE_RELATES_CONTRACT",
    "STATUTE_REF_FIELDS", "STATUTE_ANCHOR_FIELDS",
    "STATUTE_FORBIDDEN_GENERATED_KEYS", "STATUTE_FORBIDDEN_DISPLAY_KEYS",
    "is_forbidden_generated_statute_key", "is_statute_rejected_key",
    "is_valid_statute_anchor", "StatuteAnchorRef", "StatuteRelatedCaseRef",
    "StatuteRef", "sanitize_statute_ref", "assert_statute_anchored",
    # --- E6-1 文书工作台 DraftDescriptor 契约对象（第 3 契约对象，合同变更追加 statute_refs，不接线）---
    "DRAFTING_PRODUCES_CONTRACT", "DRAFTING_CONSUMES_CONTRACTS",
    "DRAFT_DESCRIPTOR_CORE_FIELDS", "DRAFT_DESCRIPTOR_E6_FIELDS",
    "DRAFT_FORBIDDEN_BODY_KEYS", "DRAFT_FORBIDDEN_JUDGMENT_KEYS",
    "DRAFT_FORBIDDEN_OUTCOME_KEYS", "STRUCTURE_SKELETON_ITEM_MAX_LEN",
    "STRUCTURE_SKELETON_MAX_ITEMS", "NOTE_MAX_LEN", "TAG_MAX_LEN",
    "is_forbidden_draft_body_key", "is_forbidden_outcome_key", "is_draft_rejected_key",
    "DraftCandidateRef", "DraftDescriptor", "sanitize_draft_descriptor",
    "assert_no_draft_body",
    # --- E7-1 案件协作工作台 CaseFolder 契约对象（第 4 契约对象，确认口径，不接线）---
    "CASEBOOK_PRODUCES_CONTRACT", "CASEBOOK_AGGREGATES_CONTRACTS",
    "CASE_FOLDER_CORE_FIELDS", "CASE_FOLDER_E7_FIELDS",
    "CASEBOOK_FORBIDDEN_JUDGMENT_KEYS", "CASEBOOK_FORBIDDEN_DRAFT_KEYS",
    "CASEBOOK_FORBIDDEN_OUTCOME_KEYS", "TITLE_MAX_LEN",
    "VALID_VISIBILITY", "DEFAULT_VISIBILITY",
    "is_forbidden_case_body_key", "is_forbidden_case_outcome_key",
    "is_case_folder_rejected_key", "CaseFolderCandidateRef", "CaseFolder",
    "sanitize_case_folder", "assert_no_case_body",
]
# E7-1 re-export 收口（CaseFolder 第 4 契约对象经 guardrails 公开面身份保持导出）。
