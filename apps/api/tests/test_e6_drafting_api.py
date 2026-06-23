"""E6-2 文书工作台 drafting 后端端点测试。

验证（对应文档 21 §E6-2 验收 / 测试要求）：
- ENABLE_DRAFTING=true（测试内 override）+ 登录：
  - POST /api/drafting/drafts 返回 DraftDescriptor；字段严格白名单、structure_skeleton
    仅标题、引用 100% 有锚点、无起草/裁判正文。
  - GET 列表/单个：对象级鉴权 + 租户隔离；越权读写 403/404，不泄露他人草稿。
  - PUT 更新：仍只存元数据，含正文型键被拒。
- ENABLE_DRAFTING=false：端点 403 安全降级，不泄露内部信息。
- POST/PUT 含起草正文 / 裁判正文 / PII / 胜负结论键被拒（422/400），异常消息不含原始内容。
- 持久层断言：落库行不含正文列、不含原始案情；note 不以全文形式进日志。
- drafting 不直接实例化 retrieval/rerank、不 import 产品包、service 不调用文本生成（AST + 行为）。

红线：fixture 只用短假数据 / hash / case_id / source_chunk_id / text_id / 元数据，
绝不写真实长起草正文或裁判正文。
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import app.api.auth as auth_api
import app.drafting.router as drafting_router_mod
from app.account.store import AccountStore
from app.account.service import AuthService
from app.core.config import Settings
from app.drafting.models import DraftDescriptorRow
from app.drafting.service import DraftingService
from app.drafting.store import DraftStore
from app.main import app

DRAFTING_DIR = Path(__file__).resolve().parents[1] / "app" / "drafting"

PW = "sup3rsecret-pw"

# drafting 响应 / 源码绝不允许出现的起草正文 / 裁判正文 / 富展示 / 胜负结论型字段。
FORBIDDEN_BODY_FIELDS = (
    "draft_body",
    "draft_content",
    "draft_text",
    "generated_text",
    "paragraph_body",
    "paragraph_text",
    "section_body",
    "opinion_text",
    "legal_opinion",
    "conclusion_text",
    "chunk_text",
    "judgment_text",
    "summary_text",
    "full_text",
    "matched_text",
    "holding_summary",
    "win_probability",
    "outcome_prediction",
    "verdict",
    "raw_case",
    "raw_query",
)

# drafting 产品包禁止直连 / 深引的内核底层与禁止 import 的其它产品包。
FORBIDDEN_DRAFTING_IMPORT_PREFIXES = (
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


def _code_without_docstrings_and_comments(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    blocked: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if ast.get_docstring(node, clean=False) is None or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            blocked.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))

    kept: list[str] = []
    for line_no, line in enumerate(src.splitlines(), start=1):
        if line_no in blocked:
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)


# --- fixtures（短假数据，临时 sqlite，不写生产库）-------------------------------

@pytest.fixture()
def engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture()
def enabled_client(engine, monkeypatch):
    """ENABLE_DRAFTING=true + ENABLE_ACCOUNT_SYSTEM=true + 注入临时 sqlite 持久层。"""
    acc = AccountStore(engine)
    acc.init_schema()
    store = DraftStore(engine)
    store.init_schema()
    s = Settings(
        DEEPSEEK_API_KEY="k",
        ENABLE_ACCOUNT_SYSTEM=True,
        ENABLE_DRAFTING=True,
    )
    monkeypatch.setattr(auth_api, "settings", s)
    monkeypatch.setattr(drafting_router_mod, "settings", s)
    auth_api.set_auth_service_for_test(AuthService(acc))
    drafting_router_mod.set_drafting_service_for_test(DraftingService(store=store))
    client = TestClient(app)
    yield client, engine
    auth_api.set_auth_service_for_test(None)
    drafting_router_mod.set_drafting_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(
        drafting_router_mod,
        "settings",
        Settings(DEEPSEEK_API_KEY="k", ENABLE_DRAFTING=False),
    )
    drafting_router_mod.set_drafting_service_for_test(None)
    return TestClient(app)


def _register_login(client: TestClient, login_name: str) -> str:
    client.post(
        "/api/auth/register",
        json={"login_name": login_name, "password": PW, "display_name": "d"},
    )
    r = client.post("/api/auth/login", json={"login_name": login_name, "password": PW})
    return r.json()["session_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _valid_create_body() -> dict:
    """合法创建请求：标题骨架 + 带锚点引用 + 短字段（零正文）。"""
    return {
        "structure_skeleton": ["争议焦点", "事实认定", "法律适用"],
        "candidate_refs": [
            {
                "case_id": "c1",
                "case_number": "(2023)刑初字第1号",
                "court": "某基层人民法院",
                "trial_level": "一审",
                "case_cause": "盗窃",
                "judgment_date": "2023-01-01",
                "source_anchors": [
                    {"case_id": "c1", "source_chunk_id": "c1_chunk0", "anchor_type": "case"}
                ],
            }
        ],
        "statute_refs": [
            {
                "statute_id": "s264",
                "law_name": "中华人民共和国刑法",
                "article_no": "第二百六十四条",
                "statute_anchors": [{"text_id": "law::s264", "anchor_type": "statute"}],
                "source_corpus": "judge_law_corpus",
                "effective_status": "current",
            }
        ],
        "note": "庭审重点",
        "tag": "刑事",
    }


# --- 1) flag 开启 + 登录：POST 创建返回收敛 DraftDescriptor（白名单 / 锚点 / 零正文）-

def test_create_returns_descriptor_whitelist_zero_body(enabled_client):
    client, _ = enabled_client
    token = _register_login(client, "author@x.io")
    resp = client.post(
        "/api/drafting/drafts", json=_valid_create_body(), headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 字段严格白名单。
    assert set(data.keys()) <= {
        "draft_id",
        "structure_skeleton",
        "candidate_refs",
        "statute_refs",
        "note",
        "tag",
        "owner_user_id",
        "team_id",
        "visibility",
        "status",
        "created_at",
        "updated_at",
    }
    assert data["draft_id"]
    # structure_skeleton 仅标题。
    assert data["structure_skeleton"] == ["争议焦点", "事实认定", "法律适用"]
    # 引用 100% 有锚点。
    assert len(data["candidate_refs"]) == 1
    for ref in data["candidate_refs"]:
        assert ref["source_anchors"]
        for a in ref["source_anchors"]:
            assert a["case_id"] and a["source_chunk_id"]
    assert len(data["statute_refs"]) == 1
    for ref in data["statute_refs"]:
        assert ref["statute_anchors"]
        for a in ref["statute_anchors"]:
            assert a["text_id"]
    # 默认 owner 私有。
    assert data["visibility"] == "private"
    assert data["team_id"] is None
    # 0 正文：响应里不得出现任何起草 / 裁判正文 / 胜负结论型字段。
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in resp.text


def test_create_drops_refs_without_anchor(enabled_client):
    """缺锚点引用 fail-closed 丢弃，保留项 100% 有锚点（不抛错、不进交付物）。"""
    client, _ = enabled_client
    token = _register_login(client, "drop@x.io")
    body = _valid_create_body()
    body["candidate_refs"].append(
        {"case_id": "c2", "court": "无锚点法院", "source_anchors": []}
    )
    resp = client.post("/api/drafting/drafts", json=body, headers=_auth(token))
    # min_length=1 的 source_anchors 在 pydantic 视图层会 422；契约层 sanitize 是丢弃。
    # 这里走 service.sanitize -> 丢弃，端点应 200 且只保留有锚点项。
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["candidate_refs"]) == 1
    assert data["candidate_refs"][0]["case_id"] == "c1"


# --- 2) GET 列表 / 单个：对象级鉴权 + 租户隔离 --------------------------------

def test_list_and_get_owner_only(enabled_client):
    client, _ = enabled_client
    token = _register_login(client, "owner1@x.io")
    created = client.post(
        "/api/drafting/drafts", json=_valid_create_body(), headers=_auth(token)
    ).json()
    draft_id = created["draft_id"]

    # 列表只含自己的草稿。
    lst = client.get("/api/drafting/drafts", headers=_auth(token))
    assert lst.status_code == 200, lst.text
    ldata = lst.json()
    assert ldata["draft_count"] == 1
    assert ldata["drafts"][0]["draft_id"] == draft_id

    # 读取单个成功。
    one = client.get(f"/api/drafting/drafts/{draft_id}", headers=_auth(token))
    assert one.status_code == 200, one.text
    assert one.json()["draft_id"] == draft_id


def test_cross_user_isolation_list_and_get(enabled_client):
    """他人不可见：跨用户列表为空，跨用户读取 404，不泄露他人草稿存在性。"""
    client, _ = enabled_client
    token_a = _register_login(client, "alice@x.io")
    created = client.post(
        "/api/drafting/drafts", json=_valid_create_body(), headers=_auth(token_a)
    ).json()
    draft_id = created["draft_id"]

    token_b = _register_login(client, "bob@x.io")
    # B 的列表为空。
    lst = client.get("/api/drafting/drafts", headers=_auth(token_b))
    assert lst.status_code == 200
    assert lst.json()["draft_count"] == 0
    # B 读取 A 的草稿 -> 404。
    one = client.get(f"/api/drafting/drafts/{draft_id}", headers=_auth(token_b))
    assert one.status_code == 404
    assert one.json()["error"]["code"] == "DRAFT_NOT_FOUND"
    # 不泄露内容。
    assert "争议焦点" not in one.text


def test_cross_user_update_denied(enabled_client):
    """越权更新：B 改 A 的草稿 -> 404（非 owner 取不到），A 内容不变。"""
    client, _ = enabled_client
    token_a = _register_login(client, "amy@x.io")
    created = client.post(
        "/api/drafting/drafts", json=_valid_create_body(), headers=_auth(token_a)
    ).json()
    draft_id = created["draft_id"]

    token_b = _register_login(client, "ben@x.io")
    body = _valid_create_body()
    body["structure_skeleton"] = ["被篡改标题"]
    resp = client.put(
        f"/api/drafting/drafts/{draft_id}", json=body, headers=_auth(token_b)
    )
    assert resp.status_code == 404
    # A 读回仍是原骨架。
    one = client.get(f"/api/drafting/drafts/{draft_id}", headers=_auth(token_a))
    assert one.json()["structure_skeleton"] == ["争议焦点", "事实认定", "法律适用"]


# --- 3) PUT 更新：仍只存元数据，正文型键被拒 ----------------------------------

def test_update_owner_metadata_only(enabled_client):
    client, _ = enabled_client
    token = _register_login(client, "upd@x.io")
    created = client.post(
        "/api/drafting/drafts", json=_valid_create_body(), headers=_auth(token)
    ).json()
    draft_id = created["draft_id"]

    body = _valid_create_body()
    body["structure_skeleton"] = ["新骨架A", "新骨架B"]
    body["note"] = "更新后的短备注"
    resp = client.put(
        f"/api/drafting/drafts/{draft_id}", json=body, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["structure_skeleton"] == ["新骨架A", "新骨架B"]
    assert data["note"] == "更新后的短备注"
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in resp.text


# --- 4) flag 关闭：403 安全降级，不泄露内部信息 ------------------------------

@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/drafting/drafts", {"structure_skeleton": ["x"]}),
        ("get", "/api/drafting/drafts", None),
        ("get", "/api/drafting/drafts/d_abc", None),
        ("put", "/api/drafting/drafts/d_abc", {"structure_skeleton": ["x"]}),
    ],
)
def test_disabled_returns_403_safe_degrade(disabled_client, method, path, body):
    fn = getattr(disabled_client, method)
    resp = fn(path, json=body) if body is not None else fn(path)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "DRAFTING_DISABLED"
    # 不泄露内部信息：无 draft 内容 / 内部栈。
    assert "structure_skeleton" not in resp.text or "drafts" not in resp.text


# --- 5) 需登录：无 token -> 401 ----------------------------------------------

def test_endpoints_require_login(enabled_client):
    client, _ = enabled_client
    resp = client.post("/api/drafting/drafts", json=_valid_create_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "DRAFTING_REQUIRES_LOGIN"
    assert client.get("/api/drafting/drafts").status_code == 401


# --- 6) 起草正文 / 裁判正文 / PII / 胜负结论键被拒（第一道闸 422 / 第二道闸 400）-

@pytest.mark.parametrize(
    "bad_top_level",
    [
        {"draft_body": "本院认为……（起草正文）"},
        {"generated_text": "模型生成的段落"},
        {"conclusion": "被告胜诉"},
        {"win_probability": 0.8},
        {"verdict": "有罪"},
        {"full_text": "裁判文书全文……"},
        {"name": "张三"},
        {"id_card": "110101199001011234"},
        {"phone": "13800138000"},
    ],
)
def test_forbidden_top_level_keys_rejected_422(enabled_client, bad_top_level):
    """顶层非白名单 / 正文 / PII / 胜负键 -> pydantic extra=forbid 即 422，不回显原始内容。"""
    client, _ = enabled_client
    token = _register_login(client, "bad1@x.io")
    body = _valid_create_body()
    body.update(bad_top_level)
    resp = client.post("/api/drafting/drafts", json=body, headers=_auth(token))
    assert resp.status_code == 422, resp.text
    for value in ("本院认为", "模型生成的段落", "被告胜诉", "裁判文书全文", "张三", "110101199001011234", "13800138000"):
        assert value not in resp.text


@pytest.mark.parametrize(
    "bad_ref",
    [
        {"case_id": "c9", "draft_body": "正文", "source_anchors": [{"case_id": "c9", "source_chunk_id": "k"}]},
        {"case_id": "c9", "chunk_text": "裁判正文片段", "source_anchors": [{"case_id": "c9", "source_chunk_id": "k"}]},
        {"case_id": "c9", "name": "李四", "source_anchors": [{"case_id": "c9", "source_chunk_id": "k"}]},
    ],
)
def test_forbidden_keys_inside_refs_rejected_400(enabled_client, bad_ref):
    """引用内夹带正文 / 裁判正文 / PII 键 -> service sanitize fail-closed -> 400，不回显原始内容。"""
    client, _ = enabled_client
    token = _register_login(client, "bad2@x.io")
    body = _valid_create_body()
    body["candidate_refs"] = [bad_ref]
    resp = client.post("/api/drafting/drafts", json=body, headers=_auth(token))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "DRAFT_REJECTED"
    for value in ("正文", "裁判正文片段", "李四"):
        assert value not in resp.text


def test_skeleton_item_too_long_rejected(enabled_client):
    """structure_skeleton 单项超长（疑似正文非标题）-> 400，异常不回显原文。"""
    client, _ = enabled_client
    token = _register_login(client, "long@x.io")
    body = _valid_create_body()
    long_para = "本院认为" + "甲" * 80  # > 60 字，疑似正文
    body["structure_skeleton"] = [long_para]
    resp = client.post("/api/drafting/drafts", json=body, headers=_auth(token))
    assert resp.status_code == 400, resp.text
    assert long_para not in resp.text


def test_update_forbidden_key_rejected(enabled_client):
    """PUT 含正文型键被拒（422 顶层）。"""
    client, _ = enabled_client
    token = _register_login(client, "updbad@x.io")
    created = client.post(
        "/api/drafting/drafts", json=_valid_create_body(), headers=_auth(token)
    ).json()
    draft_id = created["draft_id"]
    body = _valid_create_body()
    body["draft_body"] = "起草正文"
    resp = client.put(
        f"/api/drafting/drafts/{draft_id}", json=body, headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text
    assert "起草正文" not in resp.text


# --- 7) 持久层断言：落库行只含元数据列，无正文列 / 无原始案情 -------------------

def test_persisted_row_has_no_body_columns(enabled_client):
    client, engine = enabled_client
    token = _register_login(client, "persist@x.io")
    created = client.post(
        "/api/drafting/drafts", json=_valid_create_body(), headers=_auth(token)
    ).json()
    draft_id = created["draft_id"]

    # 表列只含元数据 / 引用 / 结构骨架 / 短字段 / 结构化关系，无正文列。
    cols = set(DraftDescriptorRow.__table__.columns.keys())
    forbidden_cols = {
        "draft_body",
        "draft_content",
        "draft_text",
        "generated_text",
        "paragraph_body",
        "conclusion",
        "conclusion_text",
        "full_text",
        "chunk_text",
        "judgment_text",
        "summary_text",
        "raw_case",
        "raw_query",
        "win_probability",
        "verdict",
        "password",
        "token",
    }
    assert cols & forbidden_cols == set(), f"持久层不得含正文/凭据列: {cols & forbidden_cols}"
    assert cols == {
        "draft_id",
        "owner_user_id",
        "team_id",
        "visibility",
        "structure_skeleton",
        "candidate_refs",
        "statute_refs",
        "note",
        "tag",
        "status",
        "reason_code",
        "created_at",
        "updated_at",
    }

    # 落库行内容：structure_skeleton 只含标题；JSON 列不含正文关键字。
    with Session(engine) as s:
        row = s.get(DraftDescriptorRow, draft_id)
    assert row is not None
    assert row.owner_user_id  # 带 owner
    assert row.visibility == "private"
    for forbidden in ("本院认为", "draft_body", "chunk_text", "judgment_text"):
        assert forbidden not in (row.structure_skeleton or "")
        assert forbidden not in (row.candidate_refs or "")
        assert forbidden not in (row.statute_refs or "")


def test_note_not_logged_in_full(enabled_client, caplog):
    """note 不以全文形式进日志（只记长度 + hash）。"""
    client, _ = enabled_client
    token = _register_login(client, "logtest@x.io")
    body = _valid_create_body()
    secret_note = "这是一段不应进日志的庭审备注全文XYZ"
    body["note"] = secret_note
    with caplog.at_level(logging.INFO):
        resp = client.post("/api/drafting/drafts", json=body, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_note not in log_text
    assert "drafting_create" in log_text
    assert "note_meta" in log_text


# --- 8) 静态边界：drafting 只依赖 app.kernel 公开面，不直连底层 / 不 import 产品包 -

def _iter_drafting_imports():
    modules: list[str] = []
    for path in sorted(DRAFTING_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
    return modules


def test_drafting_does_not_import_retrieval_layer_or_other_products():
    for module in _iter_drafting_imports():
        for forbidden in FORBIDDEN_DRAFTING_IMPORT_PREFIXES:
            assert not module.startswith(forbidden), (
                f"drafting 不得 import {module}（应只走 app.kernel 公开面 + 既有持久层）"
            )


def test_drafting_only_consumes_kernel_public_surface():
    """drafting 对内核的依赖只允许 app.kernel 顶层公开面（kernel / guardrails / identity）。

    例外：测试模块自身可深引；产品包源码（app/drafting/*.py）不允许深引内核私有。
    持久层允许依赖 app.core.db / app.core.config / app.api.errors / app.schemas（既有基础设施）。
    """
    allowed_kernel = (
        "app.kernel",
        "app.kernel.guardrails",
        "app.kernel.identity",
    )
    for module in _iter_drafting_imports():
        if module.startswith("app.kernel"):
            assert module in allowed_kernel, (
                f"drafting 只能消费内核顶层公开面，禁止深引 {module}"
            )


def test_drafting_service_does_not_call_text_generation():
    """service / store 源码不得 import LLM / 模型 / 检索客户端，不调用任何文本生成入口。"""
    service_src = _code_without_docstrings_and_comments(DRAFTING_DIR / "service.py")
    store_src = _code_without_docstrings_and_comments(DRAFTING_DIR / "store.py")
    router_src = _code_without_docstrings_and_comments(DRAFTING_DIR / "router.py")
    forbidden_gen_markers = (
        "llm",
        "openai",
        "deepseek",
        "completion",
        "chat.completions",
        "internalsearchservice",
        "statutesearchservice",
        "rerank",
    )
    for src in (service_src, store_src, router_src):
        low = src.lower()
        for marker in forbidden_gen_markers:
            assert marker not in low, f"drafting 源码不得含文本生成 / 检索标记: {marker}"


def test_drafting_source_has_no_body_field_literals():
    """drafting 源码不得把起草/裁判正文型字段当数据字段搬运（只可作被拒键名 / 注释）。"""
    service_src = (DRAFTING_DIR / "service.py").read_text(encoding="utf-8")
    router_src = (DRAFTING_DIR / "router.py").read_text(encoding="utf-8")
    schemas_src = (DRAFTING_DIR / "schemas.py").read_text(encoding="utf-8")
    for forbidden in (
        "draft_body",
        "generated_text",
        "paragraph_body",
        "chunk_text",
        "judgment_text",
        "win_probability",
        "outcome_prediction",
    ):
        assert forbidden not in service_src
        assert forbidden not in router_src
        assert forbidden not in schemas_src
