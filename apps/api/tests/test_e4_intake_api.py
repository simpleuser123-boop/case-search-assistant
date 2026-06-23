"""E4-3 案情录入端 intake 后端端点测试。

验证（对应文档 19 §6 E4-3 验收 / 测试要求）：
- ENABLE_INTAKE=true（测试内 override）：POST 已脱敏 SearchProfile 返回 CandidateRef[]；
  字段严格白名单、100% 有 source_anchors、0 正文。
- ENABLE_INTAKE=false：端点 403 安全降级，不泄露内部信息、不检索。
- POST 含 raw_case / raw_query / PII 键被拒（422 schema 层 / 400 防御层），异常消息不含原始 PII。
- intake 经 InternalSearchService 执行（spy），不直接实例化 retrieval / rerank（静态 AST 断言）。
- 日志只写 query_session_id / 计数 / degraded_reasons，不含 query_text / 原始案情 / PII。
- intake 产品包只依赖 app.kernel 公开面，不 import 其它产品包、不直连检索底层（静态断言）。

红线：fixture 只用短假数据 / hash / case_id / source_chunk_id / 元数据，绝不写真实长案情或裁判正文。
"""
from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.intake.service import IntakeSearchService
from app.kernel.rag import (
    CandidateRef,
    InternalSearchRequest,
    InternalSearchResult,
    SearchProfile,
)
from app.kernel.rag.internal_search_contracts import SourceAnchorRef
from app.main import app

intake_router = importlib.import_module("app.intake.router")

INTAKE_DIR = Path(__file__).resolve().parents[1] / "app" / "intake"

# intake 响应 / 源码绝不允许出现的正文 / 富展示型字段。
FORBIDDEN_BODY_FIELDS = (
    "summary",
    "summary_text",
    "highlights",
    "highlight",
    "matched_text",
    "holding_summary",
    "chunk_text",
    "full_text",
    "content",
    "body",
    "raw_query",
    "raw_case",
)

# intake 产品包禁止直连 / 深引的内核底层与禁止 import 的其它产品包。
FORBIDDEN_INTAKE_IMPORT_PREFIXES = (
    "app.retrieval",
    "app.rerank",
    "app.summary",
    "app.query_processing",
    "app.kernel.rag.retrieval",
    "app.kernel.rag.rerank",
    "app.kernel.rag.summary",
    "app.kernel.rag.query_processing",
    "app.statute",
    "app.drafting",
    "app.casebook",
)


# --- fakes（短假数据，无副作用，不写库）-------------------------------------

def _candidate_ref(case_id: str = "c1") -> CandidateRef:
    return CandidateRef(
        case_id=case_id,
        case_number=f"({case_id})刑初字第1号",
        court="某基层人民法院",
        trial_level="一审",
        case_cause="盗窃",
        judgment_date="2023-01-01",
        source_anchors=[
            SourceAnchorRef(
                case_id=case_id,
                source_chunk_id=f"{case_id}_chunk0",
                anchor_type="result",
            )
        ],
    )


class SpyInternalSearchService:
    """记录调用、回吐固定 CandidateRef[]，验证 intake 经此服务执行（不直连底层）。"""

    def __init__(
        self,
        *,
        result: InternalSearchResult | None = None,
    ) -> None:
        self._result = result or InternalSearchResult(
            candidate_refs=[_candidate_ref("c1")],
            degraded=False,
            degraded_reasons=[],
        )
        self.calls: list[dict] = []

    def search_candidate_refs(self, request, *, query_session_id=None):
        # 断言 intake 传入的是 InternalSearchRequest（经服务层装配），且 profile 已脱敏。
        assert isinstance(request, InternalSearchRequest)
        assert isinstance(request.profile, SearchProfile)
        self.calls.append(
            {
                "mode": request.mode,
                "limit": request.limit,
                "query_session_id": query_session_id,
                "query_text": request.profile.query_text,
            }
        )
        return self._result


def _enabled_settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY="k", ENABLE_INTAKE=True)


def _disabled_settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY="k", ENABLE_INTAKE=False)


@pytest.fixture()
def enabled_spy(monkeypatch):
    """ENABLE_INTAKE=true + 注入 spy InternalSearchService。返回 (client, spy)。"""
    monkeypatch.setattr(intake_router, "settings", _enabled_settings())
    spy = SpyInternalSearchService()
    intake_router.set_intake_search_service_for_test(
        IntakeSearchService(internal_search_service=spy)
    )
    client = TestClient(app)
    yield client, spy
    intake_router.set_intake_search_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(intake_router, "settings", _disabled_settings())
    intake_router.set_intake_search_service_for_test(None)
    return TestClient(app)


# --- 1) flag 开启：返回白名单 CandidateRef[]，0 正文，100% source_anchors -------

