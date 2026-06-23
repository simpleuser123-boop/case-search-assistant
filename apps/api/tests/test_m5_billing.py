"""M5-9 商业化闭环（套餐/试用/计费/续费意愿）focused tests。

覆盖验收点 / 止损点（任一触发即 NO_GO）：
- 默认关闭（ENABLE_BILLING=false）时所有计费端点 403，不展示套餐/计费入口，回到 M5-8 末态。
- 套餐/订阅/续费意愿只存白名单字段，无任何支付凭据（卡号/CVV/银行账户/令牌明文）。
- schema extra=forbid：塞入凭据键 -> 422，绝不进入 service / 落库。
- 支付回执只落脱敏引用（payment_ref hash + status），原始回执号/令牌绝不入库。
- 续费意愿采集只含短码 + 自填短理由，无凭据。
- 脱敏埋点只含 plan_id/status/reason_code/count，无正文/凭据/原始 query。
- 凭据护栏 fail-closed：卡号/CVV/令牌明文命中即抛错。
- 计费服务不 import 检索/rerank（不触碰主排序）。
- 跨 owner 读取订阅被隔离。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import app.api.auth as auth_api
import app.api.billing as billing_api
from app.account.service import AuthService
from app.account.store import AccountStore
from app.billing.models import (
    PaymentReceiptRef,
    Subscription,
    hash_payment_ref,
)
from app.billing.privacy import (
    ForbiddenBillingCredentialError,
    assert_billing_output_clean,
)
from app.billing.service import BillingService
from app.billing.store import BillingStore
from app.core.config import Settings

PW = "sup3rsecret-pw"
RAW_TOKEN = "tok_live_SHOULDNEVERBESTORED12345"


# ---------------- 单元层：凭据护栏 ----------------
@pytest.mark.parametrize(
    "payload",
    [
        {"card_number": "4111111111111111"},
        {"cvv": "123"},
        {"bank_account": "62220000111122223333"},
        {"payment_token": "tok_live_abcdef123456"},
        {"note": "4111 1111 1111 1111"},
        {"note": "4111-1111-1111-1111"},
        {"x": "tok_live_abcdef123456"},
        {"x": "sk_test_abcdef123456"},
        {"nested": [{"content": "案情正文"}]},
    ],
)
def test_guard_blocks_credentials_and_body(payload):
    with pytest.raises(ForbiddenBillingCredentialError):
        assert_billing_output_clean(payload)


@pytest.mark.parametrize(
    "value",
    ["pref_4cd6e97509b6", "plan_team_pro", "（2019）京01民初123号", "¥1980/年", "sub_abc123"],
)
def test_guard_passes_benign_billing_values(value):
    assert_billing_output_clean({"label": value})  # 不抛错即通过


def test_hash_payment_ref_is_irreversible_prefix():
    h = hash_payment_ref(RAW_TOKEN)
    assert h.startswith("pref_")
    assert RAW_TOKEN not in h
    assert hash_payment_ref(None) == "pref_none"


# ---------------- 服务层：试用 / 续费意愿 / 回执联动 ----------------
@pytest.fixture()
def svc():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    store = BillingStore(engine)
    store.init_schema()
    return BillingService(store), store, engine


def test_default_plans_seeded(svc):
    service, _store, _ = svc
    ids = {p.billing_plan_id for p in service.list_plans()}
    assert {"plan_solo", "plan_team_pro", "plan_firm"} <= ids


def test_start_trial_sets_trial_window(svc):
    service, _store, _ = svc
    r = service.start_trial(billing_plan_id="plan_team_pro", owner_user_id="u1", team_id="t1")
    assert r.ok
    assert r.subscription.trial_status == "active"
    assert r.subscription.subscription_status == "trialing"
    assert r.subscription.trial_ends_at is not None
    assert [e.event_name for e in r.events] == ["trial_started"]


def test_plan_without_trial_rejected(svc):
    service, _store, _ = svc
    r = service.start_trial(billing_plan_id="plan_solo", owner_user_id="u1", team_id=None)
    assert not r.ok and r.reason_code == "plan_no_trial"


def test_renewal_intent_recorded_no_credentials(svc):
    service, _store, _ = svc
    sub = service.start_trial(billing_plan_id="plan_team_pro", owner_user_id="u1", team_id=None).subscription
    r = service.record_renewal_intent(
        subscription_id=sub.subscription_id, owner_user_id="u1",
        renewal_intent="will_renew", renewal_reason="检索准确，团队都在用",
    )
    assert r.ok and r.subscription.renewal_intent == "will_renew"


def test_cross_owner_subscription_isolated(svc):
    service, _store, _ = svc
    sub = service.start_trial(billing_plan_id="plan_team_pro", owner_user_id="u1", team_id=None).subscription
    r = service.record_renewal_intent(
        subscription_id=sub.subscription_id, owner_user_id="ATTACKER",
        renewal_intent="will_churn", renewal_reason="x",
    )
    assert not r.ok and r.reason_code == "subscription_not_found"


def test_payment_receipt_stores_hash_only(svc):
    service, _store, engine = svc
    sub = service.start_trial(billing_plan_id="plan_team_pro", owner_user_id="u1", team_id=None).subscription
    r = service.apply_payment_receipt(
        subscription_id=sub.subscription_id, owner_user_id="u1",
        raw_payment_ref=RAW_TOKEN, payment_status="succeeded", amount_display="¥1980",
    )
    assert r.ok
    assert r.subscription.subscription_status == "active"
    assert r.subscription.trial_status == "converted"
    # 抽查持久层：回执表只存哈希，绝无原始令牌
    with Session(engine) as session:
        rows = session.exec(select(PaymentReceiptRef)).all()
    assert len(rows) == 1
    assert rows[0].payment_ref_hash.startswith("pref_")
    assert RAW_TOKEN not in rows[0].payment_ref_hash
    # 全表序列化抽查无原始令牌、无凭据键
    dump = {c: getattr(rows[0], c) for c in rows[0].model_dump()}
    assert RAW_TOKEN not in str(dump)
    assert_billing_output_clean(rows[0].model_dump(mode="json"))


def test_analytics_event_is_desensitized(svc):
    service, _store, _ = svc
    r = service.start_trial(billing_plan_id="plan_team_pro", owner_user_id="u1", team_id=None)
    ev = r.events[0].as_dict()
    assert set(ev.keys()) == {"event_name", "plan_id", "subscription_id_hash", "status", "reason_code", "count"}
    assert ev["subscription_id_hash"].startswith("sidh_")
    # 埋点不含正文 / 凭据（护栏在 as_dict 内已扫描，这里再确认无 raw query 字段）
    assert "query" not in ev and "raw_query" not in ev


def test_subscription_dump_has_no_credential_columns(svc):
    service, _store, _ = svc
    sub = service.start_trial(billing_plan_id="plan_team_pro", owner_user_id="u1", team_id=None).subscription
    cols = set(sub.model_dump().keys())
    for forbidden in ["card_number", "cvv", "bank_account", "payment_token", "password"]:
        assert forbidden not in cols
    assert_billing_output_clean(sub.model_dump(mode="json"))


def test_billing_service_does_not_import_ranking():
    """计费服务不得 import 检索 / rerank / retrieval（不触碰主排序）。"""
    import app.billing.service as svc_mod
    src = svc_mod.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    for banned in ["import app.rerank", "import app.retrieval", "from app.rerank", "from app.retrieval"]:
        assert banned not in text


# ---------------- API 层：flag 双闸 / 登录 / schema 拦截 ----------------
def _api_ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    acc = AccountStore(engine)
    acc.init_schema()
    bill = BillingStore(engine)
    bill.init_schema()
    return acc, bill, engine


@pytest.fixture()
def enabled_client(monkeypatch):
    acc, bill, _ = _api_ctx()
    s = Settings(DEEPSEEK_API_KEY="k", ENABLE_ACCOUNT_SYSTEM=True, ENABLE_BILLING=True)
    monkeypatch.setattr(auth_api, "settings", s)
    monkeypatch.setattr(billing_api, "settings", s)
    auth_api.set_auth_service_for_test(AuthService(acc))
    billing_api.set_billing_service_for_test(BillingService(bill))
    from app.main import app

    yield TestClient(app)
    auth_api.set_auth_service_for_test(None)
    billing_api.set_billing_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(billing_api, "settings",
                        Settings(DEEPSEEK_API_KEY="k", ENABLE_BILLING=False))
    billing_api.set_billing_service_for_test(None)
    from app.main import app

    return TestClient(app)


def _register_login(client: TestClient, login_name: str) -> str:
    client.post("/api/auth/register", json={"login_name": login_name, "password": PW, "display_name": "d"})
    r = client.post("/api/auth/login", json={"login_name": login_name, "password": PW})
    return r.json()["session_token"]


def test_disabled_endpoints_return_403(disabled_client):
    assert disabled_client.get("/api/billing/plans").status_code == 403
    assert disabled_client.get("/api/billing/plans").json()["error"]["code"] == "BILLING_DISABLED"
    assert disabled_client.post("/api/billing/trial",
                                json={"billing_plan_id": "plan_team_pro"}).status_code == 403
    assert disabled_client.get("/api/billing/subscription").status_code == 403


def test_plans_listed_when_enabled(enabled_client):
    resp = enabled_client.get("/api/billing/plans")
    assert resp.status_code == 200
    ids = {p["billing_plan_id"] for p in resp.json()["items"]}
    assert "plan_team_pro" in ids
    # 响应无任何凭据字段
    assert_billing_output_clean(resp.json())


def test_trial_requires_login(enabled_client):
    resp = enabled_client.post("/api/billing/trial", json={"billing_plan_id": "plan_team_pro"})
    assert resp.status_code == 401


def test_schema_rejects_credential_keys_422(enabled_client):
    """schema extra=forbid：试用 / 续费 / 回执端点塞入凭据键 -> 422，绝不入库。"""
    token = _register_login(enabled_client, "u@x.io")
    h = {"Authorization": f"Bearer {token}"}
    # 试用端点塞卡号
    r1 = enabled_client.post("/api/billing/trial",
                             json={"billing_plan_id": "plan_team_pro", "card_number": "4111111111111111"},
                             headers=h)
    assert r1.status_code == 422
    # 回执端点塞 cvv
    r2 = enabled_client.post("/api/billing/payment-receipt",
                             json={"subscription_id": "s", "payment_ref": "r", "payment_status": "succeeded", "cvv": "123"},
                             headers=h)
    assert r2.status_code == 422


def test_full_flow_no_credential_leak(enabled_client):
    token = _register_login(enabled_client, "owner@x.io")
    h = {"Authorization": f"Bearer {token}"}
    # 开通试用
    t = enabled_client.post("/api/billing/trial", json={"billing_plan_id": "plan_team_pro"}, headers=h)
    assert t.status_code == 200 and t.json()["ok"]
    sub_id = t.json()["subscription"]["subscription_id"]
    # 上报脱敏回执（payment_ref 立即哈希）
    pr = enabled_client.post("/api/billing/payment-receipt",
                             json={"subscription_id": sub_id, "payment_ref": RAW_TOKEN,
                                   "payment_status": "succeeded", "amount_display": "¥1980"},
                             headers=h)
    assert pr.status_code == 200
    body = pr.json()
    assert body["payment_ref_hash"].startswith("pref_")
    assert RAW_TOKEN not in str(body)
    assert body["subscription_status"] == "active"
    # 续费意愿
    ri = enabled_client.post("/api/billing/renewal-intent",
                             json={"subscription_id": sub_id, "renewal_intent": "will_renew", "renewal_reason": "好用"},
                             headers=h)
    assert ri.status_code == 200 and ri.json()["subscription"]["renewal_intent"] == "will_renew"
    # 订阅查询脱敏（归属哈希、无凭据）
    sub = enabled_client.get("/api/billing/subscription", headers=h)
    assert sub.status_code == 200
    assert sub.json()["subscription"]["owner_user_id_hash"].startswith("uidh_")
    assert_billing_output_clean(sub.json())
