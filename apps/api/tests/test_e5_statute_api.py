"""E5-4 法条检索端 statute 后端端点测试。

验证（对应文档 20 §E5-4 验收 / 测试要求）：
- ENABLE_STATUTE_SEARCH=true（测试内 override）：
  - POST /search 返回 StatuteRef[]；字段严格白名单、100% statute_anchors（带 text_id）、0 裁判正文。
  - POST /by-case 返回关联 StatuteRef[]（类案→法条互跳）。
  - POST /cases-by-statute 返回 CandidateRef[]（法条→类案互跳，白名单七字段 + source_anchors + 0 正文）。
- ENABLE_STATUTE_SEARCH=false：端点 403 安全降级，不检索、不泄露内部信息。
- POST 含 raw_case / raw_query / PII / 裁判正文 / 模型生成条文型键被拒（422/400），异常消息不含原始内容。
- statute 经 StatuteSearchService 执行（spy），不直接实例化 retrieval / rerank（静态 AST 断言）。
- 日志只写 query_session_id / 计数 / degraded_reasons，不含 query_text / 原始案情 / 裁判正文。
- statute 产品包只依赖 app.kernel 公开面，不 import 其它产品包、不直连检索底层（静态断言）。

红线：fixture 只用短假数据 / hash / text_id / case_id / source_chunk_id / 元数据，绝不写真实长案情或裁判正文。
"""
from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.kernel.rag import (
    CandidateRef,
    StatuteCaseRefResult,
    StatuteSearchResult,
)
from app.kernel.rag.internal_search_contracts import SourceAnchorRef
from app.kernel.guardrails.contracts import StatuteAnchorRef, StatuteRef
from app.main import app
from app.statute.service import StatuteQueryService

statute_router_mod = importlib.import_module("app.statute.router")

STATUTE_DIR = Path(__file__).resolve().parents[1] / "app" / "statute"

# statute 响应 / 源码绝不允许出现的正文 / 富展示 / 模型生成条文型字段。
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
    "generated_article",
    "llm_text",
    "paraphrased_article",
)

# statute 产品包禁止直连 / 深引的内核底层与禁止 import 的其它产品包。
FORBIDDEN_STATUTE_IMPORT_PREFIXES = (
    "app.retrieval",
    "app.rerank",
    "app.summary",
    "app.query_processing",
    "app.kernel.rag.retrieval",
    "app.kernel.rag.rerank",
    "app.kernel.rag.summary",
    "app.kernel.rag.query_processing",
    "app.intake",
    "app.drafting",
    "app.casebook",
)


# --- fakes（短假数据，无副作用，不写库）-------------------------------------

def _statute_ref(statute_id: str = "s264") -> StatuteRef:
    return StatuteRef(
        statute_id=statute_id,
        law_name="中华人民共和国刑法",
        article_no="第二百六十四条",
        statute_anchors=[
            StatuteAnchorRef(
                text_id=f"law::{statute_id}",
                law_name="中华人民共和国刑法",
                article_no="第二百六十四条",
                anchor_type="statute",
            )
        ],
        source_corpus="judge_law_corpus",
        effective_status="current",
    )


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
                anchor_type="statute_link",
            )
        ],
    )


class SpyStatuteSearchService:
    """记录调用、回吐固定 StatuteRef[] / CandidateRef[]，验证 statute 经此服务执行。"""

    def __init__(
        self,
        *,
        statute_result: StatuteSearchResult | None = None,
        case_result: StatuteCaseRefResult | None = None,
    ) -> None:
        self._statute_result = statute_result or StatuteSearchResult(
            statute_refs=[_statute_ref("s264")],
            degraded=False,
            degraded_reasons=[],
        )
        self._case_result = case_result or StatuteCaseRefResult(
            candidate_refs=[_candidate_ref("c1")],
            degraded=False,
            degraded_reasons=[],
        )
        self.search_calls: list[dict] = []
        self.by_case_calls: list[dict] = []
        self.cases_by_statute_calls: list[dict] = []

    def search_statutes(self, query, *, limit=None, query_session_id=None):
        # statute 传入的是 SearchProfile（经服务层装配，已脱敏）。
        from app.kernel.rag import SearchProfile

        assert isinstance(query, SearchProfile)
        self.search_calls.append(
            {
                "limit": limit,
                "query_session_id": query_session_id,
                "query_text": query.query_text,
            }
        )
        return self._statute_result

    def statutes_by_case(self, case, *, limit=None, query_session_id=None):
        self.by_case_calls.append(
            {"case": case, "limit": limit, "query_session_id": query_session_id}
        )
        return self._statute_result

    def cases_by_statute(self, statute, *, limit=None, query_session_id=None):
        self.cases_by_statute_calls.append(
            {"statute": statute, "limit": limit, "query_session_id": query_session_id}
        )
        return self._case_result


