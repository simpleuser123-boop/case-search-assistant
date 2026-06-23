"""E4-5 案情录入端消费边界与护栏守门（静态 AST 扫描 + 运行时契约断言，零行为改动）。

把文档 19 §1~§3 + §8（E4-5）的架构纪律与隐私红线固化成可执行测试，确保后续
E5 法条检索 / E6 文书工作台 / E7 案件协作台不绕开内部检索服务、不泄露原始案情或正文。

守门覆盖（与提示词「需要覆盖的守门」1~10 逐条对应）：
1. intake 产品包公开面/端点存在且 gated：ENABLE_INTAKE=false（默认）时端点 403 降级。
2. intake 模块不得 import statute/drafting/casebook，也不得被其它产品包 import（互不 import）。
3. intake 不深引 retrieval/rerank/summary/query_processing，必经 app.kernel.rag 公开面消费检索。
4. E7-2 后仅允许 intake/statute/drafting/casebook 四个 E 系列产品包存在。
5. intake 请求体 schema 严格 = SearchProfile 白名单五字段(+mode/limit)，extra=forbid，
   拒绝 raw_case/raw_query/PII/正文型键。
6. CandidateRef 输出严格 = E-1 白名单七字段、100% 有 source_anchors、0 正文。
7. 原始案情零上送：intake 后端代码/模型不出现 raw_case/raw_query/PII 型数据字段；后端不持久化。
8. ENABLE_INTAKE_AI_EXTRACTION 默认 false 且无 on 路径：intake 代码无服务端 AI 增强接线 /
   无原始文本接收路径。
9. include_router 数 = 16（intake + statute + drafting + casebook）；无 /api/internal、/api/ecosystem 等越界端点。
10. 测试 fixture、docs/development/e4-*.json/md 不写真实长案情、裁判正文、真实 PII。

红线：本文件 fixture 只用短假数据 / hash / case_id / source_chunk_id / 元数据。
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
INTAKE_DIR = APP_DIR / "intake"
MAIN_PY = APP_DIR / "main.py"
CONFIG_PY = APP_DIR / "core" / "config.py"

PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")
# E5-4：法条检索端 statute 是 E 系列第二个合法产品包（gated，默认 false 降级）。
# E6-2：文书工作台 drafting 是 E 系列第三个合法产品包（gated，默认 false 降级）。
# E7-2 基线上移：casebook 已落地；仍只允许 E 系列四个产品包。
ALLOWED_PRODUCT_PACKAGES = {"intake", "statute", "drafting", "casebook"}

# intake 只允许消费的内核顶层公开面（不得深引内部子模块）。
ALLOWED_KERNEL_SURFACES = ("app.kernel", "app.kernel.rag", "app.kernel.guardrails")

# 检索底层 / 内核内部子模块（深引即越界，必须经公开面 InternalSearchService）。
FORBIDDEN_DEEP_PREFIXES = (
    "app.retrieval",
    "app.rerank",
    "app.summary",
    "app.query_processing",
    "app.kernel.rag.retrieval",
    "app.kernel.rag.rerank",
    "app.kernel.rag.summary",
    "app.kernel.rag.query_processing",
    "app.kernel.rag.internal_search_service",
    "app.kernel.rag.internal_search_contracts",
)

# 正文型数据键（绝不可作为 intake 数据字段 / dict 键 / 属性搬运）。
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
    "original_fact",
    "fact_text",
    "document_text",
    "paragraph_text",
    "case_body",
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
    "residence",
    "residential_address",
    "plate_no",
    "license_plate",
    "party_name",
    "defendant_name",
    "plaintiff_name",
    "litigant_name",
    "real_name",
    "full_name",
)

WHITELIST_PROFILE = {
    "case_cause",
    "region",
    "trial_level_preference",
    "dispute_focus_keywords",
    "query_text",
}
WHITELIST_CANDIDATE = {
    "case_id",
    "case_number",
    "court",
    "trial_level",
    "case_cause",
    "judgment_date",
    "source_anchors",
}

# AI 增强子开关：本期无 on 路径，intake 可执行代码不得引用它。
AI_EXTRACTION_FLAG = "ENABLE_INTAKE_AI_EXTRACTION"

# 持久化 / 落库迹象（intake 无状态透传，不得出现在可执行代码）。
PERSISTENCE_TOKENS = (
    "Session(",
    "get_session",
    "session.add",
    ".commit(",
    "engine",
    "SQLModel",
    "select(",
    "insert(",
    "create_engine",
)

FORBIDDEN_ENDPOINT_FRAGMENTS = (
    "/api/internal/search",
    "/api/internal",
    "/api/ecosystem/search",
    "/api/ecosystem",
    # E5-4：/api/statute 已是合法 gated 端点（statute 产品包），从越界片段移除。
    "/api/drafting",
    "/api/casebook",
)

# E3/E4 既有 docs 最长连续中文 ≤20、fixture ≤17，取 40 留足安全边界（与 E3 守门同口径）。
MAX_CONTIGUOUS_CJK = 40


# --- helpers（纯静态，不 import 业务运行时）-------------------------------------

def _py_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _intake_py_files():
    return sorted(p for p in INTAKE_DIR.glob("*.py"))


def _iter_import_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _imported_names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.extend(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names)
    return names


def _code_without_docstrings(path: Path) -> str:
    """返回去掉模块/类/函数 docstring 后的源码切片拼接（保留可执行语句 + 注释除外）。

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
        # 删除整行注释与行尾注释（粗粒度：以 # 截断；字符串内 # 罕见且本仓不涉及）。
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
# 守门 1：intake 公开面/端点存在且 gated（ENABLE_INTAKE 默认 false → 403 降级）。
# ===========================================================================
def test_intake_public_face_present():
    import app.intake as intake

    for name in (
        "router",
        "IntakeSearchRequest",
        "IntakeSearchResponse",
        "IntakeCandidateRefView",
        "IntakeSearchService",
    ):
        assert hasattr(intake, name) and name in intake.__all__, (
            f"app.intake public face missing {name}"
        )


