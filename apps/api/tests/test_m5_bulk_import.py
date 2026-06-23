"""M5-6 批量导入 focused tests。

覆盖验收点 / 止损点（任一触发即 NO_GO）：
- 默认关闭（ENABLE_BULK_IMPORT=false）时所有导入端点 403，回到 M5-5/M4 末态。
- 导入只含元数据 / 引用 / 用户自填短字段；塞入正文 / 凭据 / 未知键被丢弃或拒绝，绝不入库。
- 缺锚点项（AI 内容承载型）被拒绝；缺 case_id 被拒绝；不伪造锚点。
- 导入对象默认归属当前 owner、默认私有（team_id=None / visibility=private）。
- 按 case_id 去重（批内 + 已存在）。
- 抽查实际写入沉淀对象不含正文；导入作业账本无正文列。
- 导入服务不 import 检索 / rerank（不触碰主排序）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

import app.api.auth as auth_api
import app.api.bulk_import as bulk_api
from app.account.service import AuthService
from app.account.store import AccountStore
from app.bulk_import.models import BulkImportJob
from app.bulk_import.service import BulkImportService
from app.bulk_import.store import BulkImportStore
from app.bulk_import.validation import (
    REASON_FORBIDDEN_BODY,
    REASON_INVALID_ANCHOR,
    REASON_MISSING_ANCHOR,
    REASON_MISSING_CASE_ID,
    validate_and_clean_item,
)
from app.core.config import Settings
from app.team.isolation import TenantContext
from app.team.models import SedimentationObject
from app.team.store import TeamStore

PW = "sup3rsecret-pw"


# ---------------- 单元层：导入项校验与净化 ----------------
def test_forbidden_body_keys_rejected():
    for bad in [
        {"case_id": "c1", "case_fact_body": "案情正文"},
        {"case_id": "c1", "raw_query": "原始查询"},
        {"case_id": "c1", "chunk_body": "片段正文"},
        {"case_id": "c1", "password": "x"},
        {"case_id": "c1", "content": "y"},
    ]:
        res = validate_and_clean_item(object_type="case_favorite", raw_item=bad)
        assert not res.ok and res.reason_code == REASON_FORBIDDEN_BODY


def test_unknown_keys_dropped_not_persisted():
    # 未知键（非黑名单、非白名单）被静默丢弃，但合法项仍可导入；丢弃的键不出现在 clean_payload。
    res = validate_and_clean_item(
        object_type="case_favorite",
        raw_item={"case_id": "c1", "totally_unknown": "zzz", "court": "北京一中院"},
    )
    assert res.ok
    assert "totally_unknown" not in res.clean_payload
    assert res.clean_payload.get("court") == "北京一中院"
    assert res.clean_payload.get("case_id") == "c1"


def test_missing_case_id_rejected():
    res = validate_and_clean_item(object_type="case_favorite", raw_item={"court": "X"})
    assert not res.ok and res.reason_code == REASON_MISSING_CASE_ID


def test_ai_content_type_requires_anchor():
    # case_list / report_template 无锚点 -> 拒绝（不伪造锚点）。
    for ot in ("case_list", "report_template"):
        res = validate_and_clean_item(object_type=ot, raw_item={"case_id": "c1", "list_title": "L"})
        assert not res.ok and res.reason_code == REASON_MISSING_ANCHOR


def test_invalid_anchor_rejected():
    res = validate_and_clean_item(
        object_type="case_list",
        raw_item={"case_id": "c1", "source_anchors": [{"case_id": "c1"}]},  # 缺 chunk
    )
    assert not res.ok and res.reason_code == REASON_INVALID_ANCHOR


def test_favorite_without_anchor_allowed():
    res = validate_and_clean_item(object_type="case_favorite", raw_item={"case_id": "c1"})
    assert res.ok


def test_anchor_sanitized_to_whitelist_only():
    # 锚点里塞正文键 -> 净化后只保留 case_id/source_chunk_id/anchor_type。
    res = validate_and_clean_item(
        object_type="case_list",
        raw_item={
            "case_id": "c1",
            "source_anchors": [
                {"case_id": "c1", "source_chunk_id": "k1", "anchor_type": "holding", "chunk_body": "正文不该在这"}
            ],
        },
    )
    assert res.ok
    anchor = res.clean_payload["source_anchors"][0]
    assert set(anchor.keys()) <= {"case_id", "source_chunk_id", "anchor_type"}
    assert "chunk_body" not in anchor


# ---------------- 服务层：导入默认 owner 私有 + 去重 + 无正文入库 ----------------
@pytest.fixture()
def stores():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    team = TeamStore(engine)
    team.init_schema()
    imp = BulkImportStore(engine)
    imp.init_schema()
    return BulkImportService(imp, team), team, imp, engine


def test_import_defaults_owner_private(stores):
    svc, team, _imp, engine = stores
    result = svc.import_batch(
        owner_user_id="u1", source_type="case_list_file", object_type="case_favorite",
        items=[{"case_id": "c1", "court": "北京一中院"}],
    )
    assert result.ok and result.imported_count == 1 and result.import_status == "completed"
    with Session(engine) as s:
        rows = list(s.exec(select(SedimentationObject)).all())
    assert len(rows) == 1
    obj = rows[0]
    assert obj.owner_user_id == "u1"
    assert obj.team_id is None
    assert obj.visibility == "private"


def test_import_team_id_is_ledger_only_not_object_visibility(stores):
    # 传入 team_id 只进作业账本；导入对象本身仍 owner 私有（不据此放权可见性）。
    svc, team, _imp, engine = stores
    t = team.create_team(team_name="T")
    team.add_member(team_id=t.team_id, member_user_id="u1")
    result = svc.import_batch(
        owner_user_id="u1", source_type="existing_list", object_type="case_favorite",
        items=[{"case_id": "c1"}], team_id=t.team_id,
    )
    assert result.ok
    with Session(engine) as s:
        obj = list(s.exec(select(SedimentationObject)).all())[0]
        job = s.get(BulkImportJob, result.import_job_id)
    assert obj.team_id is None and obj.visibility == "private"
    assert job.team_id == t.team_id  # 账本留痕
    # 另一团队成员在团队上下文下看不到该导入对象（仍私有）。
    team.add_member(team_id=t.team_id, member_user_id="u2")
    ctx_u2 = TenantContext(owner_user_id="u2", team_id=t.team_id)
    assert team.list_visible(ctx=ctx_u2) == []


def test_import_dedup_within_batch_and_existing(stores):
    svc, _team, _imp, engine = stores
    # 批内重复 c1。
    r1 = svc.import_batch(
        owner_user_id="u1", source_type="csv", object_type="case_favorite",
        items=[{"case_id": "c1"}, {"case_id": "c1"}, {"case_id": "c2"}],
    )
    assert r1.imported_count == 2 and r1.duplicate_count == 1
    # 再次导入 c1 -> 命中已存在去重。
    r2 = svc.import_batch(
        owner_user_id="u1", source_type="csv", object_type="case_favorite",
        items=[{"case_id": "c1"}, {"case_id": "c3"}],
    )
    assert r2.imported_count == 1 and r2.duplicate_count == 1
    with Session(engine) as s:
        rows = list(s.exec(select(SedimentationObject)).all())
    assert sorted(o.case_id for o in rows) == ["c1", "c2", "c3"]


def test_import_body_field_rejected_not_persisted(stores):
    svc, _team, _imp, engine = stores
    result = svc.import_batch(
        owner_user_id="u1", source_type="csv", object_type="case_favorite",
        items=[{"case_id": "c1", "case_fact_body": "这是案情正文不该入库"}, {"case_id": "c2"}],
    )
    # 含正文项被拒，干净项导入。
    assert result.imported_count == 1 and result.rejected_count == 1
    with Session(engine) as s:
        rows = list(s.exec(select(SedimentationObject)).all())
    # 抽查：没有任何列含正文串。
    blob = " ".join(
        str(getattr(o, c)) for o in rows for c in SedimentationObject.__table__.columns.keys()
        if getattr(o, c) is not None
    )
    assert "案情正文" not in blob
    assert [o.case_id for o in rows] == ["c2"]


def test_import_empty_and_bad_source_fail_safely(stores):
    svc, _team, _imp, _engine = stores
    r_empty = svc.import_batch(owner_user_id="u1", source_type="csv", object_type="case_favorite", items=[])
    assert not r_empty.ok and r_empty.import_status == "failed" and r_empty.degrade_reason == "empty_batch"
    r_bad = svc.import_batch(owner_user_id="u1", source_type="evil_source", object_type="case_favorite",
                             items=[{"case_id": "c1"}])
    assert not r_bad.ok and r_bad.degrade_reason == "invalid_source_type"


def test_job_ledger_has_no_body_columns():
    cols = set(BulkImportJob.__table__.columns.keys())
    forbidden = {"raw_query", "query", "case_fact_body", "candidate_body", "chunk_body",
                 "judgment_long_text", "summary_body", "holding_body", "compare_body",
                 "user_free_long_text", "text", "content", "password", "token", "session_token"}
    assert cols & forbidden == set()


def test_import_module_has_no_retrieval_side_effects():
    import app.bulk_import.service as svc_mod
    import app.bulk_import.store as store_mod
    import app.bulk_import.validation as val_mod

    for mod in (svc_mod, store_mod, val_mod):
        with open(mod.__file__, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for ln in [l for l in lines if l.lstrip().startswith(("import ", "from "))]:
            assert "retrieval" not in ln and "rerank" not in ln


# ---------------- API 层：flag 开关 + 鉴权 + 请求体抽查 ----------------
@pytest.fixture()
def api_ctx():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    acc = AccountStore(engine)
    acc.init_schema()
    team = TeamStore(engine)
    team.init_schema()
    imp = BulkImportStore(engine)
    imp.init_schema()
    return acc, team, imp, engine


@pytest.fixture()
def enabled_client(api_ctx, monkeypatch):
    acc, team, imp, _ = api_ctx
    s = Settings(DEEPSEEK_API_KEY="k", ENABLE_ACCOUNT_SYSTEM=True,
                 ENABLE_TEAM_WORKSPACE=True, ENABLE_BULK_IMPORT=True)
    monkeypatch.setattr(auth_api, "settings", s)
    monkeypatch.setattr(bulk_api, "settings", s)
    auth_api.set_auth_service_for_test(AuthService(acc))
    bulk_api.set_bulk_import_service_for_test(BulkImportService(imp, team))
    from app.main import app

    yield TestClient(app), team
    auth_api.set_auth_service_for_test(None)
    bulk_api.set_bulk_import_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(bulk_api, "settings",
                        Settings(DEEPSEEK_API_KEY="k", ENABLE_BULK_IMPORT=False))
    bulk_api.set_bulk_import_service_for_test(None)
    from app.main import app

    return TestClient(app)


def _register_login(client: TestClient, login_name: str) -> str:
    client.post("/api/auth/register", json={"login_name": login_name, "password": PW, "display_name": "d"})
    r = client.post("/api/auth/login", json={"login_name": login_name, "password": PW})
    return r.json()["session_token"]


def test_disabled_endpoints_return_403(disabled_client):
    resp = disabled_client.post("/api/bulk-import/run",
                                json={"source_type": "csv", "object_type": "case_favorite", "items": []})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "BULK_IMPORT_DISABLED"
    assert disabled_client.get("/api/bulk-import/jobs").status_code == 403


def test_endpoints_require_login(enabled_client):
    client, _ = enabled_client
    resp = client.post("/api/bulk-import/run",
                       json={"source_type": "csv", "object_type": "case_favorite", "items": [{"case_id": "c1"}]})
    assert resp.status_code == 401


def test_schema_rejects_body_field_422(enabled_client):
    """schema extra=forbid：导入项带正文键 -> 422，绝不入库。"""
    client, _ = enabled_client
    token = _register_login(client, "u@x.io")
    resp = client.post(
        "/api/bulk-import/run",
        json={"source_type": "csv", "object_type": "case_favorite",
              "items": [{"case_id": "c1", "case_fact_body": "案情正文"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_import_flow_no_body_leak(enabled_client):
    client, _team = enabled_client
    token = _register_login(client, "owner@x.io")
    body = {
        "source_type": "case_list_file",
        "object_type": "case_list",
        "items": [
            {"case_id": "c1", "case_number": "(2021)京01民终123号", "court": "北京一中院",
             "list_title": "我的类案清单",
             "source_anchors": [{"case_id": "c1", "source_chunk_id": "chunk_7"}]},
            {"case_id": "c2", "list_title": "缺锚点"},  # AI 内容承载型缺锚点 -> 拒绝
        ],
    }
    resp = client.post("/api/bulk-import/run", json=body, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported_count"] == 1
    assert data["rejected_count"] == 1
    # 逐项结果里第二项 reason 是缺锚点。
    reasons = {o["case_id"]: o["reason_code"] for o in data["outcomes"]}
    assert reasons["c2"] == "missing_source_anchor"
    # 作业列表可查，响应不含正文 / 凭据 / 原始锚点内容。
    jobs = client.get("/api/bulk-import/jobs", headers={"Authorization": f"Bearer {token}"})
    assert jobs.status_code == 200
    blob = jobs.text
    assert "案情" not in blob and PW not in blob and "chunk_7" not in blob
    items = jobs.json()["items"]
    assert items and items[0]["owner_user_id_hash"].startswith("uidh_")