def _enabled_settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY="k", ENABLE_STATUTE_SEARCH=True)


def _disabled_settings() -> Settings:
    return Settings(DEEPSEEK_API_KEY="k", ENABLE_STATUTE_SEARCH=False)


@pytest.fixture()
def enabled_spy(monkeypatch):
    """ENABLE_STATUTE_SEARCH=true + 注入 spy StatuteSearchService。返回 (client, spy)。"""
    monkeypatch.setattr(statute_router_mod, "settings", _enabled_settings())
    spy = SpyStatuteSearchService()
    statute_router_mod.set_statute_query_service_for_test(
        StatuteQueryService(statute_search_service=spy)
    )
    client = TestClient(app)
    yield client, spy
    statute_router_mod.set_statute_query_service_for_test(None)


@pytest.fixture()
def disabled_client(monkeypatch):
    monkeypatch.setattr(statute_router_mod, "settings", _disabled_settings())
    statute_router_mod.set_statute_query_service_for_test(None)
    return TestClient(app)


# --- 1) flag 开启 /search：白名单 StatuteRef[]，0 正文，100% statute_anchors ----

def test_search_returns_statute_refs_whitelist_zero_body(enabled_spy):
    client, spy = enabled_spy
    resp = client.post(
        "/api/statute/search",
        json={
            "case_cause": "盗窃",
            "region": "北京",
            "trial_level_preference": "一审",
            "dispute_focus_keywords": ["自首", "数额"],
            "query_text": "盗窃 数额 自首",
            "mode": "standard",
            "limit": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["statute_count"] == 1
    assert len(data["statute_refs"]) == 1
    ref = data["statute_refs"][0]
    assert set(ref.keys()) <= {
        "statute_id",
        "law_name",
        "article_no",
        "statute_anchors",
        "article_text",
        "source_corpus",
        "effective_status",
        "related_case_refs",
    }
    # 100% statute_anchors，每条至少 text_id（无锚点不展示）。
    assert ref["statute_anchors"]
    for anchor in ref["statute_anchors"]:
        assert anchor["text_id"]
    # 0 正文：响应里不得出现任何正文 / 富展示 / 模型生成条文型字段。
    body_text = resp.text
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in body_text
    # statute 确经 StatuteSearchService 执行（spy 被调用）。
    assert len(spy.search_calls) == 1


def test_search_passes_limit_and_mode(enabled_spy):
    client, spy = enabled_spy
    resp = client.post(
        "/api/statute/search",
        json={"query_text": "合同 违约", "mode": "expanded", "limit": 5},
    )
    assert resp.status_code == 200, resp.text
    assert spy.search_calls[0]["limit"] == 5
    assert resp.json()["search_mode"] == "expanded"


# --- 2) flag 开启 /by-case：类案→法条互跳，返回 StatuteRef[] ------------------

def test_by_case_returns_related_statutes(enabled_spy):
    client, spy = enabled_spy
    resp = client.post(
        "/api/statute/by-case", json={"case_id": "c1", "limit": 10}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["statute_count"] == 1
    assert spy.by_case_calls[0]["case"] == "c1"
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in resp.text


# --- 3) flag 开启 /cases-by-statute：法条→类案互跳，返回 CandidateRef[] --------

def test_cases_by_statute_returns_candidate_refs_whitelist(enabled_spy):
    client, spy = enabled_spy
    resp = client.post(
        "/api/statute/cases-by-statute", json={"statute_id": "s264", "limit": 10}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["candidate_count"] == 1
    ref = data["candidate_refs"][0]
    # 白名单七字段 + source_anchors。
    assert set(ref.keys()) <= {
        "case_id",
        "case_number",
        "court",
        "trial_level",
        "case_cause",
        "judgment_date",
        "source_anchors",
    }
    assert ref["source_anchors"]
    for anchor in ref["source_anchors"]:
        assert anchor["case_id"] and anchor["source_chunk_id"]
    for forbidden in FORBIDDEN_BODY_FIELDS:
        assert forbidden not in resp.text
    assert spy.cases_by_statute_calls[0]["statute"] == "s264"


def test_degraded_passthrough_no_body(enabled_spy):
    client, _ = enabled_spy
    degraded_spy = SpyStatuteSearchService(
        statute_result=StatuteSearchResult(
            statute_refs=[],
            degraded=True,
            degraded_reasons=["STATUTE_REF_DROPPED_NO_ANCHOR"],
        )
    )
    statute_router_mod.set_statute_query_service_for_test(
        StatuteQueryService(statute_search_service=degraded_spy)
    )
    resp = client.post("/api/statute/search", json={"query_text": "测试"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["degraded"] is True
    assert data["degraded_reasons"] == ["STATUTE_REF_DROPPED_NO_ANCHOR"]
    assert data["statute_count"] == 0


# --- 4) flag 关闭：403 安全降级，不检索 --------------------------------------

@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("/api/statute/search", {"query_text": "盗窃 自首"}),
        ("/api/statute/by-case", {"case_id": "c1"}),
        ("/api/statute/cases-by-statute", {"statute_id": "s264"}),
    ],
)
def test_disabled_returns_403_safe_degrade(disabled_client, endpoint, payload):
    resp = disabled_client.post(endpoint, json=payload)
    assert resp.status_code == 403
    data = resp.json()
    assert data["error"]["code"] == "STATUTE_SEARCH_DISABLED"
    # 不泄露内部信息：无 statute_refs / 内部栈 / query_text 回显。
    assert "盗窃" not in resp.text
    assert "statute_refs" not in resp.text


# --- 5) raw_case / raw_query / PII / 裁判正文 / 模型生成条文型键被拒（第一道闸）-

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
        {"generated_article": "第X条 模型杜撰条文", "query_text": "盗窃"},
        {"llm_text": "模型生成", "query_text": "盗窃"},
    ],
)
def test_forbidden_keys_rejected_no_echo(enabled_spy, bad_payload):
    client, spy = enabled_spy
    resp = client.post("/api/statute/search", json=bad_payload)
    assert resp.status_code in (400, 422), resp.text
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
        "模型杜撰条文",
        "模型生成",
    ]
    for value in pii_values:
        assert value not in resp.text
    assert spy.search_calls == []


def test_unknown_extra_field_rejected(enabled_spy):
    client, _ = enabled_spy
    resp = client.post(
        "/api/statute/search",
        json={"query_text": "盗窃", "unexpected_field": "x"},
    )
    assert resp.status_code == 422, resp.text


def test_by_case_requires_case_id(enabled_spy):
    client, _ = enabled_spy
    resp = client.post("/api/statute/by-case", json={"limit": 5})
    assert resp.status_code == 422, resp.text


# --- 6) 日志脱敏：只写 query_session_id / 计数 / degraded_reasons -------------

def test_logs_exclude_query_text_and_pii(enabled_spy, caplog):
    client, _ = enabled_spy
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/api/statute/search",
            json={"query_text": "盗窃 5000元 自首", "case_cause": "盗窃"},
        )
    assert resp.status_code == 200, resp.text
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "盗窃 5000元 自首" not in log_text
    assert "query_text" not in log_text
    assert "statute_search_completed" in log_text