def test_enable_intake_defaults_false():
    from app.core.config import Settings

    assert Settings(_env_file=None).ENABLE_INTAKE is False


def test_intake_endpoint_gated_disabled_returns_403():
    """默认 flag off：端点 403 安全降级、不检索、不回显查询，回到单产品末态。"""
    from fastapi.testclient import TestClient

    import app.intake as intake
    from app.main import app

    # app.intake 公开面直接 re-export 了 setter / INTAKE_DISABLED_CODE / router 实例。
    # 不注入服务（默认 ENABLE_INTAKE=False）。复位测试钩子确保干净态。
    intake.set_intake_search_service_for_test(None)
    client = TestClient(app)
    resp = client.post("/api/intake/search", json={"query_text": "脱敏短查询"})
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == intake.INTAKE_DISABLED_CODE
    assert "candidate_refs" not in resp.text
    assert "脱敏短查询" not in resp.text


def test_intake_route_prefix_is_api_intake():
    import app.intake as intake

    # 包命名空间的 router 即 APIRouter 实例（__init__ re-export）。
    assert intake.router.prefix == "/api/intake"


# ===========================================================================
# 守门 2：intake 不 import 其它产品包，也不被其它产品包 import（互不 import）。
# ===========================================================================
def test_intake_does_not_import_other_product_packages():
    offending: list[str] = []
    for path in _intake_py_files():
        for module in _iter_import_modules(path):
            for other in ("statute", "drafting", "casebook"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "intake import 了其它产品包（产品包互不 import）：" + "; ".join(offending)
    )


def test_intake_not_imported_by_other_product_packages():
    """其它产品包当前不存在即通过；一旦存在，立即断言其不得 import intake。"""
    offending: list[str] = []
    for other in ("statute", "drafting", "casebook"):
        pkg_dir = APP_DIR / other
        if not pkg_dir.exists():
            continue
        for path in _py_files(pkg_dir):
            for module in _iter_import_modules(path):
                if module == "app.intake" or module.startswith("app.intake."):
                    offending.append(f"{other}/{path.name}: {module}")
    assert not offending, (
        "其它产品包反向 import 了 intake（产品包互不 import）：" + "; ".join(offending)
    )


