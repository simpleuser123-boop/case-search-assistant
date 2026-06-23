"""E3-1 内部检索服务契约冻结 focused 单元测试。

验证（对应文档 18 §5 验收标准）：
- SearchProfile / CandidateRef 字段集与 E-1 白名单逐字段一致。
- sanitize 丢弃非白名单键、拒绝正文型键（fail-closed）。
- CandidateRef 不含 summary/highlights/matched_text/chunk_text/full_text/raw_*/content/body。
- source_anchors 为空或不完整（缺 case_id / source_chunk_id）的候选不能通过。
- search_result_item_to_candidate_ref 只取白名单字段 + 锚点，case_no -> case_number 映射。
- 新增契约从 app.kernel.rag 与 app.kernel 公开面可导入，且与底层为同一对象（身份保持）。
- 契约模块不 import 检索/rerank/retrieval/summary/query_processing 运行时；不接线任何 HTTP 端点。

红线：fixture 只用短假数据 / hash / case_id / source_chunk_id / 元数据，绝不写真实长案情或裁判正文。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.kernel.guardrails.contracts import (
    CANDIDATE_REF_FIELDS,
    SEARCH_PROFILE_FIELDS,
    ContractViolationError,
)
from app.kernel.rag.internal_search_contracts import (
    CandidateRef,
    InternalSearchRequest,
    InternalSearchResult,
    SearchProfile,
    SearchProfileInput,
    SourceAnchorRef,
    sanitize_candidate_ref,
    sanitize_search_profile,
    search_result_item_to_candidate_ref,
)

APP_DIR = Path(__file__).resolve().parents[1] / "app"
CONTRACT_MODULE = APP_DIR / "kernel" / "rag" / "internal_search_contracts.py"

# E-1 冻结字段集（与 test_e1_contracts.py 同口径，作为本步比对基线）。
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

# CandidateRef 绝不允许出现的正文型字段。
FORBIDDEN_ON_CANDIDATE_REF = (
    "summary",
    "highlights",
    "matched_text",
    "chunk_text",
    "full_text",
    "raw_query",
    "raw_case",
    "content",
    "body",
    "metadata",
)


# --- 字段集与 E-1 白名单一致 ---------------------------------------------------

def test_search_profile_fields_match_e1_whitelist():
    assert set(SearchProfile.model_fields) == EXPECTED_SEARCH_PROFILE
    assert set(SearchProfile.model_fields) == set(SEARCH_PROFILE_FIELDS)


def test_candidate_ref_fields_match_e1_whitelist():
    assert set(CandidateRef.model_fields) == EXPECTED_CANDIDATE_REF
    assert set(CandidateRef.model_fields) == set(CANDIDATE_REF_FIELDS)


def test_search_profile_input_alias_is_same_model():
    assert SearchProfileInput is SearchProfile


def test_candidate_ref_has_no_body_type_field():
    fields = set(CandidateRef.model_fields)
    leaked = fields & set(FORBIDDEN_ON_CANDIDATE_REF)
    assert not leaked, f"CandidateRef 泄露正文型字段: {leaked}"


# --- sanitize_search_profile ---------------------------------------------------

def test_sanitize_search_profile_drops_non_whitelist_keys():
    sp = sanitize_search_profile(
        {
            "case_cause": "买卖合同纠纷",
            "region": "上海",
            "query_text": "脱敏查询",
            "extra_meta": "drop_me",
            "internal_score": 0.9,
        }
    )
    dumped = sp.model_dump(exclude_none=True)
    assert "extra_meta" not in dumped
    assert "internal_score" not in dumped
    assert dumped["case_cause"] == "买卖合同纠纷"


@pytest.mark.parametrize("bad_key", ["raw_case", "raw_query", "full_text", "content"])
def test_sanitize_search_profile_rejects_body_keys(bad_key):
    with pytest.raises(ContractViolationError):
        sanitize_search_profile({"case_cause": "x", bad_key: "原始口语化案情……"})


def test_search_profile_forbids_extra_at_model_layer():
    with pytest.raises(Exception):
        SearchProfile(case_cause="x", not_a_field="y")


# --- sanitize_candidate_ref ----------------------------------------------------

def _good_anchor(case_id="C-1", chunk="ch-1", atype="holding"):
    return {"case_id": case_id, "source_chunk_id": chunk, "anchor_type": atype}


def test_sanitize_candidate_ref_happy_path():
    cr = sanitize_candidate_ref(
        {
            "case_id": "C-1",
            "case_number": "(2020)沪01民终1号",
            "court": "X院",
            "trial_level": "二审",
            "case_cause": "买卖合同纠纷",
            "judgment_date": "2020-06-01",
            "source_anchors": [_good_anchor()],
            "drop_me": "x",
        }
    )
    dumped = cr.model_dump(exclude_none=True)
    assert dumped["case_id"] == "C-1"
    assert dumped["case_number"] == "(2020)沪01民终1号"
    assert "drop_me" not in dumped
    assert len(dumped["source_anchors"]) == 1
    assert dumped["source_anchors"][0]["case_id"] == "C-1"
    assert dumped["source_anchors"][0]["source_chunk_id"] == "ch-1"


def test_sanitize_candidate_ref_rejects_empty_anchors():
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref({"case_id": "C-1", "source_anchors": []})


def test_sanitize_candidate_ref_rejects_missing_anchors():
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref({"case_id": "C-1"})


def test_sanitize_candidate_ref_rejects_anchor_missing_case_id():
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref(
            {"case_id": "C-1", "source_anchors": [{"source_chunk_id": "ch-1"}]}
        )


def test_sanitize_candidate_ref_rejects_anchor_missing_chunk_id():
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref(
            {"case_id": "C-1", "source_anchors": [{"case_id": "C-1"}]}
        )


def test_sanitize_candidate_ref_rejects_anchor_blank_fields():
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref(
            {"case_id": "C-1", "source_anchors": [{"case_id": "", "source_chunk_id": "ch"}]}
        )


@pytest.mark.parametrize(
    "bad_key",
    ["summary", "highlights", "matched_text", "chunk_text", "full_text", "content", "body", "raw_case"],
)
def test_sanitize_candidate_ref_rejects_body_keys(bad_key):
    payload = {"case_id": "C-1", "source_anchors": [_good_anchor()], bad_key: "正文/裁判文书……"}
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref(payload)


def test_sanitize_candidate_ref_drops_non_body_extra_keys():
    # 非白名单且非正文型键（如 metadata/score）被静默丢弃，不报错也不出现在输出。
    cr = sanitize_candidate_ref(
        {"case_id": "C-1", "source_anchors": [_good_anchor()], "metadata": {"k": "v"}, "final_score": 0.7}
    )
    dumped = cr.model_dump()
    for k in FORBIDDEN_ON_CANDIDATE_REF:
        assert k not in dumped


def test_sanitize_is_pure_does_not_mutate_input():
    payload = {"case_id": "C-1", "source_anchors": [_good_anchor()], "drop": "x"}
    snapshot_keys = set(payload)
    sanitize_candidate_ref(payload)
    assert set(payload) == snapshot_keys


# --- search_result_item_to_candidate_ref --------------------------------------

class _FakeAnchor:
    def __init__(self, case_id="C-9", chunk="ck-9", atype="reason"):
        self.case_id = case_id
        self.source_chunk_id = chunk
        self.anchor_type = atype


class _FakeItem:
    """模拟 schemas.SearchResultItem 的最小属性集（含正文型字段以验证不被搬运）。"""

    case_id = "C-9"
    case_no = "(2021)京02刑终9号"
    court = "京院"
    trial_level = "二审"
    case_cause = "盗窃"
    judgment_date = "2021-05-01"
    source_anchors = [_FakeAnchor()]
    # 正文型字段：必须被丢弃。
    summary = {"holding": "正文摘要不应出现"}
    highlights = [{"text": "高亮正文不应出现"}]
    matched_text = "命中正文不应出现"
    metadata = {"k": "v"}


def test_item_to_candidate_ref_maps_case_no_to_case_number():
    cr = search_result_item_to_candidate_ref(_FakeItem())
    dumped = cr.model_dump(exclude_none=True)
    assert dumped["case_number"] == "(2021)京02刑终9号"
    assert "case_no" not in dumped


def test_item_to_candidate_ref_strips_body_fields():
    cr = search_result_item_to_candidate_ref(_FakeItem())
    dumped = cr.model_dump()
    for k in FORBIDDEN_ON_CANDIDATE_REF:
        assert k not in dumped, f"item->CandidateRef 泄露 {k}"


def test_item_to_candidate_ref_carries_source_anchors():
    cr = search_result_item_to_candidate_ref(_FakeItem())
    assert len(cr.source_anchors) == 1
    assert cr.source_anchors[0].case_id == "C-9"
    assert cr.source_anchors[0].source_chunk_id == "ck-9"


def test_item_to_candidate_ref_fail_closed_without_anchors():
    class _NoAnchorItem:
        case_id = "C-3"
        case_no = "(2022)粤03民终3号"
        source_anchors = []

    with pytest.raises(ContractViolationError):
        search_result_item_to_candidate_ref(_NoAnchorItem())


def test_item_to_candidate_ref_accepts_mapping_input():
    item = {
        "case_id": "C-7",
        "case_no": "(2023)苏05民终7号",
        "court": "苏院",
        "source_anchors": [{"case_id": "C-7", "source_chunk_id": "ck-7"}],
        "summary": {"x": "正文"},
    }
    cr = search_result_item_to_candidate_ref(item)
    dumped = cr.model_dump(exclude_none=True)
    assert dumped["case_number"] == "(2023)苏05民终7号"
    assert "summary" not in cr.model_dump()


# --- InternalSearchRequest / InternalSearchResult -----------------------------

def test_internal_search_request_rejects_raw_body():
    with pytest.raises(Exception):
        InternalSearchRequest(profile=SearchProfile(query_text="q"), raw_case="原始案情")


def test_internal_search_request_defaults():
    req = InternalSearchRequest(profile=SearchProfile(query_text="脱敏查询"))
    assert req.mode == "standard"
    assert req.limit == 10
    assert req.include_relaxed_recall is False


def test_internal_search_result_holds_refs_only():
    cr = sanitize_candidate_ref({"case_id": "C-1", "source_anchors": [_good_anchor()]})
    res = InternalSearchResult(candidate_refs=[cr], degraded=True, degraded_reasons=["x"])
    assert res.candidate_refs[0].case_id == "C-1"
    assert res.degraded is True
    # result 模型不允许塞正文型 extra 字段。
    with pytest.raises(Exception):
        InternalSearchResult(candidate_refs=[cr], full_text="正文")


# --- 公开面导出 + 身份保持 -----------------------------------------------------

def test_symbols_importable_from_kernel_rag_surface():
    import app.kernel.rag as r

    for name in (
        "SearchProfile",
        "CandidateRef",
        "InternalSearchRequest",
        "InternalSearchResult",
        "InternalSearchMode",
        "SourceAnchorRef",
        "sanitize_search_profile",
        "sanitize_candidate_ref",
        "search_result_item_to_candidate_ref",
    ):
        assert hasattr(r, name) and name in r.__all__, f"app.kernel.rag 缺导出 {name}"


def test_symbols_importable_from_kernel_surface():
    import app.kernel as k

    for name in ("SearchProfile", "CandidateRef", "InternalSearchRequest", "InternalSearchResult"):
        assert hasattr(k, name) and name in k.__all__, f"app.kernel 缺导出 {name}"


def test_export_identity_preserved():
    import app.kernel as k
    import app.kernel.rag as r
    from app.kernel.rag.internal_search_contracts import CandidateRef as CR

    assert CR is r.CandidateRef is k.CandidateRef


# --- 不接线 / 不深引运行时（AST 静态扫描，不 import 业务运行时）------------------

def test_contract_module_does_not_import_search_runtime():
    forbidden = ("retrieval", "rerank", "summary", "query_processing", "case_store", "pipeline")
    tree = ast.parse(CONTRACT_MODULE.read_text(encoding="utf-8"), filename=str(CONTRACT_MODULE))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    for mod in imported:
        for f in forbidden:
            assert f"app.kernel.rag.{f}" not in mod and not mod.endswith(f".{f}"), (
                f"契约模块不应 import 检索运行时: {mod}"
            )


def test_contract_module_registers_no_http_endpoint():
    text = CONTRACT_MODULE.read_text(encoding="utf-8")
    for forbidden in ("APIRouter", "FastAPI", "@router", "include_router", "add_api_route"):
        assert forbidden not in text, f"契约模块不得接线 HTTP 端点: {forbidden}"
