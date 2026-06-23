"""M5-5 沉淀同步与团队共享 focused tests。

覆盖验收点 / 止损点（任一触发即 NO_GO）：
- 默认关闭（ENABLE_TEAM_SHARING=false）时所有同步 / 共享端点 403，回到 M4 本地沉淀末态。
- 同步默认 owner 私有（visibility=private / team_id=None）；不接受 team_id / visibility。
- 同步只接受元数据 / 引用 / 锚点 / 短字段；正文 / 凭据 / 未知键一律被拒，绝不入库。
- 抽查同步 / 共享请求体不含正文与原始案情。
- 共享必须显式动作；只有对象 owner + 目标团队活跃成员可共享；非 owner / 非成员被拒。
- AI 内容承载型（report_template / case_list）无来源锚点不进入共享；
  锚点缺 case_id 或 source_chunk_id 视为非法，拒绝共享。
- 共享后可见性仍由 M5-3 tenant_visibility_clause 唯一承载；取消共享降回私有。
- 持久层不存正文：SedimentationObject / SharedObject 无任何正文列。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import app.api.auth as auth_api
import app.api.sharing as sharing_api
from app.account.store import AccountStore
from app.account.service import AuthService
from app.core.config import Settings
from app.sharing.anchors import (
    REASON_INVALID_ANCHOR,
    REASON_NO_ANCHOR,
    REASON_OK,
    is_valid_anchor,
    validate_anchors_for_share,
)
from app.sharing.models import SharedObject
from app.sharing.service import SharingService
from app.sharing.store import SharingStore
from app.team.isolation import TenantContext, tenant_visibility_clause
from app.team.models import SedimentationObject
from app.team.store import TeamStore

PW = "sup3rsecret-pw"


# ---------------- 单元层：锚点校验（无锚点不进入共享）----------------
def test_valid_anchor_requires_case_id_and_chunk_id():
    assert is_valid_anchor({"case_id": "c1", "source_chunk_id": "k1"})
    assert not is_valid_anchor({"case_id": "c1"})  # 缺 chunk
    assert not is_valid_anchor({"source_chunk_id": "k1"})  # 缺 case
    assert not is_valid_anchor({"case_id": "", "source_chunk_id": "k1"})  # 空 case
    assert not is_valid_anchor("not-a-dict")


def test_report_without_anchor_rejected():
    ok, reason = validate_anchors_for_share(object_type="report_template", anchors=[])
    assert not ok and reason == REASON_NO_ANCHOR


def test_list_with_valid_anchor_ok():
    ok, reason = validate_anchors_for_share(
        object_type="case_list", anchors=[{"case_id": "c1", "source_chunk_id": "k1"}]
    )
    assert ok and reason == REASON_OK


def test_any_invalid_anchor_rejected():
    ok, reason = validate_anchors_for_share(
        object_type="case_list",
        anchors=[{"case_id": "c1", "source_chunk_id": "k1"}, {"case_id": "c2"}],
    )
    assert not ok and reason == REASON_INVALID_ANCHOR


def test_favorite_without_anchor_allowed():
    # 纯引用型（收藏）可不带锚点。
    ok, reason = validate_anchors_for_share(object_type="case_favorite", anchors=[])
    assert ok and reason == REASON_OK


# ---------------- 服务层：同步默认私有 + 无正文上送 ----------------
@pytest.fixture()
def stores():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    team = TeamStore(engine)
    team.init_schema()
    sharing = SharingStore(engine)
    sharing.init_schema()
    return SharingService(sharing, team), team, sharing, engine


def test_sync_defaults_to_owner_private(stores):
    svc, team, _sharing, engine = stores
    result = svc.sync_local(owner_user_id="u1", object_type="case_favorite", payload={"case_id": "c1"})
    assert result.ok
    with Session(engine) as s:
        obj = s.get(SedimentationObject, result.object_id)
    assert obj.visibility == "private"
    assert obj.team_id is None
    assert obj.owner_user_id == "u1"


def test_sync_rejects_body_and_credential_keys(stores):
    svc, _team, _sharing, _engine = stores
    for bad in [{"case_fact_body": "案情正文"}, {"raw_query": "原始查询"}, {"password": "x"}, {"content": "y"}]:
        r = svc.sync_local(owner_user_id="u1", object_type="case_list", payload={**bad})
        assert not r.ok and r.reason_code == "forbidden_field"


def test_sync_rejects_unknown_keys(stores):
    svc, _team, _sharing, _engine = stores
    r = svc.sync_local(owner_user_id="u1", object_type="case_list", payload={"totally_unknown": "z"})
    assert not r.ok


# ---------------- 服务层：共享需显式 + owner + 成员 + 锚点 ----------------
def _make_team_with_member(team: TeamStore, member: str) -> str:
    t = team.create_team(team_name="T")
    team.add_member(team_id=t.team_id, member_user_id=member)
    return t.team_id


def test_share_requires_owner(stores):
    svc, team, _sharing, _engine = stores
    tid = _make_team_with_member(team, "u1")
    # u1 同步一个带锚点的清单
    obj = svc.sync_local(owner_user_id="u1", object_type="case_list",
                         payload={"list_title": "L", "source_anchors": [{"case_id": "c1", "source_chunk_id": "k1"}]})
    # u2 不是 owner -> 拒绝
    r = svc.share_to_team(actor_user_id="u2", object_id=obj.object_id, team_id=tid)
    assert not r.ok and r.reason_code == "not_owner"


def test_share_requires_active_member(stores):
    svc, team, _sharing, _engine = stores
    tid = _make_team_with_member(team, "someone_else")
    obj = svc.sync_local(owner_user_id="u1", object_type="case_favorite", payload={"case_id": "c1"})
    # u1 是 owner 但不是该团队成员 -> 拒绝
    r = svc.share_to_team(actor_user_id="u1", object_id=obj.object_id, team_id=tid)
    assert not r.ok and r.reason_code == "not_a_member"


def test_share_report_without_anchor_rejected(stores):
    svc, team, _sharing, _engine = stores
    tid = _make_team_with_member(team, "u1")
    obj = svc.sync_local(owner_user_id="u1", object_type="report_template", payload={"report_id": "r1"})
    r = svc.share_to_team(actor_user_id="u1", object_id=obj.object_id, team_id=tid)
    assert not r.ok and r.reason_code == "missing_source_anchor"


def test_share_promotes_visibility_and_unshare_reverts(stores):
    svc, team, _sharing, engine = stores
    tid = _make_team_with_member(team, "u1")
    obj = svc.sync_local(owner_user_id="u1", object_type="case_list",
                         payload={"list_title": "L", "source_anchors": [{"case_id": "c1", "source_chunk_id": "k1"}]})
    # 共享前：团队成员（另一个用户 u2，先入团）看不到（仍 private）。
    team.add_member(team_id=tid, member_user_id="u2")
    ctx_u2 = TenantContext(owner_user_id="u2", team_id=tid)
    assert team.list_visible(ctx=ctx_u2) == []
    # 共享：owner u1 显式共享给团队。
    r = svc.share_to_team(actor_user_id="u1", object_id=obj.object_id, team_id=tid)
    assert r.ok and r.visibility == "team" and r.anchor_count == 1
    # 共享后：可见性由 M5-3 唯一过滤点承载，u2 现在能看到。
    visible = team.list_visible(ctx=ctx_u2)
    assert [o.object_id for o in visible] == [obj.object_id]
    # 取消共享：降回私有，u2 再次看不到。
    u = svc.unshare(actor_user_id="u1", object_id=obj.object_id)
    assert u.ok and u.visibility == "private"
    assert team.list_visible(ctx=ctx_u2) == []


def test_unshare_requires_owner(stores):
    svc, team, _sharing, _engine = stores
    tid = _make_team_with_member(team, "u1")
    obj = svc.sync_local(owner_user_id="u1", object_type="case_favorite", payload={"case_id": "c1"})
    r = svc.unshare(actor_user_id="intruder", object_id=obj.object_id)
    assert not r.ok and r.reason_code == "not_owner"


def test_cross_team_not_visible_after_share(stores):
    """共享给团队 A 的对象，团队 B 的成员一律不可见（跨团队隔离不被共享破坏）。"""
    svc, team, _sharing, _engine = stores
    tid_a = _make_team_with_member(team, "u1")
    tid_b = _make_team_with_member(team, "u3")
    obj = svc.sync_local(owner_user_id="u1", object_type="case_list",
                         payload={"list_title": "L", "source_anchors": [{"case_id": "c1", "source_chunk_id": "k1"}]})
    svc.share_to_team(actor_user_id="u1", object_id=obj.object_id, team_id=tid_a)
    # 团队 B 成员 u3 在团队 B 上下文里看不到团队 A 的共享对象。
    ctx_b = TenantContext(owner_user_id="u3", team_id=tid_b)
    assert team.list_visible(ctx=ctx_b) == []


def test_persistence_layer_has_no_body_columns(stores):
    """SedimentationObject / SharedObject 表里不存在任何正文列。"""
    sediment_cols = set(SedimentationObject.__table__.columns.keys())
    shared_cols = set(SharedObject.__table__.columns.keys())
    forbidden = {"raw_query", "query", "case_fact_body", "candidate_body", "chunk_body",
                 "judgment_long_text", "summary_body", "holding_body", "compare_body",
                 "user_free_long_text", "text", "content", "password", "token", "session_token"}
    assert sediment_cols & forbidden == set()
    assert shared_cols & forbidden == set()


# ---------------- API 层：flag 开关 + 端点鉴权 + 请求体抽查 ----------------
@pytest.fixture()
def api_ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    acc = AccountStore(engine)
    acc.init_schema()
    team = TeamStore(engine)
    team.init_schema()
    sharing = SharingStore(engine)
    sharing.init_schema()
    return acc, team, sharing, engine


@pytest.fixture()
def enabled_client(api_ctx, monkeypatch):
    acc, team, sharing, _ = api_ctx
    s = Settings(DEEPSEEK_API_KEY="k", ENABLE_ACCOUNT_SYSTEM=True,
                 ENABLE_TEAM_WORKSPACE=True, ENABLE_TEAM_SHARING=True)
    monkeypatch.setattr(auth_api, "settings", s)
    monkeypatch.setattr(sharing_api, "settings", s)
    auth_api.set_auth_service_for_test(AuthService(acc))
    sharing_api.set_sharing_service_for_test(SharingService(sharing, team))
    from app.main import app

    yield TestClient(app), team
    auth_api.set_auth_service_for_test(None)
    sharing_api.set_sharing_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(sharing_api, "settings",
                        Settings(DEEPSEEK_API_KEY="k", ENABLE_TEAM_SHARING=False))
    sharing_api.set_sharing_service_for_test(None)
    from app.main import app

    return TestClient(app)


def _register_login(client: TestClient, login_name: str) -> str:
    client.post("/api/auth/register", json={"login_name": login_name, "password": PW, "display_name": "d"})
    r = client.post("/api/auth/login", json={"login_name": login_name, "password": PW})
    return r.json()["session_token"]


def test_disabled_endpoints_return_403(disabled_client):
    for path, payload in [
        ("/api/sharing/sync", {"object_type": "case_favorite"}),
        ("/api/sharing/share", {"object_id": "o", "team_id": "t"}),
        ("/api/sharing/unshare", {"object_id": "o"}),
        ("/api/sharing/team", {"team_id": "t"}),
    ]:
        resp = disabled_client.post(path, json=payload)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "TEAM_SHARING_DISABLED"
    assert disabled_client.get("/api/sharing/mine").status_code == 403


def test_endpoints_require_login(enabled_client):
    client, _ = enabled_client
    assert client.post("/api/sharing/sync", json={"object_type": "case_favorite"}).status_code == 401


def test_sync_rejects_body_field_via_schema(enabled_client):
    """schema extra=forbid：请求体带正文键 -> 422，绝不入库。"""
    client, _ = enabled_client
    token = _register_login(client, "u@x.io")
    resp = client.post("/api/sharing/sync",
                       json={"object_type": "case_list", "case_fact_body": "案情正文"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


def test_sync_then_share_flow_no_body(enabled_client):
    client, team = enabled_client
    token = _register_login(client, "owner@x.io")
    owner_uid = auth_api._get_service().resolve_session(session_token=token).account.user_id  # noqa: SLF001
    # owner 建团并加入
    t = team.create_team(team_name="T")
    team.add_member(team_id=t.team_id, member_user_id=owner_uid)
    # 同步一个带合法锚点的清单（默认私有）。
    sync_body = {
        "object_type": "case_list",
        "list_title": "我的类案清单",
        "case_number": "(2021)京01民终123号",
        "source_anchors": [{"case_id": "c1", "source_chunk_id": "chunk_7"}],
    }
    sync = client.post("/api/sharing/sync", json=sync_body, headers={"Authorization": f"Bearer {token}"})
    assert sync.status_code == 200
    body = sync.json()
    assert body["ok"] and body["visibility"] == "private"
    object_id = body["object_id"]
    # 显式共享给团队 -> 200 team。
    share = client.post("/api/sharing/share", json={"object_id": object_id, "team_id": t.team_id},
                        headers={"Authorization": f"Bearer {token}"})
    assert share.status_code == 200 and share.json()["visibility"] == "team"
    # 团队共享列表可查，且响应不含正文 / 凭据 / 原始锚点内容。
    listing = client.post("/api/sharing/team", json={"team_id": t.team_id},
                          headers={"Authorization": f"Bearer {token}"})
    assert listing.status_code == 200
    blob = listing.text
    assert "案情" not in blob and PW not in blob and "chunk_7" not in blob
    # owner / team 标识以哈希呈现。
    items = listing.json()["items"]
    assert items and items[0]["owner_user_id_hash"].startswith("uidh_")
    assert items[0]["shared_with_team_id_hash"].startswith("tidh_")


def test_share_no_anchor_report_rejected_via_api(enabled_client):
    client, team = enabled_client
    token = _register_login(client, "rep@x.io")
    owner_uid = auth_api._get_service().resolve_session(session_token=token).account.user_id  # noqa: SLF001
    t = team.create_team(team_name="T")
    team.add_member(team_id=t.team_id, member_user_id=owner_uid)
    sync = client.post("/api/sharing/sync", json={"object_type": "report_template", "report_id": "r1"},
                       headers={"Authorization": f"Bearer {token}"})
    object_id = sync.json()["object_id"]
    share = client.post("/api/sharing/share", json={"object_id": object_id, "team_id": t.team_id},
                        headers={"Authorization": f"Bearer {token}"})
    assert share.status_code == 200
    assert share.json()["ok"] is False and share.json()["reason_code"] == "missing_source_anchor"


def test_non_member_cannot_list_team_shares(enabled_client):
    client, team = enabled_client
    token = _register_login(client, "outsider@x.io")
    t = team.create_team(team_name="T")  # outsider 不是成员
    resp = client.post("/api/sharing/team", json={"team_id": t.team_id},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "SHARING_NOT_MEMBER"


def test_sharing_module_has_no_retrieval_side_effects():
    """共享模块不引入检索 / rerank / 排序副作用（不 import retrieval/rerank）。"""
    import app.sharing.service as svc_mod
    import app.sharing.store as store_mod
    import app.sharing.anchors as anchors_mod

    for mod in (svc_mod, store_mod, anchors_mod):
        src = mod.__file__
        with open(src, encoding="utf-8") as f:
            lines = f.read().splitlines()
        import_lines = [ln for ln in lines if ln.lstrip().startswith(("import ", "from "))]
        for ln in import_lines:
            assert "retrieval" not in ln and "rerank" not in ln