# ===========================================================================
# 守门 3：intake 不深引检索底层，必经 app.kernel.rag 公开面消费检索。
# ===========================================================================
def test_intake_does_not_deep_import_retrieval_layer():
    offending: list[str] = []
    for path in _intake_py_files():
        for module in _iter_import_modules(path):
            for prefix in FORBIDDEN_DEEP_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "intake 深引检索底层 / 内核内部子模块（应只走 app.kernel.rag 公开面）："
        + "; ".join(offending)
    )


def test_intake_consumes_only_kernel_top_surface():
    offending: list[str] = []
    for path in _intake_py_files():
        for module in _iter_import_modules(path):
            if module.startswith("app.kernel"):
                if module not in ALLOWED_KERNEL_SURFACES:
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "intake 对内核只能消费顶层公开面，禁止深引：" + "; ".join(offending)
    )


def test_intake_service_references_internal_search_service():
    """intake 检索经 InternalSearchService（公开面）执行，而非复制检索主路径。"""
    service_src = (INTAKE_DIR / "service.py").read_text(encoding="utf-8")
    assert "InternalSearchService" in service_src
    assert "search_candidate_refs" in service_src
    # 不得内联检索执行原语（绕过内部服务）。
    for primitive in (
        "VectorRetrievalService",
        "FactSimilarityReranker",
        "merge_case_candidates(",
        "split_low_confidence_candidates(",
    ):
        assert primitive not in service_src, (
            f"intake service 内联检索执行原语 {primitive}（应经 InternalSearchService）"
        )


# ===========================================================================
# 守门 4：E7-2 后仅允许 E 系列四个产品包存在。
# ===========================================================================
def test_only_expected_e_series_product_packages_exist():
    existing = {p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()}
    unexpected = existing - ALLOWED_PRODUCT_PACKAGES
    assert not unexpected, (
        "E7-2 阶段仅允许 intake / statute / drafting / casebook 产品包，发现越界产品包："
        + ", ".join(sorted(unexpected))
    )
    assert "intake" in existing, "intake 产品包必须存在（E4-3 已建）"


# ===========================================================================
# 守门 5：请求体 schema 严格 = SearchProfile 白名单五字段(+mode/limit)，extra=forbid。
# ===========================================================================
def test_intake_request_schema_whitelist_and_extra_forbid():
    from app.intake.schemas import IntakeSearchRequest

    fields = set(IntakeSearchRequest.model_fields)
    # 五字段白名单 + 仅结构化检索参数 mode/limit。
    assert fields == WHITELIST_PROFILE | {"mode", "limit"}, (
        f"IntakeSearchRequest 字段集偏离白名单：{fields ^ (WHITELIST_PROFILE | {'mode', 'limit'})}"
    )
    assert IntakeSearchRequest.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    "bad_key",
    [
        "raw_case",
        "raw_query",
        "full_text",
        "content",
        "name",
        "id_card",
        "phone",
        "address",
        "email",
        "chunk_text",
    ],
)
def test_intake_request_rejects_body_and_pii_keys_at_model_layer(bad_key):
    """请求体模型层 extra=forbid：任何正文型 / PII 型键即 ValidationError（第一道闸）。"""
    from pydantic import ValidationError

    from app.intake.schemas import IntakeSearchRequest

    with pytest.raises(ValidationError):
        IntakeSearchRequest(query_text="脱敏短查询", **{bad_key: "X"})


def test_intake_request_accepts_pure_whitelist_payload():
    from app.intake.schemas import IntakeSearchRequest

    req = IntakeSearchRequest(
        case_cause="合同纠纷",
        region="X省",
        trial_level_preference="二审",
        dispute_focus_keywords=["违约金"],
        query_text="脱敏短查询",
        mode="standard",
        limit=10,
    )
    assert set(req.model_dump()) == WHITELIST_PROFILE | {"mode", "limit"}


