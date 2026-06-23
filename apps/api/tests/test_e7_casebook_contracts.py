"""E7-1 案件协作工作台入口合同 focused 单元测试。

验证（与文档 22 §4 E7-1 目标 + 文档 16 §4 / 17 §3.4 逐条对应）：
- CaseFolder 契约对象确认：E-1 §3.4 冻结的核心九字段保持不变（case_folder_id /
  owner_user_id / team_id / visibility / search_profile_summary / candidate_refs /
  draft_descriptors / created_at / updated_at），E7 权威白名单为其超集 +
  用户自填短字段 title/note/tag，extra=forbid。
- 只归集不起草不下结论：拒绝四类键——①裁判正文型 ②起草正文型 ③PII 型 ④胜负/结论型；
  fail-closed，异常消息只暴露键名 / reason code，绝不回显原始值。
- search_profile_summary 脱敏白名单子集：注入原始案情/正文/PII 键 fail-closed；
  非白名单键被主动丢弃，只保留 SearchProfile 脱敏白名单子集。
- 引用必带锚点：candidate_refs 缺锚点项 fail-closed 丢弃；draft_descriptors 走 E6
  sanitize（其内层引用缺锚点亦丢弃），保留项 100% 有锚点。
- visibility 缺省补 private；非法 visibility 值被拒（默认私有红线）。
- sanitize_case_folder 输出只含白名单字段、零裁判正文、零起草正文、零原始案情。
- 公开面导出可达（app.kernel.guardrails / app.contracts），身份保持（is 同一对象）。
- E7-1 不越界：casebook_contract 不 import 检索/rerank/retrieval/summary/内核 rag 服务/
  任何产品包；CASE_FOLDER_E7_FIELDS 不进 CONTRACT_FIELD_WHITELIST（E-1 四对象核心口径不动）；
  ENABLE_CASEBOOK + VITE 镜像默认 false。E7-2 后允许新增 casebook 产品包，include_router 为 16。

红线：本文件 fixture 只用短假数据 / hash / text_id / case_id / source_chunk_id / 元数据；
不写真实裁判正文、起草正文、真实 PII / 原始案情。纯模型层断言 + 纯 AST 静态扫描，
不触发检索/DB/网络。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.contracts import (
    CANDIDATE_REF_FIELDS,
    CASE_FOLDER_CORE_FIELDS,
    CASE_FOLDER_E7_FIELDS,
    CASE_FOLDER_FIELDS,
    CASEBOOK_AGGREGATES_CONTRACTS,
    CASEBOOK_FORBIDDEN_DRAFT_KEYS,
    CASEBOOK_FORBIDDEN_JUDGMENT_KEYS,
    CASEBOOK_FORBIDDEN_OUTCOME_KEYS,
    CASEBOOK_PRODUCES_CONTRACT,
    CONTRACT_FIELD_WHITELIST,
    DEFAULT_VISIBILITY,
    SEARCH_PROFILE_FIELDS,
    TITLE_MAX_LEN,
    VALID_VISIBILITY,
    CaseFolder,
    CaseFolderCandidateRef,
    ContractViolationError,
    assert_no_case_body,
    is_case_folder_rejected_key,
    is_forbidden_case_body_key,
    is_forbidden_case_outcome_key,
    sanitize_case_folder,
)

API_ROOT = Path(__file__).resolve().parents[1]
CASEBOOK_CONTRACT_PATH = (
    API_ROOT / "app" / "kernel" / "guardrails" / "contracts" / "casebook_contract.py"
)

# 合法锚点（case_id + source_chunk_id 均非空）。
_VALID_ANCHOR = [{"case_id": "c1", "source_chunk_id": "ck1", "anchor_type": "semantic"}]


def _valid_candidate(case_id: str = "c1") -> dict:
    return {"case_id": case_id, "court": "X院", "source_anchors": _VALID_ANCHOR}


def _valid_draft(draft_id: str = "d1") -> dict:
    return {
        "draft_id": draft_id,
        "structure_skeleton": ["事实与争议", "类案要旨"],
        "candidate_refs": [{"case_id": "c1", "source_anchors": _VALID_ANCHOR}],
    }


# --- 1. CaseFolder 契约确认：核心九字段不变 + E7 超集 + extra=forbid ------------------

def test_case_folder_core_fields_frozen_nine():
    """E-1 §3.4 冻结核心九字段保持不变（与 whitelist.CASE_FOLDER_FIELDS 逐位一致）。"""
    expected = {
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
    assert CASE_FOLDER_FIELDS == expected
    assert CASE_FOLDER_CORE_FIELDS == CASE_FOLDER_FIELDS


def test_case_folder_e7_fields_is_superset_with_short_fields():
    """E7 权威白名单 = 核心九字段 + 用户自填短字段 title/note/tag。"""
    assert CASE_FOLDER_E7_FIELDS == CASE_FOLDER_FIELDS | {"title", "note", "tag"}
    # 短字段确实是 E7 追加，不污染 E-1 核心。
    for f in ("title", "note", "tag"):
        assert f not in CASE_FOLDER_FIELDS


def test_case_folder_e7_fields_not_in_contract_whitelist():
    """CASE_FOLDER_E7_FIELDS 不进 CONTRACT_FIELD_WHITELIST（E-1 四对象核心口径不动）。"""
    assert CONTRACT_FIELD_WHITELIST["CaseFolder"] == CASE_FOLDER_FIELDS
    # 没有任何 CONTRACT_FIELD_WHITELIST 值被替换为 E7 超集。
    assert CASE_FOLDER_E7_FIELDS not in CONTRACT_FIELD_WHITELIST.values()


def test_case_folder_extra_forbid_rejects_unknown_key():
    with pytest.raises(ValueError):
        CaseFolder(case_folder_id="f1", owner_user_id="u1", unexpected_key="x")


def test_casebook_contract_direction_constants():
    assert CASEBOOK_PRODUCES_CONTRACT == "CaseFolder"
    assert set(CASEBOOK_AGGREGATES_CONTRACTS) == {
        "SearchProfile",
        "CandidateRef",
        "DraftDescriptor",
    }


# --- 2. 四类禁止键 fail-closed + 异常不回显原始值 -----------------------------------

@pytest.mark.parametrize(
    "bad_key,secret_value",
    [
        ("judgment_text", "SECRET-JUDGMENT-BODY"),
        ("chunk_text", "SECRET-CHUNK-BODY"),
        ("summary_text", "SECRET-SUMMARY"),
        ("highlight_text", "SECRET-HL"),
        ("matched_text", "SECRET-MATCH"),
        ("full_text", "SECRET-FULL"),
        ("content", "SECRET-CONTENT"),
        ("draft_body", "SECRET-DRAFT"),
        ("generated_text", "SECRET-GEN"),
        ("opinion_text", "SECRET-OPINION"),
        ("paragraph_body", "SECRET-PARA"),
        ("conclusion_text", "SECRET-CONCL"),
        ("name", "SECRET-NAME"),
        ("id_card", "SECRET-ID"),
        ("phone", "SECRET-PHONE"),
        ("address", "SECRET-ADDR"),
        ("win_probability", "0.99"),
        ("outcome_prediction", "SECRET-OUTCOME"),
        ("verdict", "SECRET-VERDICT"),
        ("case_summary_text", "SECRET-CASE-SUMMARY"),
    ],
)
def test_forbidden_keys_rejected_without_value_leak(bad_key, secret_value):
    payload = {"case_folder_id": "f1", "owner_user_id": "u1", bad_key: secret_value}
    with pytest.raises(ContractViolationError) as exc:
        sanitize_case_folder(payload)
    # 异常消息只暴露键名，绝不回显原始值。
    assert secret_value not in str(exc.value)


def test_is_case_folder_rejected_key_covers_four_classes():
    for k in ("judgment_text", "draft_body", "name", "win_probability"):
        assert is_case_folder_rejected_key(k)
    assert is_forbidden_case_body_key("chunk_text")
    assert is_forbidden_case_body_key("draft_body")
    assert is_forbidden_case_outcome_key("verdict")
    assert not is_case_folder_rejected_key("case_folder_id")


# --- 3. search_profile_summary 脱敏白名单子集 ---------------------------------------

def test_search_profile_summary_keeps_only_dehydrated_subset():
    folder = sanitize_case_folder(
        {
            "case_folder_id": "f1",
            "owner_user_id": "u1",
            "search_profile_summary": {
                "case_cause": "民间借贷纠纷",
                "region": "上海",
                "not_a_whitelist_key": "DROP-ME",
            },
        }
    )
    assert folder.search_profile_summary == {
        "case_cause": "民间借贷纠纷",
        "region": "上海",
    }
    # 只保留 SearchProfile 脱敏白名单子集键。
    assert set(folder.search_profile_summary).issubset(SEARCH_PROFILE_FIELDS)


@pytest.mark.parametrize("raw_key", ["raw_case", "raw_query", "fact_text", "name", "id_card"])
def test_search_profile_summary_rejects_raw_or_pii(raw_key):
    with pytest.raises(ContractViolationError):
        sanitize_case_folder(
            {
                "case_folder_id": "f1",
                "owner_user_id": "u1",
                "search_profile_summary": {raw_key: "SECRET-RAW-CASE"},
            }
        )


# --- 4. 引用必带锚点：缺锚点丢弃，保留项 100% 有锚点 --------------------------------

def test_candidate_refs_missing_anchor_dropped():
    folder = sanitize_case_folder(
        {
            "case_folder_id": "f1",
            "owner_user_id": "u1",
            "candidate_refs": [
                _valid_candidate("c1"),
                {"case_id": "c2"},  # 无 source_anchors → 丢弃
                {"case_id": "c3", "source_anchors": [{"case_id": "c3"}]},  # 缺 chunk → 丢弃
            ],
        }
    )
    assert len(folder.candidate_refs) == 1
    # 保留项 100% 有锚点。
    for ref in folder.candidate_refs:
        assert ref.source_anchors
        for a in ref.source_anchors:
            assert a["case_id"] and a["source_chunk_id"]


def test_draft_descriptors_kept_have_anchors():
    folder = sanitize_case_folder(
        {
            "case_folder_id": "f1",
            "owner_user_id": "u1",
            "draft_descriptors": [_valid_draft("d1")],
        }
    )
    assert len(folder.draft_descriptors) == 1
    for d in folder.draft_descriptors:
        for ref in d.candidate_refs:
            assert ref.source_anchors


def test_draft_descriptor_with_forbidden_key_raises():
    with pytest.raises(ContractViolationError):
        sanitize_case_folder(
            {
                "case_folder_id": "f1",
                "owner_user_id": "u1",
                "draft_descriptors": [
                    {
                        "draft_id": "d1",
                        "structure_skeleton": ["标题"],
                        "draft_body": "SECRET-DRAFT-BODY",
                    }
                ],
            }
        )


# --- 5. visibility 默认 private + 非法值拒绝 ----------------------------------------

def test_visibility_defaults_to_private():
    folder = sanitize_case_folder({"case_folder_id": "f1", "owner_user_id": "u1"})
    assert folder.visibility == DEFAULT_VISIBILITY == "private"


def test_visibility_team_allowed():
    folder = sanitize_case_folder(
        {"case_folder_id": "f1", "owner_user_id": "u1", "visibility": "team"}
    )
    assert folder.visibility == "team"


@pytest.mark.parametrize("bad", ["public", "world", "shared", "PRIVATE"])
def test_visibility_invalid_rejected(bad):
    with pytest.raises(ValueError):
        sanitize_case_folder(
            {"case_folder_id": "f1", "owner_user_id": "u1", "visibility": bad}
        )


def test_visibility_empty_string_defaults_to_private():
    """空串 visibility 视为「缺省」→ 补 private（隐私安全默认，非显式非法值）。"""
    folder = sanitize_case_folder(
        {"case_folder_id": "f1", "owner_user_id": "u1", "visibility": ""}
    )
    assert folder.visibility == "private"


def test_valid_visibility_set():
    assert VALID_VISIBILITY == {"private", "team"}


# --- 6. sanitize 输出只含白名单字段、零正文 ----------------------------------------

def test_sanitize_output_only_whitelist_fields():
    folder = sanitize_case_folder(
        {
            "case_folder_id": "f1",
            "owner_user_id": "u1",
            "team_id": "t1",
            "visibility": "team",
            "search_profile_summary": {"case_cause": "借贷", "region": "沪"},
            "candidate_refs": [_valid_candidate("c1")],
            "draft_descriptors": [_valid_draft("d1")],
            "title": "案件A",
            "note": "短备注",
            "tag": "借贷",
            "garbage_key": "DROP",
        }
    )
    dump = folder.model_dump(exclude_none=True)
    assert set(dump).issubset(CASE_FOLDER_E7_FIELDS)
    assert "garbage_key" not in dump


def test_title_length_capped():
    with pytest.raises(ValueError):
        CaseFolder(
            case_folder_id="f1",
            owner_user_id="u1",
            title="x" * (TITLE_MAX_LEN + 1),
        )


def test_assert_no_case_body_catches_nested_body():
    with pytest.raises(ContractViolationError):
        assert_no_case_body(
            {
                "case_folder_id": "f1",
                "owner_user_id": "u1",
                "candidate_refs": [{"case_id": "c1", "chunk_text": "SECRET-NESTED-BODY"}],
            }
        )


def test_assert_no_case_body_catches_summary_pii():
    with pytest.raises(ContractViolationError):
        assert_no_case_body(
            {
                "case_folder_id": "f1",
                "owner_user_id": "u1",
                "search_profile_summary": {"name": "SECRET-NAME"},
            }
        )


def test_assert_no_case_body_passes_clean_folder():
    # 干净 folder 不抛错。
    assert_no_case_body(
        {
            "case_folder_id": "f1",
            "owner_user_id": "u1",
            "candidate_refs": [_valid_candidate("c1")],
            "search_profile_summary": {"case_cause": "借贷"},
        }
    )


# --- 7. 公开面导出可达 + 身份保持 ---------------------------------------------------

def test_public_face_identity_preserved():
    from app.kernel.guardrails import (
        CaseFolder as G_CaseFolder,
        sanitize_case_folder as g_sanitize,
        assert_no_case_body as g_assert,
        CASE_FOLDER_E7_FIELDS as g_fields,
    )
    from app.kernel.guardrails.contracts import (
        CaseFolder as K_CaseFolder,
        sanitize_case_folder as k_sanitize,
    )
    # app.contracts（本文件顶部导入）/ guardrails / contracts 三面身份保持。
    assert CaseFolder is G_CaseFolder is K_CaseFolder
    assert sanitize_case_folder is g_sanitize is k_sanitize
    assert assert_no_case_body is g_assert
    assert CASE_FOLDER_E7_FIELDS is g_fields


# --- 8. E7-1/E7-2 不越界：静态边界 + flag 默认 false + casebook 包受控接入 --------

def _casebook_imports() -> set[str]:
    tree = ast.parse(CASEBOOK_CONTRACT_PATH.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name)
    return mods


def test_casebook_contract_no_runtime_or_product_imports():
    mods = _casebook_imports()
    forbidden_substrings = (
        "retrieval",
        "rerank",
        "summary",
        "kernel.rag",
        "app.intake",
        "app.statute",
        "app.drafting",
        "app.casebook",
        "router",
        "fastapi",
        "sqlmodel",
        "sqlalchemy",
    )
    for m in mods:
        for bad in forbidden_substrings:
            assert bad not in m, f"casebook_contract 不应 import {m!r}（含 {bad!r}）"


def test_casebook_contract_only_relative_sibling_imports():
    """只依赖同包 whitelist / intake_contract / statute_contract / drafting_contract。"""
    tree = ast.parse(CASEBOOK_CONTRACT_PATH.read_text(encoding="utf-8"))
    rel_mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module:
            rel_mods.add(node.module)
    assert rel_mods.issubset(
        {"whitelist", "intake_contract", "statute_contract", "drafting_contract"}
    ), rel_mods


def test_enable_casebook_flag_default_false():
    from app.core.config import settings

    assert settings.ENABLE_CASEBOOK is False


def test_include_router_count_after_e7_2_is_16():
    main_src = (API_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert main_src.count("app.include_router") == 16


def test_casebook_product_package_created_only_at_e7_2_boundary():
    casebook_dir = API_ROOT / "app" / "casebook"
    assert casebook_dir.exists(), "E7-2 应建立 casebook 产品包"
    assert {p.name for p in casebook_dir.glob("*.py")} >= {
        "__init__.py",
        "models.py",
        "store.py",
        "schemas.py",
        "service.py",
        "router.py",
    }
