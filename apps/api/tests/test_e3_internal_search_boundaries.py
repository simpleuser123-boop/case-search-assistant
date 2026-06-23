"""E3-4 消费边界与护栏守门（静态 AST 扫描 + 运行时契约断言，零搜索行为改动）。

把 E3（文档 18 第3节共用控制提示词 + 第1节总目标）的架构纪律固化成可执行测试，
确保后续 E4 案情录入端、E5 法条检索、E6 文书工作台、E7 案件协作台只能经
内部检索服务（InternalSearchService / 公开服务面）消费检索能力，不得：
- 绕开内部服务直连 retrieval / rerank / summary / 现有 /api/search；
- 泄露候选正文 / chunk 正文 / 裁判文书全文 / summary / highlight / matched_text；
- E7-2 之后仅允许 intake / statute / drafting / casebook 四个 E 系列产品包，
  不得新增其它产品包或 E3 对外端点。

守门覆盖（与提示词「需要覆盖的守门」1~8 逐条对应）：
1. E3 内部服务公开面存在。
2. E3 服务模块不得 import 产品包。
3. E7-2 后仅允许存在 intake / statute / drafting / casebook 四个 E 系列产品包。
4. api/ 下不得新增绕过公开面的内核深引；/api/search 必须经内部服务执行。
5. 不得新增 /api/internal/search、/api/ecosystem/search 等 E3 对外端点。
6. CandidateRef 输出字段严格等于 E-1 白名单子集。
7. CandidateRef 必须有 source_anchors；锚点只含元数据。
8. 测试 fixture、docs/development e3 文档不得写入长正文。

红线：本文件 fixture 只用短假数据 / hash / case_id / source_chunk_id / 元数据。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DEV_DIR = REPO_ROOT / "docs" / "development"
TESTS_DIR = Path(__file__).resolve().parent

SERVICE_MODULE = APP_DIR / "kernel" / "rag" / "internal_search_service.py"
CONTRACT_MODULE = APP_DIR / "kernel" / "rag" / "internal_search_contracts.py"
SEARCH_API = APP_DIR / "api" / "search.py"

PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")
ALLOWED_PRODUCT_PACKAGES_AFTER_E4_3 = {"intake"}
# E5-4：法条检索端 statute 是 E 系列第二个合法产品包（gated，默认 false 降级）。
ALLOWED_PRODUCT_PACKAGES_AFTER_E5_4 = {"intake", "statute"}
# E7-2：casebook 是 E 系列第四个合法产品包（gated，默认 false 降级）。
ALLOWED_PRODUCT_PACKAGES_AFTER_E7_2 = {"intake", "statute", "drafting", "casebook"}

KERNEL_TOP_PACKAGES = (
    "retrieval", "rerank", "query_processing", "summary",
    "account", "team", "permission", "sharing",
    "contracts", "pipeline", "case_store",
)

EXPECTED_CANDIDATE_REF_FIELDS = {
    "case_id", "case_number", "court", "trial_level",
    "case_cause", "judgment_date", "source_anchors",
}

FORBIDDEN_CANDIDATE_FIELDS = (
    "summary", "summary_text", "highlights", "highlight", "highlight_text",
    "matched_text", "holding_summary", "metadata", "text", "content", "body",
    "chunk_text", "full_text", "raw_query", "raw_case",
)

FORBIDDEN_ENDPOINT_FRAGMENTS = (
    "/api/internal/search",
    "/api/internal",
    "/api/ecosystem/search",
    "/api/ecosystem",
)

# E3 既有 docs 最长连续中文 19、fixture 13，取 40 留足安全边界。
MAX_CONTIGUOUS_CJK = 40


def _iter_import_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _py_files(root):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


# --- 守门 1：E3 内部服务公开面存在且身份一致 -------------------------------
E3_PUBLIC_SYMBOLS = (
    "InternalSearchService",
    "InternalSearchExecutionResult",
    "InternalSearchRequest",
    "InternalSearchResult",
    "SearchProfile",
    "SearchProfileInput",
    "CandidateRef",
    "SourceAnchorRef",
    "InternalSearchMode",
    "sanitize_search_profile",
    "sanitize_candidate_ref",
    "search_result_item_to_candidate_ref",
)


def test_e3_public_face_importable_from_kernel_rag():
    import app.kernel.rag as rag

    missing = [
        name for name in E3_PUBLIC_SYMBOLS
        if not hasattr(rag, name) or name not in rag.__all__
    ]
    assert not missing, f"app.kernel.rag public face missing E3 symbols: {missing}"


def test_e3_public_face_importable_from_kernel_top():
    import app.kernel as kernel

    for name in ("InternalSearchService", "SearchProfile", "CandidateRef"):
        assert hasattr(kernel, name) and name in kernel.__all__, (
            f"app.kernel top public face missing {name}"
        )


def test_e3_public_face_identity_preserved():
    import app.kernel as kernel
    import app.kernel.rag as rag
    from app.kernel.rag.internal_search_service import InternalSearchService as Svc
    from app.kernel.rag.internal_search_contracts import (
        CandidateRef as CR,
        SearchProfile as SP,
    )

    assert rag.InternalSearchService is Svc is kernel.InternalSearchService
    assert rag.CandidateRef is CR is kernel.CandidateRef
    assert rag.SearchProfile is SP is kernel.SearchProfile


# --- 守门 2：E3 服务/契约模块不得 import 任何产品包命名空间 ----------------
@pytest.mark.parametrize("module_path", [SERVICE_MODULE, CONTRACT_MODULE])
def test_e3_modules_do_not_import_product_packages(module_path):
    offending = []
    for module in _iter_import_modules(module_path):
        for product in PRODUCT_PACKAGES:
            if module == f"app.{product}" or module.startswith(f"app.{product}."):
                offending.append(f"{module_path.name}: {module}")
    assert not offending, (
        "E3 service/contract module imports product package: " + "; ".join(offending)
    )


# --- 守门 3：E7-2 后仅允许 E 系列四个产品包存在 -----------------------------
def test_only_expected_e_series_product_packages_exist_after_e7_2():
    existing = {p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()}
    unexpected = existing - ALLOWED_PRODUCT_PACKAGES_AFTER_E7_2
    assert not unexpected, (
        "only intake / statute / drafting / casebook product packages are allowed after E7-2, found unexpected: "
        + ", ".join(sorted(unexpected))
    )


# --- 守门 4：检索消费链路只经公开面；/api/search 经内部服务执行 ------------
def _deep_kernel_imports(path):
    deep = []
    for module in _iter_import_modules(path):
        if not module.startswith("app."):
            continue
        if module.startswith("app.kernel"):
            continue
        parts = module.split(".")
        if len(parts) >= 2 and parts[1] in KERNEL_TOP_PACKAGES:
            deep.append(module)
    return deep


def test_search_api_consumes_kernel_via_surface_only():
    deep = _deep_kernel_imports(SEARCH_API)
    assert not deep, (
        "api/search.py bypasses app.kernel public face (deep import): " + "; ".join(deep)
    )


def test_search_api_executes_via_internal_search_service():
    source = SEARCH_API.read_text(encoding="utf-8")
    assert "InternalSearchService" in source, (
        "/api/search must reference InternalSearchService"
    )
    assert ".execute(" in source, "/api/search must call internal service execute()"
    for primitive in ("merge_case_candidates(", "split_low_confidence_candidates("):
        assert primitive not in source, (
            f"/api/search still inlines retrieval primitive {primitive}"
        )


def test_other_api_consumers_must_use_internal_service_for_search():
    search_primitives = (
        "VectorRetrievalService",
        "FactSimilarityReranker",
        "merge_case_candidates",
        "split_low_confidence_candidates",
    )
    offenders = []
    api_dir = APP_DIR / "api"
    for path in _py_files(api_dir):
        if path.name == "search.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names.extend(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported_names.extend(a.name for a in node.names)
        leaked = [n for n in imported_names if n in search_primitives]
        if leaked:
            offenders.append(f"{path.name}: {', '.join(leaked)}")
    assert not offenders, (
        "api consumer bypasses InternalSearchService: " + "; ".join(offenders)
    )


# --- 守门 5：不得新增 E3 对外检索端点 -------------------------------------
def test_no_forbidden_search_endpoints_registered():
    offenders = []
    api_dir = APP_DIR / "api"
    for path in _py_files(api_dir):
        source = path.read_text(encoding="utf-8")
        for fragment in FORBIDDEN_ENDPOINT_FRAGMENTS:
            if fragment in source:
                offenders.append(f"{path.name}: {fragment}")
    assert not offenders, (
        "forbidden E3 external search endpoint fragment: " + "; ".join(offenders)
    )


def test_internal_service_module_registers_no_http_endpoint():
    for module_path in (SERVICE_MODULE, CONTRACT_MODULE):
        source = module_path.read_text(encoding="utf-8")
        for token in ("APIRouter", "FastAPI", "@router", "include_router", "add_api_route"):
            assert token not in source, f"{module_path.name} must not wire HTTP: {token}"


def test_include_router_count_matches_e7_2_baseline():
    main_py = APP_DIR / "main.py"
    source = main_py.read_text(encoding="utf-8")
    count = source.count("app.include_router(")
    assert count == 16, f"include_router count must stay 16 (E7-2 baseline), got {count}"
    assert "app.include_router(intake_router)" in source
    assert "app.include_router(statute_router)" in source
    assert "app.include_router(drafting_router)" in source
    assert "app.include_router(casebook_router)" in source


# --- 守门 6：CandidateRef 输出字段严格等于 E-1 白名单子集 ------------------
def test_candidate_ref_fields_strictly_equal_e1_whitelist():
    from app.kernel.guardrails.contracts import CANDIDATE_REF_FIELDS
    from app.kernel.rag import CandidateRef

    fields = set(CandidateRef.model_fields)
    assert fields == EXPECTED_CANDIDATE_REF_FIELDS, (
        f"CandidateRef fields deviate from E-1 whitelist: {fields ^ EXPECTED_CANDIDATE_REF_FIELDS}"
    )
    assert fields == set(CANDIDATE_REF_FIELDS)


def test_candidate_ref_has_no_forbidden_fields():
    from app.kernel.rag import CandidateRef

    leaked = set(CandidateRef.model_fields) & set(FORBIDDEN_CANDIDATE_FIELDS)
    assert not leaked, f"CandidateRef leaks body/display field: {leaked}"


def test_candidate_ref_forbids_extra_keys_at_model_layer():
    from app.kernel.rag import CandidateRef

    good_anchor = {"case_id": "C-1", "source_chunk_id": "ch-1"}
    for bad_key in ("summary", "highlights", "matched_text", "content", "body", "text", "metadata"):
        with pytest.raises(Exception):
            CandidateRef(
                case_id="C-1",
                source_anchors=[good_anchor],
                **{bad_key: "x"},
            )


def test_candidate_ref_output_dump_only_whitelist_keys():
    from app.kernel.rag import sanitize_candidate_ref

    cr = sanitize_candidate_ref(
        {
            "case_id": "C-1",
            "case_number": "no-1",
            "court": "court-x",
            "trial_level": "second",
            "case_cause": "cause-x",
            "judgment_date": "2020-06-01",
            "source_anchors": [{"case_id": "C-1", "source_chunk_id": "ch-1", "anchor_type": "result"}],
            "metadata": {"k": "v"},
            "final_score": 0.9,
        }
    )
    dumped = cr.model_dump()
    assert set(dumped) == EXPECTED_CANDIDATE_REF_FIELDS
    for forbidden in FORBIDDEN_CANDIDATE_FIELDS:
        assert forbidden not in dumped, f"CandidateRef output leaks {forbidden}"


# --- 守门 7：CandidateRef 必须有 source_anchors；锚点只含元数据 ------------
def test_candidate_ref_requires_non_empty_source_anchors():
    from app.kernel.guardrails.contracts import ContractViolationError
    from app.kernel.rag import sanitize_candidate_ref

    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref({"case_id": "C-1"})
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref({"case_id": "C-1", "source_anchors": []})


def test_source_anchor_fields_are_metadata_only():
    from app.kernel.rag import SourceAnchorRef

    fields = set(SourceAnchorRef.model_fields)
    assert fields == {"case_id", "source_chunk_id", "anchor_type"}, (
        f"SourceAnchorRef fields deviate: {fields}"
    )
    leaked = fields & set(FORBIDDEN_CANDIDATE_FIELDS)
    assert not leaked, f"SourceAnchorRef has body field: {leaked}"


def test_source_anchor_forbids_extra_keys():
    from app.kernel.rag import SourceAnchorRef

    for bad_key in ("chunk_text", "matched_text", "content", "text", "body", "summary"):
        with pytest.raises(Exception):
            SourceAnchorRef(case_id="C-1", source_chunk_id="ch-1", **{bad_key: "x"})


def test_anchor_incomplete_candidate_rejected():
    from app.kernel.guardrails.contracts import ContractViolationError
    from app.kernel.rag import sanitize_candidate_ref

    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref(
            {"case_id": "C-1", "source_anchors": [{"source_chunk_id": "ch-1"}]}
        )
    with pytest.raises(ContractViolationError):
        sanitize_candidate_ref(
            {"case_id": "C-1", "source_anchors": [{"case_id": "C-1"}]}
        )


# --- 守门 8：fixture / docs 不得写入长正文 --------------------------------
def _max_contiguous_cjk(text):
    best_len = 0
    cur = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            cur += 1
            if cur > best_len:
                best_len = cur
        else:
            cur = 0
    return best_len


def test_e3_test_fixtures_have_no_long_body_text():
    offenders = []
    for path in sorted(TESTS_DIR.glob("test_e3_*.py")):
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E3 fixture suspected long body text: " + "; ".join(offenders)


def test_e3_docs_have_no_long_body_text():
    if not DOCS_DEV_DIR.exists():
        pytest.skip("docs/development absent")
    offenders = []
    targets = sorted(DOCS_DEV_DIR.glob("e3-*.md")) + sorted(DOCS_DEV_DIR.glob("e3-*.json"))
    for path in targets:
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E3 docs suspected long body text: " + "; ".join(offenders)
