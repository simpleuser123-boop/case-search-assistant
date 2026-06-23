"""健康检查与密钥校验的烟雾测试（Day0 §4.2 验收：测试命令可跑）。

不依赖外部 PostgreSQL/Chroma 即可运行：db/chroma 字段允许 False。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api import health as health_module
from app.main import app
from app.core.config import Settings, missing_secrets
from app.core.runtime import safe_config_snapshot

client = TestClient(app)


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # secrets_present 必须是布尔字典，且绝不能泄露任何值
    assert set(body["secrets_present"].keys()) == {"DEEPSEEK_API_KEY"}
    assert all(isinstance(v, bool) for v in body["secrets_present"].values())
    assert set(body["feature_flags"].keys()) == {
        "ENABLE_QUERY_REWRITE",
        "ENABLE_WEIGHTED_RERANK",
        "ENABLE_SUMMARY",
        "ENABLE_EXPANDED_SEARCH",
    }
    assert all(isinstance(v, bool) for v in body["feature_flags"].values())
    assert body["feature_flags"] == {
        "ENABLE_QUERY_REWRITE": False,
        "ENABLE_WEIGHTED_RERANK": False,
        "ENABLE_SUMMARY": False,
        "ENABLE_EXPANDED_SEARCH": False,
    }
    assert {"app_version", "source_root", "process_id", "started_at", "config_digest"} <= set(
        body["runtime"].keys()
    )
    assert isinstance(body["runtime"]["process_id"], int)
    assert body["runtime"]["source_root"]
    assert body["runtime"]["started_at"]
    assert len(body["runtime"]["config_digest"]) == 64
    assert {"db", "ollama", "chroma"} <= set(body["dependencies"].keys())
    for name in ("db", "ollama", "chroma"):
        assert "reachable" in body["dependencies"][name]
        assert "degraded_reason" in body["dependencies"][name]
    assert {"driver", "host", "database", "reachable", "degraded_reason"} <= set(
        body["dependencies"]["db"].keys()
    )
    assert body["db_reachable"] is body["dependencies"]["db"]["reachable"]


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "case-search-api"


def test_missing_secrets_reports_names_only():
    empty = Settings(DEEPSEEK_API_KEY="")
    missing = missing_secrets(empty)
    assert "DEEPSEEK_API_KEY" in missing


def test_present_secrets_not_missing():
    filled = Settings(DEEPSEEK_API_KEY="x")
    assert missing_secrets(filled) == []


def test_feature_flag_defaults_are_conservative():
    assert Settings.model_fields["ENABLE_QUERY_REWRITE"].default is False
    assert Settings.model_fields["ENABLE_WEIGHTED_RERANK"].default is False
    assert Settings.model_fields["ENABLE_SUMMARY"].default is False
    assert Settings.model_fields["ENABLE_EXPANDED_SEARCH"].default is False


def test_runtime_config_snapshot_does_not_leak_secret_values():
    config = Settings(
        DEEPSEEK_API_KEY="real-deepseek-secret",
        DATABASE_URL="postgresql://postgres:real-db-password@localhost:5432/case_search",
        REDIS_URL="redis://:real-redis-password@localhost:6379/0",
    )

    serialized = json.dumps(safe_config_snapshot(config), ensure_ascii=False, sort_keys=True)

    assert "real-deepseek-secret" not in serialized
    assert "real-db-password" not in serialized
    assert "real-redis-password" not in serialized
    assert "postgres:real-db-password" not in serialized


def test_db_status_returns_sanitized_connection_details(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _statement):
            return None

    class FakeEngine:
        url = make_url("postgresql+psycopg2://postgres:real-db-password@db.internal:5432/case_search")
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(health_module, "engine", FakeEngine())

    status = health_module._db_status()
    serialized = json.dumps(status, ensure_ascii=False, sort_keys=True)

    assert status["reachable"] is True
    assert status["driver"] == "postgresql+psycopg2"
    assert status["host"] == "db.internal"
    assert status["database"] == "case_search"
    assert status["degraded_reason"] is None
    assert "real-db-password" not in serialized
    assert "postgres:real-db-password" not in serialized


def test_db_status_unreachable_uses_sanitized_reason(monkeypatch):
    class FailingEngine:
        url = make_url("postgresql://postgres:real-db-password@db.internal:5432/case_search")
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            raise RuntimeError("postgresql://postgres:real-db-password@db.internal:5432/case_search")

    monkeypatch.setattr(health_module, "engine", FailingEngine())

    status = health_module._db_status()
    serialized = json.dumps(status, ensure_ascii=False, sort_keys=True)

    assert status["reachable"] is False
    assert status["degraded_reason"] == "connection_failed"
    assert "real-db-password" not in serialized
    assert "postgres:real-db-password" not in serialized
