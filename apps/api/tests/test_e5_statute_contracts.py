"""E5-1 法条法规检索入口合同 focused 单元测试。

验证（与文档 20 §4 E5-1 目标逐条对应）：
- StatuteRef 契约对象冻结：字段集 = 文档 16 §4.1 / 17 §3.5 / 20 §4 白名单，extra=forbid。
- 法条来源锚点红线：statute_anchors 必填非空，每条至少 text_id（指向法条语料）；
  无锚点即 fail-closed 拒绝（「无锚点不展示、不杜撰条文」）。
- 拒绝裁判正文型键（full_text/content/chunk_text/summary_text/highlight_text/matched_text）。
- 拒绝 PII 型键（name/id_card/phone/address/...）。
- 拒绝「模型生成条文型」键（generated_article/llm_text/paraphrased_article/...）。
- 法条↔类案互跳口径：StatuteRef.related_case_refs = CandidateRef 同款（白名单七字段、
  必有 source_anchors、零裁判正文）；CandidateRef 侧字段未被改（仍是 E-1 七字段）。
- sanitize_statute_ref / assert_statute_anchored 为纯函数、不改输入、fail-closed。
- ENABLE_STATUTE_SEARCH + VITE 镜像默认 false；config.py 与 .env.example 一致。
- E5-1 不越界：statute_contract 模块不 import 检索/rerank/retrieval/summary/内核 rag 服务。
  契约纯度断言仍恒成立；产品包/router 计数已于 E5-7 调和为 E5-4 后实况
  （statute 合法落地、include_router=14），见 §8。

红线：本文件 fixture 只用短假数据 / hash / text_id / case_id / source_chunk_id / 元数据；
不写真实长条文正文、裁判正文、真实 PII。纯模型层断言 + 纯 AST 静态扫描，不触发检索/DB/网络。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.contracts import (
    CANDIDATE_REF_FIELDS,
    ContractViolationError,
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
from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
APP_DIR = Path(__file__).resolve().parents[1] / "app"
CONFIG_PY = APP_DIR / "core" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
STATUTE_CONTRACT_PY = (
    APP_DIR / "kernel" / "guardrails" / "contracts" / "statute_contract.py"
)
MAIN_PY = APP_DIR / "main.py"

# 文档 16/17/20 冻结的 StatuteRef 字段白名单（权威口径）。
EXPECTED_STATUTE_REF_FIELDS = {
    "statute_id",
    "law_name",
    "article_no",
    "statute_anchors",
    "article_text",
    "source_corpus",
    "effective_status",
    "related_case_refs",
}
EXPECTED_STATUTE_ANCHOR_FIELDS = {"text_id", "law_name", "article_no", "anchor_type"}
EXPECTED_CANDIDATE_REF_FIELDS = {
    "case_id",
    "case_number",
    "court",
    "trial_level",
    "case_cause",
    "judgment_date",
    "source_anchors",
}

# E5 涉及的 flag（沿用 E-1 五产品 flag，不新增产品级 flag）。
E5_BACKEND_FLAGS = ("ENABLE_STATUTE_SEARCH",)
E5_ENV_FLAGS = ("ENABLE_STATUTE_SEARCH", "VITE_ENABLE_STATUTE_SEARCH")
# 行为零变化基线：其余产品 + rerank flag 默认必须仍 false。
OTHER_FLAGS_MUST_BE_FALSE = (
    "ENABLE_ECOSYSTEM",
    "ENABLE_INTAKE",
    "ENABLE_INTAKE_AI_EXTRACTION",
    "ENABLE_DRAFTING",
    "ENABLE_CASEBOOK",
    "ENABLE_WEIGHTED_RERANK",
)

# E7-2 reconciliation: statute / drafting / casebook 已按阶段合法落地。
ALLOWED_PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")

# statute 契约模块绝不可 import 的检索底层 / 内核内部子模块。
FORBIDDEN_IMPORT_PREFIXES = (
    "app.retrieval",
    "app.rerank",
    "app.summary",
    "app.query_processing",
    "app.kernel.rag",
    "app.kernel.rag.retrieval",
    "app.kernel.rag.rerank",
    "app.kernel.rag.summary",
    "app.kernel.rag.internal_search_service",
    "app.kernel.rag.internal_search_contracts",
    "app.intake",
    "app.statute",
)


def _valid_anchor() -> dict:
    return {"text_id": "law-264", "law_name": "刑法", "article_no": "第264条"}


def _valid_case_ref() -> dict:
    return {
        "case_id": "case-1",
        "case_number": "(2020)X刑初1号",
        "source_anchors": [{"case_id": "case-1", "source_chunk_id": "ck-1"}],
    }


# ===========================================================================
# 1. StatuteRef 字段集冻结
# ===========================================================================
def test_statute_ref_field_whitelist_matches_doc():
    assert set(STATUTE_REF_FIELDS) == EXPECTED_STATUTE_REF_FIELDS


def test_statute_anchor_field_whitelist_matches_doc():
    assert set(STATUTE_ANCHOR_FIELDS) == EXPECTED_STATUTE_ANCHOR_FIELDS


def test_statute_contract_direction_constants():
    assert STATUTE_PRODUCES_CONTRACT == "StatuteRef"
    assert STATUTE_RELATES_CONTRACT == "CandidateRef"


def test_statute_ref_model_field_set_equals_whitelist():
    assert set(StatuteRef.model_fields) == EXPECTED_STATUTE_REF_FIELDS


def test_statute_anchor_model_field_set_equals_whitelist():
    assert set(StatuteAnchorRef.model_fields) == EXPECTED_STATUTE_ANCHOR_FIELDS


def test_candidate_ref_fields_unchanged_by_e5():
    # 互跳不得改 CandidateRef 字段：仍是 E-1 七字段。
    assert set(CANDIDATE_REF_FIELDS) == EXPECTED_CANDIDATE_REF_FIELDS
    assert set(StatuteRelatedCaseRef.model_fields) == EXPECTED_CANDIDATE_REF_FIELDS


# ===========================================================================
# 2. extra=forbid + happy path
# ===========================================================================
def test_statute_ref_extra_forbid():
    assert StatuteRef.model_config.get("extra") == "forbid"
    assert StatuteAnchorRef.model_config.get("extra") == "forbid"
    assert StatuteRelatedCaseRef.model_config.get("extra") == "forbid"


def test_sanitize_statute_ref_happy_path():
    ref = sanitize_statute_ref(
        {
            "statute_id": "s-264",
            "law_name": "刑法",
            "article_no": "第264条",
            "statute_anchors": [_valid_anchor()],
            "article_text": "盗窃公私财物，数额较大的……",  # 短片段，仅作 fixture
            "source_corpus": "judge_law_corpus",
            "effective_status": "current",
        }
    )
    assert ref.law_name == "刑法"
    assert ref.statute_anchors[0].text_id == "law-264"
    assert ref.related_case_refs == []


def test_sanitize_statute_ref_drops_non_whitelist_keys():
    ref = sanitize_statute_ref(
        {
            "statute_id": "s1",
            "law_name": "刑法",
            "statute_anchors": [_valid_anchor()],
            "unknown_field": "dropped",
            "rank_score": 0.9,
        }
    )
    dumped = ref.model_dump()
    assert "unknown_field" not in dumped
    assert "rank_score" not in dumped


# ===========================================================================
# 3. 锚点红线：无锚点 / 不完整锚点 fail-closed
# ===========================================================================
def test_statute_ref_requires_non_empty_anchors():
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {"statute_id": "s1", "law_name": "刑法", "statute_anchors": []}
        )


def test_statute_ref_missing_anchors_key_rejected():
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref({"statute_id": "s1", "law_name": "刑法"})


def test_statute_anchor_without_text_id_rejected():
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [{"law_name": "刑法", "article_no": "第264条"}],
            }
        )


def test_is_valid_statute_anchor():
    assert is_valid_statute_anchor({"text_id": "t1"}) is True
    assert is_valid_statute_anchor({"text_id": ""}) is False
    assert is_valid_statute_anchor({"law_name": "刑法"}) is False
    assert is_valid_statute_anchor("not-a-dict") is False


# ===========================================================================
# 4. 拒绝裁判正文型 / 富展示型 / PII 型 / 模型生成条文型键（fail-closed）
# ===========================================================================
@pytest.mark.parametrize(
    "bad_key",
    [
        "full_text",
        "content",
        "chunk_text",
        "judgment_text",
        "case_body",
        "raw_case",
    ],
)
def test_statute_ref_rejects_body_keys(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                bad_key: "X",
            }
        )


@pytest.mark.parametrize(
    "bad_key", ["summary", "summary_text", "highlights", "highlight_text", "matched_text"]
)
def test_statute_ref_rejects_display_keys(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                bad_key: "X",
            }
        )


@pytest.mark.parametrize(
    "bad_key", ["name", "id_card", "phone", "address", "defendant_name"]
)
def test_statute_ref_rejects_pii_keys(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                bad_key: "X",
            }
        )


@pytest.mark.parametrize(
    "bad_key",
    [
        "generated_article",
        "llm_text",
        "ai_generated_article",
        "paraphrased_article",
        "rewritten_article",
        "synthesized_article",
    ],
)
def test_statute_ref_rejects_model_generated_keys(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                bad_key: "杜撰条文",
            }
        )


def test_is_forbidden_generated_statute_key_case_insensitive():
    assert is_forbidden_generated_statute_key("Generated_Article") is True
    assert is_forbidden_generated_statute_key("LLM_TEXT") is True
    assert is_forbidden_generated_statute_key("law_name") is False


def test_is_statute_rejected_key_union():
    # 裁判正文 / 富展示 / PII / 模型生成型 全在拒绝集内；白名单字段不在。
    for k in ("full_text", "summary", "id_card", "generated_article"):
        assert is_statute_rejected_key(k) is True
    for k in ("statute_id", "law_name", "article_no", "statute_anchors"):
        assert is_statute_rejected_key(k) is False


# ===========================================================================
# 5. 法条↔类案互跳：related_case_refs = CandidateRef 同款（无正文）
# ===========================================================================
def test_statute_ref_related_case_refs_happy_path():
    ref = sanitize_statute_ref(
        {
            "statute_id": "s1",
            "law_name": "刑法",
            "statute_anchors": [_valid_anchor()],
            "related_case_refs": [_valid_case_ref()],
        }
    )
    assert len(ref.related_case_refs) == 1
    assert ref.related_case_refs[0].case_id == "case-1"
    assert ref.related_case_refs[0].source_anchors[0]["source_chunk_id"] == "ck-1"


def test_related_case_ref_requires_source_anchors():
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                "related_case_refs": [{"case_id": "c1"}],  # 缺 source_anchors
            }
        )


def test_related_case_ref_drops_body_and_non_whitelist_keys():
    # 互跳类案引用即便混入正文型键也必须 fail-closed（不静默搬运裁判正文）。
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                "related_case_refs": [
                    {
                        "case_id": "c1",
                        "source_anchors": [
                            {"case_id": "c1", "source_chunk_id": "ck1"}
                        ],
                        "full_text": "裁判正文不得内嵌",
                    }
                ],
            }
        )


def test_related_case_ref_dump_only_whitelist_keys():
    ref = sanitize_statute_ref(
        {
            "statute_id": "s1",
            "law_name": "刑法",
            "statute_anchors": [_valid_anchor()],
            "related_case_refs": [
                {
                    "case_id": "c1",
                    "court": "某法院",
                    "rank_score": 0.9,  # 非白名单
                    "source_anchors": [{"case_id": "c1", "source_chunk_id": "ck1"}],
                }
            ],
        }
    )
    dumped = ref.related_case_refs[0].model_dump()
    assert "rank_score" not in dumped
    assert set(dumped).issubset(EXPECTED_CANDIDATE_REF_FIELDS)


# ===========================================================================
# 6. assert_statute_anchored 纯函数 + 不改输入
# ===========================================================================
def test_assert_statute_anchored_passes_with_anchor():
    assert_statute_anchored(
        {"statute_id": "s1", "law_name": "刑法", "statute_anchors": [_valid_anchor()]}
    )


def test_assert_statute_anchored_rejects_missing_anchor():
    with pytest.raises(ContractViolationError):
        assert_statute_anchored({"statute_id": "s1", "law_name": "刑法"})


def test_assert_statute_anchored_rejects_generated_key():
    with pytest.raises(ContractViolationError):
        assert_statute_anchored(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                "generated_article": "杜撰",
            }
        )


def test_sanitize_statute_ref_does_not_mutate_input():
    payload = {
        "statute_id": "s1",
        "law_name": "刑法",
        "statute_anchors": [_valid_anchor()],
        "related_case_refs": [_valid_case_ref()],
    }
    snapshot = repr(payload)
    sanitize_statute_ref(payload)
    assert repr(payload) == snapshot


def test_violation_message_does_not_echo_value():
    # 异常消息只暴露键名，不回显键值（避免正文/PII 进入异常）。
    secret = "SENSITIVE_VALUE_SHOULD_NOT_APPEAR"
    with pytest.raises(ContractViolationError) as exc:
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [_valid_anchor()],
                "full_text": secret,
            }
        )
    assert secret not in str(exc.value)


# ===========================================================================
# 7. flag 默认值 + .env.example 一致
# ===========================================================================
def test_enable_statute_search_defaults_false():
    settings = Settings(_env_file=None)
    assert settings.ENABLE_STATUTE_SEARCH is False


def test_other_product_and_rerank_flags_still_false():
    settings = Settings(_env_file=None)
    for flag in OTHER_FLAGS_MUST_BE_FALSE:
        assert getattr(settings, flag) is False, f"{flag} 默认必须仍为 false"


def test_env_example_statute_flags_false():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for flag in E5_ENV_FLAGS:
        assert f"{flag}=false" in text, f".env.example 缺少 {flag}=false"
        assert f"{flag}=true" not in text, f".env.example 不得把 {flag} 默认改 true"


# ===========================================================================
# 8. statute_contract 纯度 + 产品包/router 实况（E5-7 调和至 E5-4 后口径）
#    契约模块零越界依赖；statute 合法落地为第 2 产品包；include_router=14。
# ===========================================================================
def _iter_import_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def test_statute_contract_does_not_import_retrieval_or_rag():
    for mod in _iter_import_modules(STATUTE_CONTRACT_PY):
        for bad in FORBIDDEN_IMPORT_PREFIXES:
            assert not (mod == bad or mod.startswith(bad + ".")), (
                f"statute_contract.py 不得 import {mod}（越界依赖检索/rerank/内核 rag/产品包）"
            )


def test_product_packages_match_e7_2_baseline():
    # E7-2 reconciliation：casebook 已合法落地；仍不得出现其它产品能力包。
    existing = [pkg for pkg in ALLOWED_PRODUCT_PACKAGES if (APP_DIR / pkg).exists()]
    assert {"intake", "statute", "drafting", "casebook"} <= set(existing)


def test_include_router_count_is_16():
    # E7-2 reconciliation：新增 casebook_router 后 include_router 由 15 → 16。
    text = MAIN_PY.read_text(encoding="utf-8")
    assert text.count("include_router") == 16, (
        "include_router 数必须为 16（intake + statute + drafting + casebook）"
    )


def test_statute_router_wired_post_e5_4():
    # E5-7 reconciliation：E5-4 后 statute_router 经 import + include_router 接线。
    # 注意端点 prefix=/api/statute 定义在 statute/router.py，main.py 仅 include。
    text = MAIN_PY.read_text(encoding="utf-8")
    assert "statute_router" in text, "main.py 必须 import 并接线 statute_router（E5-4）"
    assert "app.include_router(statute_router)" in text, "main.py 必须 include statute_router"


def test_statute_contract_module_is_pure_no_fastapi():
    # 契约模块不得引入 FastAPI / router / 端点接线。
    for mod in _iter_import_modules(STATUTE_CONTRACT_PY):
        assert not mod.startswith("fastapi"), "statute_contract.py 不得 import fastapi"
