"""M5-2 账号体系与认证骨架 focused tests。

覆盖验收点：
- 默认关闭（ENABLE_ACCOUNT_SYSTEM=false）时所有账号端点 403，行为回 M4 末态。
- 账号只存白名单字段 + 密码单向哈希；明文密码绝不入库。
- 会话只存 token 哈希；原始 token 不入库、不入日志。
- 注册/登录/登出/会话骨架正确；登出吊销会话。
- 单用户态迁移认领：默认不自动执行（需 confirm），仅元数据/锚点，缺锚点降级、含正文/未知键拒绝。
- 账号体系不引入任何排序/检索副作用（不 import 检索/rerank）。
- 日志只含 user_id_hash/status/reason_code，无凭据/正文。
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import app.api.auth as auth_api
from app.account.migration import (
    CLAIM_REASON_FORBIDDEN_KEY,
    CLAIM_REASON_MISSING_ANCHOR,
    CLAIM_REASON_OK,
    evaluate_claim,
)
from app.account.models import Account, AccountSession, hash_session_token, hash_user_id
from app.account.service import AuthService
from app.account.store import AccountStore
from app.core.config import Settings
from app.core.password import hash_password, is_hashed, verify_password
from app.main import app

PLAINTEXT = "sup3rsecret-pw"


@pytest.fixture()
def sqlite_store():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    store = AccountStore(engine)
    store.init_schema()
    return store, engine


@pytest.fixture()
def enabled_client(sqlite_store, monkeypatch):
    store, _engine = sqlite_store
    monkeypatch.setattr(
        auth_api, "settings", Settings(DEEPSEEK_API_KEY="k", ENABLE_ACCOUNT_SYSTEM=True)
    )
    auth_api.set_auth_service_for_test(AuthService(store))
    yield TestClient(app), store
    auth_api.set_auth_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(
        auth_api, "settings", Settings(DEEPSEEK_API_KEY="k", ENABLE_ACCOUNT_SYSTEM=False)
    )
    auth_api.set_auth_service_for_test(None)
    return TestClient(app)


# --- 默认安全态 ---
def test_account_system_flag_default_false():
    assert Settings.model_fields["ENABLE_ACCOUNT_SYSTEM"].default is False


def test_disabled_returns_403_on_all_account_endpoints(disabled_client):
    c = disabled_client
    r = c.post("/api/auth/login", json={"login_name": "a", "password": "b"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ACCOUNT_SYSTEM_DISABLED"
    r = c.get("/api/auth/session")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ACCOUNT_SYSTEM_DISABLED"
    r = c.post("/api/auth/logout")
    assert r.status_code == 403


# --- 密码哈希红线 ---
def test_password_hashed_one_way_and_verifiable():
    hashed = hash_password(PLAINTEXT)
    assert is_hashed(hashed)
    assert hashed.startswith("pbkdf2_sha256$")
    assert PLAINTEXT not in hashed
    assert verify_password(PLAINTEXT, hashed) is True
    assert verify_password("wrong", hashed) is False
    # 两次哈希不同盐 -> 不同输出
    assert hash_password(PLAINTEXT) != hashed


def test_store_rejects_plaintext_password(sqlite_store):
    store, _ = sqlite_store
    with pytest.raises(ValueError):
        store.create_account(
            user_id="u_x", login_name="x@x.io", password_hash="not-a-hash-plaintext"
        )


# --- 白名单字段：account 表只含合同声明字段 ---
def test_account_table_only_whitelist_columns():
    cols = set(Account.__table__.columns.keys())
    assert cols == {
        "user_id",
        "login_name",
        "display_name",
        "account_status",
        "auth_provider",
        "auth_subject_ref",
        "password_hash",
        "created_at",
        "updated_at",
        "reason_code",
    }
    # 不得出现明文凭据 / 正文列
    for forbidden in ("plaintext_password", "password", "token", "session_token", "raw_query", "case_fact_body"):
        assert forbidden not in cols


def test_session_table_stores_token_hash_not_plaintext():
    cols = set(AccountSession.__table__.columns.keys())
    assert "token_hash" in cols
    # 绝不存原始 token
    assert "token" not in cols
    assert "session_token" not in cols


# --- 注册/登录/登出/会话 ---
def test_register_then_login_session_logout_flow(enabled_client):
    c, store = enabled_client
    r = c.post(
        "/api/auth/register",
        json={"login_name": "alice@x.io", "password": PLAINTEXT, "display_name": "Alice"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # 注册不下发 token
    assert body["session_token"] is None
    assert body["account"]["display_name"] == "Alice"

    r = c.post("/api/auth/login", json={"login_name": "alice@x.io", "password": PLAINTEXT})
    assert r.status_code == 200
    token = r.json()["session_token"]
    assert token

    r = c.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["account"]["display_name"] == "Alice"

    r = c.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["ok"] is True

    # 登出后会话失效
    r = c.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["ok"] is False


def test_login_wrong_password_rejected(enabled_client):
    c, _ = enabled_client
    c.post("/api/auth/register", json={"login_name": "bob@x.io", "password": PLAINTEXT})
    r = c.post("/api/auth/login", json={"login_name": "bob@x.io", "password": "wrong-pw-xxx"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "LOGIN_REJECTED"


def test_duplicate_login_name_rejected(enabled_client):
    c, _ = enabled_client
    c.post("/api/auth/register", json={"login_name": "dup@x.io", "password": PLAINTEXT})
    r = c.post("/api/auth/register", json={"login_name": "dup@x.io", "password": PLAINTEXT})
    assert r.status_code == 400


def test_stored_password_is_hash_no_plaintext(enabled_client):
    c, store = enabled_client
    c.post("/api/auth/register", json={"login_name": "carol@x.io", "password": PLAINTEXT})
    acc = store.get_by_login_name("carol@x.io")
    assert acc is not None
    assert is_hashed(acc.password_hash)
    assert PLAINTEXT not in acc.password_hash


def test_session_row_stores_only_token_hash(enabled_client):
    c, store = enabled_client
    c.post("/api/auth/register", json={"login_name": "dan@x.io", "password": PLAINTEXT})
    token = c.post(
        "/api/auth/login", json={"login_name": "dan@x.io", "password": PLAINTEXT}
    ).json()["session_token"]
    with Session(store._engine) as s:  # noqa: SLF001 - test introspection
        rows = s.exec(select(AccountSession)).all()
    assert len(rows) == 1
    # 库里只存哈希，且等于 token 的 sha256；不存原始 token
    assert rows[0].token_hash == hash_session_token(token)
    assert rows[0].token_hash != token


# --- 单用户态迁移认领 ---
def test_claim_requires_explicit_confirm(enabled_client):
    c, _ = enabled_client
    c.post("/api/auth/register", json={"login_name": "ev@x.io", "password": PLAINTEXT})
    token = c.post(
        "/api/auth/login", json={"login_name": "ev@x.io", "password": PLAINTEXT}
    ).json()["session_token"]
    r = c.post(
        "/api/auth/claim",
        json={"confirm": False, "items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["reason_code"] == "confirm_required"


def test_claim_requires_login(enabled_client):
    c, _ = enabled_client
    r = c.post("/api/auth/claim", json={"confirm": True, "items": []})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "CLAIM_REQUIRES_LOGIN"


def test_claim_counts_and_reason_codes(enabled_client):
    c, _ = enabled_client
    c.post("/api/auth/register", json={"login_name": "fi@x.io", "password": PLAINTEXT})
    token = c.post(
        "/api/auth/login", json={"login_name": "fi@x.io", "password": PLAINTEXT}
    ).json()["session_token"]
    items = [
        {  # ok: anchored
            "object_type": "case_favorite",
            "case_id": "c1",
            "source_anchors": [{"case_id": "c1", "source_chunk_id": "c1-0"}],
        },
        {  # degraded: no anchor
            "object_type": "case_favorite",
            "case_id": "c2",
            "source_anchors": [],
        },
    ]
    r = c.post(
        "/api/auth/claim",
        json={"confirm": True, "items": items},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["claimed_count"] == 1
    assert body["degraded_count"] == 1
    assert body["rejected_count"] == 0
    # owner hash 脱敏，无明文 user_id
    assert body["owner_user_id_hash"].startswith("uidh_")


def test_claim_rejects_body_and_unknown_keys_pure_fn():
    # 含禁用键（正文/凭据）-> rejected
    out = evaluate_claim(
        owner_user_id="u_1",
        items=[
            {"case_id": "c1", "source_anchors": [{"case_id": "c1", "source_chunk_id": "x"}], "raw_query": "案情正文"},
            {"object_type": "case_favorite", "case_id": "c2", "source_anchors": [{"case_id": "c2", "source_chunk_id": "y"}]},
        ],
    )
    assert out.rejected_count == 1
    assert out.claimed_count == 1
    assert out.reason_codes.get(CLAIM_REASON_FORBIDDEN_KEY) == 1
    assert out.reason_codes.get(CLAIM_REASON_OK) == 1


def test_claim_missing_anchor_degraded_not_fabricated():
    out = evaluate_claim(
        owner_user_id="u_1",
        items=[{"object_type": "case_list", "case_id": "c1", "source_anchors": [{"case_id": "c1"}]}],
    )
    # 缺 source_chunk_id -> 降级，不伪造
    assert out.degraded_count == 1
    assert out.reason_codes.get(CLAIM_REASON_MISSING_ANCHOR) == 1


# --- 隐私/日志：无凭据明文、无正文 ---
def test_logs_contain_no_credentials_or_plaintext(enabled_client, caplog):
    c, _ = enabled_client
    caplog.set_level(logging.INFO, logger="case_search")
    c.post(
        "/api/auth/register",
        json={"login_name": "secretuser@x.io", "password": PLAINTEXT, "display_name": "Zoe"},
    )
    token = c.post(
        "/api/auth/login", json={"login_name": "secretuser@x.io", "password": PLAINTEXT}
    ).json()["session_token"]
    c.get("/api/auth/session", headers={"Authorization": f"Bearer {token}"})
    text = caplog.text
    # 不得出现明文密码、原始 token、login_name、display_name
    assert PLAINTEXT not in text
    assert token not in text
    assert "secretuser@x.io" not in text
    assert "Zoe" not in text
    # 应出现脱敏字段
    assert "user_id_hash=" in text
    assert "reason_code=" in text


def test_user_id_hash_is_deterministic_and_masked():
    h = hash_user_id("u_abc")
    assert h.startswith("uidh_")
    assert "u_abc" not in h
    assert hash_user_id("u_abc") == h


# --- 账号体系不引入排序/检索副作用 ---
def test_account_modules_do_not_import_ranking_or_retrieval():
    import app.account.service as svc
    import app.account.store as st
    import app.account.models as md
    import app.account.migration as mg

    for mod in (svc, st, md, mg):
        src = mod.__file__
        with open(src, "r", encoding="utf-8") as f:
            content = f.read()
        for forbidden in ("from app.rerank", "from app.retrieval", "import rerank", "qrels", "relevance_label"):
            assert forbidden not in content, f"{src} must not couple to ranking/retrieval ({forbidden})"

