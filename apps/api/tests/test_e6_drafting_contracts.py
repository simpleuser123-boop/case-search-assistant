"""E6-1 文书工作台入口合同 focused 单元测试。

验证（与文档 21 §2 E6-1 目标 + 文档 16 §4 / 17 §3.3 逐条对应）：
- DraftDescriptor 契约对象确认：E-1 §3.3 冻结的核心五字段保持不变（draft_id /
  structure_skeleton / candidate_refs / note / tag），E6 权威白名单为其超集 + 可选
  statute_refs（合同变更登记 2026-06-18）+ 持久层元数据，extra=forbid。
- 只组装不起草：拒绝四类键——①起草正文型 ②裁判正文型 ③PII 型 ④胜负/结论型；
  fail-closed，异常消息只暴露键名 / reason code，绝不回显原始值。
- structure_skeleton 标题校验：超长项（疑似正文）被拒并记 reason code；非标题项被拒。
- 引用必带锚点：candidate_refs / statute_refs 缺锚点项 fail-closed 丢弃，保留项 100% 有锚点。
- sanitize_draft_descriptor 输出只含白名单字段、零起草正文、零裁判正文。
- 公开面导出可达（app.kernel.guardrails / app.contracts），身份保持（is 同一对象）。
- E6-1 不越界：drafting_contract 不 import 检索/rerank/retrieval/summary/内核 rag 服务/
  任何产品包；ENABLE_DRAFTING + VITE 镜像默认 false。（E7-2 基线：include_router=16、drafting/casebook 包已建。）

红线：本文件 fixture 只用短假数据 / hash / text_id / case_id / source_chunk_id / 元数据；
不写真实起草正文、裁判正文、真实 PII。纯模型层断言 + 纯 AST 静态扫描，不触发检索/DB/网络。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.contracts import (
    CANDIDATE_REF_FIELDS,
    ContractViolationError,
    DRAFT_DESCRIPTOR_FIELDS,
    DRAFT_DESCRIPTOR_CORE_FIELDS,
    DRAFT_DESCRIPTOR_E6_FIELDS,
    DRAFT_FORBIDDEN_BODY_KEYS,
    DRAFT_FORBIDDEN_JUDGMENT_KEYS,
    DRAFT_FORBIDDEN_OUTCOME_KEYS,
    DRAFTING_CONSUMES_CONTRACTS,
    DRAFTING_PRODUCES_CONTRACT,
    STRUCTURE_SKELETON_ITEM_MAX_LEN,
    DraftCandidateRef,
    DraftDescriptor,
    assert_no_draft_body,
    is_draft_rejected_key,
    is_forbidden_draft_body_key,
    is_forbidden_outcome_key,
    sanitize_draft_descriptor,
)

API_ROOT = Path(__file__).resolve().parents[1]
DRAFTING_CONTRACT_PATH = (
    API_ROOT / "app" / "kernel" / "guardrails" / "contracts" / "drafting_contract.py"
)
MAIN_PATH = API_ROOT / "app" / "main.py"


def _anchored_candidate(case_id: str = "C-1") -> dict:
    return {
        "case_id": case_id,
        "court": "X 法院",
        "source_anchors": [{"case_id": case_id, "source_chunk_id": "ck-1"}],
    }


def _anchored_statute(statute_id: str = "S-1") -> dict:
    return {
        "statute_id": statute_id,
        "law_name": "刑法",
        "article_no": "264",
        "statute_anchors": [{"text_id": "law-264-0"}],
    }


# --- 1. DraftDescriptor 白名单确认（E-1 五字段不变 + E6 超集）---------------------


def test_e1_core_five_fields_unchanged():
    """E-1 §3.3 冻结的核心五字段保持逐位一致（不擅自增删）。"""
    assert set(DRAFT_DESCRIPTOR_CORE_FIELDS) == {
        "draft_id",
        "structure_skeleton",
        "candidate_refs",
        "note",
        "tag",
    }
    # DRAFT_DESCRIPTOR_CORE_FIELDS 单点复用 E-1 whitelist，is 同一冻结对象。
    assert DRAFT_DESCRIPTOR_CORE_FIELDS is DRAFT_DESCRIPTOR_FIELDS


def test_e6_whitelist_is_superset_with_statute_refs():
    """E6 权威白名单 = 核心五字段 + 可选 statute_refs（合同变更）+ 持久层元数据。"""
    assert set(DRAFT_DESCRIPTOR_CORE_FIELDS) <= set(DRAFT_DESCRIPTOR_E6_FIELDS)
    assert "statute_refs" in DRAFT_DESCRIPTOR_E6_FIELDS
    assert {"created_at", "updated_at", "owner_user_id", "team_id", "visibility"} <= set(
        DRAFT_DESCRIPTOR_E6_FIELDS
    )
    # 白名单内不得出现任何起草正文 / 裁判正文 / 胜负结论型字段。
    forbidden = (
        DRAFT_FORBIDDEN_BODY_KEYS
        | DRAFT_FORBIDDEN_JUDGMENT_KEYS
        | DRAFT_FORBIDDEN_OUTCOME_KEYS
    )
    assert not (set(DRAFT_DESCRIPTOR_E6_FIELDS) & forbidden)


def test_contract_direction_constants():
    assert DRAFTING_PRODUCES_CONTRACT == "DraftDescriptor"
    assert "CandidateRef" in DRAFTING_CONSUMES_CONTRACTS
    assert "StatuteRef" in DRAFTING_CONSUMES_CONTRACTS


def test_model_extra_forbid():
    """extra=forbid：非白名单键在模型层即被拒。"""
    with pytest.raises(Exception):
        DraftDescriptor(draft_id="D", structure_skeleton=["t"], bogus_field=1)


# --- 2. 只组装不起草：四类禁止键被拒，异常不回显原始值 -----------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        # ①起草正文型
        "draft_body",
        "draft_content",
        "generated_text",
        "opinion_text",
        "paragraph_body",
        "conclusion_text",
        # ②裁判正文型
        "chunk_text",
        "judgment_text",
        "summary_text",
        "highlight_text",
        "matched_text",
        # ③PII 型
        "name",
        "id_card",
        "phone",
        "address",
        # ④胜负/结论型
        "win_probability",
        "outcome_prediction",
        "verdict",
        # 模型生成条文型（引用法条兜底）
        "generated_article",
    ],
)
def test_forbidden_keys_rejected_no_value_leak(bad_key):
    secret = "SENSITIVE_VALUE_SHOULD_NOT_LEAK"
    payload = {
        "draft_id": "D-1",
        "structure_skeleton": ["一、基本案情"],
        bad_key: secret,
    }
    with pytest.raises(ContractViolationError) as exc:
        sanitize_draft_descriptor(payload)
    # 异常消息只暴露键名 / reason，绝不回显原始值。
    assert secret not in str(exc.value)


def test_assert_no_draft_body_top_level():
    with pytest.raises(ContractViolationError):
        assert_no_draft_body({"draft_id": "D", "draft_body": "起草正文不应出现"})


def test_assert_no_draft_body_nested_refs():
    """嵌套引用内夹带裁判正文也被 fail-closed 捕获。"""
    with pytest.raises(ContractViolationError):
        assert_no_draft_body(
            {
                "draft_id": "D",
                "candidate_refs": [{"case_id": "C", "chunk_text": "裁判正文泄露"}],
            }
        )


def test_helper_predicates():
    assert is_forbidden_draft_body_key("DRAFT_BODY")
    assert is_forbidden_outcome_key("Win_Probability")
    assert is_draft_rejected_key("chunk_text")
    assert is_draft_rejected_key("id_card")
    assert not is_draft_rejected_key("draft_id")
    assert not is_draft_rejected_key("structure_skeleton")


# --- 3. structure_skeleton 标题校验 ----------------------------------------------


def test_skeleton_item_too_long_rejected_with_reason_code():
    long_item = "正" * (STRUCTURE_SKELETON_ITEM_MAX_LEN + 1)
    with pytest.raises(ContractViolationError) as exc:
        sanitize_draft_descriptor(
            {"draft_id": "D", "structure_skeleton": [long_item]}
        )
    # 记 reason code（不回显正文）。
    assert "TOO_LONG" in str(exc.value)
    assert long_item not in str(exc.value)


def test_skeleton_empty_rejected():
    with pytest.raises(ContractViolationError) as exc:
        sanitize_draft_descriptor({"draft_id": "D", "structure_skeleton": []})
    assert "EMPTY" in str(exc.value)


def test_skeleton_non_title_item_rejected():
    with pytest.raises(ContractViolationError) as exc:
        sanitize_draft_descriptor(
            {"draft_id": "D", "structure_skeleton": ["一、基本案情", "   "]}
        )
    assert "NOT_TITLE" in str(exc.value)


def test_skeleton_titles_accepted():
    d = sanitize_draft_descriptor(
        {
            "draft_id": "D-1",
            "structure_skeleton": ["一、基本案情", "二、争议焦点", "三、参考类案"],
        }
    )
    assert d.structure_skeleton == ["一、基本案情", "二、争议焦点", "三、参考类案"]


# --- 4. 引用必带锚点：缺锚点 fail-closed 丢弃，保留项 100% 有锚点 -----------------


def test_candidate_refs_without_anchor_dropped():
    d = sanitize_draft_descriptor(
        {
            "draft_id": "D-1",
            "structure_skeleton": ["一、参考类案"],
            "candidate_refs": [
                _anchored_candidate("C-1"),
                {"case_id": "C-2", "source_anchors": []},  # 无锚点 → 丢弃
                {"case_id": "C-3"},  # 缺 source_anchors → 丢弃
            ],
        }
    )
    assert len(d.candidate_refs) == 1
    assert d.candidate_refs[0].case_id == "C-1"
    # 保留项 100% 有非空锚点。
    for ref in d.candidate_refs:
        assert ref.source_anchors and all(
            a.get("case_id") and a.get("source_chunk_id") for a in ref.source_anchors
        )


def test_statute_refs_without_anchor_dropped():
    d = sanitize_draft_descriptor(
        {
            "draft_id": "D-1",
            "structure_skeleton": ["二、法律依据"],
            "statute_refs": [
                _anchored_statute("S-1"),
                {"statute_id": "S-2", "law_name": "刑法", "statute_anchors": []},  # 丢弃
            ],
        }
    )
    assert len(d.statute_refs) == 1
    assert d.statute_refs[0].statute_id == "S-1"
    for ref in d.statute_refs:
        assert ref.statute_anchors and all(a.text_id for a in ref.statute_anchors)


def test_candidate_ref_incomplete_anchor_dropped():
    """锚点缺 source_chunk_id → 整条丢弃（不进交付物）。"""
    d = sanitize_draft_descriptor(
        {
            "draft_id": "D",
            "structure_skeleton": ["一"],
            "candidate_refs": [
                {"case_id": "C-1", "source_anchors": [{"case_id": "C-1"}]},
            ],
        }
    )
    assert d.candidate_refs == []


# --- 5. sanitize 输出只含白名单字段、零正文 --------------------------------------


def test_sanitize_drops_non_whitelist_keys_and_subkeys():
    d = sanitize_draft_descriptor(
        {
            "draft_id": "D-1",
            "structure_skeleton": ["一、基本案情"],
            "candidate_refs": [
                {
                    **_anchored_candidate("C-1"),
                    "internal_score": 0.99,  # 非白名单 → 丢弃
                    "rank": 3,
                }
            ],
            "note": "短备注",
            "tag": "盗窃",
            "unknown_top_level": "drop_me",
            "visibility": "private",
        }
    )
    dump = d.model_dump(exclude_none=True)
    assert "unknown_top_level" not in dump
    cand = d.candidate_refs[0].model_dump(exclude_none=True)
    assert "internal_score" not in cand and "rank" not in cand
    assert set(cand) <= set(CANDIDATE_REF_FIELDS)
    # 全 dump 内无任何禁止键。
    forbidden = (
        DRAFT_FORBIDDEN_BODY_KEYS
        | DRAFT_FORBIDDEN_JUDGMENT_KEYS
        | DRAFT_FORBIDDEN_OUTCOME_KEYS
    )

    def _keys(obj):
        out = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.add(k)
                out |= _keys(v)
        elif isinstance(obj, list):
            for it in obj:
                out |= _keys(it)
        return out

    assert not (_keys(dump) & forbidden)


def test_sanitize_is_pure_no_input_mutation():
    payload = {
        "draft_id": "D-1",
        "structure_skeleton": ["一"],
        "candidate_refs": [_anchored_candidate("C-1")],
    }
    import copy

    snapshot = copy.deepcopy(payload)
    sanitize_draft_descriptor(payload)
    assert payload == snapshot


def test_visibility_defaults_private():
    d = sanitize_draft_descriptor({"draft_id": "D", "structure_skeleton": ["一"]})
    assert d.visibility == "private"


# --- 6. 公开面导出可达 + 身份保持 -------------------------------------------------


def test_public_face_identity_preserved():
    from app.kernel.guardrails import (
        DraftDescriptor as G_DraftDescriptor,
        sanitize_draft_descriptor as g_sanitize,
        assert_no_draft_body as g_assert,
        DRAFT_DESCRIPTOR_E6_FIELDS as g_fields,
    )
    from app.contracts import (
        DraftDescriptor as C_DraftDescriptor,
        sanitize_draft_descriptor as c_sanitize,
    )

    assert G_DraftDescriptor is DraftDescriptor is C_DraftDescriptor
    assert g_sanitize is sanitize_draft_descriptor is c_sanitize
    assert g_assert is assert_no_draft_body
    assert g_fields is DRAFT_DESCRIPTOR_E6_FIELDS


# --- 7. E6-1 不越界：静态 AST 断言 + router 计数 + flag 默认 false ----------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def test_drafting_contract_does_not_import_runtime_or_products():
    """drafting_contract 不 import 检索/rerank/retrieval/summary/内核 rag 服务/任何产品包。"""
    mods = _imported_modules(DRAFTING_CONTRACT_PATH)
    forbidden_substrings = (
        "retrieval",
        "rerank",
        "summary",
        "kernel.rag",
        "internal_search",
        "statute_search_service",
        # 产品包
        "app.intake",
        "app.statute",
        "app.drafting",
        "app.casebook",
    )
    for mod in mods:
        for bad in forbidden_substrings:
            assert bad not in mod, f"drafting_contract 不应 import {mod!r}（命中 {bad!r}）"
    # 只允许同包相对 import + typing/pydantic。
    for mod in mods:
        assert mod.startswith(".") is False  # ImportFrom relative 记为 module=None 已过滤
    # 仅依赖同包 whitelist / intake_contract / statute_contract（相对导入）。


def test_drafting_contract_uses_only_relative_sibling_imports():
    """确认 drafting_contract 只相对 import 同包契约模块 + 标准库/pydantic。"""
    tree = ast.parse(DRAFTING_CONTRACT_PATH.read_text(encoding="utf-8"))
    relative_targets: set[str] = set()
    absolute_mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                relative_targets.add(node.module or "")
            elif node.module:
                absolute_mods.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                absolute_mods.add(alias.name)
    # 相对导入只指向同包契约纯函数模块。
    assert relative_targets <= {"whitelist", "intake_contract", "statute_contract"}
    # 绝对导入只允许标准库 + pydantic（无 app.* 业务/运行时）。
    for mod in absolute_mods:
        assert not mod.startswith("app."), f"不应绝对 import 业务模块 {mod!r}"


def test_include_router_is_16_after_e7_2():
    """E7-2 接线后 include_router=16（新增 casebook_router）。"""
    text = MAIN_PATH.read_text(encoding="utf-8")
    count = sum(
        1
        for line in text.splitlines()
        if "app.include_router" in line and not line.strip().startswith("#")
    )
    assert count == 16, f"include_router 期望 16，实得 {count}（E7-2 新增 casebook）"


def test_drafting_and_casebook_packages_exist_after_e7_2():
    """E6-2 建 drafting 产品包；E7-2 建 casebook 产品包。"""
    assert (API_ROOT / "app" / "drafting").exists()
    assert (API_ROOT / "app" / "casebook").exists()


def test_enable_drafting_default_false():
    """ENABLE_DRAFTING / ENABLE_CASEBOOK 默认 false（不接线、不默认开启）。"""
    config_text = (API_ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert "ENABLE_DRAFTING: bool = False" in config_text
    assert "ENABLE_CASEBOOK: bool = False" in config_text


