"""E4-2 案情录入端脱敏纯函数 focused 单元测试。

验证（对应文档 19 §5 E4-2 验收标准）：
- 含 PII 的短假案情经脱敏后 0 PII 残留；SearchProfile 输出字段集严格 = E-1 白名单五字段。
- query_text 已脱敏（不含被识别的假手机号 / 假身份证 / 假姓名 / 假邮箱 token）。
- 结构化抽取（case_cause/region/trial_level_preference/dispute_focus_keywords）尽力而为且口径稳定。
- 后端防御层拒绝 raw_case/raw_query/PII 型键，fail-closed 抛 ContractViolationError，
  且异常消息不回显原始 PII 值。
- 后端防御层对「键名合法但值夹带 PII」做值级脱敏（第二道闸）。
- 前后端规则口径一致（共享 fixture，期望抽取结果一致）。

红线：fixture 只用短假数据（假姓名 / 假手机号 / 假身份证 / 假邮箱），绝不写真实 PII 或长案情。
"""
from __future__ import annotations

import pytest

from app.kernel.guardrails import (
    build_search_profile_from_raw,
    redact_pii,
    sanitize_intake_profile_payload,
)
from app.kernel.guardrails.contracts import (
    ContractViolationError,
    SEARCH_PROFILE_FIELDS,
)
from app.kernel.guardrails.contracts.intake_sanitize import (
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_ID_CARD,
    PLACEHOLDER_NAME,
    PLACEHOLDER_PHONE,
    extract_case_cause,
    extract_dispute_focus_keywords,
    extract_region,
    extract_trial_level_preference,
)

# --- 短假案情 fixture（与前端 sanitize.test.ts 共享口径，纯假数据）---------------
# 假姓名「张三 / 李四」、假手机号、假身份证（18 位占位）、假邮箱。
FAKE_RAW_CASE = (
    "原告张三与被告李四买卖合同纠纷一案，在上海某区审理。"
    "张三手机号13800001111，身份证110101199001011234，"
    "邮箱zhangsan@example.com。双方就货款与违约金存在争议，已进入二审。"
)

# 被识别为 PII 的假 token（断言它们绝不出现在任何输出里）。
PII_TOKENS = (
    "张三",
    "李四",
    "13800001111",
    "110101199001011234",
    "zhangsan@example.com",
)


# --- redact_pii：移除 / 占位，0 残留 -------------------------------------------

def test_redact_pii_removes_all_pii_tokens():
    redacted = redact_pii(FAKE_RAW_CASE)
    for token in PII_TOKENS:
        assert token not in redacted, f"PII 残留: {token}"
    # 占位符应出现（确实做了替换而非删空）。
    assert PLACEHOLDER_PHONE in redacted
    assert PLACEHOLDER_ID_CARD in redacted
    assert PLACEHOLDER_EMAIL in redacted
    assert PLACEHOLDER_NAME in redacted


def test_redact_pii_is_pure_on_empty():
    assert redact_pii("") == ""


def test_redact_pii_redacts_standalone_name_repetition():
    # 「张三」第二次无标签复述也应被占位。
    text = "原告张三主张权利，张三另行举证。"
    redacted = redact_pii(text)
    assert "张三" not in redacted


def test_redact_pii_keeps_role_label():
    redacted = redact_pii("被告李四未到庭")
    assert "被告" in redacted
    assert "李四" not in redacted


# --- build_search_profile_from_raw：白名单 + 0 PII ------------------------------

def test_profile_fields_strictly_whitelist():
    profile = build_search_profile_from_raw(FAKE_RAW_CASE)
    assert set(profile.keys()) == set(SEARCH_PROFILE_FIELDS)


def test_profile_has_zero_pii_residue():
    profile = build_search_profile_from_raw(FAKE_RAW_CASE)
    blob = repr(profile)
    for token in PII_TOKENS:
        assert token not in blob, f"SearchProfile 残留 PII: {token}"


