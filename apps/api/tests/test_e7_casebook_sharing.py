"""E7-4 案件协作工作台 CaseFolder 共享与协作权限测试。

验证（对应文档 22 §E7-4 验收 / 测试要求）：
- 共享切换：owner 把 private->team 成功（owner 须为该 team 活跃成员）；team->private 成功；
  非 owner 切换被拒（404，不泄露他人协作夹存在性）。
- 对象级鉴权矩阵（复用 M5 多租户）：
  * private folder：仅 owner 可读写，其它人（含同 team）一律 404。
  * team folder：owner 可读写；同 team 成员可读、不可写；非 team 成员 404。
  * 跨租户：不同 team / 无 team 关系一律 404，不泄露存在性。
- visibility 仅 private|team；public 或非法值被拒（422，schema Literal 层）。
- ENABLE_CASEBOOK=false：共享端点 403 安全降级。
- 共享不放开正文：共享后读取仍零正文、引用仍只带锚点。
- 审计 / 日志只记元数据（user_id_hash / case_folder_id_hash / visibility / has_team），无正文 / 原始案情。

红线：fixture 只用短假数据 / hash / case_id / source_chunk_id / 元数据，绝不写真实长裁判正文。
复用 M5 团队成员关系（TeamService）与租户隔离，不另起一套权限模型。
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

import app.api.auth as auth_api
import app.api.team as team_api
import app.casebook.router as casebook_router_mod
from app.account.store import AccountStore
from app.account.service import AuthService
from app.core.config import Settings
from app.casebook.service import CasebookService
from app.casebook.store import CaseFolderStore
from app.team.service import TeamService
from app.team.store import TeamStore
from app.main import app

PW = "sup3rsecret-pw"

# 共享后读取响应绝不允许出现的裁判 / 起草正文 / 胜负结论型字段。
FORBIDDEN_BODY_FIELDS = (
    "chunk_text",
    "judgment_text",
    "judgment_full_text",
    "summary_text",
    "draft_body",
    "generated_text",
    "case_summary_text",
    "win_probability",
    "outcome_prediction",
    "verdict",
    "raw_case",
)


@pytest.fixture()
def engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


@pytest.fixture()
def client(engine, monkeypatch):
    """ENABLE_CASEBOOK=true + ENABLE_ACCOUNT_SYSTEM=true，account / casebook / team 同引擎。

    casebook 共享复用 M5 TeamService（同一成员关系账本），三者共享同一临时 sqlite engine。
    """
    acc = AccountStore(engine)
    acc.init_schema()
    cb_store = CaseFolderStore(engine)
    cb_store.init_schema()
    team_store = TeamStore(engine)
    team_store.init_schema()

    s = Settings(
        DEEPSEEK_API_KEY="k",
        ENABLE_ACCOUNT_SYSTEM=True,
        ENABLE_CASEBOOK=True,
    )
    monkeypatch.setattr(auth_api, "settings", s)
    monkeypatch.setattr(casebook_router_mod, "settings", s)
    auth_api.set_auth_service_for_test(AuthService(acc))
    casebook_router_mod.set_casebook_service_for_test(CasebookService(store=cb_store))
    team_api.set_team_service_for_test(TeamService(team_store))

    client = TestClient(app)
    yield client, team_store
    auth_api.set_auth_service_for_test(None)
    casebook_router_mod.set_casebook_service_for_test(None)
    team_api.set_team_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(
        casebook_router_mod,
        "settings",
        Settings(DEEPSEEK_API_KEY="k", ENABLE_CASEBOOK=False),
    )
    casebook_router_mod.set_casebook_service_for_test(None)
    return TestClient(app)


# --- helpers ---------------------------------------------------------------------

def _register_login(client: TestClient, login_name: str) -> str:
    client.post(
        "/api/auth/register",
        json={"login_name": login_name, "password": PW, "display_name": "d"},
    )
    r = client.post("/api/auth/login", json={"login_name": login_name, "password": PW})
    return r.json()["session_token"]


def _user_id(client: TestClient, token: str) -> str:
    r = client.get("/api/auth/session", headers=_auth(token))
    return r.json()["account"]["user_id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _team_auth(token: str, team_id: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Team-Id": team_id}


def _valid_create_body() -> dict:
    return {
        "search_profile_summary": {
            "case_cause": "盗窃",
            "region": "某省",
            "trial_level_preference": "一审",
            "dispute_focus_keywords": ["数额"],
            "query_text": "盗窃数额认定",
        },
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
        "draft_descriptors": [],
        "title": "盗窃数额类案归集",
        "note": "本夹聚合数额认定争议",
        "tag": "刑事",
    }


def _create_folder(client: TestClient, token: str) -> str:
    resp = client.post(
        "/api/casebook/folders", json=_valid_create_body(), headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["case_folder_id"]


def _make_team(team_store: TeamStore, *members: str) -> str:
    team = team_store.create_team(team_name="t", reason_code="test")
    for m in members:
        team_store.add_member(team_id=team.team_id, member_user_id=m, reason_code="test")
    return team.team_id


# --- 1) 共享切换：private -> team -> private --------------------------------------

def test_owner_share_private_to_team_then_unshare(client):
    cl, team_store = client
    token = _register_login(cl, "owner@x.io")
    uid = _user_id(cl, token)
    team_id = _make_team(team_store, uid)
    folder_id = _create_folder(cl, token)

    # private -> team（owner 是该 team 活跃成员）。
    shared = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team", "team_id": team_id},
        headers=_auth(token),
    )
    assert shared.status_code == 200, shared.text
    data = shared.json()
    assert data["visibility"] == "team"
    assert data["team_id"] == team_id

    # team -> private（team_id 一并清空）。
    unshared = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "private"},
        headers=_auth(token),
    )
    assert unshared.status_code == 200, unshared.text
    udata = unshared.json()
    assert udata["visibility"] == "private"
    assert udata["team_id"] is None


def test_share_to_team_requires_active_membership(client):
    """owner 不是目标 team 活跃成员 -> 404（不泄露 team / folder 存在性）。"""
    cl, team_store = client
    token = _register_login(cl, "nonmember@x.io")
    # 团队只含别人，不含本 owner。
    other = _register_login(cl, "other_member@x.io")
    other_uid = _user_id(cl, other)
    team_id = _make_team(team_store, other_uid)
    folder_id = _create_folder(cl, token)

    resp = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team", "team_id": team_id},
        headers=_auth(token),
    )
    assert resp.status_code == 404


def test_share_to_team_without_team_id_rejected(client):
    cl, _ = client
    token = _register_login(cl, "noteam@x.io")
    folder_id = _create_folder(cl, token)
    resp = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team"},
        headers=_auth(token),
    )
    assert resp.status_code == 400


def test_non_owner_share_denied_404(client):
    """非 owner 切换共享 -> 404（不泄露他人协作夹存在性）。"""
    cl, team_store = client
    owner = _register_login(cl, "owner2@x.io")
    owner_uid = _user_id(cl, owner)
    attacker = _register_login(cl, "attacker@x.io")
    attacker_uid = _user_id(cl, attacker)
    team_id = _make_team(team_store, owner_uid, attacker_uid)
    folder_id = _create_folder(cl, owner)

    # 即便 attacker 与 owner 同 team，attacker 也不能改 owner 的 folder 可见性。
    resp = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team", "team_id": team_id},
        headers=_auth(attacker),
    )
    assert resp.status_code == 404


# --- 2) 对象级鉴权矩阵 ------------------------------------------------------------

def test_private_folder_only_owner(client):
    """private folder：仅 owner 可读，其它人（含同 team）一律 404。"""
    cl, team_store = client
    owner = _register_login(cl, "p_owner@x.io")
    owner_uid = _user_id(cl, owner)
    mate = _register_login(cl, "p_mate@x.io")
    mate_uid = _user_id(cl, mate)
    team_id = _make_team(team_store, owner_uid, mate_uid)
    folder_id = _create_folder(cl, owner)  # 默认 private

    # owner 可读。
    assert cl.get(f"/api/casebook/folders/{folder_id}", headers=_auth(owner)).status_code == 200
    # 同 team 成员带 team 上下文也读不到他人 private 行 -> 404。
    assert (
        cl.get(f"/api/casebook/folders/{folder_id}", headers=_team_auth(mate, team_id)).status_code
        == 404
    )


def test_team_folder_owner_rw_member_ro_nonmember_404(client):
    """team folder：owner 读写；同 team 成员可读、不可写；非成员 404。"""
    cl, team_store = client
    owner = _register_login(cl, "t_owner@x.io")
    owner_uid = _user_id(cl, owner)
    mate = _register_login(cl, "t_mate@x.io")
    mate_uid = _user_id(cl, mate)
    outsider = _register_login(cl, "t_outsider@x.io")
    team_id = _make_team(team_store, owner_uid, mate_uid)
    folder_id = _create_folder(cl, owner)
    # owner 共享给 team。
    assert (
        cl.post(
            f"/api/casebook/folders/{folder_id}/share",
            json={"visibility": "team", "team_id": team_id},
            headers=_auth(owner),
        ).status_code
        == 200
    )

    # 同 team 成员带 team 上下文可读。
    read_mate = cl.get(f"/api/casebook/folders/{folder_id}", headers=_team_auth(mate, team_id))
    assert read_mate.status_code == 200
    assert read_mate.json()["visibility"] == "team"

    # 同 team 成员不可写（PUT 非 owner -> 404）。
    upd = cl.put(
        f"/api/casebook/folders/{folder_id}",
        json=_valid_create_body(),
        headers=_team_auth(mate, team_id),
    )
    assert upd.status_code == 404

    # 同 team 成员不可改可见性（share 非 owner -> 404）。
    share_mate = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "private"},
        headers=_auth(mate),
    )
    assert share_mate.status_code == 404

    # 非 team 成员（不带 / 带任意 team 上下文）一律 404。
    assert cl.get(f"/api/casebook/folders/{folder_id}", headers=_auth(outsider)).status_code == 404


def test_team_folder_visible_in_member_list(client):
    """同 team 成员带 team 上下文列出时能看到 team 共享夹；非成员看不到。"""
    cl, team_store = client
    owner = _register_login(cl, "l_owner@x.io")
    owner_uid = _user_id(cl, owner)
    mate = _register_login(cl, "l_mate@x.io")
    mate_uid = _user_id(cl, mate)
    team_id = _make_team(team_store, owner_uid, mate_uid)
    folder_id = _create_folder(cl, owner)
    cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team", "team_id": team_id},
        headers=_auth(owner),
    )

    # mate 带 team 上下文列出可见。
    lst = cl.get("/api/casebook/folders", headers=_team_auth(mate, team_id))
    assert lst.status_code == 200
    ids = [f["case_folder_id"] for f in lst.json()["folders"]]
    assert folder_id in ids

    # mate 不带 team 上下文（单用户私有态）看不到。
    lst_private = cl.get("/api/casebook/folders", headers=_auth(mate))
    assert lst_private.status_code == 200
    assert folder_id not in [f["case_folder_id"] for f in lst_private.json()["folders"]]


def test_cross_tenant_no_existence_leak(client):
    """跨租户（不属于该 team 的成员）读取 team folder -> 404，不泄露存在性。"""
    cl, team_store = client
    owner = _register_login(cl, "x_owner@x.io")
    owner_uid = _user_id(cl, owner)
    team_a = _make_team(team_store, owner_uid)
    folder_id = _create_folder(cl, owner)
    cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team", "team_id": team_a},
        headers=_auth(owner),
    )

    # 另一个用户即便伪造 X-Team-Id=team_a（非成员），resolve_tenant 降级私有 -> 404。
    intruder = _register_login(cl, "x_intruder@x.io")
    resp = cl.get(f"/api/casebook/folders/{folder_id}", headers=_team_auth(intruder, team_a))
    assert resp.status_code == 404


# --- 3) visibility 只 private|team；public / 非法值被拒 ---------------------------

@pytest.mark.parametrize("bad", ["public", "global", "world", "", "Team", "PRIVATE"])
def test_share_visibility_only_private_or_team(client, bad):
    cl, _ = client
    token = _register_login(cl, f"vis_{abs(hash(bad)) % 9999}@x.io")
    folder_id = _create_folder(cl, token)
    resp = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": bad, "team_id": "t1"},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


def test_share_rejects_extra_body_keys(client):
    """共享端点 extra=forbid：夹带摘要 / 引用 / 正文键 -> 422（共享零正文承载）。"""
    cl, team_store = client
    token = _register_login(cl, "extra@x.io")
    uid = _user_id(cl, token)
    team_id = _make_team(team_store, uid)
    folder_id = _create_folder(cl, token)
    resp = cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={
            "visibility": "team",
            "team_id": team_id,
            "candidate_refs": [{"case_id": "c1"}],
            "judgment_text": "本院认为……",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422
    assert "本院认为" not in resp.text


# --- 4) ENABLE_CASEBOOK=false 共享端点 403 -----------------------------------------

def test_disabled_share_returns_403(disabled_client):
    resp = disabled_client.post(
        "/api/casebook/folders/cf_x/share", json={"visibility": "team", "team_id": "t1"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "CASEBOOK_DISABLED"


def test_share_requires_login(client):
    cl, _ = client
    resp = cl.post(
        "/api/casebook/folders/cf_x/share", json={"visibility": "private"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "CASEBOOK_REQUIRES_LOGIN"


# --- 5) 共享不放开正文：共享后读取仍零正文，引用仍只带锚点 -------------------------

def test_share_does_not_open_body(client):
    cl, team_store = client
    owner = _register_login(cl, "body_owner@x.io")
    owner_uid = _user_id(cl, owner)
    mate = _register_login(cl, "body_mate@x.io")
    mate_uid = _user_id(cl, mate)
    team_id = _make_team(team_store, owner_uid, mate_uid)
    folder_id = _create_folder(cl, owner)
    cl.post(
        f"/api/casebook/folders/{folder_id}/share",
        json={"visibility": "team", "team_id": team_id},
        headers=_auth(owner),
    )

    read = cl.get(f"/api/casebook/folders/{folder_id}", headers=_team_auth(mate, team_id))
    assert read.status_code == 200
    data = read.json()
    # 共享后引用仍 100% 带锚点，零正文。
    for ref in data["candidate_refs"]:
        assert ref["source_anchors"]
        for a in ref["source_anchors"]:
            assert a["case_id"] and a["source_chunk_id"]
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in read.text


# --- 6) 审计 / 日志只记元数据 -----------------------------------------------------

def test_share_log_metadata_only(client, caplog):
    cl, team_store = client
    token = _register_login(cl, "audit@x.io")
    uid = _user_id(cl, token)
    team_id = _make_team(team_store, uid)
    folder_id = _create_folder(cl, token)
    secret_note = "机密备注XYZ不该进共享日志"
    body = _valid_create_body()
    body["note"] = secret_note
    cl.put(f"/api/casebook/folders/{folder_id}", json=body, headers=_auth(token))

    with caplog.at_level(logging.INFO):
        resp = cl.post(
            f"/api/casebook/folders/{folder_id}/share",
            json={"visibility": "team", "team_id": team_id},
            headers=_auth(token),
        )
    assert resp.status_code == 200, resp.text
    log_text = "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name == "case_search" and "casebook_share" in r.getMessage()
    )
    assert "casebook_share" in log_text
    assert secret_note not in log_text
    # 团队 id / case_folder_id 不以明文进审计日志（visibility 短枚举可记）。
    assert team_id not in log_text
    assert folder_id not in log_text