# --- 7) 静态边界：statute 只依赖 app.kernel 公开面，不直连底层 / 不 import 产品包 -

def _iter_statute_imports():
    modules: list[str] = []
    for path in sorted(STATUTE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
    return modules


def test_statute_does_not_import_retrieval_layer_or_other_products():
    for module in _iter_statute_imports():
        for forbidden in FORBIDDEN_STATUTE_IMPORT_PREFIXES:
            assert not module.startswith(forbidden), (
                f"statute 不得 import {module}（应只走 app.kernel 公开面）"
            )


def test_statute_only_consumes_kernel_public_surface():
    """statute 对内核的依赖只允许 app.kernel / app.kernel.rag / app.kernel.guardrails 顶层公开面。

    例外：测试模块自身可深引内核契约子模块构造 fake；产品包源码（app/statute/*.py）不允许。
    """
    allowed = (
        "app.kernel",
        "app.kernel.rag",
        "app.kernel.guardrails",
    )
    for module in _iter_statute_imports():
        if module.startswith("app.kernel"):
            assert module in allowed, (
                f"statute 只能消费内核顶层公开面，禁止深引 {module}"
            )


def test_statute_source_has_no_body_field_literals():
    """statute 源码不得把正文 / 模型生成条文型字段当数据字段搬运（只可作被拒键名 / 注释）。"""
    service_src = (STATUTE_DIR / "service.py").read_text(encoding="utf-8")
    router_src = (STATUTE_DIR / "router.py").read_text(encoding="utf-8")
    for forbidden in (
        "chunk_text",
        "full_text",
        "matched_text",
        "holding_summary",
        "generated_article",
        "llm_text",
        "paraphrased_article",
    ):
        assert forbidden not in service_src
        assert forbidden not in router_src
