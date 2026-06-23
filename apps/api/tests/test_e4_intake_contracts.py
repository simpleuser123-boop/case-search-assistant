"""E4-1 案情录入端入口合同 focused 单元测试。

验证：
- intake 契约方向冻结：只产出 SearchProfile（白名单五字段）、只消费 CandidateRef（白名单七字段）。
- intake 白名单复用 E-1 冻结口径，逐字段一致（不另写第二套白名单）。
- PII 型键黑名单：name/id_card/phone/address/email 等出现即被拒绝（fail-closed）。
- 「原始案情零上送」断言：正文型键 + PII 型键均被显式拒绝，异常消息不回显键值。
- sanitize_intake_search_profile 丢弃非白名单键、拒绝正文/PII 型键、纯函数不改输入。
- ENABLE_INTAKE_AI_EXTRACTION + VITE 镜像默认 false，config.py 与 .env.example 一致；
  ENABLE_INTAKE 默认仍 false。
- E4-1 不越界：intake_contract 模块不 import 检索/rerank/retrieval 运行时。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import (
    CANDIDATE_REF_FIELDS,
    SEARCH_PROFILE_FIELDS,
    ContractViolationError,
    INTAKE_CANDIDATE_REF_FIELDS,
    INTAKE_CONSUMES_CONTRACT,
    INTAKE_FORBIDDEN_PII_KEYS,
    INTAKE_PRODUCES_CONTRACT,
    INTAKE_SEARCH_PROFILE_FIELDS,
    assert_no_raw_case_payload,
    is_forbidden_pii_key,
    is_intake_rejected_key,
    sanitize_intake_search_profile,
)
from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PY = PROJECT_ROOT / "apps" / "api" / "app" / "core" / "config.py"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
INTAKE_CONTRACT_PY = (
    PROJECT_ROOT
    / "apps"
    / "api"
    / "app"
    / "kernel"
    / "guardrails"
    / "contracts"
    / "intake_contract.py"
)

# E4-1 新增/相关 flag 默认必须为 false。
E4_BACKEND_FLAGS = ("ENABLE_INTAKE", "ENABLE_INTAKE_AI_EXTRACTION")
E4_ENV_FLAGS = (
    "ENABLE_INTAKE",
    "ENABLE_INTAKE_AI_EXTRACTION",
    "VITE_ENABLE_INTAKE",
    "VITE_ENABLE_INTAKE_AI_EXTRACTION",
)

# E-1 冻结字段集（与文档 17 §3 逐字段一致）。
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


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# --- 契约方向与白名单冻结（复用 E-1，逐字段一致）---


def test_intake_contract_direction_frozen():
    assert INTAKE_PRODUCES_CONTRACT == "SearchProfile"
    assert INTAKE_CONSUMES_CONTRACT == "CandidateRef"


def test_intake_search_profile_whitelist_is_e1_five_fields():
    assert set(INTAKE_SEARCH_PROFILE_FIELDS) == EXPECTED_SEARCH_PROFILE
    # 单点复用 E-1，不另写第二套白名单。
    assert INTAKE_SEARCH_PROFILE_FIELDS is SEARCH_PROFILE_FIELDS


def test_intake_candidate_ref_whitelist_is_e1_seven_fields():
    assert set(INTAKE_CANDIDATE_REF_FIELDS) == EXPECTED_CANDIDATE_REF
    assert INTAKE_CANDIDATE_REF_FIELDS is CANDIDATE_REF_FIELDS


def test_intake_whitelist_contains_no_pii_or_body_key():
    # 白名单本身绝不含 PII 型键。
    for field in INTAKE_SEARCH_PROFILE_FIELDS:
        assert not is_forbidden_pii_key(field), f"{field} 不应在白名单中"
    for field in INTAKE_CANDIDATE_REF_FIELDS:
        assert not is_forbidden_pii_key(field), f"{field} 不应在白名单中"


# --- PII 型键黑名单与拒绝口径 ---


@pytest.mark.parametrize(
    "pii_key",
    ["name", "id_card", "phone", "mobile", "email", "address", "bank_card", "plate_no"],
)
def test_is_forbidden_pii_key_hits(pii_key):
    assert is_forbidden_pii_key(pii_key)
    assert is_intake_rejected_key(pii_key)


def test_is_forbidden_pii_key_case_insensitive():
    assert is_forbidden_pii_key("ID_Card")
    assert is_forbidden_pii_key("  Phone  ")


def test_is_intake_rejected_key_covers_body_keys():
    # 正文型键也被 intake 入口拒绝。
    assert is_intake_rejected_key("raw_case")
    assert is_intake_rejected_key("full_text")


def test_whitelist_field_is_not_rejected():
    for field in EXPECTED_SEARCH_PROFILE:
        assert not is_intake_rejected_key(field)


# --- 原始案情零上送断言（fail-closed，异常不回显键值）---


@pytest.mark.parametrize("bad_key", ["raw_case", "raw_query", "full_text", "content"])
def test_assert_no_raw_case_rejects_body_keys(bad_key):
    with pytest.raises(ContractViolationError):
        assert_no_raw_case_payload({bad_key: "X"})


@pytest.mark.parametrize("bad_key", ["name", "id_card", "phone", "address", "email"])
def test_assert_no_raw_case_rejects_pii_keys(bad_key):
    with pytest.raises(ContractViolationError):
        assert_no_raw_case_payload({bad_key: "X"})


def test_assert_no_raw_case_passes_for_whitelist_payload():
    # 纯白名单 payload 不触发异常。
    assert_no_raw_case_payload(
        {"case_cause": "合同纠纷", "region": "X省", "query_text": "脱敏短查询"}
    )


def test_assert_exception_message_does_not_leak_value():
    secret_value = "张三_138SECRET0000"
    with pytest.raises(ContractViolationError) as exc:
        assert_no_raw_case_payload({"name": secret_value})
    assert secret_value not in str(exc.value)


# --- sanitize_intake_search_profile：白名单 + fail-closed + 纯函数 ---


def test_sanitize_intake_keeps_only_whitelist():
    out = sanitize_intake_search_profile(
        {
            "case_cause": "合同纠纷",
            "region": "X省",
            "trial_level_preference": "二审",
            "dispute_focus_keywords": ["违约金"],
            "query_text": "脱敏短查询",
            "unknown_extra": "drop_me",
        }
    )
    assert set(out) == EXPECTED_SEARCH_PROFILE
    assert "unknown_extra" not in out


@pytest.mark.parametrize("bad_key", ["raw_case", "name", "id_card", "phone"])
def test_sanitize_intake_rejects_body_and_pii(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_intake_search_profile({"case_cause": "合同纠纷", bad_key: "X"})


def test_sanitize_intake_is_pure_does_not_mutate_input():
    payload = {"case_cause": "合同纠纷", "drop": "x"}
    snapshot = dict(payload)
    sanitize_intake_search_profile(payload)
    assert payload == snapshot


# --- flag 默认值复核 ---


def test_e4_backend_flags_default_false_in_settings():
    s = Settings(_env_file=None)
    for flag in E4_BACKEND_FLAGS:
        assert getattr(s, flag) is False, f"{flag} must default to False"


def test_enable_intake_ai_extraction_declared_false_in_config():
    text = CONFIG_PY.read_text(encoding="utf-8")
    assert "ENABLE_INTAKE_AI_EXTRACTION: bool = False" in text


def test_e4_flags_present_and_false_in_env_example():
    values = _env_example_values()
    for flag in E4_ENV_FLAGS:
        assert flag in values, f"{flag} missing in .env.example"
        assert values[flag] == "false", f"{flag} must be false in .env.example"


# --- E4-1 不越界：intake_contract 不依赖检索/rerank/retrieval 运行时 ---


def test_intake_contract_module_does_not_import_retrieval_runtime():
    forbidden = ("retrieval", "rerank", "summary", "query_processing", "case_store")
    text = INTAKE_CONTRACT_PY.read_text(encoding="utf-8")
    for mod in forbidden:
        assert (
            f"import {mod}" not in text and f"from app.{mod}" not in text
        ), f"intake_contract.py must not import core runtime module {mod}"
