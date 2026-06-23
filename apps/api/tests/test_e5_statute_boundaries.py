"""E5-6 法条检索 statute 消费边界与护栏守门（静态 AST 扫描 + 运行时契约断言，零行为改动）。

把文档 20 §1~§3 + §9（E5-6）的架构纪律与法条红线固化成可执行测试，确保后续
E6 文书工作台 / E7 案件协作台不绕开内核法条检索服务、不泄露裁判正文、不展示无锚点 /
模型杜撰条文。对标 E4-5 守门范式（纯 AST + pydantic 模型层断言，不触发检索 / DB / 网络）。

守门覆盖（与提示词「需要覆盖的守门」1~11 逐条对应）：
1. statute 产品包公开面/端点存在且 gated：ENABLE_STATUTE_SEARCH=false（默认）时端点 403 降级。
2. statute 不 import intake/drafting/casebook，也不被它们 import（互不 import；见 test_e2a 追加）。
3. statute 不深引 retrieval/rerank/summary/query_processing，必经 app.kernel.rag
   StatuteSearchService 消费检索（见 test_e2a 追加 + 本文件源码扫描）。
4. E7-2 后仅 intake + statute + drafting + casebook 四个 E 系列产品包存在。
5. statute 三端点请求体 schema 严格白名单 + extra=forbid，拒裁判正文 / PII / 原始案情型键。
6. StatuteRef 输出严格 = E5-1 白名单字段、100% statute_anchors(text_id 非空)、无裁判正文型键；
   article_text 只来自语料、不由模型生成（拒绝 generated_article/llm_text/... 型键）。
7. 互跳只走契约对象：法条→类案出 CandidateRef（白名单七字段、100% source_anchors、0 正文）；
   类案→法条出 StatuteRef（带锚点）；两侧都不携带对侧正文。
8. statute 后端代码/模型/日志不出现裁判正文型字段、原始案情/PII；statute 不持久化查询/结果。
9. include_router 数 = 16（intake + statute + drafting + casebook）；无 /api/internal、/api/ecosystem 等越界端点。
10. 法条索引与案件索引物理隔离（不同 collection + 不同 persist 目录）；E5 未重建/未改案件产物。
11. 测试 fixture、docs/development/e5-*.json/md 不写真实长案情、裁判正文、真实 PII；
    法条 fixture 不嵌真实长正文。

红线：本文件 fixture 只用短假数据 / hash / text_id / case_id / source_chunk_id / 元数据。
纯 AST 静态扫描 + pydantic 模型层运行时断言（不触发检索 / DB 写 / 网络 / 模型副作用）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DEV_DIR = REPO_ROOT / "docs" / "development"
TESTS_DIR = Path(__file__).resolve().parent
STATUTE_DIR = APP_DIR / "statute"
MAIN_PY = APP_DIR / "main.py"
CONFIG_PY = APP_DIR / "core" / "config.py"
STATUTE_SERVICE_MODULE = APP_DIR / "kernel" / "rag" / "statute_search_service.py"
STATUTE_INDEX_PIPELINE = APP_DIR / "kernel" / "data" / "pipeline" / "build_statute_index.py"

PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")
# E7-2 后 E 系列合法产品包集合（intake + statute + drafting + casebook）。
ALLOWED_PRODUCT_PACKAGES = {"intake", "statute", "drafting", "casebook"}

# statute 只允许消费的内核顶层公开面（不得深引内部子模块）。
ALLOWED_KERNEL_SURFACES = ("app.kernel", "app.kernel.rag", "app.kernel.guardrails")

# 检索底层 / 内核内部子模块（深引即越界，必须经公开面 StatuteSearchService）。
FORBIDDEN_DEEP_PREFIXES = (
    "app.retrieval",
    "app.rerank",
    "app.summary",
    "app.query_processing",
    "app.kernel.rag.retrieval",
    "app.kernel.rag.rerank",
    "app.kernel.rag.summary",
    "app.kernel.rag.query_processing",
    "app.intake",
    "app.drafting",
    "app.casebook",
)

# 正文型数据键（绝不可作为 statute 数据字段 / dict 键 / 属性搬运）。
FORBIDDEN_BODY_TOKENS = (
    "raw_case",
    "raw_query",
    "raw_text",
    "full_text",
    "fulltext",
    "chunk_text",
    "chunk_content",
    "matched_text",
    "holding_summary",
    "judgment_full_text",
    "judgment_text",
    "summary_text",
    "highlight_text",
    "original_fact",
    "fact_text",
    "document_text",
    "paragraph_text",
    "case_body",
)

# 模型生成条文型键（与「条文必锚定语料、不得由模型生成」红线冲突，出现即越界）。
FORBIDDEN_GENERATED_TOKENS = (
    "generated_article",
    "generated_text",
    "generated_statute",
    "llm_text",
    "llm_article",
    "ai_article",
    "ai_generated_article",
    "model_generated_text",
    "paraphrased_article",
    "article_paraphrase",
    "rewritten_article",
    "synthesized_article",
    "drafted_article",
    "hallucinated_text",
)

# PII 型数据键（具体到不会与通用标识符冲突的键名）。
FORBIDDEN_PII_TOKENS = (
    "id_card",
    "id_card_no",
    "id_number",
    "identity_card",
    "passport_no",
    "phone_no",
    "phone_number",
    "mobile_no",
    "telephone",
    "email_address",
    "bank_card",
    "bank_account",
    "home_address",
    "residential_address",
    "plate_no",
    "license_plate",
    "party_name",
    "defendant_name",
    "plaintiff_name",
    "litigant_name",
    "real_name",
)

# StatuteRef 白名单字段（与 statute_contract STATUTE_REF_FIELDS 逐字段一致）。
WHITELIST_STATUTE_REF = {
    "statute_id",
    "law_name",
    "article_no",
    "statute_anchors",
    "article_text",
    "source_corpus",
    "effective_status",
    "related_case_refs",
}
# CandidateRef 白名单七字段（互跳法条→类案，与 E-1 一致）。
WHITELIST_CANDIDATE = {
    "case_id",
    "case_number",
    "court",
    "trial_level",
    "case_cause",
    "judgment_date",
    "source_anchors",
}

# 持久化 / 落库迹象（statute 无状态透传，不得出现在可执行代码）。
PERSISTENCE_TOKENS = (
    "Session(",
    "get_session",
    "session.add",
    ".commit(",
    "create_engine",
    "SQLModel",
    "insert(",
)

FORBIDDEN_ENDPOINT_FRAGMENTS = (
    "/api/internal/search",
    "/api/internal",
    "/api/ecosystem/search",
    "/api/ecosystem",
    "/api/drafting",
    "/api/casebook",
)

# 法条 / 案件索引物理隔离常量（与 build_statute_index.py 冻结口径一致）。
STATUTE_COLLECTION = "statute_chunks_bge_m3_v1"
CASE_COLLECTION = "case_chunks_bge_m3_v1"

# E3/E4 既有 docs/fixture 最长连续中文 ≤40 安全边界（与既有守门同口径）。
MAX_CONTIGUOUS_CJK = 40

# --- helpers（纯静态，不 import 业务运行时）-------------------------------------

def _py_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _statute_py_files():
    return sorted(p for p in STATUTE_DIR.glob("*.py"))


def _iter_import_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _code_without_docstrings(path: Path) -> str:
    """返回去掉模块/类/函数 docstring + 行内注释后的源码切片（保留可执行语句）。

    用 AST 定位所有 docstring 节点并按行剔除，再删除行内 # 注释，从而把
    『docstring/注释里把禁用键名当反例提及』与『可执行代码真的搬运禁用键』区分开。
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    doc_line_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            body0 = node.body[0]
            if isinstance(body0, ast.Expr) and isinstance(body0.value, ast.Constant):
                doc_line_ranges.append((body0.lineno, body0.end_lineno or body0.lineno))
    blocked = set()
    for start, end in doc_line_ranges:
        blocked.update(range(start, end + 1))
    kept: list[str] = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i in blocked:
            continue
        code = line.split("#", 1)[0]
        kept.append(code)
    return "\n".join(kept)


