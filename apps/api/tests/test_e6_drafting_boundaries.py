"""E6-5 文书工作台 drafting 消费边界与护栏守门（静态 AST 扫描 + 运行时契约断言，零行为改动）。

把文档 21 §1.3 红线 + §8（E6-5）的架构纪律与文书工作台红线固化成可执行测试，确保
drafting 产品包「只组装锚定来源、不起草结论」，且不绕开内核公开面、不泄露正文 / 胜负 /
结论、不与其它产品包互相 import。对标 E5-6 守门范式（纯 AST + pydantic/runtime 断言）。

守门覆盖（与提示词「需要覆盖的守门」九类逐条对应）：
1. 只组装不起草：drafting service/router 不调用任何文本生成；DraftDescriptor 不含起草正文 /
   段落正文 / 结论 / 胜负字段（静态源码扫描 + 运行时契约断言双重）。
2. 引用必带锚点：DraftDescriptor.candidate_refs/statute_refs 100% 有 source_anchors/
   statute_anchors；无锚点 fail-closed 丢弃（运行时 sanitize 断言）。
3. 持久层零正文：drafting 持久层模型/表无正文列；落库行不含裁判正文 / 候选 / chunk 正文 /
   原始案情 / 起草正文（模型字段 + 写白名单 + 禁用键静态断言）。
4. 导出守门：导出强制免责头、导出内容无正文 / 无胜负 / 无结论、无锚点引用不进导出
   （前端导出模块源码静态断言；不取 article_text）。
5. drafting 不直连检索底层：AST 断言 drafting 不 import retrieval/rerank/summary/
   query_processing，只经 app.kernel 公开面。
6. 产品包互不 import：drafting 不 import intake/statute/casebook；它们也不 import drafting。
7. CandidateRef/StatuteRef 仍零正文：被 drafting 引用后字段不被增删、无正文。
8. casebook 已在 E7-2 落地：仅允许 main.py 挂载 casebook_router；ENABLE_CASEBOOK 默认 false。
9. 多租户/鉴权：drafting 端点对象级鉴权 + 租户隔离 + 默认 private（与 M5 同款）。

环境纪律（同 E5-3/E5-4/E6-2）：静态 AST / 源码扫描类断言纯 stdlib，VM 可独立执行；
运行时契约 / TestClient 类断言需 pydantic_core / sqlmodel / fastapi，host .venv311 为权威，
VM 缺依赖时经 pytest.importorskip 安全跳过（不放宽，只是取证环境差异）。

红线：本文件 fixture 只用短假数据 / hash / case_id / source_chunk_id / text_id / 元数据，
绝不写真实长起草正文或裁判正文。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REPO_ROOT = Path(__file__).resolve().parents[3]
DOCS_DEV_DIR = REPO_ROOT / "docs" / "development"
TESTS_DIR = Path(__file__).resolve().parent
DRAFTING_DIR = APP_DIR / "drafting"
MAIN_PY = APP_DIR / "main.py"
CONFIG_PY = APP_DIR / "core" / "config.py"
WEB_SRC = REPO_ROOT / "apps" / "web" / "src"
DRAFTING_EXPORT_TS = WEB_SRC / "lib" / "draftingExport.ts"

# E 系列产品包命名空间。
PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")
# E7-2 后 E 系列合法产品包集合（intake + statute + drafting + casebook）。
E6_ALLOWED_PRODUCT_PACKAGES = {"intake", "statute", "drafting", "casebook"}

# drafting 只允许消费的内核顶层公开面（深引内部子模块即越界）。
ALLOWED_KERNEL_SURFACES = ("app.kernel", "app.kernel.rag", "app.kernel.guardrails", "app.kernel.identity")

# 检索底层 / 内核内部子模块（深引即越界，必须经公开面服务）+ 其它产品包前缀。
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
    "app.statute",
    "app.casebook",
)

# 起草正文 / 段落正文 / 结论型键（与「只组装不起草」红线冲突，出现即越界）。
FORBIDDEN_DRAFT_BODY_TOKENS = (
    "draft_body",
    "draft_content",
    "draft_text",
    "generated_text",
    "generated_draft",
    "opinion_text",
    "legal_opinion",
    "paragraph_body",
    "paragraph_text",
    "section_body",
    "conclusion_text",
    "argument_text",
    "reasoning_text",
    "auto_drafted_text",
    "ai_text",
    "model_generated_text",
)

# 裁判正文 / 候选 / chunk 正文 / 原始案情型键（持久层 / 导出零正文红线）。
FORBIDDEN_JUDGMENT_BODY_TOKENS = (
    "chunk_text",
    "chunk_content",
    "judgment_text",
    "judgment_full_text",
    "summary_text",
    "highlight_text",
    "matched_text",
    "holding_summary",
    "case_body",
    "document_text",
    "raw_case",
    "raw_query",
    "full_text",
    "fact_text",
    "original_fact",
)

# 胜负 / 结论 / 裁判结果预测型键（结构性红线，不可用 flag 放开）。
FORBIDDEN_OUTCOME_TOKENS = (
    "win_probability",
    "winning_probability",
    "win_rate",
    "success_probability",
    "outcome_prediction",
    "predicted_outcome",
    "verdict",
    "verdict_prediction",
    "judgment_prediction",
    "result_prediction",
    "litigation_outcome",
    "case_outcome",
)

# PII 型数据键（不与通用标识符冲突的具体键名）。
FORBIDDEN_PII_TOKENS = (
    "id_card",
    "id_card_no",
    "id_number",
    "passport_no",
    "phone_no",
    "phone_number",
    "mobile_no",
    "email_address",
    "bank_card",
    "bank_account",
    "home_address",
    "residential_address",
    "party_name",
    "defendant_name",
    "plaintiff_name",
    "real_name",
)

# 凭据型键（持久层 / 日志绝不承载）。
FORBIDDEN_CREDENTIAL_TOKENS = (
    "password",
    "session_token",
    "access_token",
    "refresh_token",
    "api_key",
    "secret_key",
)

# 文本生成 / AI 起草调用迹象（service 绝不调用任何文本生成）。
TEXT_GENERATION_TOKENS = (
    "openai",
    "deepseek",
    "chat.completions",
    "ChatCompletion",
    "llm.generate",
    "generate_text",
    "complete(",
    "summarize(",
    "draft_paragraph",
    "generate_draft",
)

# CandidateRef 白名单七字段（与 E-1 一致；被 drafting 引用后不增删）。
WHITELIST_CANDIDATE = {
    "case_id",
    "case_number",
    "court",
    "trial_level",
    "case_cause",
    "judgment_date",
    "source_anchors",
}
# StatuteRef 白名单字段（与 E5-1 一致）。
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

# 免责头必含关键语义（导出强制免责头，与 E6-4 DRAFT_EXPORT_DISCLAIMER_LINES 同口径）。
EXPORT_DISCLAIMER_REQUIRED_FRAGMENTS = (
    "不构成法律意见",
    "人工复核",
)

# E3/E4/E5 既有 fixture 最长连续中文 ≤40 安全边界（与既有守门同口径）。
MAX_CONTIGUOUS_CJK = 40


# --- helpers（纯静态，不 import 业务运行时）-------------------------------------

def _py_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _drafting_py_files():
    if not DRAFTING_DIR.exists():
        return []
    return sorted(p for p in DRAFTING_DIR.glob("*.py"))


def _iter_import_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def _code_without_docstrings(path: Path) -> str:
    """返回去掉模块/类/函数 docstring + 行内注释后的源码（保留可执行语句）。

    把『docstring/注释里把禁用键名当反例提及』与『可执行代码真的搬运禁用键』区分开。
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