def test_enabled_returns_candidate_refs_whitelist_zero_body(enabled_spy):
    client, spy = enabled_spy
    resp = client.post(
        "/api/intake/search",
        json={
            "case_cause": "盗窃",
            "region": "北京",
            "trial_level_preference": "一审",
            "dispute_focus_keywords": ["自首", "数额"],
            "query_text": "盗窃 5000元 自首",
            "mode": "standard",
            "limit": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["candidate_count"] == 1
    assert len(data["candidate_refs"]) == 1
    ref = data["candidate_refs"][0]
    # 字段严格白名单（七字段 + source_anchors）。
    assert set(ref.keys()) <= {
        "case_id",
        "case_number",
        "court",
        "trial_level",
        "case_cause",
        "judgment_date",
        "source_anchors",
    }
    # 100% 有 source_anchors，每条至少 case_id + source_chunk_id。
    assert ref["source_anchors"]
    for anchor in ref["source_anchors"]:
        assert anchor["case_id"] and anchor["source_chunk_id"]
    # 0 正文：响应里不得出现任何正文 / 富展示型字段。
    body_text = resp.text
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in body_text
    # intake 确经 InternalSearchService 执行（spy 被调用）。
    assert len(spy.calls) == 1


def test_enabled_passes_mode_and_limit_to_internal_service(enabled_spy):
    client, spy = enabled_spy
    resp = client.post(
        "/api/intake/search",
        json={"query_text": "合同 违约", "mode": "expanded", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    assert spy.calls[0]["mode"] == "expanded"
    assert spy.calls[0]["limit"] == 5
    assert resp.json()["search_mode"] == "expanded"


def test_enabled_degraded_passthrough_no_body(enabled_spy, monkeypatch):
    client, _ = enabled_spy
    degraded_spy = SpyInternalSearchService(
        result=InternalSearchResult(
            candidate_refs=[],
            degraded=True,
            degraded_reasons=["DATA_SOURCE_UNAVAILABLE"],
        )
    )
    intake_router.set_intake_search_service_for_test(
        IntakeSearchService(internal_search_service=degraded_spy)
    )
    resp = client.post("/api/intake/search", json={"query_text": "测试"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["degraded"] is True
    assert data["degraded_reasons"] == ["DATA_SOURCE_UNAVAILABLE"]
    assert data["candidate_count"] == 0


# --- 2) flag 关闭：403 安全降级，不检索 --------------------------------------

def test_disabled_returns_403_safe_degrade(disabled_client):
    resp = disabled_client.post(
        "/api/intake/search", json={"query_text": "盗窃 自首"}
    )
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "INTAKE_DISABLED"
    # 不泄露内部信息：无 candidate_refs / 内部栈 / query_text 回显。
    assert "盗窃" not in resp.text
    assert "candidate_refs" not in resp.text


# --- 3) raw_case / raw_query / PII 键被拒（schema extra=forbid 第一道闸）------

@pytest.mark.parametrize(
    "bad_payload",
    [
        {"raw_case": "张三于北京盗窃5000元后自首", "query_text": "盗窃"},
        {"raw_query": "被告李四电话13800138000", "query_text": "合同"},
        {"name": "王五", "query_text": "借贷"},
        {"id_card": "110101199001011234", "query_text": "诈骗"},
        {"phone": "13800138000", "query_text": "纠纷"},
        {"address": "北京市海淀区中关村大街1号", "query_text": "侵权"},
        {"email": "a@b.com", "query_text": "侵权"},
        {"full_text": "裁判文书全文……", "query_text": "盗窃"},
        {"content": "正文……", "query_text": "盗窃"},
    ],
)
def test_forbidden_keys_rejected_no_pii_echo(enabled_spy, bad_payload):
    client, spy = enabled_spy
    resp = client.post("/api/intake/search", json=bad_payload)
    # schema extra=forbid -> 422（主路径）。
    assert resp.status_code in (400, 422), resp.text
    # 异常消息不回显原始 PII / 正文值。
    pii_values = [
        "张三",
        "李四",
        "王五",
        "13800138000",
        "110101199001011234",
        "中关村大街1号",
        "a@b.com",
        "裁判文书全文",
        "正文……",
    ]
    for value in pii_values:
        assert value not in resp.text
    # 被拒请求不得触达检索服务。
    assert spy.calls == []


def test_unknown_extra_field_rejected(enabled_spy):
    client, _ = enabled_spy
    resp = client.post(
        "/api/intake/search",
        json={"query_text": "盗窃", "unexpected_field": "x"},
    )
    assert resp.status_code == 422, resp.text


# --- 4) 日志脱敏：只写 query_session_id / 计数 / degraded_reasons -------------

def test_logs_exclude_query_text_and_pii(enabled_spy, caplog):
    client, _ = enabled_spy
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/api/intake/search",
            json={"query_text": "盗窃 5000元 自首", "case_cause": "盗窃"},
        )
    assert resp.status_code == 200, resp.text
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "盗窃 5000元 自首" not in log_text
    assert "query_text" not in log_text
    # 计数 / 会话维度日志在场。
    assert "intake_search_completed" in log_text


# --- 5) 静态边界：intake 只依赖 app.kernel 公开面，不直连底层 / 不 import 产品包 --

def _iter_intake_imports():
    modules: list[str] = []
    for path in sorted(INTAKE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
    return modules


def test_intake_does_not_import_retrieval_layer_or_other_products():
    for module in _iter_intake_imports():
        for forbidden in FORBIDDEN_INTAKE_IMPORT_PREFIXES:
            assert not module.startswith(forbidden), (
                f"intake 不得 import {module}（应只走 app.kernel 公开面）"
            )


def test_intake_only_consumes_kernel_public_surface():
    """intake 对内核的依赖只允许 app.kernel / app.kernel.rag / app.kernel.guardrails 顶层公开面。"""
    for module in _iter_intake_imports():
        if module.startswith("app.kernel"):
            assert module in ("app.kernel", "app.kernel.rag", "app.kernel.guardrails"), (
                f"intake 只能消费内核顶层公开面，禁止深引 {module}"
            )


def test_intake_source_has_no_body_field_literals():
    """intake 源码不得把正文型字段当数据字段搬运（只可作被拒键名 / 注释）。"""
    service_src = (INTAKE_DIR / "service.py").read_text(encoding="utf-8")
    router_src = (INTAKE_DIR / "router.py").read_text(encoding="utf-8")
    for forbidden in ("chunk_text", "full_text", "matched_text", "holding_summary"):
        assert forbidden not in service_src
        assert forbidden not in router_src