def _max_contiguous_cjk(text: str) -> int:
    best = cur = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


# ===========================================================================
# 守门 1：statute 公开面/端点存在且 gated（ENABLE_STATUTE_SEARCH 默认 false → 403 降级）。
# ===========================================================================
def test_statute_public_face_present():
    import app.statute as statute

    for name in (
        "router",
        "StatuteSearchRequest",
        "StatuteByCaseRequest",
        "StatuteCasesByStatuteRequest",
        "StatuteSearchResponse",
        "StatuteCasesResponse",
        "StatuteRefView",
        "StatuteCandidateRefView",
        "StatuteQueryService",
    ):
        assert hasattr(statute, name) and name in statute.__all__, (
            f"app.statute public face missing {name}"
        )


def test_enable_statute_search_defaults_false():
    from app.core.config import Settings

    assert Settings(_env_file=None).ENABLE_STATUTE_SEARCH is False


def test_enable_statute_search_declared_false_in_config():
    text = CONFIG_PY.read_text(encoding="utf-8")
    assert "ENABLE_STATUTE_SEARCH: bool = False" in text


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("/api/statute/search", {"query_text": "盗窃 自首"}),
        ("/api/statute/by-case", {"case_id": "c1"}),
        ("/api/statute/cases-by-statute", {"statute_id": "s264"}),
    ],
)
def test_statute_endpoints_gated_disabled_returns_403(endpoint, payload):
    """默认 flag off：三端点 403 安全降级、不检索、不回显查询，回到单产品末态。"""
    import importlib

    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import app

    statute_router_mod = importlib.import_module("app.statute.router")
    monkey_settings = Settings(DEEPSEEK_API_KEY="k", ENABLE_STATUTE_SEARCH=False)
    original = statute_router_mod.settings
    statute_router_mod.settings = monkey_settings
    statute_router_mod.set_statute_query_service_for_test(None)
    try:
        resp = TestClient(app).post(endpoint, json=payload)
    finally:
        statute_router_mod.settings = original
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "STATUTE_SEARCH_DISABLED"
    assert "statute_refs" not in resp.text
    assert "盗窃" not in resp.text


