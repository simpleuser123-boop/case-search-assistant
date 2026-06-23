"""E7-2 案件协作工作台 casebook 后端端点测试。

验证（对应文档 22 §E7-2 验收 / 测试要求）：
- ENABLE_CASEBOOK=true（测试内 override）+ 登录：
  - POST /api/casebook/folders 返回 CaseFolder；字段严格白名单、search_profile_summary
    仅脱敏子集、引用 100% 有锚点、无裁判/起草正文、visibility 默认 private。
  - GET 列表/单个：对象级鉴权 + 租户隔离 + visibility 过滤；越权读写 404，不泄露他人 folder。
  - PUT 更新：仍只存元数据，含正文型键被拒。
- ENABLE_CASEBOOK=false：端点 403 安全降级，不泄露内部信息。
- POST/PUT 含裁判/起草正文 / 原始案情 / PII / 胜负结论键被拒（422/400），异常消息不含原始内容。
- 持久层断言：落库行不含正文列、不含原始案情；note 不以全文形式进日志。
- casebook 不直接实例化 retrieval/rerank、不 import 产品包、service 不调用文本生成（AST + 行为）。

红线：fixture 只用短假数据 / hash / case_id / source_chunk_id / text_id / 元数据，
绝不写真实长裁判正文或起草正文。
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
import app.casebook.router as casebook_router_mod
from app.account.store import AccountStore
from app.account.service import AuthService
from app.core.config import Settings
from app.casebook.models import CaseFolderRow
from app.casebook.service import CasebookService
from app.casebook.store import CaseFolderStore
from app.main import app

CASEBOOK_DIR = Path(__file__).resolve().parents[1] / "app" / "casebook"

PW = "sup3rsecret-pw"

# casebook 响应 / 源码绝不允许出现的裁判 / 起草正文 / 富展示 / 胜负结论型字段。
FORBIDDEN_BODY_FIELDS = (
    "chunk_text",
    "judgment_text",
    "judgment_full_text",
    "summary_text",
    "highlight_text",
    "matched_text",
    "holding_summary",
    "case_body",
    "document_text",
    "draft_body",
    "draft_content",
    "draft_text",
    "generated_text",
    "paragraph_body",
    "opinion_text",
    "legal_opinion",
    "conclusion_text",
    "case_summary_text",
    "win_probability",
    "outcome_prediction",
    "verdict",
    "raw_case",
    "raw_query",
)

# casebook 产品包禁止直连 / 深引的内核底层与禁止 import 的其它产品包。
FORBIDDEN_CASEBOOK_IMPORT_PREFIXES = (
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
    "app.drafting",
)


# --- fixtures（短假数据，临时 sqlite，不写生产库）-------------------------------

@pytest.fixture()
def engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture()
def enabled_client(engine, monkeypatch):
    """ENABLE_CASEBOOK=true + ENABLE_ACCOUNT_SYSTEM=true + 注入临时 sqlite 持久层。"""
    acc = AccountStore(engine)
    acc.init_schema()
    store = CaseFolderStore(engine)
    store.init_schema()
    s = Settings(
        DEEPSEEK_API_KEY="k",
        ENABLE_ACCOUNT_SYSTEM=True,
        ENABLE_CASEBOOK=True,
    )
    monkeypatch.setattr(auth_api, "settings", s)
    monkeypatch.setattr(casebook_router_mod, "settings", s)
    auth_api.set_auth_service_for_test(AuthService(acc))
    casebook_router_mod.set_casebook_service_for_test(CasebookService(store=store))
    client = TestClient(app)
    yield client, engine
    auth_api.set_auth_service_for_test(None)
    casebook_router_mod.set_casebook_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(
        casebook_router_mod,
        "settings",
        Settings(DEEPSEEK_API_KEY="k", ENABLE_CASEBOOK=False),
    )
    casebook_router_mod.set_casebook_service_for_test(None)
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


def _valid_candidate_ref() -> dict:
    return {
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


def _valid_draft_descriptor() -> dict:
    return {
        "draft_id": "d1",
        "structure_skeleton": ["争议焦点", "事实认定", "法律适用"],
        "candidate_refs": [_valid_candidate_ref()],
        "statute_refs": [],
        "note": "庭审重点",
        "tag": "刑事",
    }


def _valid_create_body() -> dict:
    """合法创建请求：脱敏摘要 + 带锚点引用 + 短字段（零正文）。"""
    return {
        "search_profile_summary": {
            "case_cause": "盗窃",
            "region": "某省",
            "trial_level_preference": "一审",
            "dispute_focus_keywords": ["数额", "既遂"],
            "query_text": "盗窃数额认定",
        },
        "candidate_refs": [_valid_candidate_ref()],
        "draft_descriptors": [_valid_draft_descriptor()],
        "title": "盗窃数额类案归集",
        "note": "本夹聚合数额认定争议",
        "tag": "刑事",
    }


# --- 1) flag 开启 + 登录：POST 创建返回收敛 CaseFolder（白名单 / 锚点 / 零正文）-

def test_create_returns_folder_whitelist_zero_body(enabled_client):
    client, _ = enabled_client
    token = _register_login(client, "owner@x.io")
    resp = client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 字段严格白名单。
    assert set(data.keys()) <= {
        "case_folder_id",
        "owner_user_id",
        "team_id",
        "visibility",
        "search_profile_summary",
        "candidate_refs",
        "draft_descriptors",
        "title",
        "note",
        "tag",
        "status",
        "created_at",
        "updated_at",
    }
    assert data["case_folder_id"]
    # search_profile_summary 仅 SearchProfile 脱敏白名单子集键。
    assert set(data["search_profile_summary"].keys()) <= {
        "case_cause",
        "region",
        "trial_level_preference",
        "dispute_focus_keywords",
        "query_text",
    }
    # 引用 100% 有锚点。
    assert len(data["candidate_refs"]) == 1
    for ref in data["candidate_refs"]:
        assert ref["source_anchors"]
        for a in ref["source_anchors"]:
            assert a["case_id"] and a["source_chunk_id"]
    assert len(data["draft_descriptors"]) == 1
    for d in data["draft_descriptors"]:
        assert d["structure_skeleton"] == ["争议焦点", "事实认定", "法律适用"]
        for rc in d["candidate_refs"]:
            assert rc["source_anchors"]
    # 默认 owner 私有。
    assert data["visibility"] == "private"
    assert data["team_id"] is None
    # 0 正文：响应里不得出现任何裁判 / 起草正文 / 胜负结论型字段。
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
    resp = client.post("/api/casebook/folders", json=body, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["candidate_refs"]) == 1
    assert data["candidate_refs"][0]["case_id"] == "c1"


def test_create_summary_strips_non_whitelist_keys(enabled_client):
    """search_profile_summary 非脱敏白名单键被主动丢弃（只保留 SearchProfile 子集）。"""
    client, _ = enabled_client
    token = _register_login(client, "summary@x.io")
    body = _valid_create_body()
    body["search_profile_summary"]["extra_marker"] = "should_be_dropped"
    resp = client.post("/api/casebook/folders", json=body, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "extra_marker" not in data["search_profile_summary"]


# --- 2) GET 列表 / 单个：对象级鉴权 + 租户隔离 --------------------------------

def test_list_and_get_owner_only(enabled_client):
    client, _ = enabled_client
    token = _register_login(client, "owner1@x.io")
    created = client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token)
    ).json()
    folder_id = created["case_folder_id"]

    lst = client.get("/api/casebook/folders", headers=_auth(token))
    assert lst.status_code == 200, lst.text
    ldata = lst.json()
    assert ldata["folder_count"] == 1
    assert ldata["folders"][0]["case_folder_id"] == folder_id

    one = client.get(f"/api/casebook/folders/{folder_id}", headers=_auth(token))
    assert one.status_code == 200, one.text
    assert one.json()["case_folder_id"] == folder_id


def test_cross_user_isolation_list_and_get(enabled_client):
    """他人不可见：跨用户列表为空，跨用户读取 404，不泄露他人协作夹存在性。"""
    client, _ = enabled_client
    token_a = _register_login(client, "alice@x.io")
    token_b = _register_login(client, "bob@x.io")
    created = client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token_a)
    ).json()
    folder_id = created["case_folder_id"]

    # bob 列表为空。
    lst_b = client.get("/api/casebook/folders", headers=_auth(token_b))
    assert lst_b.status_code == 200
    assert lst_b.json()["folder_count"] == 0

    # bob 读取 alice 的 folder -> 404。
    one_b = client.get(f"/api/casebook/folders/{folder_id}", headers=_auth(token_b))
    assert one_b.status_code == 404


def test_cross_user_update_denied(enabled_client):
    """跨用户更新 -> 404（不泄露他人协作夹是否存在）。"""
    client, _ = enabled_client
    token_a = _register_login(client, "alice2@x.io")
    token_b = _register_login(client, "bob2@x.io")
    created = client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token_a)
    ).json()
    folder_id = created["case_folder_id"]

    upd = client.put(
        f"/api/casebook/folders/{folder_id}",
        json=_valid_create_body(),
        headers=_auth(token_b),
    )
    assert upd.status_code == 404


def test_update_owner_metadata_only(enabled_client):
    """owner 更新只改元数据/引用/短字段，仍零正文。"""
    client, _ = enabled_client
    token = _register_login(client, "upd@x.io")
    created = client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token)
    ).json()
    folder_id = created["case_folder_id"]

    body = _valid_create_body()
    body["title"] = "更新后的标题"
    body["note"] = "更新后的备注"
    upd = client.put(
        f"/api/casebook/folders/{folder_id}", json=body, headers=_auth(token)
    )
    assert upd.status_code == 200, upd.text
    data = upd.json()
    assert data["title"] == "更新后的标题"
    assert data["note"] == "更新后的备注"
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in upd.text


# --- 3) ENABLE_CASEBOOK=false 安全降级 -----------------------------------------

@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/casebook/folders", {}),
        ("get", "/api/casebook/folders", None),
        ("get", "/api/casebook/folders/cf_x", None),
        ("put", "/api/casebook/folders/cf_x", {}),
    ],
)
def test_disabled_returns_403_safe_degrade(disabled_client, method, path, body):
    client = disabled_client
    fn = getattr(client, method)
    resp = fn(path, json=body) if body is not None else fn(path)
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "CASEBOOK_DISABLED"
    # 降级响应不泄露内部信息。
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in resp.text


def test_endpoints_require_login(enabled_client):
    """flag 开启但未登录 -> 401（不泄露内部信息）。"""
    client, _ = enabled_client
    resp = client.post("/api/casebook/folders", json=_valid_create_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "CASEBOOK_REQUIRES_LOGIN"


# --- 4) 禁止键拒绝（裁判/起草正文 / 原始案情 / PII / 胜负结论）-------------------

@pytest.mark.parametrize(
    "bad_top_level",
    [
        {"judgment_text": "被告人……"},
        {"draft_body": "本院认为……"},
        {"case_summary_text": "综述……"},
        {"win_probability": 0.9},
        {"raw_case": "我朋友昨天……"},
        {"name": "张三"},
    ],
)
def test_forbidden_top_level_keys_rejected_422(enabled_client, bad_top_level):
    """顶层正文/原始案情/PII/胜负键在 pydantic extra=forbid 层即 422。"""
    client, _ = enabled_client
    token = _register_login(client, "bad@x.io")
    body = _valid_create_body()
    body.update(bad_top_level)
    resp = client.post("/api/casebook/folders", json=body, headers=_auth(token))
    assert resp.status_code == 422, resp.text
    # 异常消息不回显原始内容值。
    for v in bad_top_level.values():
        if isinstance(v, str):
            assert v not in resp.text


def test_forbidden_keys_inside_summary_rejected_400(enabled_client):
    """search_profile_summary 内夹带正文/PII 键 -> service sanitize fail-closed -> 400。"""
    client, _ = enabled_client
    token = _register_login(client, "badsum@x.io")
    body = _valid_create_body()
    body["search_profile_summary"]["judgment_text"] = "被告人……"
    resp = client.post("/api/casebook/folders", json=body, headers=_auth(token))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "CASE_FOLDER_REJECTED"
    assert "被告人" not in resp.text


def test_forbidden_keys_inside_candidate_ref_rejected_400(enabled_client):
    """candidate_refs 内夹带正文键 -> service sanitize fail-closed -> 400。"""
    client, _ = enabled_client
    token = _register_login(client, "badref@x.io")
    body = _valid_create_body()
    body["candidate_refs"][0]["chunk_text"] = "裁判文书正文……"
    resp = client.post("/api/casebook/folders", json=body, headers=_auth(token))
    assert resp.status_code == 400, resp.text
    assert "裁判文书正文" not in resp.text


def test_update_forbidden_key_rejected(enabled_client):
    """更新含正文型键 -> 422（pydantic 层）。"""
    client, _ = enabled_client
    token = _register_login(client, "badupd@x.io")
    created = client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token)
    ).json()
    folder_id = created["case_folder_id"]
    body = _valid_create_body()
    body["draft_body"] = "本院认为……"
    upd = client.put(
        f"/api/casebook/folders/{folder_id}", json=body, headers=_auth(token)
    )
    assert upd.status_code == 422, upd.text


# --- 5) 持久层断言：落库行不含正文列 / 不含原始案情 -----------------------------

def test_persisted_row_has_no_body_columns(enabled_client):
    """落库行只含元数据/引用/短字段列；无裁判/起草正文列、无原始案情。"""
    client, engine = enabled_client
    token = _register_login(client, "persist@x.io")
    client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token)
    )
    with Session(engine) as session:
        rows = session.exec(select(CaseFolderRow)).all()
    assert len(rows) == 1
    row = rows[0]
    # 列集合严格白名单（无任何正文列）。
    col_names = set(CaseFolderRow.__table__.columns.keys())
    assert col_names == {
        "case_folder_id",
        "owner_user_id",
        "team_id",
        "visibility",
        "search_profile_summary",
        "candidate_refs",
        "draft_descriptors",
        "title",
        "note",
        "tag",
        "status",
        "reason_code",
        "created_at",
        "updated_at",
    }
    # 落库的 JSON 列不含任何正文型字段名。
    blob = " ".join(
        str(x)
        for x in (
            row.search_profile_summary,
            row.candidate_refs,
            row.draft_descriptors,
        )
    )
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in blob


def test_note_not_logged_in_full(enabled_client, caplog):
    """note 不以全文形式进日志（只记 note_meta = 长度 + hash）。"""
    client, _ = enabled_client
    token = _register_login(client, "log@x.io")
    secret_note = "这是一段不该进日志全文的机密备注XYZ"
    body = _valid_create_body()
    body["note"] = secret_note
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/api/casebook/folders", json=body, headers=_auth(token)
        )
    assert resp.status_code == 200, resp.text
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_note not in log_text
    assert "casebook_create" in log_text
    assert "note_meta" in log_text


# --- 6) 静态边界：casebook 只依赖 app.kernel 公开面，不直连底层 / 不 import 产品包 -

def _iter_casebook_imports():
    modules: list[str] = []
    for path in sorted(CASEBOOK_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
    return modules


def test_casebook_does_not_import_retrieval_layer_or_other_products():
    for module in _iter_casebook_imports():
        for forbidden in FORBIDDEN_CASEBOOK_IMPORT_PREFIXES:
            assert not module.startswith(forbidden), (
                f"casebook 不得 import {module}（应只走 app.kernel 公开面 + 既有持久层）"
            )


def test_casebook_only_consumes_kernel_public_surface():
    """casebook 对内核的依赖只允许 app.kernel 顶层公开面（kernel / guardrails / identity）。

    产品包源码（app/casebook/*.py）不允许深引内核私有；
    持久层允许依赖 app.core.db / app.core.config / app.api.errors / app.schemas（既有基础设施）。
    """
    allowed_kernel = (
        "app.kernel",
        "app.kernel.guardrails",
        "app.kernel.identity",
    )
    for module in _iter_casebook_imports():
        if module.startswith("app.kernel"):
            assert module in allowed_kernel, (
                f"casebook 只能消费内核顶层公开面，禁止深引 {module}"
            )


def test_casebook_service_does_not_call_text_generation():
    """service / store / router 源码不得 import LLM / 模型 / 检索客户端，不调用任何文本生成入口。"""
    service_src = (CASEBOOK_DIR / "service.py").read_text(encoding="utf-8")
    store_src = (CASEBOOK_DIR / "store.py").read_text(encoding="utf-8")
    router_src = (CASEBOOK_DIR / "router.py").read_text(encoding="utf-8")
    forbidden_gen_markers = (
        "llm",
        "openai",
        "deepseek",
        "completion",
        "chat.completions",
        "internalsearchservice",
        "statutesearchservice",
        "draftingservice",
        "rerank",
    )
    for src in (service_src, store_src, router_src):
        low = src.lower()
        for marker in forbidden_gen_markers:
            assert marker not in low, f"casebook 源码不得含文本生成 / 检索标记: {marker}"


def test_casebook_source_has_no_body_field_literals():
    """casebook 源码不得把裁判/起草正文型字段当数据字段搬运（只可作被拒键名 / 注释）。"""
    service_src = (CASEBOOK_DIR / "service.py").read_text(encoding="utf-8")
    router_src = (CASEBOOK_DIR / "router.py").read_text(encoding="utf-8")
    schemas_src = (CASEBOOK_DIR / "schemas.py").read_text(encoding="utf-8")
    for forbidden in (
        "draft_body",
        "generated_text",
        "paragraph_body",
        "chunk_text",
        "judgment_text",
        "case_summary_text",
        "win_probability",
        "outcome_prediction",
    ):
        assert forbidden not in service_src
        assert forbidden not in router_src
        assert forbidden not in schemas_src
