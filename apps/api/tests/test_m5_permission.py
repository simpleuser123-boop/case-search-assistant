"""M5-4 权限分级与对象级访问控制 focused tests。

覆盖验收点（越权未拦截 / 默认授予过宽 / 审计含正文 / flag 默认开 即 NO_GO）：
- 默认关闭（ENABLE_PERMISSION_TIERING=false）时所有权限端点 403，回到 M5-3 / M4 末态。
- 默认最小权限：非 owner 未显式授权对 private 对象有效权限为 none，读/写/删全被拒。
- owner 拥有全权；显式 grant 后 grantee 获得 viewer/editor，越权写仍被拒。
- private 对象不因团队成员身份放权（团队角色只对 team 可见对象生效）。
- 越权读 / 写 / 授权一律被拒并写审计（result=deny）。
- 审计只含脱敏字段（actor/object 哈希、action、result、reason_code、permission_level），
  无正文 / 无凭据 / 无原始 object_id 明文。
- 权限模块不引入排序 / 检索副作用（不 import retrieval/rerank）。
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

import app.api.auth as auth_api
import app.api.permission as perm_api
from app.account.service import AuthService
from app.account.store import AccountStore
from app.core.config import Settings
from app.permission.access import (
    ACTION_DELETE,
    ACTION_READ,
    ACTION_WRITE,
    ObjectAccessInput,
    authorize,
)
from app.permission.models import (
    PERMISSION_EDITOR,
    PERMISSION_NONE,
    PERMISSION_OWNER,
    PERMISSION_VIEWER,
    PermissionAudit,
    hash_object_id,
)
from app.permission.service import PermissionService
from app.permission.store import PermissionStore
from app.team.isolation import TenantContext
from app.team.store import TeamStore

PW = "sup3rsecret-pw"


# ---------------- 单元层：access 判定（默认最小权限）----------------
def test_owner_has_full_permission():
    f = ObjectAccessInput(actor_user_id="u1", owner_user_id="u1",
                          object_visibility="private", object_team_id=None)
    assert authorize(ACTION_READ, f).allowed
    assert authorize(ACTION_WRITE, f).allowed
    assert authorize(ACTION_DELETE, f).allowed
    assert authorize(ACTION_READ, f).effective_level == PERMISSION_OWNER


def test_non_owner_private_no_grant_denied_by_default():
    """默认最小权限：非 owner 对 private 对象无任何授权 -> 一律拒绝。"""
    f = ObjectAccessInput(actor_user_id="u2", owner_user_id="u1",
                          object_visibility="private", object_team_id=None)
    assert not authorize(ACTION_READ, f).allowed
    assert not authorize(ACTION_WRITE, f).allowed
    assert not authorize(ACTION_DELETE, f).allowed
    assert authorize(ACTION_READ, f).effective_level == PERMISSION_NONE


def test_viewer_grant_can_read_not_write():
    f = ObjectAccessInput(actor_user_id="u2", owner_user_id="u1",
                          object_visibility="private", object_team_id=None,
                          granted_level=PERMISSION_VIEWER)
    assert authorize(ACTION_READ, f).allowed
    assert not authorize(ACTION_WRITE, f).allowed
    assert not authorize(ACTION_DELETE, f).allowed


def test_editor_grant_can_write_not_delete():
    f = ObjectAccessInput(actor_user_id="u2", owner_user_id="u1",
                          object_visibility="private", object_team_id=None,
                          granted_level=PERMISSION_EDITOR)
    assert authorize(ACTION_READ, f).allowed
    assert authorize(ACTION_WRITE, f).allowed
    assert not authorize(ACTION_DELETE, f).allowed  # delete 仍仅限 owner


def test_team_role_only_applies_to_team_visible_objects():
    """private 对象不因团队成员身份放权；team 可见对象才按团队角色折算。"""
    private_obj = ObjectAccessInput(actor_user_id="u2", owner_user_id="u1",
                                    object_visibility="private", object_team_id="t1",
                                    actor_team_role_level=PERMISSION_EDITOR)
    assert not authorize(ACTION_READ, private_obj).allowed  # private 不放权
    team_obj = ObjectAccessInput(actor_user_id="u2", owner_user_id="u1",
                                 object_visibility="team", object_team_id="t1",
                                 actor_team_role_level=PERMISSION_VIEWER)
    assert authorize(ACTION_READ, team_obj).allowed
    assert not authorize(ACTION_WRITE, team_obj).allowed


# ---------------- service 层：对象级鉴权 + 审计 ----------------
@pytest.fixture()
def svc_stores():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    team = TeamStore(engine)
    team.init_schema()
    perm = PermissionStore(engine)
    perm.init_schema()
    return team, perm, engine


def _make_private_obj(team_store: TeamStore, owner: str):
    ctx = TenantContext(owner_user_id=owner)
    return team_store.create_sediment(ctx=ctx, object_type="case_favorite", visibility="private",
                                      payload={"case_id": "c1", "note": "n"})


def test_service_owner_read_allowed_and_audited(svc_stores):
    team, perm, _ = svc_stores
    svc = PermissionService(perm, team)
    obj = _make_private_obj(team, "owner1")
    res = svc.read_object(actor_user_id="owner1", object_id=obj.object_id)
    assert res.allowed
    assert res.object_view is not None
    audits = perm.list_audit()
    assert any(a.action == "read" and a.result == "allow" for a in audits)


def test_service_cross_user_read_denied_and_audited(svc_stores):
    """越权读他人 private 对象 -> 拒绝 + 写 deny 审计。"""
    team, perm, _ = svc_stores
    svc = PermissionService(perm, team)
    obj = _make_private_obj(team, "owner1")
    res = svc.read_object(actor_user_id="intruder", object_id=obj.object_id)
    assert not res.allowed
    assert res.object_view is None
    deny = [a for a in perm.list_audit() if a.result == "deny" and a.action == "read"]
    assert deny, "越权读必须写 deny 审计"
    assert deny[0].object_id_hash == hash_object_id(obj.object_id)


def test_grant_then_read_allowed_then_revoke_denied(svc_stores):
    team, perm, _ = svc_stores
    svc = PermissionService(perm, team)
    obj = _make_private_obj(team, "owner1")
    # 授权前：被拒
    assert not svc.read_object(actor_user_id="u2", object_id=obj.object_id).allowed
    # owner 授 viewer
    g = svc.grant(actor_user_id="owner1", object_id=obj.object_id, grantee_user_id="u2", permission_level="viewer")
    assert g["ok"]
    assert svc.read_object(actor_user_id="u2", object_id=obj.object_id).allowed
    # viewer 不能写
    assert not svc.authorize_action(actor_user_id="u2", object_id=obj.object_id, action=ACTION_WRITE).allowed
    # 撤销后：再次被拒
    assert svc.revoke(actor_user_id="owner1", object_id=obj.object_id, grantee_user_id="u2")["ok"]
    assert not svc.read_object(actor_user_id="u2", object_id=obj.object_id).allowed


def test_non_owner_cannot_grant(svc_stores):
    """非 owner（即便有 editor 授权）也不能对外授权。"""
    team, perm, _ = svc_stores
    svc = PermissionService(perm, team)
    obj = _make_private_obj(team, "owner1")
    svc.grant(actor_user_id="owner1", object_id=obj.object_id, grantee_user_id="u2", permission_level="editor")
    # u2 有 editor，但不是 owner -> 不能再授权给 u3
    r = svc.grant(actor_user_id="u2", object_id=obj.object_id, grantee_user_id="u3", permission_level="viewer")
    assert not r["ok"]
    assert svc.read_object(actor_user_id="u3", object_id=obj.object_id).allowed is False


def test_assign_role_requires_owner(svc_stores):
    team, perm, _ = svc_stores
    svc = PermissionService(perm, team)
    # 无 owner 角色的 actor 不能分配角色
    r = svc.assign_role(actor_user_id="rando", team_id="t1", member_user_id="x", role="editor")
    assert not r["ok"]
    assert r["reason_code"] == "not_owner"
    # bootstrap owner 后可分配
    svc.bootstrap_owner(team_id="t1", owner_user_id="boss")
    r2 = svc.assign_role(actor_user_id="boss", team_id="t1", member_user_id="x", role="editor")
    assert r2["ok"]


def test_audit_has_no_body_or_credentials(svc_stores):
    """审计行只含脱敏字段，无正文 / 凭据 / 原始 object_id 明文。"""
    team, perm, _ = svc_stores
    svc = PermissionService(perm, team)
    obj = _make_private_obj(team, "owner1")
    svc.read_object(actor_user_id="intruder", object_id=obj.object_id)
    svc.grant(actor_user_id="owner1", object_id=obj.object_id, grantee_user_id="u2", permission_level="viewer")
    for a in perm.list_audit():
        blob = f"{a.actor_user_id_hash}{a.object_id_hash}{a.action}{a.result}{a.reason_code}{a.permission_level}"
        assert PW not in blob
        assert "case_fact" not in blob and "raw_query" not in blob
        # 原始 object_id 不得出现在审计（只存哈希）
        assert obj.object_id not in blob
        assert a.actor_user_id_hash.startswith("uidh_")
        if a.object_id_hash:
            assert a.object_id_hash.startswith("oidh_")


# ---------------- API 层：flag 开关 + 端点鉴权 ----------------
@pytest.fixture()
def api_ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    acc = AccountStore(engine)
    acc.init_schema()
    team = TeamStore(engine)
    team.init_schema()
    perm = PermissionStore(engine)
    perm.init_schema()
    return acc, team, perm, engine


@pytest.fixture()
def enabled_client(api_ctx, monkeypatch):
    acc, team, perm, _ = api_ctx
    s = Settings(DEEPSEEK_API_KEY="k", ENABLE_ACCOUNT_SYSTEM=True,
                 ENABLE_TEAM_WORKSPACE=True, ENABLE_PERMISSION_TIERING=True)
    monkeypatch.setattr(auth_api, "settings", s)
    monkeypatch.setattr(perm_api, "settings", s)
    auth_api.set_auth_service_for_test(AuthService(acc))
    perm_api.set_permission_service_for_test(PermissionService(perm, team))
    from app.main import app

    yield TestClient(app), team
    auth_api.set_auth_service_for_test(None)
    perm_api.set_permission_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(perm_api, "settings",
                        Settings(DEEPSEEK_API_KEY="k", ENABLE_PERMISSION_TIERING=False))
    perm_api.set_permission_service_for_test(None)
    from app.main import app

    return TestClient(app)


def _register_login(client: TestClient, login_name: str) -> str:
    client.post("/api/auth/register", json={"login_name": login_name, "password": PW, "display_name": "d"})
    r = client.post("/api/auth/login", json={"login_name": login_name, "password": PW})
    return r.json()["session_token"]


def test_disabled_endpoints_return_403(disabled_client):
    for path, payload in [
        ("/api/permission/role", {"team_id": "t", "member_user_id": "m", "role": "editor"}),
        ("/api/permission/grant", {"object_id": "o", "grantee_user_id": "g", "permission_level": "viewer"}),
        ("/api/permission/object/read", {"object_id": "o"}),
    ]:
        resp = disabled_client.post(path, json=payload)
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PERMISSION_TIERING_DISABLED"
    resp = disabled_client.get("/api/permission/audit")
    assert resp.status_code == 403


def test_endpoints_require_login(enabled_client):
    client, _ = enabled_client
    resp = client.post("/api/permission/object/read", json={"object_id": "o"})
    assert resp.status_code == 401


def test_api_cross_user_read_denied(enabled_client):
    client, team = enabled_client
    # owner 注册并创建一个 private 对象（直接经 store，模拟 M5-3 沉淀）。
    owner_token = _register_login(client, "owner@x.io")
    owner_session = auth_api._get_service().resolve_session(session_token=owner_token)  # noqa: SLF001
    owner_uid = owner_session.account.user_id
    obj = team.create_sediment(ctx=TenantContext(owner_user_id=owner_uid),
                               object_type="case_favorite", visibility="private",
                               payload={"case_id": "c1"})
    # owner 读自己的对象 -> 200
    r_owner = client.post("/api/permission/object/read", json={"object_id": obj.object_id},
                          headers={"Authorization": f"Bearer {owner_token}"})
    assert r_owner.status_code == 200
    assert r_owner.json()["effective_level"] == "owner"
    # 入侵者登录读他人对象 -> 403
    intruder_token = _register_login(client, "intruder@x.io")
    r_intruder = client.post("/api/permission/object/read", json={"object_id": obj.object_id},
                             headers={"Authorization": f"Bearer {intruder_token}"})
    assert r_intruder.status_code == 403
    assert r_intruder.json()["error"]["code"] == "PERMISSION_DENIED"


def test_api_grant_flow_and_audit(enabled_client):
    client, team = enabled_client
    owner_token = _register_login(client, "owner2@x.io")
    owner_uid = auth_api._get_service().resolve_session(session_token=owner_token).account.user_id  # noqa: SLF001
    grantee_token = _register_login(client, "grantee@x.io")
    grantee_uid = auth_api._get_service().resolve_session(session_token=grantee_token).account.user_id  # noqa: SLF001
    obj = team.create_sediment(ctx=TenantContext(owner_user_id=owner_uid),
                               object_type="case_list", visibility="private",
                               payload={"list_title": "L"})
    # 授权前 grantee 读 -> 403
    assert client.post("/api/permission/object/read", json={"object_id": obj.object_id},
                       headers={"Authorization": f"Bearer {grantee_token}"}).status_code == 403
    # owner 授权 viewer
    g = client.post("/api/permission/grant",
                    json={"object_id": obj.object_id, "grantee_user_id": grantee_uid, "permission_level": "viewer"},
                    headers={"Authorization": f"Bearer {owner_token}"})
    assert g.status_code == 200
    # 授权后 grantee 读 -> 200
    assert client.post("/api/permission/object/read", json={"object_id": obj.object_id},
                       headers={"Authorization": f"Bearer {grantee_token}"}).status_code == 200
    # 审计可查（owner 视角）
    audit = client.get("/api/permission/audit", headers={"Authorization": f"Bearer {owner_token}"})
    assert audit.status_code == 200
    items = audit.json()["items"]
    assert any(i["action"] == "grant" and i["result"] == "allow" for i in items)
    # 审计响应不含正文 / 凭据 / 原始 object_id
    blob = audit.text
    assert PW not in blob and obj.object_id not in blob


def test_grantee_cannot_grant_to_others(enabled_client):
    """grantee（editor）不是 owner -> 不能再对外授权（防止权限提升）。"""
    client, team = enabled_client
    owner_token = _register_login(client, "owner3@x.io")
    owner_uid = auth_api._get_service().resolve_session(session_token=owner_token).account.user_id  # noqa: SLF001
    g_token = _register_login(client, "ed@x.io")
    g_uid = auth_api._get_service().resolve_session(session_token=g_token).account.user_id  # noqa: SLF001
    obj = team.create_sediment(ctx=TenantContext(owner_user_id=owner_uid),
                               object_type="case_favorite", visibility="private", payload={"case_id": "c9"})
    client.post("/api/permission/grant",
                json={"object_id": obj.object_id, "grantee_user_id": g_uid, "permission_level": "editor"},
                headers={"Authorization": f"Bearer {owner_token}"})
    r = client.post("/api/permission/grant",
                    json={"object_id": obj.object_id, "grantee_user_id": "victim", "permission_level": "viewer"},
                    headers={"Authorization": f"Bearer {g_token}"})
    assert r.status_code == 403


def test_no_retrieval_or_ranking_side_effects():
    """权限模块不得 import 检索 / rerank / 主排序（防止改变既有默认行为）。"""
    import re

    import app.permission.access as a
    import app.permission.service as s
    import app.permission.store as st
    # 只检查真实 import 语句，不误伤 docstring 里出现的字样。
    import_re = re.compile(r"^\s*(?:from|import)\s+\S+", re.MULTILINE)
    for mod in (a, s, st):
        src = open(mod.__file__).read()
        imports = "\n".join(import_re.findall(src))
        assert "retrieval" not in imports
        assert "rerank" not in imports
        assert "vector_retrieval" not in imports