# ===========================================================================
# 守门 2/3/4：跨包 import 方向静态守门（与 test_e2a 追加互补，本文件聚焦 statute 源码）。
# ===========================================================================
def test_statute_does_not_import_other_product_packages():
    offending: list[str] = []
    for path in _statute_py_files():
        for module in _iter_import_modules(path):
            for other in ("intake", "drafting", "casebook"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "statute import 了其它产品包（产品包互不 import）：" + "; ".join(offending)
    )


def test_statute_consumes_only_kernel_public_surface():
    offending: list[str] = []
    for path in _statute_py_files():
        for module in _iter_import_modules(path):
            if module.startswith("app.kernel") and module not in ALLOWED_KERNEL_SURFACES:
                offending.append(f"{path.name}: {module}")
    assert not offending, (
        "statute 绕过 app.kernel 公开面深引内核内部（应只走 rag/guardrails 公开面）："
        + "; ".join(offending)
    )


def test_statute_does_not_deep_import_retrieval_runtime():
    offending: list[str] = []
    for path in _statute_py_files():
        for module in _iter_import_modules(path):
            for prefix in FORBIDDEN_DEEP_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "statute 直引检索运行时底层 / 其它产品包（应经 app.kernel.rag StatuteSearchService）："
        + "; ".join(offending)
    )


def test_statute_consumes_kernel_statute_search_service():
    """statute 服务层必须经内核公开面 StatuteSearchService 消费检索（单一权威路径）。"""
    service_src = (STATUTE_DIR / "service.py").read_text(encoding="utf-8")
    assert "StatuteSearchService" in service_src, (
        "statute service 未引用内核 StatuteSearchService（法条检索唯一允许的消费面）"
    )
    # 经公开面 app.kernel.rag 导入（而非深引子模块）。
    modules = list(_iter_import_modules(STATUTE_DIR / "service.py"))
    assert "app.kernel.rag" in modules, "statute service 应经 app.kernel.rag 公开面消费"


def test_only_expected_e_series_product_packages_exist_after_e7_2():
    existing = {p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()}
    unexpected = existing - ALLOWED_PRODUCT_PACKAGES
    assert not unexpected, (
        "只允许 intake + statute + drafting + casebook 产品包（E7-2 后），发现越界产品包："
        + ", ".join(sorted(unexpected))
    )


