"""E-2a 共享内核 import 边界守门测试（静态 AST 扫描，零运行时副作用）。

把文档 16 §3 / 文档 17 §2.2 的依赖方向变成可执行断言：
(a) 内核不得反向 import 任何产品包命名空间（intake/statute/drafting/casebook）。
(b) 未来产品能力包之间不得互相 import（当前不存在 → 断言「不存在即通过」+ 规则预置）。
(c) 非内核消费代码（检索链路 api/search.py）不得绕过公开面深引内核内部私有子模块；
    现有 cases.py / health.py 既有深引为 grandfather 基线，只禁「新增」深引。

实现用 ast 静态解析源码，不 import 任何业务模块，避免触发 DB / 网络 / 模型副作用，
也不会因 flag 默认值或环境依赖而漂移。
"""
from __future__ import annotations

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# 内核四组成员（文档 17 §2.1 冻结口径）。
KERNEL_GROUPS = {
    "rag": ("retrieval", "rerank", "query_processing", "summary"),
    "identity": ("account", "team", "permission", "sharing"),
    "guardrails": ("contracts",),
    "data": ("pipeline", "case_store"),
}
KERNEL_TOP_PACKAGES = tuple(
    pkg for group in KERNEL_GROUPS.values() for pkg in group
)

# 未来产品能力包命名空间（E-4~E-7 才建；本步预置规则）。
PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")

# 检索链路允许的内核公开面前缀。
KERNEL_SURFACE_PREFIXES = (
    "app.kernel",
    "app.kernel.rag",
    "app.kernel.identity",
    "app.kernel.guardrails",
    "app.kernel.data",
)

# Grandfather 基线：E-2a 之前 api/ 下已存在的内核深引消费方（路径相对 APP_DIR）。
# E-2a 只收敛检索主链路 search.py（已走公开面，故不在名单内）；其余 M5 身份/商业化
# 端点的既有深引不在本步收敛范围（留待 E-2b shim / 后续步骤），此处冻结为基线，
# 只断言「不新增」绕过公开面的深引。
GRANDFATHER_DEEP_IMPORTS = {
    "api/auth.py",
    "api/billing.py",
    "api/bulk_import.py",
    "api/cases.py",
    "api/health.py",
    "api/permission.py",
    "api/sharing.py",
    "api/team.py",
}