# ===========================================================================
# 守门 6：CandidateRef 输出严格 = E-1 白名单七字段、100% source_anchors、0 正文。
# ===========================================================================
def test_intake_candidate_ref_view_is_e1_seven_fields():
    from app.intake.schemas import IntakeCandidateRefView

    assert set(IntakeCandidateRefView.model_fields) == WHITELIST_CANDIDATE
    assert IntakeCandidateRefView.model_config.get("extra") == "forbid"


def test_intake_candidate_ref_view_requires_source_anchors():
    """source_anchors min_length=1：100% 有锚点，空锚点即 ValidationError。"""
    from pydantic import ValidationError

    from app.intake.schemas import IntakeCandidateRefView

    with pytest.raises(ValidationError):
        IntakeCandidateRefView(case_id="C-1", source_anchors=[])


@pytest.mark.parametrize(
    "bad_key", ["summary", "highlights", "matched_text", "content", "body", "chunk_text"]
)
def test_intake_candidate_ref_view_forbids_body_keys(bad_key):
    from pydantic import ValidationError

    from app.intake.schemas import IntakeCandidateRefView

    good_anchor = {"case_id": "C-1", "source_chunk_id": "ch-1"}
    with pytest.raises(ValidationError):
        IntakeCandidateRefView(
            case_id="C-1", source_anchors=[good_anchor], **{bad_key: "x"}
        )


def test_intake_source_anchor_view_is_metadata_only():
    from app.intake.schemas import IntakeSourceAnchorView

    fields = set(IntakeSourceAnchorView.model_fields)
    assert fields == {"case_id", "source_chunk_id", "anchor_type"}
    assert IntakeSourceAnchorView.model_config.get("extra") == "forbid"


def test_intake_candidate_ref_view_dump_only_whitelist_keys():
    from app.intake.schemas import IntakeCandidateRefView

    view = IntakeCandidateRefView(
        case_id="C-1",
        case_number="no-1",
        court="court-x",
        trial_level="二审",
        case_cause="cause-x",
        judgment_date="2020-06-01",
        source_anchors=[{"case_id": "C-1", "source_chunk_id": "ch-1", "anchor_type": "result"}],
    )
    dumped = view.model_dump()
    assert set(dumped) == WHITELIST_CANDIDATE
    for forbidden in FORBIDDEN_BODY_TOKENS:
        assert forbidden not in dumped


# ===========================================================================
# 守门 7：原始案情零上送 —— intake 可执行代码不出现正文 / PII 型数据键；不持久化。
# ===========================================================================
def test_intake_executable_code_has_no_body_or_pii_data_tokens():
    """intake 可执行代码（剔除 docstring/注释）不得搬运正文 / PII 型数据键。

    docstring/注释里把这些键名当『被拒反例』提及是允许的；本断言只盯可执行语句。
    """
    offenders: list[str] = []
    for path in _intake_py_files():
        code = _code_without_docstrings(path)
        for token in FORBIDDEN_BODY_TOKENS + FORBIDDEN_PII_TOKENS:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "intake 可执行代码出现正文 / PII 型数据键（原始案情零上送红线）："
        + "; ".join(offenders)
    )


def test_intake_does_not_persist_anything():
    """intake 无状态透传：可执行代码不得出现持久层 / 落库迹象。"""
    offenders: list[str] = []
    for path in _intake_py_files():
        code = _code_without_docstrings(path)
        for token in PERSISTENCE_TOKENS:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "intake 出现持久化 / 落库迹象（应无状态透传）：" + "; ".join(offenders)
    )


def test_intake_modules_do_not_import_persistence_layer():
    offenders: list[str] = []
    for path in _intake_py_files():
        for module in _iter_import_modules(path):
            if (
                module.startswith("app.db")
                or module.startswith("app.models")
                or module == "sqlmodel"
                or module.startswith("app.kernel.data")
            ):
                offenders.append(f"{path.name}: {module}")
    assert not offenders, (
        "intake import 了持久层（应无状态透传、不落库）：" + "; ".join(offenders)
    )