# ===========================================================================
# 守门 5：statute 三端点请求体 schema 严格白名单 + extra=forbid，拒正文/PII/原始案情型键。
# ===========================================================================
def test_statute_search_request_is_strict_whitelist():
    from app.statute.schemas import StatuteSearchRequest

    fields = set(StatuteSearchRequest.model_fields)
    assert fields == {
        "case_cause",
        "region",
        "trial_level_preference",
        "dispute_focus_keywords",
        "query_text",
        "mode",
        "limit",
    }
    assert StatuteSearchRequest.model_config.get("extra") == "forbid"


def test_statute_by_case_request_is_strict_whitelist():
    from app.statute.schemas import StatuteByCaseRequest, StatuteCasesByStatuteRequest

    assert set(StatuteByCaseRequest.model_fields) == {"case_id", "mode", "limit"}
    assert StatuteByCaseRequest.model_config.get("extra") == "forbid"
    assert set(StatuteCasesByStatuteRequest.model_fields) == {
        "statute_id",
        "mode",
        "limit",
    }
    assert StatuteCasesByStatuteRequest.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    "bad_key",
    list(FORBIDDEN_BODY_TOKENS)
    + list(FORBIDDEN_PII_TOKENS)
    + list(FORBIDDEN_GENERATED_TOKENS)
    + ["raw_case", "name", "id_card", "phone", "address", "email", "content"],
)
def test_statute_search_request_rejects_forbidden_keys(bad_key):
    from pydantic import ValidationError

    from app.statute.schemas import StatuteSearchRequest

    with pytest.raises(ValidationError):
        StatuteSearchRequest(query_text="盗窃", **{bad_key: "x"})


def test_statute_request_schemas_have_no_body_or_pii_fields():
    from app.statute.schemas import (
        StatuteByCaseRequest,
        StatuteCasesByStatuteRequest,
        StatuteSearchRequest,
    )

    forbidden = set(FORBIDDEN_BODY_TOKENS) | set(FORBIDDEN_PII_TOKENS) | set(
        FORBIDDEN_GENERATED_TOKENS
    )
    for model in (
        StatuteSearchRequest,
        StatuteByCaseRequest,
        StatuteCasesByStatuteRequest,
    ):
        leaked = set(model.model_fields) & forbidden
        assert not leaked, f"{model.__name__} 泄露正文/PII/模型生成型字段：{leaked}"


# ===========================================================================
# 守门 6：StatuteRef 输出 = E5-1 白名单、100% statute_anchors(text_id)、无正文；条文不杜撰。
# ===========================================================================
def test_statute_ref_fields_strictly_equal_e5_1_whitelist():
    from app.kernel.guardrails.contracts import STATUTE_REF_FIELDS
    from app.kernel.guardrails import StatuteRef

    fields = set(StatuteRef.model_fields)
    assert fields == WHITELIST_STATUTE_REF, (
        f"StatuteRef 字段偏离 E5-1 白名单：{fields ^ WHITELIST_STATUTE_REF}"
    )
    assert fields == set(STATUTE_REF_FIELDS)
    assert StatuteRef.model_config.get("extra") == "forbid"


def test_statute_ref_has_no_body_or_generated_fields():
    from app.kernel.guardrails import StatuteRef

    forbidden = set(FORBIDDEN_BODY_TOKENS) | set(FORBIDDEN_GENERATED_TOKENS)
    leaked = set(StatuteRef.model_fields) & forbidden
    assert not leaked, f"StatuteRef 泄露正文/模型生成条文型字段：{leaked}"


def test_statute_ref_requires_non_empty_anchors_with_text_id():
    """无锚点 / 锚点缺 text_id 即 fail-closed（无锚点不展示、不杜撰）。"""
    from app.kernel.guardrails import ContractViolationError, sanitize_statute_ref

    # 无 statute_anchors → 拒绝。
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref({"statute_id": "s1", "law_name": "刑法"})
    # 空 statute_anchors → 拒绝。
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {"statute_id": "s1", "law_name": "刑法", "statute_anchors": []}
        )
    # 锚点缺 text_id → 拒绝。
    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [{"law_name": "刑法"}],
            }
        )