def _iter_imports(path: Path):
    """产出 (module_str, node) ——module_str 为 import 的目标模块全名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # 相对 import：用包路径还原（本仓内核/产品包均用绝对 app.* import，
                # 相对 import 仅出现在包内部，归属由所在文件决定，不影响跨包断言）。
                yield (node.module or ""), node
            else:
                yield (node.module or ""), node


def _py_files(root: Path):
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


# ---------------------------------------------------------------------------
# (a) 内核不得反向 import 任何产品包命名空间。
# ---------------------------------------------------------------------------
def test_kernel_surface_does_not_import_product_packages():
    kernel_dir = APP_DIR / "kernel"
    offending: list[str] = []
    for path in _py_files(kernel_dir):
        for module, _node in _iter_imports(path):
            for product in PRODUCT_PACKAGES:
                if module == f"app.{product}" or module.startswith(f"app.{product}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "app/kernel 公开面反向 import 了产品包（违反单向依赖）：" + "; ".join(offending)
    )


def test_kernel_member_packages_do_not_import_product_packages():
    offending: list[str] = []
    for pkg in KERNEL_TOP_PACKAGES:
        pkg_dir = APP_DIR / pkg
        if not pkg_dir.exists():
            continue
        for path in _py_files(pkg_dir):
            for module, _node in _iter_imports(path):
                for product in PRODUCT_PACKAGES:
                    if module == f"app.{product}" or module.startswith(f"app.{product}."):
                        rel = path.relative_to(APP_DIR)
                        offending.append(f"{rel}: {module}")
    assert not offending, (
        "内核成员包反向 import 了产品包（违反单向依赖）：" + "; ".join(offending)
    )


# ---------------------------------------------------------------------------
# (b) 产品能力包命名空间互不 import；当前不存在即通过（规则预置）。
# ---------------------------------------------------------------------------
def test_product_packages_absent_or_do_not_cross_import():
    existing = [p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()]
    if not existing:
        # 当前 E-2a 阶段不存在任何产品包 → 规则预置成立，断言「不存在即通过」。
        assert existing == [], "E-2a 阶段不应存在产品包"
        return
    # 一旦未来产品包落地，立即生效：任一产品包不得 import 另一产品包。
    offending: list[str] = []
    for pkg in existing:
        for path in _py_files(APP_DIR / pkg):
            for module, _node in _iter_imports(path):
                for other in PRODUCT_PACKAGES:
                    if other == pkg:
                        continue
                    if module == f"app.{other}" or module.startswith(f"app.{other}."):
                        offending.append(f"{pkg} -> {module}")
    assert not offending, (
        "产品能力包之间互相 import（违反能力包只依赖内核不互相依赖）：" + "; ".join(offending)
    )


# ---------------------------------------------------------------------------
# (c) 检索消费链路只经公开面引用内核；search.py 不得深引内核内部子模块。
# ---------------------------------------------------------------------------
def _deep_kernel_imports(path: Path) -> list[str]:
    """返回该文件对内核成员包的『深引内部子模块』import（绕过公开面）。"""
    deep: list[str] = []
    for module, _node in _iter_imports(path):
        if not module.startswith("app."):
            continue
        if module.startswith("app.kernel"):
            continue  # 走公开面，允许
        parts = module.split(".")
        # app.<pkg> 或 app.<pkg>.<sub...>
        if len(parts) >= 2 and parts[1] in KERNEL_TOP_PACKAGES:
            # 深引判定：引用到内核内部子模块（app.<pkg>.<sub>），绕过 app.kernel 公开面。
            if len(parts) >= 3:
                deep.append(module)
            else:
                # 直接 import 包顶层（app.retrieval）也属绕过公开面消费内核。
                deep.append(module)
    return deep


def test_search_chain_consumes_kernel_via_surface_only():
    search_py = APP_DIR / "api" / "search.py"
    deep = _deep_kernel_imports(search_py)
    assert not deep, (
        "api/search.py 绕过 app.kernel 公开面深引内核内部子模块（E-2a 应已收敛）："
        + "; ".join(deep)
    )


def test_no_new_deep_kernel_imports_beyond_grandfather():
    """除 grandfather 基线外，api/ 下消费方不得新增绕过公开面的内核深引。"""
    api_dir = APP_DIR / "api"
    new_offenders: list[str] = []
    for path in _py_files(api_dir):
        rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
        if rel in GRANDFATHER_DEEP_IMPORTS:
            continue
        deep = _deep_kernel_imports(path)
        if deep:
            new_offenders.append(f"{rel}: {', '.join(deep)}")
    assert not new_offenders, (
        "消费方新增了绕过公开面的内核深引（只允许 grandfather 基线）："
        + "; ".join(new_offenders)
    )


def test_grandfather_files_still_exist():
    """grandfather 名单文件须存在，避免名单悄悄失效后漏掉新增深引。"""
    for rel in GRANDFATHER_DEEP_IMPORTS:
        assert (APP_DIR / rel).exists(), f"grandfather 名单文件缺失：{rel}"


# ---------------------------------------------------------------------------
# 公开面完整性：四组成员声明与公开面文件齐备（纯结构断言，走 AST/文件存在性，不 import）。
# ---------------------------------------------------------------------------
def test_kernel_surface_files_present():
    kernel_dir = APP_DIR / "kernel"
    # 顶层公开面必须存在。
    assert (kernel_dir / "__init__.py").exists(), "kernel 公开面文件缺失：__init__.py"
    # 四组公开面：E-2a 为单文件 <group>.py；E-2b 物理迁移后为包目录 <group>/__init__.py。
    # 两种形态任一存在即视为公开面齐备（纯结构断言，不 import）。
    for group in ("rag", "identity", "guardrails", "data"):
        as_module = (kernel_dir / f"{group}.py").exists()
        as_package = (kernel_dir / group / "__init__.py").exists()
        assert as_module or as_package, (
            f"kernel 公开面缺失：既无 {group}.py 也无 {group}/__init__.py"
        )


# ===========================================================================
# E3-4 追加：E3 消费边界规则（append-only，不放宽上面任何 E-2 既有规则）。
#
# E-2 规则锁定「内核单向依赖 + 检索链路只经公开面消费内核」；E3 在其之上把
# 「内部检索服务为后续产品唯一允许的检索消费面」固化为可执行纪律：检索执行原语
# （VectorRetrievalService / FactSimilarityReranker / merge_case_candidates /
# split_low_confidence_candidates）只允许在内核内部与已收敛的 search.py 出现，
# api/ 下其它消费方不得绕过 InternalSearchService 直引这些原语。
# 纯 AST 静态扫描，不 import 业务运行时，不依赖 flag / 环境。
# ===========================================================================

# 检索执行原语（绕过内部服务即视为越界消费）。
E3_SEARCH_EXECUTION_PRIMITIVES = (
    "VectorRetrievalService",
    "FactSimilarityReranker",
    "merge_case_candidates",
    "split_low_confidence_candidates",
)


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


def test_e3_api_consumers_do_not_bypass_internal_search_service():
    """除已收敛的 search.py 外，api/ 下消费方不得直引检索执行原语绕过内部服务。"""
    api_dir = APP_DIR / "api"
    offenders: list[str] = []
    for path in _py_files(api_dir):
        if path.name == "search.py":
            continue  # search.py 经 InternalSearchService.execute() 执行，属唯一权威消费方
        leaked = [n for n in _imported_names(path) if n in E3_SEARCH_EXECUTION_PRIMITIVES]
        if leaked:
            rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
            offenders.append(f"{rel}: {', '.join(leaked)}")
    assert not offenders, (
        "api/ 消费方绕过 InternalSearchService 直引检索执行原语（E3 应已收敛）："
        + "; ".join(offenders)
    )


def test_e3_search_api_references_internal_search_service():
    """/api/search 主路径必须引用并经 InternalSearchService 执行（单一权威）。"""
    search_py = APP_DIR / "api" / "search.py"
    source = search_py.read_text(encoding="utf-8")
    assert "InternalSearchService" in source, "/api/search 未引用 InternalSearchService"
    assert ".execute(" in source, "/api/search 未经内部服务 execute() 执行"


def test_e3_internal_service_module_does_not_import_product_packages():
    """E3 internal service/contract modules must not import any product package."""
    modules = (
        APP_DIR / "kernel" / "rag" / "internal_search_service.py",
        APP_DIR / "kernel" / "rag" / "internal_search_contracts.py",
    )
    offending = []
    for path in modules:
        for module, _node in _iter_imports(path):
            for product in PRODUCT_PACKAGES:
                if module == f"app.{product}" or module.startswith(f"app.{product}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "E3 internal service/contract module imports product package: "
        + "; ".join(offending)
    )


# ===========================================================================
# E4-5 追加：录入端 intake 产品包消费边界（append-only，不放宽任何 E-2/E-3 规则）。
#
# E-2 锁「内核单向依赖」、E3 锁「检索执行原语单一权威」；E4 在其上把
# 「intake 是录入端唯一允许的检索消费产品包，且只经 app.kernel.rag 公开面消费，
# 产品包之间互不 import，AI 增强子开关无 on 路径」固化为可执行纪律。
# 仍是纯 AST 静态扫描，不 import 业务运行时，不依赖 flag / 环境。
# 详细的端点 gated / schema 白名单 / CandidateRef 零正文等运行时断言见
# tests/test_e4_intake_boundaries.py；此处只追加跨包 import 方向的静态守门。
# ===========================================================================

# intake 只允许消费的内核顶层公开面（深引内部子模块即越界）。
E4_ALLOWED_KERNEL_SURFACES = ("app.kernel", "app.kernel.rag", "app.kernel.guardrails")
# AI 增强子开关：本期无 on 路径，intake 可执行代码不得引用它（仅 docstring/注释可提及）。
E4_AI_EXTRACTION_FLAG = "ENABLE_INTAKE_AI_EXTRACTION"


def _intake_py_files():
    intake_dir = APP_DIR / "intake"
    if not intake_dir.exists():
        return []
    return sorted(intake_dir.glob("*.py"))


def test_e4_intake_does_not_import_other_product_packages():
    """intake 不得 import 其它产品包（statute/drafting/casebook）——产品包互不 import。"""
    offending: list[str] = []
    for path in _intake_py_files():
        for module, _node in _iter_imports(path):
            for other in ("statute", "drafting", "casebook"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "intake import 了其它产品包（产品包互不 import）：" + "; ".join(offending)
    )


def test_e4_intake_consumes_only_kernel_public_surface():
    """intake 对内核只能消费顶层公开面，禁止深引 retrieval/rerank/summary 等内部子模块。"""
    offending: list[str] = []
    for path in _intake_py_files():
        for module, _node in _iter_imports(path):
            if module.startswith("app.kernel") and module not in E4_ALLOWED_KERNEL_SURFACES:
                offending.append(f"{path.name}: {module}")
            # 直引内核成员顶层包（绕过 app.kernel 公开面）也是越界。
            parts = module.split(".")
            if (
                len(parts) >= 2
                and parts[0] == "app"
                and parts[1] in KERNEL_TOP_PACKAGES
            ):
                offending.append(f"{path.name}: {module}")
    assert not offending, (
        "intake 绕过 app.kernel 公开面深引内核内部（应只走 rag/guardrails 公开面）："
        + "; ".join(offending)
    )


def test_e4_intake_does_not_reference_ai_extraction_flag_in_imports():
    """intake 不得 import 任何与 AI 增强子开关相关的运行时模块（本期无 on 路径）。

    这里只做 import 方向的静态守门；更细的『可执行代码不引用 flag 字符串』断言在
    tests/test_e4_intake_boundaries.py。两处互补，均不放宽 E-2/E-3 既有规则。
    """
    forbidden_runtime = ("retrieval", "rerank", "summary", "query_processing")
    offending: list[str] = []
    for path in _intake_py_files():
        for module, _node in _iter_imports(path):
            for mod in forbidden_runtime:
                if module == f"app.{mod}" or module.startswith(f"app.{mod}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "intake 直引检索运行时底层（应经 app.kernel.rag InternalSearchService）："
        + "; ".join(offending)
    )


# ===========================================================================
# E5-6 追加：法条检索 statute 产品包消费边界（append-only，不放宽任何 E-2/E-3/E-4 规则）。
#
# E-2 锁「内核单向依赖」、E3 锁「检索执行原语单一权威」、E4 锁「intake 只经公开面消费」；
# E5 在其上把「statute 是法条检索唯一允许的检索消费产品包，且只经 app.kernel.rag
# StatuteSearchService（内核公开面）消费检索能力；产品包之间互不 import；statute 不深引
# retrieval/rerank/summary/query_processing；当前仅 intake + statute 两个产品包」
# 固化为可执行纪律。仍是纯 AST 静态扫描，不 import 业务运行时，不依赖 flag / 环境。
# 端点 gated / schema 白名单 / StatuteRef 锚点 / CandidateRef 零正文等运行时断言见
# tests/test_e5_statute_boundaries.py；此处只追加跨包 import 方向的静态守门。
# ===========================================================================

# statute 只允许消费的内核顶层公开面（深引内部子模块即越界）。
E5_ALLOWED_KERNEL_SURFACES = ("app.kernel", "app.kernel.rag", "app.kernel.guardrails")
# E7-2 reconciliation：E6-2 已合法落地 drafting；E7-2 已合法落地 casebook。
# 这里保留 E5 守门函数名，但产品包清单按当前 E7-2 基线验收。
E5_ALLOWED_PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")


def _statute_py_files():
    statute_dir = APP_DIR / "statute"
    if not statute_dir.exists():
        return []
    return sorted(statute_dir.glob("*.py"))


def test_e5_statute_does_not_import_other_product_packages():
    """statute 不得 import 其它产品包（intake/drafting/casebook）——产品包互不 import。"""
    offending: list[str] = []
    for path in _statute_py_files():
        for module, _node in _iter_imports(path):
            for other in ("intake", "drafting", "casebook"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "statute import 了其它产品包（产品包互不 import）：" + "; ".join(offending)
    )


def test_e5_statute_not_imported_by_other_product_packages():
    """statute 不得被其它产品包 import（intake 等）——产品包互不 import（反向）。"""
    offending: list[str] = []
    for other in ("intake", "drafting", "casebook"):
        other_dir = APP_DIR / other
        if not other_dir.exists():
            continue
        for path in _py_files(other_dir):
            for module, _node in _iter_imports(path):
                if module == "app.statute" or module.startswith("app.statute."):
                    rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                    offending.append(f"{rel}: {module}")
    assert not offending, (
        "其它产品包反向 import 了 statute（产品包互不 import）：" + "; ".join(offending)
    )


def test_e5_statute_consumes_only_kernel_public_surface():
    """statute 对内核只能消费顶层公开面，禁止深引 retrieval/rerank/summary 等内部子模块。"""
    offending: list[str] = []
    for path in _statute_py_files():
        for module, _node in _iter_imports(path):
            if module.startswith("app.kernel") and module not in E5_ALLOWED_KERNEL_SURFACES:
                offending.append(f"{path.name}: {module}")
            # 直引内核成员顶层包（绕过 app.kernel 公开面）也是越界。
            parts = module.split(".")
            if (
                len(parts) >= 2
                and parts[0] == "app"
                and parts[1] in KERNEL_TOP_PACKAGES
            ):
                offending.append(f"{path.name}: {module}")
    assert not offending, (
        "statute 绕过 app.kernel 公开面深引内核内部（应只走 rag/guardrails 公开面）："
        + "; ".join(offending)
    )


def test_e5_statute_does_not_deep_import_retrieval_runtime():
    """statute 不得直引检索运行时底层（应经 app.kernel.rag StatuteSearchService）。"""
    forbidden_runtime = ("retrieval", "rerank", "summary", "query_processing")
    offending: list[str] = []
    for path in _statute_py_files():
        for module, _node in _iter_imports(path):
            for mod in forbidden_runtime:
                if module == f"app.{mod}" or module.startswith(f"app.{mod}."):
                    offending.append(f"{path.name}: {module}")
                # app.kernel.rag.<runtime> 形态的深引同样越界（绕过 StatuteSearchService）。
                if module.startswith(f"app.kernel.rag.{mod}"):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "statute 直引检索运行时底层（应经 app.kernel.rag StatuteSearchService）："
        + "; ".join(offending)
    )


def test_e5_reconciled_product_packages_match_e6_baseline():
    """E7-2 后 E 系列允许 intake + statute + drafting + casebook。"""
    existing = [p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()]
    unexpected = [p for p in existing if p not in E5_ALLOWED_PRODUCT_PACKAGES]
    assert not unexpected, (
        "出现了不应存在的产品包（E7-2 后仅允许 intake + statute + drafting + casebook）："
        + ", ".join(unexpected)
    )


def test_e5_statute_search_service_does_not_import_product_packages():
    """内核法条检索服务（公开面消费方）不得反向 import 任何产品包。"""
    module_path = APP_DIR / "kernel" / "rag" / "statute_search_service.py"
    offending: list[str] = []
    for module, _node in _iter_imports(module_path):
        for product in PRODUCT_PACKAGES:
            if module == f"app.{product}" or module.startswith(f"app.{product}."):
                offending.append(f"{module_path.name}: {module}")
    assert not offending, (
        "内核法条检索服务 import 了产品包（违反单向依赖）：" + "; ".join(offending)
    )


# ===========================================================================
# E6-5 追加：文书工作台 drafting 产品包消费边界（append-only，不放宽任何 E-2/E-3/E-4/E-5 规则）。
#
# E-2 锁「内核单向依赖」、E3 锁「检索执行原语单一权威」、E4 锁「intake 只经公开面消费」、
# E5 锁「statute 只经 StatuteSearchService 公开面消费」；E6 在其上把
# 「drafting 是文书工作台唯一允许的『组装』产品包，只依赖 app.kernel 公开面
# （guardrails 的 DraftDescriptor/sanitize + identity 的 TenantContext），不深引
# retrieval/rerank/summary/query_processing，不复制检索主路径；产品包之间互不 import
# （drafting 不 import intake/statute/casebook，它们也不 import drafting）；
# 内核不反向 import drafting；E7-2 后合法产品包 = intake + statute + drafting + casebook」
# 固化为可执行纪律。仍是纯 AST 静态扫描，不 import 业务运行时，不依赖 flag / 环境。
#
# 重要：本段为 append-only，**不放宽**上面任何 E-2/E-2a/E-3/E-4/E-5 依赖方向规则。
# E6-6 已将 E5-4 时点「仅 intake+statute」产品包快照上移到 E6 当前基线：
# intake + statute + drafting + casebook 合法。
# 端点 gated / schema 白名单 / 持久层零正文 / 导出免责头 / DraftDescriptor 锚点等运行时断言见
# tests/test_e6_drafting_boundaries.py；此处只追加 drafting 跨包 import 方向的静态守门。
# ===========================================================================

# drafting 只允许消费的内核顶层公开面（深引内部子模块即越界）。
E6_ALLOWED_KERNEL_SURFACES = (
    "app.kernel",
    "app.kernel.rag",
    "app.kernel.guardrails",
    "app.kernel.identity",
)
# E7-2 后 E 系列合法产品包集合（intake + statute + drafting + casebook）。
E6_ALLOWED_PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")


def _drafting_py_files():
    drafting_dir = APP_DIR / "drafting"
    if not drafting_dir.exists():
        return []
    return sorted(drafting_dir.glob("*.py"))


def test_e6_drafting_does_not_import_other_product_packages():
    """drafting 不得 import 其它产品包（intake/statute/casebook）——产品包互不 import。"""
    offending: list[str] = []
    for path in _drafting_py_files():
        for module, _node in _iter_imports(path):
            for other in ("intake", "statute", "casebook"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "drafting import 了其它产品包（产品包互不 import）：" + "; ".join(offending)
    )


def test_e6_drafting_not_imported_by_other_product_packages():
    """drafting 不得被其它产品包 import（intake/statute/casebook）——产品包互不 import（反向）。"""
    offending: list[str] = []
    for other in ("intake", "statute", "casebook"):
        other_dir = APP_DIR / other
        if not other_dir.exists():
            continue
        for path in _py_files(other_dir):
            for module, _node in _iter_imports(path):
                if module == "app.drafting" or module.startswith("app.drafting."):
                    rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                    offending.append(f"{rel}: {module}")
    assert not offending, (
        "其它产品包反向 import 了 drafting（产品包互不 import）：" + "; ".join(offending)
    )


def test_e6_kernel_does_not_import_drafting():
    """内核不得反向 import drafting（单向依赖：内核 ← 产品包，绝不反向）。"""
    kernel_dir = APP_DIR / "kernel"
    offending: list[str] = []
    for path in _py_files(kernel_dir):
        for module, _node in _iter_imports(path):
            if module == "app.drafting" or module.startswith("app.drafting."):
                rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                offending.append(f"{rel}: {module}")
    assert not offending, (
        "内核反向 import 了 drafting（违反单向依赖）：" + "; ".join(offending)
    )


def test_e6_drafting_consumes_only_kernel_public_surface():
    """drafting 对内核只能消费顶层公开面，禁止深引 retrieval/rerank/summary 等内部子模块。"""
    offending: list[str] = []
    for path in _drafting_py_files():
        for module, _node in _iter_imports(path):
            if module.startswith("app.kernel") and module not in E6_ALLOWED_KERNEL_SURFACES:
                offending.append(f"{path.name}: {module}")
            # 直引内核成员顶层包（绕过 app.kernel 公开面）也是越界。
            parts = module.split(".")
            if (
                len(parts) >= 2
                and parts[0] == "app"
                and parts[1] in KERNEL_TOP_PACKAGES
            ):
                offending.append(f"{path.name}: {module}")
    assert not offending, (
        "drafting 绕过 app.kernel 公开面深引内核内部（应只走 kernel 公开面）："
        + "; ".join(offending)
    )


def test_e6_drafting_does_not_deep_import_retrieval_runtime():
    """drafting 不得直引检索运行时底层（应经 app.kernel 公开面服务，组装而非检索）。"""
    forbidden_runtime = ("retrieval", "rerank", "summary", "query_processing")
    offending: list[str] = []
    for path in _drafting_py_files():
        for module, _node in _iter_imports(path):
            for mod in forbidden_runtime:
                if module == f"app.{mod}" or module.startswith(f"app.{mod}."):
                    offending.append(f"{path.name}: {module}")
                if module.startswith(f"app.kernel.rag.{mod}"):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "drafting 直引检索运行时底层（应经 app.kernel 公开面，组装而非检索）："
        + "; ".join(offending)
    )


def test_e6_casebook_still_forbidden_drafting_allowed():
    """E7-2 后合法产品包 = intake + statute + drafting + casebook。

    本断言只校验当前合法集合，不放宽产品包互不 import / 内核单向依赖规则。
    """
    existing = [p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()]
    unexpected = [p for p in existing if p not in E6_ALLOWED_PRODUCT_PACKAGES]
    assert not unexpected, (
        "出现不应存在的产品包（E7-2 后仅允许 intake + statute + drafting + casebook）："
        + ", ".join(unexpected)
    )
    assert "drafting" in existing, "drafting 产品包必须存在（E6-2 已建）"
    assert "casebook" in existing, "casebook 产品包必须存在（E7-2 已建）"


# ===========================================================================
# E7-5 追加：案件协作工作台 casebook 产品包消费边界（append-only，不放宽任何
# E-2/E-2a/E-3/E-4/E-5/E-6 规则）。
#
# E7 在既有「内核单向依赖 + 产品包互不 import + 只经公开面消费内核」纪律上，继续把
# casebook 的边界钉死为：只依赖 app.kernel.guardrails / app.kernel.identity
# 公开面拿契约与租户上下文；不 import intake/statute/drafting；其它产品包不 import
# casebook；内核不反向 import casebook；不直连 retrieval/rerank/summary/
# query_processing 底层。仍是纯 AST 静态扫描，不 import 业务运行时。
# ===========================================================================

E7_ALLOWED_KERNEL_SURFACES = (
    "app.kernel",
    "app.kernel.guardrails",
    "app.kernel.identity",
)
E7_ALLOWED_PRODUCT_PACKAGES = ("intake", "statute", "drafting", "casebook")


def _casebook_py_files():
    casebook_dir = APP_DIR / "casebook"
    if not casebook_dir.exists():
        return []
    return sorted(casebook_dir.glob("*.py"))


def test_e7_casebook_does_not_import_other_product_packages():
    """casebook 不得 import 其它产品包（intake/statute/drafting）。"""
    offending: list[str] = []
    for path in _casebook_py_files():
        for module, _node in _iter_imports(path):
            for other in ("intake", "statute", "drafting"):
                if module == f"app.{other}" or module.startswith(f"app.{other}."):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "casebook import 了其它产品包（产品包互不 import）：" + "; ".join(offending)
    )


def test_e7_casebook_not_imported_by_other_product_packages():
    """其它产品包不得反向 import casebook。"""
    offending: list[str] = []
    for other in ("intake", "statute", "drafting"):
        other_dir = APP_DIR / other
        if not other_dir.exists():
            continue
        for path in _py_files(other_dir):
            for module, _node in _iter_imports(path):
                if module == "app.casebook" or module.startswith("app.casebook."):
                    rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                    offending.append(f"{rel}: {module}")
    assert not offending, (
        "其它产品包反向 import 了 casebook（产品包互不 import）：" + "; ".join(offending)
    )


def test_e7_kernel_does_not_import_casebook():
    """内核不得反向 import casebook（单向依赖仍成立）。"""
    kernel_dir = APP_DIR / "kernel"
    offending: list[str] = []
    for path in _py_files(kernel_dir):
        for module, _node in _iter_imports(path):
            if module == "app.casebook" or module.startswith("app.casebook."):
                rel = str(path.relative_to(APP_DIR)).replace("\\", "/")
                offending.append(f"{rel}: {module}")
    assert not offending, (
        "内核反向 import 了 casebook（违反单向依赖）：" + "; ".join(offending)
    )


def test_e7_casebook_consumes_only_kernel_public_surface():
    """casebook 对内核只能消费公开面，禁止深引 rag/retrieval 底层。"""
    offending: list[str] = []
    for path in _casebook_py_files():
        for module, _node in _iter_imports(path):
            if module.startswith("app.kernel") and module not in E7_ALLOWED_KERNEL_SURFACES:
                offending.append(f"{path.name}: {module}")
            parts = module.split(".")
            if (
                len(parts) >= 2
                and parts[0] == "app"
                and parts[1] in KERNEL_TOP_PACKAGES
            ):
                offending.append(f"{path.name}: {module}")
    assert not offending, (
        "casebook 绕过 app.kernel 公开面深引内核内部（应只走 guardrails/identity 公开面）："
        + "; ".join(offending)
    )


def test_e7_casebook_does_not_deep_import_retrieval_runtime():
    """casebook 不得直引 retrieval/rerank/summary/query_processing。"""
    forbidden_runtime = ("retrieval", "rerank", "summary", "query_processing")
    offending: list[str] = []
    for path in _casebook_py_files():
        for module, _node in _iter_imports(path):
            for mod in forbidden_runtime:
                if module == f"app.{mod}" or module.startswith(f"app.{mod}."):
                    offending.append(f"{path.name}: {module}")
                if module.startswith(f"app.kernel.rag.{mod}"):
                    offending.append(f"{path.name}: {module}")
    assert not offending, (
        "casebook 直引检索运行时底层（应只经 app.kernel 公开面消费契约）："
        + "; ".join(offending)
    )


def test_e7_reconciled_product_packages_match_current_baseline():
    """E7-2 后合法产品包集合显式包含 casebook。"""
    existing = [p for p in PRODUCT_PACKAGES if (APP_DIR / p).exists()]
    unexpected = [p for p in existing if p not in E7_ALLOWED_PRODUCT_PACKAGES]
    assert not unexpected, (
        "出现不应存在的产品包（E7 后仅允许 intake + statute + drafting + casebook）："
        + ", ".join(unexpected)
    )
    assert "casebook" in existing, "casebook 产品包必须存在（E7-2 已建）"