# ===========================================================================
# 守门 8：ENABLE_INTAKE_AI_EXTRACTION 默认 false 且无 on 路径（intake 无 AI 接线）。
# ===========================================================================
def test_ai_extraction_flag_defaults_false():
    from app.core.config import Settings

    assert Settings(_env_file=None).ENABLE_INTAKE_AI_EXTRACTION is False


def test_ai_extraction_flag_declared_false_in_config():
    text = CONFIG_PY.read_text(encoding="utf-8")
    assert f"{AI_EXTRACTION_FLAG}: bool = False" in text


def test_intake_executable_code_does_not_reference_ai_extraction_flag():
    """intake 可执行代码不得引用 AI 增强子 flag（无 on 路径）；仅 docstring 说明允许。"""
    offenders: list[str] = []
    for path in _intake_py_files():
        code = _code_without_docstrings(path)
        if AI_EXTRACTION_FLAG in code:
            offenders.append(path.name)
    assert not offenders, (
        f"intake 可执行代码引用 {AI_EXTRACTION_FLAG}（本期应无 on 路径，仅注释/docstring 说明）："
        + "; ".join(offenders)
    )


def test_intake_has_no_server_side_ai_or_raw_text_intake_path():
    """intake 可执行代码无服务端 AI 增强接线 / 无原始文本接收路径。"""
    ai_tokens = (
        "deepseek",
        "openai",
        "llm",
        "chat.completions",
        "ai_extract",
        "ai_enhance",
        "extract_from_raw",
        "raw_text",
    )
    offenders: list[str] = []
    for path in _intake_py_files():
        code = _code_without_docstrings(path).lower()
        for token in ai_tokens:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "intake 可执行代码出现服务端 AI 增强 / 原始文本接收迹象（本期不接线）："
        + "; ".join(offenders)
    )


# ===========================================================================
# 守门 9：include_router=16（intake + statute + drafting + casebook）；无越界端点。
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
    targets = list(_py_files(api_dir)) + _intake_py_files() + [MAIN_PY]
    for path in targets:
        source = path.read_text(encoding="utf-8")
        for fragment in FORBIDDEN_ENDPOINT_FRAGMENTS:
            if fragment in source:
                offenders.append(f"{path.name}: {fragment}")
    assert not offenders, (
        "出现越界端点片段（/api/internal、/api/ecosystem、其它产品端点）："
        + "; ".join(offenders)
    )


def test_intake_registers_only_search_endpoint():
    """intake router 仅暴露 POST /api/intake/search，不新增其它对外端点。"""
    router_src = (INTAKE_DIR / "router.py").read_text(encoding="utf-8")
    tree = ast.parse(router_src)
    route_decorators = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                # @router.post/get/put/delete(...)
                func = dec.func if isinstance(dec, ast.Call) else dec
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "router"
                    and func.attr in ("get", "post", "put", "delete", "patch")
                ):
                    route_decorators += 1
    assert route_decorators == 1, (
        f"intake router 应仅注册 1 个端点(/search)，实际 {route_decorators}"
    )


# ===========================================================================
# 守门 10：测试 fixture / docs e4-* 不写真实长案情、裁判正文、真实 PII。
# ===========================================================================
def test_e4_test_fixtures_have_no_long_body_text():
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.glob("test_e4_*.py")):
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E4 fixture 疑似长正文：" + "; ".join(offenders)


def test_e4_docs_have_no_long_body_text():
    if not DOCS_DEV_DIR.exists():
        pytest.skip("docs/development absent")
    offenders: list[str] = []
    targets = sorted(DOCS_DEV_DIR.glob("e4-*.md")) + sorted(DOCS_DEV_DIR.glob("e4-*.json"))
    for path in targets:
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E4 docs 疑似长正文：" + "; ".join(offenders)
