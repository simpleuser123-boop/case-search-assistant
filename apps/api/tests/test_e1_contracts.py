"""E-1 契约白名单 focused 单元测试。

验证：
- 4 个契约对象字段集与文档 17 §3 逐字段一致。
- sanitize 丢弃非白名单键。
- sanitize 拒绝正文型键（fail-closed，抛 ContractViolationError）。
- 5 个产品 flag 默认 false，config.py 与 .env.example 一致。
- E-1 不接线：contracts 模块不 import 检索/rerank/retrieval/summary 运行时。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import (
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
from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PY = PROJECT_ROOT / "apps" / "api" / "app" / "core" / "config.py"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
CONTRACTS_DIR = PROJECT_ROOT / "apps" / "api" / "app" / "contracts"

E1_PRODUCT_FLAGS = (
    "ENABLE_ECOSYSTEM",
    "ENABLE_INTAKE",
    "ENABLE_STATUTE_SEARCH",
    "ENABLE_DRAFTING",
    "ENABLE_CASEBOOK",
)

E1_VITE_FLAGS = tuple(f"VITE_{flag}" for flag in E1_PRODUCT_FLAGS)

# 历史 flag 默认必须仍为 false（行为零变化护栏）。
LEGACY_FLAGS_DEFAULT_FALSE = (
    "ENABLE_WEIGHTED_RERANK",
    "ENABLE_SEARCH_HISTORY",
    "ENABLE_CASE_FAVORITE",
    "ENABLE_CASE_LIST",
    "ENABLE_LIST_EXPORT",
    "ENABLE_REPORT_TEMPLATE",
    "ENABLE_TEAM_REUSE",
    "ENABLE_ACCOUNT_SYSTEM",
    "ENABLE_TEAM_WORKSPACE",
    "ENABLE_PERMISSION_TIERING",
    "ENABLE_TEAM_SHARING",
    "ENABLE_BULK_IMPORT",
    "ENABLE_TENDENCY_ANALYSIS",
    "ENABLE_BILLING",
)

# 文档 17 §3 权威字段集（逐字段硬编码，作为冻结基线，防止白名单被悄悄改动）。
EXPECTED_SEARCH_PROFILE = {
    "case_cause",
    "region",
    "trial_level_preference",
    "dispute_focus_keywords",
    "query_text",
}
EXPECTED_CANDIDATE_REF = {
    "case_id",
    "case_number",
    "court",
    "trial_level",
    "case_cause",
    "judgment_date",
    "source_anchors",
}
EXPECTED_DRAFT_DESCRIPTOR = {
    "draft_id",
    "structure_skeleton",
    "candidate_refs",
    "note",
    "tag",
}
EXPECTED_CASE_FOLDER = {
    "case_folder_id",
    "owner_user_id",
    "team_id",
    "visibility",
    "search_profile_summary",
    "candidate_refs",
    "draft_descriptors",
    "created_at",
    "updated_at",
}


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# --- 字段白名单与文档 17 §3 逐字段一致 ---


def test_search_profile_fields_match_doc_s3():
    assert set(SEARCH_PROFILE_FIELDS) == EXPECTED_SEARCH_PROFILE


def test_candidate_ref_fields_match_doc_s3():
    assert set(CANDIDATE_REF_FIELDS) == EXPECTED_CANDIDATE_REF


def test_draft_descriptor_fields_match_doc_s3():
    assert set(DRAFT_DESCRIPTOR_FIELDS) == EXPECTED_DRAFT_DESCRIPTOR


def test_case_folder_fields_match_doc_s3():
    assert set(CASE_FOLDER_FIELDS) == EXPECTED_CASE_FOLDER


def test_contract_whitelist_has_exactly_four_objects():
    assert set(CONTRACT_FIELD_WHITELIST) == {
        "SearchProfile",
        "CandidateRef",
        "DraftDescriptor",
        "CaseFolder",
    }


def test_no_whitelist_contains_body_type_field():
    # 任何白名单出现正文型字段即 NO_GO，这里硬性断言不相交。
    for name, fields in CONTRACT_FIELD_WHITELIST.items():
        leaked = set(fields) & FORBIDDEN_BODY_KEYS
        assert not leaked, f"{name} leaked body-type fields: {leaked}"


# --- sanitize 行为 ---


def test_sanitize_drops_non_whitelist_keys():
    payload = {
        "case_id": "C-1",
        "court": "X 法院",
        "unknown_extra": "should_drop",
        "internal_score": 0.9,
    }
    out = sanitize_contract("CandidateRef", payload)
    assert out == {"case_id": "C-1", "court": "X 法院"}
    assert "unknown_extra" not in out
    assert "internal_score" not in out


def test_sanitize_keeps_only_whitelisted_search_profile_keys():
    payload = {
        "case_cause": "买卖合同纠纷",
        "region": "上海",
        "query_text": "脱敏后的查询",
        "extra_meta": "drop_me",
    }
    out = sanitize_contract("SearchProfile", payload)
    assert set(out) == {"case_cause", "region", "query_text"}


@pytest.mark.parametrize(
    "bad_key",
    ["raw_case", "raw_query", "full_text", "content", "chunk_text", "judgment_full_text"],
)
def test_sanitize_rejects_body_type_keys(bad_key):
    payload = {"case_id": "C-1", bad_key: "案情/裁判文书正文……"}
    with pytest.raises(ContractViolationError):
        sanitize_contract("CandidateRef", payload)


def test_sanitize_rejects_body_key_case_insensitive():
    payload = {"case_id": "C-1", "Full_Text": "x"}
    with pytest.raises(ContractViolationError):
        sanitize_contract("CandidateRef", payload)


def test_sanitize_rejects_unknown_contract_name():
    with pytest.raises(ContractViolationError):
        sanitize_contract("NotAContract", {"a": 1})


def test_is_forbidden_body_key():
    assert is_forbidden_body_key("raw_query")
    assert is_forbidden_body_key("  CONTENT  ")
    assert not is_forbidden_body_key("case_id")
    assert not is_forbidden_body_key("query_text")  # 已脱敏 query 不是正文型键


def test_sanitize_is_pure_does_not_mutate_input():
    payload = {"case_id": "C-1", "drop": "x"}
    snapshot = dict(payload)
    sanitize_contract("CandidateRef", payload)
    assert payload == snapshot


# --- 5 个产品 flag 默认 false，config 与 .env.example 一致 ---


def test_e1_product_flags_default_false_in_settings():
    s = Settings(_env_file=None)
    for flag in E1_PRODUCT_FLAGS:
        assert getattr(s, flag) is False, f"{flag} must default to False"


def test_legacy_flags_still_default_false():
    s = Settings(_env_file=None)
    for flag in LEGACY_FLAGS_DEFAULT_FALSE:
        assert getattr(s, flag) is False, f"{flag} must still default to False"


def test_e1_backend_flags_present_and_false_in_env_example():
    values = _env_example_values()
    for flag in E1_PRODUCT_FLAGS:
        assert flag in values, f"{flag} missing in .env.example"
        assert values[flag] == "false", f"{flag} must be false in .env.example"


def test_e1_vite_mirror_flags_present_and_false_in_env_example():
    values = _env_example_values()
    for flag in E1_VITE_FLAGS:
        assert flag in values, f"{flag} missing in .env.example"
        assert values[flag] == "false", f"{flag} must be false in .env.example"


def test_config_py_declares_all_five_product_flags():
    text = CONFIG_PY.read_text(encoding="utf-8")
    for flag in E1_PRODUCT_FLAGS:
        assert f"{flag}: bool = False" in text, f"{flag} not declared False in config.py"


# --- E-1 不越界：contracts 模块不依赖检索/rerank/retrieval 运行时 ---


def test_contracts_module_does_not_import_retrieval_runtime():
    forbidden = ("retrieval", "rerank", "summary", "query_processing", "case_store")
    for py in CONTRACTS_DIR.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for mod in forbidden:
            assert (
                f"import {mod}" not in text and f"from app.{mod}" not in text
            ), f"{py.name} must not import core runtime module {mod}"