def _code_without_docstrings_and_denylists(path: Path) -> str:
    """在 _code_without_docstrings 基础上，再剔除『禁用键名单常量』赋值块。

    drafting 持久层 store.py 故意把 ``draft_body`` / ``password`` 等键名以**字符串字面量**
    列入 ``DRAFT_FORBIDDEN_PERSIST_KEYS`` 防御性黑名单——这是『拒绝这些键入库』的安全机制，
    与『可执行代码真的搬运正文键』恰好相反。本 helper 把任何赋值目标名形如
    ``*FORBIDDEN*`` / ``*DENY*`` / ``*BLOCK*`` 的常量定义整块剔除，避免把黑名单误判为泄露。
    单趟扫描原始源码：同时剔除 docstring 行、行内注释、黑名单常量赋值块。
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    blocked: set[int] = set()
    # (a) docstring 行。
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            body0 = node.body[0]
            if isinstance(body0, ast.Expr) and isinstance(body0.value, ast.Constant):
                blocked.update(range(body0.lineno, (body0.end_lineno or body0.lineno) + 1))
    # (b) 黑名单常量赋值块（*FORBIDDEN* / *DENY* / *BLOCK*）。
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for tgt in targets:
            name = tgt.id if isinstance(tgt, ast.Name) else ""
            upper = name.upper()
            if "FORBIDDEN" in upper or "DENY" in upper or "BLOCK" in upper:
                blocked.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    kept: list[str] = []
    for i, line in enumerate(src.splitlines(), start=1):
        if i in blocked:
            continue
        kept.append(line.split("#", 1)[0])
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
# 守门 1：只组装不起草 —— service/router 不调用任何文本生成；
#         drafting 可执行代码不出现起草正文 / 段落正文 / 结论 / 胜负型键（静态）。
# ===========================================================================
def test_drafting_code_has_no_text_generation_call():
    """drafting 全包可执行代码（剔除 docstring/注释）不得出现任何文本生成 / AI 起草调用迹象。"""
    offenders: list[str] = []
    for path in _drafting_py_files():
        code = _code_without_docstrings(path)
        for token in TEXT_GENERATION_TOKENS:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "drafting 可执行代码出现文本生成 / AI 起草调用迹象（违反『只组装不起草』红线）："
        + "; ".join(offenders)
    )


def test_drafting_code_has_no_draft_body_or_outcome_tokens():
    """drafting 可执行代码不得搬运起草正文 / 裁判正文 / 结论 / 胜负型键（只组装不起草 + 零正文）。

    docstring/注释把这些键名当『被拒反例』提及是允许的；持久层 store.py 的禁用键**黑名单常量**
    （DRAFT_FORBIDDEN_PERSIST_KEYS）是防御机制亦不算泄露，故用 denylist-aware 扫描剔除。
    本断言只盯真正搬运正文的可执行语句。
    """
    offenders: list[str] = []
    forbidden = (
        FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_JUDGMENT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
    )
    for path in _drafting_py_files():
        code = _code_without_docstrings_and_denylists(path)
        for token in forbidden:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, (
        "drafting 可执行代码出现起草正文 / 裁判正文 / 结论 / 胜负型键（红线）："
        + "; ".join(offenders)
    )


def test_drafting_assemble_is_sanitize_not_generation():
    """service.assemble_draft 必须等价于 sanitize 收敛（组装），而非生成文本。"""
    service_src = (DRAFTING_DIR / "service.py").read_text(encoding="utf-8")
    assert "assemble_draft" in service_src, "service 应提供 assemble_draft 组装入口"
    assert "sanitize_draft_descriptor" in service_src, (
        "assemble_draft 应经 sanitize_draft_descriptor 收敛（组装而非起草）"
    )
    # service 不得引用任何文本生成 / 模型客户端模块。
    modules = list(_iter_import_modules(DRAFTING_DIR / "service.py"))
    for mod in modules:
        assert not mod.startswith("openai") and not mod.startswith("app.summary"), (
            f"service 引用了文本生成 / 摘要底层：{mod}"
        )


def test_drafting_descriptor_model_has_no_body_or_outcome_fields_runtime():
    """运行时：DraftDescriptor 字段集不含起草正文 / 裁判正文 / 结论 / 胜负型字段（host 权威）。"""
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import DraftDescriptor

    forbidden = set(
        FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_JUDGMENT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
    )
    leaked = set(DraftDescriptor.model_fields) & forbidden
    assert not leaked, f"DraftDescriptor 泄露起草正文 / 裁判正文 / 结论 / 胜负型字段：{leaked}"
    assert DraftDescriptor.model_config.get("extra") == "forbid"


@pytest.mark.parametrize("bad_key", list(FORBIDDEN_DRAFT_BODY_TOKENS) + list(FORBIDDEN_OUTCOME_TOKENS))
def test_drafting_sanitize_rejects_draft_body_and_outcome_keys_runtime(bad_key):
    """运行时：sanitize_draft_descriptor 对起草正文 / 胜负结论型键 fail-closed（host 权威）。"""
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import ContractViolationError, sanitize_draft_descriptor

    with pytest.raises(ContractViolationError):
        sanitize_draft_descriptor(
            {"draft_id": "d1", "structure_skeleton": ["一、基本案情"], bad_key: "x"}
        )


def test_drafting_assert_no_draft_body_runtime():
    """运行时：assert_no_draft_body 对夹带起草正文 / 结论的 payload fail-closed。"""
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import assert_no_draft_body, ContractViolationError

    # 合法骨架不抛错。
    assert_no_draft_body({"draft_id": "d1", "structure_skeleton": ["一、争议焦点"]})
    # 夹带起草正文键 → 抛错。
    with pytest.raises(ContractViolationError):
        assert_no_draft_body(
            {"draft_id": "d1", "structure_skeleton": ["一"], "draft_body": "x"}
        )


# ===========================================================================
# 守门 5：drafting 不直连检索底层 —— 只经 app.kernel 公开面（AST 静态）。
# ===========================================================================
def test_drafting_consumes_only_kernel_public_surface():
    """drafting 对内核只能消费顶层公开面，禁止深引 retrieval/rerank/summary 等内部子模块。"""
    offending: list[str] = []
    kernel_top = ("retrieval", "rerank", "summary", "query_processing", "account",
                  "team", "permission", "sharing", "pipeline", "case_store", "contracts")
    for path in _drafting_py_files():
        for module in _iter_import_modules(path):
            if module.startswith("app.kernel") and module not in ALLOWED_KERNEL_SURFACES:
                offending.append(f"{path.name}: {module}")
            parts = module.split(".")
            if len(parts) >= 2 and parts[0] == "app" and parts[1] in kernel_top:
                offending.append(f"{path.name}: {module}")
    assert not offending, (
        "drafting 绕过 app.kernel 公开面深引内核内部（应只走 kernel 公开面）："
        + "; ".join(offending)
    )


def test_drafting_does_not_deep_import_retrieval_runtime():
    """drafting 不得直引检索运行时底层 / 其它产品包（应经 app.kernel 公开面服务）。"""
    offending: list[str] = []
    for path in _drafting_py_files():
        for module in _iter_import_modules(path):
            for prefix in FORBIDDEN_DEEP_PREFIXES:
                if module == prefix or module.startswith(prefix + "."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "drafting 直引检索运行时底层 / 其它产品包（违反单向依赖）：" + "; ".join(offending)
    )


# ===========================================================================
# 守门 6：产品包互不 import —— drafting 不 import intake/statute/casebook；
#         它们也不 import drafting。
# ===========================================================================
def test_drafting_does_not_import_other_product_packages():
    offending: list[str] = []
    for path in _drafting_py_files():
        for module in _iter_import_modules(path):
            for other in ("intake", "statute", "casebook"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "drafting import 了其它产品包（产品包互不 import）：" + "; ".join(offending)
    )


def test_drafting_not_imported_by_other_product_packages():
    """drafting 不得被其它产品包 import（intake/statute/casebook）——反向互不 import。"""
    offending: list[str] = []
    for other in ("intake", "statute", "casebook"):
        other_dir = APP_DIR / other
        if not other_dir.exists():
            continue
        for path in _py_files(other_dir):
            for module in _iter_import_modules(path):
                if module == "app.drafting" or module.startswith("app.drafting."):
                    rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                    offending.append(f"{rel}: {module}")
    assert not offending, (
        "其它产品包反向 import 了 drafting（产品包互不 import）：" + "; ".join(offending)
    )


def test_kernel_does_not_import_drafting():
    """内核不得反向 import drafting（单向依赖：内核 ← 产品包，绝不反向）。"""
    offending: list[str] = []
    for path in _py_files(APP_DIR / "kernel"):
        for module in _iter_import_modules(path):
            if module == "app.drafting" or module.startswith("app.drafting."):
                rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                offending.append(f"{rel}: {module}")
    assert not offending, (
        "内核反向 import 了 drafting（违反单向依赖）：" + "; ".join(offending)
    )


# ===========================================================================
# 守门 2：引用必带锚点 —— candidate_refs/statute_refs 100% 有锚点；无锚点 fail-closed 丢弃。
# ===========================================================================
def test_drafting_candidate_ref_requires_source_anchors_runtime():
    """运行时：DraftCandidateRef 无 source_anchors / 锚点缺字段 → 拒绝（host 权威）。"""
    pytest.importorskip("pydantic_core")
    from pydantic import ValidationError
    from app.kernel.guardrails import DraftCandidateRef

    # 无 source_anchors → 拒绝（min_length=1）。
    with pytest.raises(ValidationError):
        DraftCandidateRef(case_id="c1", source_anchors=[])
    # 锚点缺 source_chunk_id → 拒绝。
    with pytest.raises(ValidationError):
        DraftCandidateRef(case_id="c1", source_anchors=[{"case_id": "c1"}])


def test_drafting_sanitize_drops_anchorless_refs_keeps_anchored_runtime():
    """运行时：sanitize_draft_descriptor 丢弃无锚点引用，保留项 100% 有锚点（缺锚点不进交付物）。"""
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import sanitize_draft_descriptor

    descriptor = sanitize_draft_descriptor(
        {
            "draft_id": "d1",
            "structure_skeleton": ["一、基本案情", "二、参考类案"],
            "candidate_refs": [
                # 有锚点 → 保留。
                {
                    "case_id": "c1",
                    "case_number": "no-1",
                    "source_anchors": [{"case_id": "c1", "source_chunk_id": "c1_ch0"}],
                },
                # 无锚点 → 丢弃。
                {"case_id": "c2", "case_number": "no-2"},
                # 锚点不完整 → 丢弃。
                {"case_id": "c3", "source_anchors": [{"case_id": "c3"}]},
            ],
        }
    )
    assert len(descriptor.candidate_refs) == 1, "无锚点 / 锚点不完整的引用应被丢弃"
    for ref in descriptor.candidate_refs:
        assert ref.source_anchors, "保留的引用必须 100% 有锚点"
        for anchor in ref.source_anchors:
            assert anchor.get("case_id") and anchor.get("source_chunk_id")


def test_drafting_sanitize_drops_anchorless_statute_refs_runtime():
    """运行时：statute_refs 缺锚点丢弃；禁止键仍 fail-closed 抛错。"""
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import ContractViolationError, sanitize_draft_descriptor

    # 无 statute_anchors → 丢弃（不抛错）。
    descriptor = sanitize_draft_descriptor(
        {
            "draft_id": "d1",
            "structure_skeleton": ["一、参考法条"],
            "statute_refs": [{"statute_id": "s1", "law_name": "刑法"}],
        }
    )
    assert descriptor.statute_refs == [], "无锚点的 statute_ref 应被丢弃"

    # 法条引用夹带模型生成条文型键 → fail-closed 抛错。
    with pytest.raises(ContractViolationError):
        sanitize_draft_descriptor(
            {
                "draft_id": "d1",
                "structure_skeleton": ["一"],
                "statute_refs": [
                    {
                        "statute_id": "s1",
                        "law_name": "刑法",
                        "statute_anchors": [{"text_id": "law::s1"}],
                        "generated_article": "x",
                    }
                ],
            }
        )


# ===========================================================================
# 守门 3：持久层零正文 —— drafting 持久层模型/表无正文列；写白名单无正文；禁用键显式拒绝。
# ===========================================================================
def test_drafting_persist_model_has_no_body_columns():
    """draft_descriptor 表字段不含起草正文 / 裁判正文 / 结论 / 胜负 / 原始案情列（静态 AST）。"""
    src = (DRAFTING_DIR / "models.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # 收集 DraftDescriptorRow 的字段名（类体内带注解的赋值）。
    field_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "DraftDescriptorRow":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.append(stmt.target.id)
    assert field_names, "未解析到 DraftDescriptorRow 字段（结构异常）"
    forbidden = set(
        FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_JUDGMENT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
        + FORBIDDEN_PII_TOKENS
        + FORBIDDEN_CREDENTIAL_TOKENS
    )
    leaked = set(field_names) & forbidden
    assert not leaked, f"draft_descriptor 表出现正文 / 结论 / PII / 凭据列：{leaked}"
    # 正向白名单：只允许元数据 / 引用 / 骨架(标题) / 短字段 / 结构化状态列。
    allowed = {
        "draft_id", "owner_user_id", "team_id", "visibility",
        "structure_skeleton", "candidate_refs", "statute_refs",
        "note", "tag", "status", "reason_code", "created_at", "updated_at",
    }
    unexpected = set(field_names) - allowed
    assert not unexpected, f"draft_descriptor 表出现白名单外的列：{unexpected}"


def test_drafting_store_write_whitelist_and_forbidden_keys():
    """持久层写白名单不含正文键；禁用键集合覆盖正文 / 结论 / 凭据（静态断言）。"""
    pytest.importorskip("sqlmodel")
    from app.drafting.store import DRAFT_WRITE_ALLOWED_KEYS, DRAFT_FORBIDDEN_PERSIST_KEYS

    body_like = set(FORBIDDEN_DRAFT_BODY_TOKENS) | set(FORBIDDEN_JUDGMENT_BODY_TOKENS)
    leaked = DRAFT_WRITE_ALLOWED_KEYS & body_like
    assert not leaked, f"持久层写白名单泄露正文键：{leaked}"
    # 禁用键集合应至少覆盖核心起草正文 / 裁判正文 / 凭据键。
    for token in ("draft_body", "generated_text", "chunk_text", "judgment_text",
                  "raw_case", "raw_query", "password", "token"):
        assert token in DRAFT_FORBIDDEN_PERSIST_KEYS, f"持久层禁用键缺少 {token}"


def test_drafting_store_rejects_forbidden_persist_keys_runtime():
    """运行时：DraftStore._sanitize_persist_payload 对禁用键 / 未知键 fail-closed（host 权威）。"""
    pytest.importorskip("sqlmodel")
    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine
    from app.drafting.store import DraftStore

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    store = DraftStore(engine)
    # 禁用正文键 → ValueError。
    with pytest.raises(ValueError):
        store._sanitize_persist_payload({"draft_body": "x"})
    # 未知键 → ValueError。
    with pytest.raises(ValueError):
        store._sanitize_persist_payload({"unknown_field": "x"})


def test_drafting_logs_do_not_emit_body_or_note_fulltext():
    """drafting 日志只写 hash / 计数 / reason_code / note 元信息；不写正文 / note 全文。"""
    router_src = _code_without_docstrings(DRAFTING_DIR / "router.py")
    service_src = _code_without_docstrings(DRAFTING_DIR / "service.py")
    for src, name in ((router_src, "router.py"), (service_src, "service.py")):
        for token in ("draft_body=%", "note=%s", "raw_case", "chunk_text=%", "judgment_text=%"):
            assert token not in src, f"{name} 日志疑似写入正文 / note 全文：{token}"
        # note 入日志须经脱敏元信息函数（长度 + hash），不得直接打印 note 全文。
    assert "note_log_meta" in router_src, "router 日志应经 note_log_meta 脱敏（不写 note 全文）"


# ===========================================================================
# 守门 4：导出守门 —— 强制免责头、导出内容无正文 / 无胜负 / 无结论、无锚点引用不进导出。
# ===========================================================================
def test_drafting_export_module_exists():
    assert DRAFTING_EXPORT_TS.exists(), "E6-4 导出模块 draftingExport.ts 缺失"


def test_drafting_export_forces_disclaimer_header():
    """导出模块强制注入免责头，且免责头含关键语义（不构成法律意见 / 人工复核）。"""
    src = DRAFTING_EXPORT_TS.read_text(encoding="utf-8")
    assert "DRAFT_EXPORT_DISCLAIMER_LINES" in src, "导出未定义强制免责头常量"
    # markdown / text 两条生成路径都注入免责头。
    assert src.count("DRAFT_EXPORT_DISCLAIMER_LINES") >= 3, (
        "免责头应在常量定义 + markdown + text 两条导出路径均被注入"
    )
    for fragment in EXPORT_DISCLAIMER_REQUIRED_FRAGMENTS:
        assert fragment in src, f"导出免责头缺关键语义：{fragment}"


def test_drafting_export_does_not_read_article_text():
    """导出收敛法条引用时绝不读取 article_text（条文正文回法条页核验，不沉淀进导出）。"""
    src = DRAFTING_EXPORT_TS.read_text(encoding="utf-8")
    # collectStatuteRows 的导出行类型 ExportStatuteRow 不含 article_text；
    # 且 .article_text 不出现在导出取值表达式中（仅注释/红线说明可提及）。
    # 去注释后扫描可执行取值。
    code_lines = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        code = line.split("//", 1)[0]
        code_lines.append(code)
    code = "\n".join(code_lines)
    assert "ref.article_text" not in code and ".article_text" not in code, (
        "导出可执行代码读取了 article_text（条文正文不得进导出）"
    )


def test_drafting_export_drops_anchorless_refs():
    """导出收敛只取带锚点引用（candidateRefHasAnchor / statuteRefHasAnchor 过滤）。"""
    src = DRAFTING_EXPORT_TS.read_text(encoding="utf-8")
    assert "candidateRefHasAnchor" in src and "statuteRefHasAnchor" in src
    assert ".filter(candidateRefHasAnchor)" in src, "类案引用导出应过滤无锚点项"
    assert ".filter(statuteRefHasAnchor)" in src, "法条引用导出应过滤无锚点项"


def test_drafting_export_has_forbidden_phrase_guard():
    """导出模块定义禁用绝对话术 / 胜负话术清单并提供校验函数（不导出胜负 / 覆盖承诺）。"""
    src = DRAFTING_EXPORT_TS.read_text(encoding="utf-8")
    assert "FORBIDDEN_EXPORT_PHRASES" in src
    assert "containsForbiddenExportPhrase" in src
    for phrase in ("胜诉概率", "已查全"):
        assert phrase in src, f"导出禁用话术清单缺 {phrase}"


def test_drafting_export_executable_code_has_no_body_tokens():
    """导出模块可执行代码（去注释）不搬运裁判正文 / 起草正文型字段。"""
    src = DRAFTING_EXPORT_TS.read_text(encoding="utf-8")
    code_lines = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        code_lines.append(line.split("//", 1)[0])
    code = "\n".join(code_lines)
    # 导出行类型字段名扫描：裁判 / 起草正文型 token 不应作为取值字段出现。
    for token in ("chunk_text", "judgment_text", "draft_body", "generated_text",
                  "summary_text", "matched_text", "raw_case"):
        assert token not in code, f"导出可执行代码出现正文型字段：{token}"


# ===========================================================================
# 守门 7：CandidateRef/StatuteRef 仍零正文 —— 被 drafting 引用后字段不增删、无正文。
# ===========================================================================
def test_drafting_candidate_ref_fields_equal_e1_whitelist_runtime():
    """运行时：DraftCandidateRef 字段集 = E-1 CandidateRef 白名单七字段（不因被引用而增删）。"""
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import DraftCandidateRef

    fields = set(DraftCandidateRef.model_fields)
    assert fields == WHITELIST_CANDIDATE, (
        f"DraftCandidateRef 字段偏离 E-1 CandidateRef 白名单：{fields ^ WHITELIST_CANDIDATE}"
    )
    assert DraftCandidateRef.model_config.get("extra") == "forbid"
    leaked = fields & (set(FORBIDDEN_JUDGMENT_BODY_TOKENS) | set(FORBIDDEN_DRAFT_BODY_TOKENS))
    assert not leaked, f"DraftCandidateRef 泄露正文型字段：{leaked}"


def test_drafting_statute_ref_fields_equal_e5_whitelist_runtime():
    """运行时：drafting 引用的 StatuteRef 字段集 = E5-1 白名单（不因被引用而增删）。"""
    pytest.importorskip("pydantic_core")
    from app.kernel.guardrails import StatuteRef

    fields = set(StatuteRef.model_fields)
    assert fields == WHITELIST_STATUTE_REF, (
        f"StatuteRef 字段偏离 E5-1 白名单：{fields ^ WHITELIST_STATUTE_REF}"
    )
    assert StatuteRef.model_config.get("extra") == "forbid"


def test_drafting_response_view_carries_no_body_fields():
    """drafting 响应视图（schemas）字段集不含起草正文 / 裁判正文 / 结论型键（静态 AST）。"""
    src = (DRAFTING_DIR / "schemas.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = set(
        FORBIDDEN_DRAFT_BODY_TOKENS
        + FORBIDDEN_JUDGMENT_BODY_TOKENS
        + FORBIDDEN_OUTCOME_TOKENS
    )
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.target.id in forbidden:
                        offenders.append(f"{node.name}.{stmt.target.id}")
    assert not offenders, f"drafting 响应视图泄露正文 / 结论型字段：{offenders}"


# ===========================================================================
# 守门 8：casebook 已在 E7-2 落地 —— ENABLE_CASEBOOK 默认 false；
#         E7-2 后仅 intake + statute + drafting + casebook 四个合法产品包。
# ===========================================================================
def test_casebook_package_exists_after_e7_2():
    assert (APP_DIR / "casebook").exists(), "casebook 产品包必须存在（E7-2 已建）"


def test_e6_allowed_product_packages_only():
    """E7-2 后仅 intake + statute + drafting + casebook 四个产品包。"""
    existing = {p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()}
    unexpected = existing - E6_ALLOWED_PRODUCT_PACKAGES
    assert not unexpected, (
        "出现不应存在的产品包（E7-2 后仅允许 intake + statute + drafting + casebook）："
        + ", ".join(sorted(unexpected))
    )
    assert "drafting" in existing, "drafting 产品包必须存在（E6-2 已建）"
    assert "casebook" in existing, "casebook 产品包必须存在（E7-2 已建）"


def test_enable_casebook_and_drafting_default_false():
    """ENABLE_CASEBOOK / ENABLE_DRAFTING 默认 false（运行时 + 配置声明双重）。"""
    text = CONFIG_PY.read_text(encoding="utf-8")
    assert "ENABLE_CASEBOOK: bool = False" in text
    assert "ENABLE_DRAFTING: bool = False" in text
    deps = pytest.importorskip("pydantic_settings", reason="config runtime needs pydantic-settings")
    del deps
    from app.core.config import Settings

    s = Settings(_env_file=None)
    assert s.ENABLE_DRAFTING is False
    assert s.ENABLE_CASEBOOK is False


def test_all_product_and_ai_flags_default_false():
    """7 产品 flag + AI 抽取 flag + 加权 rerank 默认全 false（任一 true 即 NO_GO）。"""
    deps = pytest.importorskip("pydantic_settings", reason="config runtime needs pydantic-settings")
    del deps
    from app.core.config import Settings

    s = Settings(_env_file=None)
    for flag in (
        "ENABLE_ECOSYSTEM",
        "ENABLE_INTAKE",
        "ENABLE_STATUTE_SEARCH",
        "ENABLE_DRAFTING",
        "ENABLE_CASEBOOK",
        "ENABLE_INTAKE_AI_EXTRACTION",
        "ENABLE_WEIGHTED_RERANK",
    ):
        assert getattr(s, flag) is False, f"{flag} 默认必须为 false"


# ===========================================================================
# 守门 9：多租户/鉴权 —— drafting 端点对象级鉴权 + 租户隔离 + 默认 private（与 M5 同款）。
# ===========================================================================
def test_drafting_default_visibility_private():
    """drafting 持久层默认可见性 = private（与 M5 同款）。"""
    src = (DRAFTING_DIR / "models.py").read_text(encoding="utf-8")
    assert 'VISIBILITY_PRIVATE = "private"' in src
    assert "default=VISIBILITY_PRIVATE" in src, "visibility 列默认须为 private"


def test_drafting_store_enforces_tenant_isolation():
    """持久层读取强制租户过滤（_tenant_clause），无『无过滤读取』对外路径（静态）。"""
    src = (DRAFTING_DIR / "store.py").read_text(encoding="utf-8")
    assert "_tenant_clause" in src, "store 须有租户过滤子句"
    # list_visible / get_visible 两个读取入口都拼接 _tenant_clause。
    assert "def list_visible" in src and "def get_visible" in src
    assert src.count("_tenant_clause(ctx)") >= 2, "读取入口须强制拼接租户过滤"
    # 写入 / 更新前做租户一致性校验。
    assert "_assert_write_within_tenant" in src
    # 更新仅 owner（owner_user_id == ctx.owner_user_id）。
    assert "DraftDescriptorRow.owner_user_id == ctx.owner_user_id" in src


def test_drafting_endpoints_require_login_and_gated():
    """drafting 四端点均需登录 + ENABLE_DRAFTING gated（静态：每个端点都有门控 + 鉴权调用）。"""
    src = (DRAFTING_DIR / "router.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    endpoint_funcs = []
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
                    endpoint_funcs.append(node)
    assert len(endpoint_funcs) == 4, f"drafting 应注册 4 个端点，实际 {len(endpoint_funcs)}"
    for fn in endpoint_funcs:
        body_src = ast.get_source_segment(src, fn) or ""
        assert "_enabled()" in body_src, f"端点 {fn.name} 缺 ENABLE_DRAFTING 门控"
        assert "_require_login" in body_src, f"端点 {fn.name} 缺登录鉴权"


def test_drafting_disabled_returns_403_runtime():
    """运行时：ENABLE_DRAFTING=false 时端点 403 安全降级，不泄露内部信息（host 权威）。"""
    pytest.importorskip("fastapi")
    pytest.importorskip("sqlmodel")
    import importlib
    from fastapi.testclient import TestClient
    from app.core.config import Settings
    from app.main import app

    drafting_router_mod = importlib.import_module("app.drafting.router")
    monkey = Settings(DEEPSEEK_API_KEY="k", ENABLE_DRAFTING=False)
    original = drafting_router_mod.settings
    drafting_router_mod.settings = monkey
    drafting_router_mod.set_drafting_service_for_test(None)
    try:
        resp = TestClient(app).post(
            "/api/drafting/drafts",
            json={"structure_skeleton": ["一、基本案情"]},
        )
    finally:
        drafting_router_mod.settings = original
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "DRAFTING_DISABLED"


# ===========================================================================
# include_router=16（intake + statute + drafting + casebook）；casebook 端点只在 casebook 产品包内定义。
# ===========================================================================
def test_include_router_count_is_16():
    source = MAIN_PY.read_text(encoding="utf-8")
    count = source.count("app.include_router(")
    assert count == 16, f"include_router 数必须为 16（E7-2 基线），实际 {count}"
    assert "app.include_router(drafting_router)" in source
    assert "app.include_router(intake_router)" in source
    assert "app.include_router(statute_router)" in source
    assert "app.include_router(casebook_router)" in source


def test_no_casebook_endpoint_fragment_inside_drafting():
    offenders: list[str] = []
    for path in _drafting_py_files():
        source = path.read_text(encoding="utf-8")
        for fragment in ("/api/casebook", "casebook_router"):
            if fragment in source:
                offenders.append(f"{path.name}: {fragment}")
    assert not offenders, "drafting 包内出现 casebook 端点片段：" + "; ".join(offenders)


# ===========================================================================
# 隐私扫描：drafting 全包可执行代码 + fixture / docs 不承载正文 / PII / 凭据 / 长正文。
# ===========================================================================
def test_drafting_executable_code_has_no_pii_or_credential_tokens():
    offenders: list[str] = []
    forbidden = FORBIDDEN_PII_TOKENS + FORBIDDEN_CREDENTIAL_TOKENS
    for path in _drafting_py_files():
        code = _code_without_docstrings_and_denylists(path)
        for token in forbidden:
            if token in code:
                offenders.append(f"{path.name}: {token}")
    # session_token 在鉴权解析处可能合法出现（Bearer 解析），单独豁免该上下文。
    offenders = [o for o in offenders if "session_token" not in o]
    assert not offenders, (
        "drafting 可执行代码出现 PII / 凭据型键（红线）：" + "; ".join(offenders)
    )


def test_e6_test_fixtures_have_no_long_body_text():
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.glob("test_e6_*.py")):
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E6 fixture 疑似长正文：" + "; ".join(offenders)


def test_e6_docs_have_no_long_body_text():
    if not DOCS_DEV_DIR.exists():
        pytest.skip("docs/development absent")
    offenders: list[str] = []
    targets = sorted(DOCS_DEV_DIR.glob("e6-*.md")) + sorted(DOCS_DEV_DIR.glob("e6-*.json"))
    for path in targets:
        length = _max_contiguous_cjk(path.read_text(encoding="utf-8"))
        if length > MAX_CONTIGUOUS_CJK:
            offenders.append(f"{path.name}: {length} cjk chars")
    assert not offenders, "E6 docs 疑似长正文：" + "; ".join(offenders)
