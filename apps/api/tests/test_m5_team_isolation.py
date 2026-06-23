"""M5-3 团队空间与数据隔离 focused tests。

覆盖验收点（隔离不彻底即 NO_GO）：
- 默认关闭（ENABLE_TEAM_WORKSPACE=false）时所有团队端点 403，行为回 M5-2 / M4 末态。
- 沉淀对象按 team_id / owner_user_id 行级强隔离：**跨团队、跨用户串读 = 否**。
- team_id 为空时行为与单用户私有一致（只看自己的私有行）。
- 非团队成员越权传 team_id -> 降级单用户私有，绝不读到他团队数据。
- 写入越权 / 含正文键被拒，正文绝不入库。
- 团队持久层只存白名单字段，无正文列。
- 日志只含 user_id_hash / team_id_hash / count / reason_code，无凭据 / 正文。
- 团队模块不引入排序 / 检索副作用。
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

import app.api.auth as auth_api
import app.api.team as team_api
from app.account.service import AuthService
from app.account.store import AccountStore
from app.core.config import Settings
from app.team.isolation import (
    TenantContext,
    assert_write_within_tenant,
    tenant_visibility_clause,
)
from app.team.models import (
    VISIBILITY_PRIVATE,
    VISIBILITY_TEAM,
    SedimentationObject,
    Team,
    TeamMembership,
    Workspace,
)
from app.team.service import TeamService
from app.team.store import TeamStore

PW = "sup3rsecret-pw"


@pytest.fixture()
def stores():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    acc = AccountStore(engine)
    acc.init_schema()
    team = TeamStore(engine)
    team.init_schema()
    return acc, team, engine


@pytest.fixture()
def enabled_client(stores, monkeypatch):
    acc, team, _engine = stores
    enabled_settings = Settings(
        DEEPSEEK_API_KEY="k", ENABLE_ACCOUNT_SYSTEM=True, ENABLE_TEAM_WORKSPACE=True
    )
    monkeypatch.setattr(auth_api, "settings", enabled_settings)
    monkeypatch.setattr(team_api, "settings", enabled_settings)
    auth_api.set_auth_service_for_test(AuthService(acc))
    team_api.set_team_service_for_test(TeamService(team))
    from app.main import app

    yield TestClient(app), TeamService(team)
    auth_api.set_auth_service_for_test(None)
    team_api.set_team_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(
        team_api, "settings", Settings(DEEPSEEK_API_KEY="k", ENABLE_TEAM_WORKSPACE=False)
    )
    team_api.set_team_service_for_test(None)
    from app.main import app

    return TestClient(app)


def _register_login(c, name):
    c.post("/api/auth/register", json={"login_name": name, "password": PW})
    return c.post("/api/auth/login", json={"login_name": name, "password": PW}).json()[
        "session_token"
    ]


# --- 默认安全态 ---
def test_team_workspace_flag_default_false():
    assert Settings.model_fields["ENABLE_TEAM_WORKSPACE"].default is False


def test_disabled_returns_403_on_all_team_endpoints(disabled_client):
    c = disabled_client
    for method, path, body in [
        ("post", "/api/team/create", {"team_name": "x"}),
        ("get", "/api/team/list", None),
        ("post", "/api/team/sediment", {"object_type": "case_favorite"}),
        ("post", "/api/team/sediment/list", {}),
    ]:
        r = getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "TEAM_WORKSPACE_DISABLED"


# --- 白名单：团队持久层只含结构化字段，无正文列 ---
def test_team_tables_only_whitelist_columns():
    team_cols = set(Team.__table__.columns.keys())
    assert team_cols == {"team_id", "team_name", "status", "created_at", "updated_at", "reason_code"}
    ws_cols = set(Workspace.__table__.columns.keys())
    assert ws_cols == {"workspace_id", "team_id", "workspace_name", "status", "created_at", "reason_code"}
    mem_cols = set(TeamMembership.__table__.columns.keys())
    assert mem_cols == {
        "membership_id", "team_id", "workspace_id", "member_user_id", "status", "created_at", "reason_code",
    }
    sed_cols = set(SedimentationObject.__table__.columns.keys())
    for forbidden in (
        "raw_query", "case_fact_body", "candidate_body", "chunk_body",
        "judgment_long_text", "summary_body", "password", "token", "session_token",
    ):
        assert forbidden not in sed_cols, f"sediment table must not have body/credential col {forbidden}"
    # 必含租户隔离字段
    for required in ("owner_user_id", "team_id", "workspace_id", "visibility"):
        assert required in sed_cols


# --- 核心：跨租户串读 = 否 ---
def test_cross_team_read_is_no(stores):
    """A 团队的 team 可见沉淀，B 团队成员绝对读不到。"""
    _acc, team, _engine = stores
    svc = TeamService(team)
    # 用户 A 建团 TA 并写一条 team 可见沉淀
    ta = svc.create_team(owner_user_id="userA", team_name="TeamA")["team_id"]
    res_a = svc.resolve_tenant(owner_user_id="userA", team_id=ta)
    assert res_a.downgraded is False
    svc.save_sediment(
        ctx=res_a.ctx, object_type="case_favorite", visibility=VISIBILITY_TEAM,
        payload={"case_id": "cA", "source_anchors": [{"case_id": "cA", "source_chunk_id": "cA-0"}]},
    )
    # 用户 B 建团 TB
    tb = svc.create_team(owner_user_id="userB", team_name="TeamB")["team_id"]
    res_b = svc.resolve_tenant(owner_user_id="userB", team_id=tb)
    # B 在自己团队上下文里列出沉淀 -> 看不到 A 团队的任何对象
    b_items = svc.list_sediment(ctx=res_b.ctx)
    assert all(item.case_id != "cA" for item in b_items)
    assert len(b_items) == 0


def test_cross_user_private_read_is_no(stores):
    """单用户私有态：A 的私有沉淀，B 在私有态读不到。"""
    _acc, team, _engine = stores
    svc = TeamService(team)
    ctx_a = svc.resolve_tenant(owner_user_id="userA").ctx
    svc.save_sediment(ctx=ctx_a, object_type="case_favorite", visibility=VISIBILITY_PRIVATE,
                      payload={"case_id": "cPrivA"})
    ctx_b = svc.resolve_tenant(owner_user_id="userB").ctx
    b_items = svc.list_sediment(ctx=ctx_b)
    assert all(item.case_id != "cPrivA" for item in b_items)
    assert len(b_items) == 0


def test_non_member_team_id_downgrades_to_private(stores):
    """非成员越权传他团队 team_id -> 降级单用户私有，绝不读到他团队数据。"""
    _acc, team, _engine = stores
    svc = TeamService(team)
    ta = svc.create_team(owner_user_id="userA", team_name="TeamA")["team_id"]
    res_a = svc.resolve_tenant(owner_user_id="userA", team_id=ta)
    svc.save_sediment(ctx=res_a.ctx, object_type="case_list", visibility=VISIBILITY_TEAM,
                      payload={"case_id": "cTeamA"})
    # 用户 C 不是 TeamA 成员，却传 ta
    res_c = svc.resolve_tenant(owner_user_id="userC", team_id=ta)
    assert res_c.downgraded is True
    assert res_c.reason_code == "not_a_member"
    assert res_c.ctx.team_id is None  # 降级为私有
    c_items = svc.list_sediment(ctx=res_c.ctx)
    assert len(c_items) == 0


def test_same_team_member_sees_team_shared(stores):
    """同团队成员能看到 team 可见沉淀；但他人 private 仍不可见。"""
    _acc, team, _engine = stores
    svc = TeamService(team)
    ta = svc.create_team(owner_user_id="userA", team_name="TeamA")["team_id"]
    svc.add_member(team_id=ta, member_user_id="userM")
    res_a = svc.resolve_tenant(owner_user_id="userA", team_id=ta)
    svc.save_sediment(ctx=res_a.ctx, object_type="case_favorite", visibility=VISIBILITY_TEAM,
                      payload={"case_id": "cShared"})
    # A 的私有行（不共享团队）
    res_a_priv = svc.resolve_tenant(owner_user_id="userA", team_id=ta)
    svc.save_sediment(ctx=res_a_priv.ctx, object_type="case_favorite", visibility=VISIBILITY_PRIVATE,
                      payload={"case_id": "cAPriv"})
    res_m = svc.resolve_tenant(owner_user_id="userM", team_id=ta)
    assert res_m.downgraded is False
    m_items = svc.list_sediment(ctx=res_m.ctx)
    case_ids = {i.case_id for i in m_items}
    assert "cShared" in case_ids  # 团队共享可见
    assert "cAPriv" not in case_ids  # 他人私有不可见


def test_get_visible_cross_tenant_returns_none(stores):
    """按 object_id 直取也强制租户过滤：跨租户取不到（None）。"""
    _acc, team, _engine = stores
    svc = TeamService(team)
    ta = svc.create_team(owner_user_id="userA", team_name="TeamA")["team_id"]
    res_a = svc.resolve_tenant(owner_user_id="userA", team_id=ta)
    save = svc.save_sediment(ctx=res_a.ctx, object_type="case_favorite", visibility=VISIBILITY_TEAM,
                             payload={"case_id": "cX"})
    oid = save["object_id"]
    # 他人私有态直取该 object_id
    ctx_b = svc.resolve_tenant(owner_user_id="userB").ctx
    assert svc.get_sediment(ctx=ctx_b, object_id=oid) is None


# --- 隔离过滤条件本身（纯逻辑）---
def test_private_context_clause_excludes_team_rows():
    ctx = TenantContext(owner_user_id="u1")  # team_id None
    assert ctx.is_single_user_private() is True
    # 团队态写入会被拒
    with pytest.raises(ValueError):
        assert_write_within_tenant(ctx, team_id="t1", visibility=VISIBILITY_PRIVATE)
    with pytest.raises(ValueError):
        assert_write_within_tenant(ctx, team_id=None, visibility=VISIBILITY_TEAM)


def test_write_team_id_must_match_context():
    ctx = TenantContext(owner_user_id="u1", team_id="tX")
    with pytest.raises(ValueError):
        assert_write_within_tenant(ctx, team_id="tY", visibility=VISIBILITY_TEAM)
    # 一致则通过
    assert_write_within_tenant(ctx, team_id="tX", visibility=VISIBILITY_TEAM)


# --- 写入：含正文 / 未知键被拒，正文绝不入库 ---
def test_sediment_write_rejects_body_keys(stores):
    _acc, team, _engine = stores
    svc = TeamService(team)
    ctx = svc.resolve_tenant(owner_user_id="userA").ctx
    out = svc.save_sediment(ctx=ctx, object_type="case_favorite", visibility=VISIBILITY_PRIVATE,
                            payload={"case_id": "c1", "raw_query": "案情正文应被拒"})
    assert out["ok"] is False
    assert out["reason_code"] == "forbidden_field"
    # 库里不应有该对象
    assert len(svc.list_sediment(ctx=ctx)) == 0


def test_sediment_store_persists_no_body(stores):
    """库内行只含白名单字段值，无正文。"""
    _acc, team, _engine = stores
    svc = TeamService(team)
    ctx = svc.resolve_tenant(owner_user_id="userA").ctx
    svc.save_sediment(ctx=ctx, object_type="case_favorite", visibility=VISIBILITY_PRIVATE,
                      payload={"case_id": "c1", "note": "短备注", "source_anchors": [{"case_id": "c1", "source_chunk_id": "c1-0"}]})
    items = svc.list_sediment(ctx=ctx)
    assert len(items) == 1
    assert items[0].case_id == "c1"
    assert items[0].owner_user_id_hash.startswith("uidh_")


# --- API 层端到端：跨团队串读 = 否 ---
def test_api_cross_team_isolation_end_to_end(enabled_client):
    c, _svc = enabled_client
    token_a = _register_login(c, "alice@x.io")
    token_b = _register_login(c, "bob@x.io")
    ha = {"Authorization": f"Bearer {token_a}"}
    hb = {"Authorization": f"Bearer {token_b}"}
    # A 建团并写 team 可见沉淀
    ta = c.post("/api/team/create", json={"team_name": "TA"}, headers=ha).json()["team"]["team_id"]
    save = c.post(
        "/api/team/sediment",
        json={"object_type": "case_favorite", "team_id": ta, "visibility": "team", "case_id": "cAA"},
        headers=ha,
    )
    assert save.status_code == 200 and save.json()["ok"] is True
    assert save.json()["tenant_downgraded"] is False
    # B 用 A 的 team_id 越权列出 -> 降级私有, 看不到 cAA
    listed = c.post("/api/team/sediment/list", json={"team_id": ta}, headers=hb)
    assert listed.status_code == 200
    body = listed.json()
    assert body["tenant_downgraded"] is True
    assert all(item["case_id"] != "cAA" for item in body["items"])
    assert len(body["items"]) == 0


def test_api_requires_login(enabled_client):
    c, _ = enabled_client
    r = c.post("/api/team/create", json={"team_name": "x"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "TEAM_REQUIRES_LOGIN"


# --- 日志：无凭据 / 无正文 ---
def test_logs_contain_no_credentials_or_body(enabled_client, caplog):
    c, _ = enabled_client
    caplog.set_level(logging.INFO, logger="case_search")
    token = _register_login(c, "zoe@x.io")
    h = {"Authorization": f"Bearer {token}"}
    ta = c.post("/api/team/create", json={"team_name": "SecretTeamName"}, headers=h).json()["team"]["team_id"]
    c.post("/api/team/sediment", json={"object_type": "case_favorite", "team_id": ta, "visibility": "team", "case_id": "cLog", "note": "私密备注内容"}, headers=h)
    text = caplog.text
    assert PW not in text
    assert token not in text
    assert "zoe@x.io" not in text
    assert "私密备注内容" not in text
    assert "user_id_hash=" in text
    assert "team_id_hash=" in text


# --- 团队模块不引入排序 / 检索副作用 ---
def test_team_modules_do_not_import_ranking_or_retrieval():
    import app.team.isolation as iso
    import app.team.models as md
    import app.team.service as svc
    import app.team.store as st

    for mod in (iso, md, svc, st):
        with open(mod.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        for forbidden in ("from app.rerank", "from app.retrieval", "import rerank", "qrels", "relevance_label"):
            assert forbidden not in content, f"{mod.__file__} must not couple to ranking/retrieval ({forbidden})"