def test_statute_ref_output_dump_only_whitelist_keys_with_anchors():
    from app.kernel.guardrails import sanitize_statute_ref

    ref = sanitize_statute_ref(
        {
            "statute_id": "s264",
            "law_name": "刑法",
            "article_no": "第x条",
            "statute_anchors": [{"text_id": "law::s264", "anchor_type": "statute"}],
            "article_text": "短条文占位",
            "source_corpus": "judge_law_corpus",
            "effective_status": "current",
            # 非白名单键应被静默丢弃。
            "final_score": 0.9,
            "metadata": {"k": "v"},
        }
    )
    dumped = ref.model_dump()
    assert set(dumped) <= WHITELIST_STATUTE_REF
    assert dumped["statute_anchors"]
    for anchor in dumped["statute_anchors"]:
        assert anchor["text_id"]
    for forbidden in FORBIDDEN_BODY_TOKENS + FORBIDDEN_GENERATED_TOKENS:
        assert forbidden not in dumped


@pytest.mark.parametrize(
    "bad_key",
    list(FORBIDDEN_GENERATED_TOKENS)
    + ["full_text", "chunk_text", "summary_text", "matched_text", "id_card", "name"],
)
def test_statute_ref_rejects_body_pii_and_generated_keys(bad_key):
    from app.kernel.guardrails import ContractViolationError, sanitize_statute_ref

    with pytest.raises(ContractViolationError):
        sanitize_statute_ref(
            {
                "statute_id": "s1",
                "law_name": "刑法",
                "statute_anchors": [{"text_id": "law::s1"}],
                bad_key: "x",
            }
        )


def test_statute_anchored_assertion_fails_closed_on_generated_or_missing_anchor():
    from app.kernel.guardrails import assert_statute_anchored, ContractViolationError

    # 模型生成条文型键 → 拒绝。
    with pytest.raises(ContractViolationError):
        assert_statute_anchored(
            {"statute_anchors": [{"text_id": "law::s1"}], "generated_article": "x"}
        )
    # 缺锚点 → 拒绝。
    with pytest.raises(ContractViolationError):
        assert_statute_anchored({"statute_id": "s1"})


# ===========================================================================
# 守门 7：互跳只走契约对象 —— 法条→类案出 CandidateRef（白名单七字段、100% 锚点、0 正文）；
#         类案→法条出 StatuteRef（带锚点）；两侧都不携带对侧正文。
# ===========================================================================
def test_cross_jump_statute_to_case_yields_candidate_ref_whitelist():
    """法条→类案互跳的视图 = E-1 CandidateRef 白名单七字段 + 锚点，零正文。"""
    from app.statute.schemas import StatuteCandidateRefView

    assert set(StatuteCandidateRefView.model_fields) == WHITELIST_CANDIDATE
    assert StatuteCandidateRefView.model_config.get("extra") == "forbid"
    view = StatuteCandidateRefView(
        case_id="c1",
        case_number="no-1",
        court="court-x",
        trial_level="一审",
        case_cause="盗窃",
        judgment_date="2023-01-01",
        source_anchors=[
            {"case_id": "c1", "source_chunk_id": "c1_ch0", "anchor_type": "statute_link"}
        ],
    )
    dumped = view.model_dump()
    assert set(dumped) == WHITELIST_CANDIDATE
    assert dumped["source_anchors"]
    for anchor in dumped["source_anchors"]:
        assert anchor["case_id"] and anchor["source_chunk_id"]
    for forbidden in FORBIDDEN_BODY_TOKENS:
        assert forbidden not in dumped


def test_cross_jump_candidate_view_requires_source_anchors():
    from pydantic import ValidationError

    from app.statute.schemas import StatuteCandidateRefView

    with pytest.raises(ValidationError):
        StatuteCandidateRefView(case_id="c1", source_anchors=[])


def test_cross_jump_case_to_statute_yields_anchored_statute_ref():
    """类案→法条互跳的 StatuteRef 视图必带 statute_anchors（text_id 非空）。"""
    from app.statute.schemas import StatuteRefView

    assert set(StatuteRefView.model_fields) == WHITELIST_STATUTE_REF
    assert StatuteRefView.model_config.get("extra") == "forbid"
    from pydantic import ValidationError

    # 无 statute_anchors → 拒绝（min_length=1）。
    with pytest.raises(ValidationError):
        StatuteRefView(statute_id="s1", law_name="刑法", statute_anchors=[])