def test_query_text_is_redacted_and_present():
    profile = build_search_profile_from_raw(FAKE_RAW_CASE)
    qt = profile["query_text"]
    assert qt, "query_text 不应为空"
    for token in PII_TOKENS:
        assert token not in qt


def test_query_text_length_capped():
    long_text = "买卖合同纠纷。" + ("争议" * 500)
    profile = build_search_profile_from_raw(long_text)
    assert len(profile["query_text"]) <= 280


# --- 结构化抽取口径 -------------------------------------------------------------

def test_extract_case_cause_known():
    assert extract_case_cause("这是一起买卖合同纠纷") == "买卖合同纠纷"


def test_extract_case_cause_charge_fallback():
    assert extract_case_cause("被控盗窃罪") == "盗窃罪"


def test_extract_region():
    assert extract_region("案件在上海审理") == "上海"


def test_extract_region_earliest_wins():
    # 上海在前、北京在后 -> 取最早出现。
    assert extract_region("上海与北京两地") == "上海"


def test_extract_trial_level_priority():
    # 同时含一审 / 二审 -> 取更高审级倾向。
    assert extract_trial_level_preference("一审判决后提起二审") == "二审"
    assert extract_trial_level_preference("申请再审") == "再审"


def test_extract_dispute_keywords_dedup_and_cap():
    kws = extract_dispute_focus_keywords("货款、违约金、违约金、利息争议")
    assert "货款" in kws and "违约金" in kws and "利息" in kws
    assert len(kws) == len(set(kws))
    assert len(kws) <= 8


def test_profile_extracts_expected_elements():
    profile = build_search_profile_from_raw(FAKE_RAW_CASE)
    assert profile["case_cause"] == "买卖合同纠纷"
    assert profile["region"] == "上海"
    assert profile["trial_level_preference"] == "二审"
    assert "货款" in profile["dispute_focus_keywords"]
    assert "违约金" in profile["dispute_focus_keywords"]


# --- 后端防御层：fail-closed 拒绝 PII / 正文型键 -------------------------------

@pytest.mark.parametrize("bad_key", ["raw_case", "raw_query", "full_text", "content"])
def test_payload_rejects_body_keys(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_intake_profile_payload({"case_cause": "x", bad_key: "原始口语化案情……"})


@pytest.mark.parametrize("bad_key", ["name", "id_card", "phone", "address", "email"])
def test_payload_rejects_pii_keys(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_intake_profile_payload({"query_text": "脱敏查询", bad_key: "张三"})


def test_payload_exception_does_not_echo_pii_value():
    secret = "13800001111"
    try:
        sanitize_intake_profile_payload({"phone": secret, "query_text": "q"})
    except ContractViolationError as exc:
        assert secret not in str(exc), "异常消息不得回显原始 PII 值"
    else:
        pytest.fail("应抛 ContractViolationError")


def test_payload_drops_non_whitelist_keys():
    out = sanitize_intake_profile_payload(
        {"case_cause": "买卖合同纠纷", "region": "上海", "internal_score": 0.9}
    )
    assert set(out.keys()) <= set(SEARCH_PROFILE_FIELDS)
    assert "internal_score" not in out


def test_payload_value_level_redaction_second_gate():
    # 键名合法（query_text），但值里仍夹带假手机号 -> 值级脱敏移除。
    out = sanitize_intake_profile_payload(
        {"query_text": "联系13800001111核实货款", "dispute_focus_keywords": ["违约金"]}
    )
    assert "13800001111" not in out["query_text"]
    assert PLACEHOLDER_PHONE in out["query_text"]
    assert out["dispute_focus_keywords"] == ["违约金"]


def test_payload_is_pure_does_not_mutate_input():
    payload = {"case_cause": "买卖合同纠纷", "region": "上海"}
    snapshot = dict(payload)
    sanitize_intake_profile_payload(payload)
    assert payload == snapshot