def test_cross_jump_related_case_ref_is_candidate_whitelist_zero_body():
    """StatuteRef.related_case_refs（法条→类案）= CandidateRef 同款白名单七字段，零正文。"""
    from app.kernel.guardrails.contracts import StatuteRelatedCaseRef

    assert set(StatuteRelatedCaseRef.model_fields) == WHITELIST_CANDIDATE
    assert StatuteRelatedCaseRef.model_config.get("extra") == "forbid"
    forbidden = set(FORBIDDEN_BODY_TOKENS)
    leaked = set(StatuteRelatedCaseRef.model_fields) & forbidden
    assert not leaked, f"互跳类案引用泄露正文型字段：{leaked}"


def test_statute_response_views_carry_no_opposite_side_body():
    """StatuteRefView / StatuteCandidateRefView 字段集均不含对侧正文型键。"""
    from app.statute.schemas import StatuteCandidateRefView, StatuteRefView

    forbidden = set(FORBIDDEN_BODY_TOKENS) | set(FORBIDDEN_GENERATED_TOKENS)
    for model in (StatuteRefView, StatuteCandidateRefView):
        leaked = set(model.model_fields) & forbidden
        assert not leaked, f"{model.__name__} 泄露正文/模型生成型字段：{leaked}"


# ===========================================================================
# 守门 8：statute 后端代码/模型/日志无正文型 / 原始案情 / PII；statute 不持久化。
# ===========================================================================
def test_statute_executable_code_has_no_body_or_pii_data_tokens():
    """statute 可执行代码（剔除 docstring/注释）不得搬运正文 / PII / 模型生成条文型键。

    docstring/注释里把这些键名当『被拒反例』提及是允许的；本断言只盯可执行语句。
    """
    offenders: list[str] = []
    forbidden = (
        FORBIDDEN_BODY_TOKENS + FORBIDDEN_PII_TOKENS + FORBIDDEN_GENERATED_TOKENS
    )
    for path in _statute_py_files():
        code = _code_without_docstrings(path)
        for token in forbidden:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "statute 可执行代码出现正文 / PII / 模型生成条文型键（红线）：" + "; ".join(offenders)
    )


def test_statute_does_not_persist_anything():
    """statute 无状态透传：可执行代码不得出现持久层 / 落库迹象。"""
    offenders: list[str] = []
    for path in _statute_py_files():
        code = _code_without_docstrings(path)
        for token in PERSISTENCE_TOKENS:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "statute 出现持久化 / 落库迹象（应无状态透传）：" + "; ".join(offenders)
    )


def test_statute_modules_do_not_import_persistence_layer():
    offenders: list[str] = []
    for path in _statute_py_files():
        for module in _iter_import_modules(path):
            if (
                module.startswith("app.db")
                or module.startswith("app.models")
                or module == "sqlmodel"
                or module.startswith("app.kernel.data")
            ):
                offenders.append(f"{path.name}: {module}")
    assert not offenders, (
        "statute import 了持久层（应无状态透传、不落库）：" + "; ".join(offenders)
    )


def test_statute_logs_do_not_emit_body_or_query_text():
    """statute 日志只写 query_session_id / 计数 / degraded_reasons；不写 query_text / 正文。"""
    router_src = _code_without_docstrings(STATUTE_DIR / "router.py")
    service_src = _code_without_docstrings(STATUTE_DIR / "service.py")
    # 日志格式串里不得出现 query_text / 正文型占位。
    for src, name in ((router_src, "router.py"), (service_src, "service.py")):
        for token in ("query_text=%", "raw_case", "article_text=%"):
            assert token not in src, f"{name} 日志疑似写入正文 / query_text：{token}"


# ===========================================================================
# 守门 9：include_router=16（intake + statute + drafting + casebook）；无 /api/internal、/api/ecosystem 越界端点。
# E7-2 基线上移：E6-2 基线 15 → E7-2 新增 casebook_router → 16。
# ===========================================================================
def test_include_router_count_is_16():
    source = MAIN_PY.read_text(encoding="utf-8")
    count = source.count("app.include_router(")
    assert count == 16, f"include_router 数必须为 16（E7-2 基线：intake+statute+drafting+casebook），实际 {count}"
    assert "app.include_router(intake_router)" in source
    assert "app.include_router(statute_router)" in source
    assert "app.include_router(drafting_router)" in source
    assert "app.include_router(casebook_router)" in source


def test_no_out_of_bound_endpoints_registered():
    offenders: list[str] = []
    api_dir = APP_DIR / "api"
    targets = list(_py_files(api_dir)) + _statute_py_files() + [MAIN_PY]
    for path in targets:
        source = path.read_text(encoding="utf-8")
        for fragment in FORBIDDEN_ENDPOINT_FRAGMENTS:
            if fragment in source:
                offenders.append(f"{path.name}: {fragment}")
    assert not offenders, (
        "出现越界端点片段（/api/internal、/api/ecosystem、drafting/casebook 端点）："
        + "; ".join(offenders)
    )


def test_statute_registers_only_three_search_endpoints():
    """statute router 仅暴露 search / by-case / cases-by-statute 三个端点，不新增其它对外端点。"""
    router_src = (STATUTE_DIR / "router.py").read_text(encoding="utf-8")
    tree = ast.parse(router_src)
    route_decorators = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                func = dec.func if isinstance(dec, ast.Call) else dec
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "router"
                    and func.attr in ("get", "post", "put", "delete", "patch")
                ):
                    route_decorators += 1
    assert route_decorators == 3, (
        f"statute router 应仅注册 3 个端点，实际 {route_decorators}"
    )


# ===========================================================================
# 守门 10：法条索引与案件索引物理隔离；E5 未重建/未修改案件产物。
# ===========================================================================
def test_statute_index_physically_isolated_from_case_index():
    """法条索引 collection / persist 目录与案件索引隔离（不同名 + 不同目录）。"""
    src = STATUTE_INDEX_PIPELINE.read_text(encoding="utf-8")
    assert f'STATUTE_COLLECTION = "{STATUTE_COLLECTION}"' in src
    assert STATUTE_COLLECTION != CASE_COLLECTION
    # persist 目录解析函数承诺『永远不等于案件 persist 目录』。
    assert "resolve_statute_persist_dir" in src
    assert "物理隔离" in src


def test_statute_index_pipeline_does_not_write_case_collection():
    """法条索引构建脚本绝不写/改案件 collection（只 get_or_create 自己的 statute collection）。"""
    code = _code_without_docstrings(STATUTE_INDEX_PIPELINE)
    # 不得对案件 collection 调用写操作（add/upsert/delete）。
    assert "case_chunks_bge_m3_v1" not in code or "CASE_COLLECTION" in code
    for write_op in (".delete(", ".upsert("):
        # 写操作只能落在 statute collection 上下文；案件 collection 仅作隔离断言常量。
        if write_op in code:
            assert "CASE_COLLECTION" not in code.split(write_op)[0].splitlines()[-1], (
                f"法条索引脚本疑似对案件 collection 执行 {write_op}"
            )


def test_e5_does_not_modify_case_artifact_files_in_repo():
    """E5 法条产物与案件产物为独立文件；案件产物文件名未被 statute 模块写入引用。"""
    # statute 产品包 / 内核法条服务可执行代码不得写 cases.jsonl / chunks.jsonl（案件产物）。
    targets = _statute_py_files() + [STATUTE_SERVICE_MODULE]
    offenders: list[str] = []
    for path in targets:
        code = _code_without_docstrings(path)
        for case_artifact in ("cases.jsonl", "chunks.jsonl"):
            # 只读 chunks.jsonl 取代表性 chunk 锚点是允许的；写入才越界。
            if case_artifact in code:
                # 确认无写打开（"w"/"a" 模式）。
                for mode in ('"w"', "'w'", '"a"', "'a'"):
                    assert mode not in code, (
                        f"{path.name} 疑似以写模式打开案件产物 {case_artifact}"
                    )
    assert not offenders


# ===========================================================================
# 守门 11：测试 fixture / docs e5-* 不写真实长案情、裁判正文、真实 PII；法条 fixture 不嵌长正文。
# ===========================================================================
def test_e5_test_fixtures_have_no_long_body_text():
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.glob("test_e5_*.py")):
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E5 fixture 疑似长正文：" + "; ".join(offenders)


def test_e5_docs_have_no_long_body_text():
    if not DOCS_DEV_DIR.exists():
        pytest.skip("docs/development absent")
    offenders: list[str] = []
    targets = sorted(DOCS_DEV_DIR.glob("e5-*.md")) + sorted(DOCS_DEV_DIR.glob("e5-*.json"))
    for path in targets:
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E5 docs 疑似长正文：" + "; ".join(offenders)
