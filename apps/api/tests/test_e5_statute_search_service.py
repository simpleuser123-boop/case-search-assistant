"""E5-3 内核法条检索服务测试（StatuteSearchService 三入口 + 锚点丢弃 + 静态边界）。

覆盖（与提示词「测试要求」逐条对应）：
1. search_statutes：查询 -> StatuteRef[]；每条有 statute_anchors（text_id 非空）、
   字段严格白名单、无裁判正文型键；缺锚点候选被丢弃并记 STATUTE_REF_DROPPED_NO_ANCHOR。
2. statutes_by_case：CandidateRef / case_id -> 关联 StatuteRef[]（基于 E5-2 标注），无正文。
3. cases_by_statute：StatuteRef / statute_id -> CandidateRef[]，严格白名单七字段 +
   100% source_anchors + 0 正文；无锚点类案被丢弃并记 STATUTE_CASE_REF_DROPPED_NO_ANCHOR。
4. 静态断言：service 不 import fastapi/app.api/app.schemas/产品包；不从聚合 __init__ 回引；
   公开面身份保持（app.kernel.rag.X is app.kernel.X）。

红线：fixture 只用短假法条 / 假 case_id / 假 text_id / 假 chunk_id，绝不写真实长正文 / PII。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.kernel.rag.statute_search_service import (
    CaseLinkHit,
    STATUTE_CASE_REF_DROPPED_NO_ANCHOR,
    STATUTE_REF_DROPPED_NO_ANCHOR,
    StatuteHit,
    StatuteSearchService,
)
from app.kernel.rag.internal_search_contracts import CandidateRef, SearchProfile
from app.kernel.guardrails.contracts import StatuteRef

APP_DIR = Path(__file__).resolve().parents[1] / "app"
SERVICE_MODULE = APP_DIR / "kernel" / "rag" / "statute_search_service.py"

# StatuteRef 白名单（与 statute_contract.STATUTE_REF_FIELDS 同口径，独立列出做守门比对）。
EXPECTED_STATUTE_REF_FIELDS = {
    "statute_id", "law_name", "article_no", "statute_anchors",
    "article_text", "source_corpus", "effective_status", "related_case_refs",
}
EXPECTED_CANDIDATE_REF_FIELDS = {
    "case_id", "case_number", "court", "trial_level",
    "case_cause", "judgment_date", "source_anchors",
}
# 绝不应出现在 StatuteRef / CandidateRef 上的裁判正文 / 富展示 / 模型生成条文型键。
FORBIDDEN_BODY_KEYS = (
    "full_text", "content", "chunk_text", "body", "raw_case", "raw_query",
    "summary", "summary_text", "highlights", "highlight", "highlight_text",
    "matched_text", "holding_summary", "text",
    "generated_article", "llm_text", "paraphrased_article", "rewritten_article",
)


# --- fake 数据端口（短假数据，确定性，绝无真实正文 / PII）-----------------------

class FakeCorpus:
    """可注入的假法条数据端口：用最小假法条 / 假关联，覆盖正常 + 缺锚点丢弃路径。"""

    def __init__(
        self,
        *,
        statutes: list[StatuteHit] | None = None,
        case_links: dict[str, list[StatuteHit]] | None = None,
        statute_cases: dict[str, list[CaseLinkHit]] | None = None,
    ) -> None:
        self._statutes = statutes or []
        self._case_links = case_links or {}
        self._statute_cases = statute_cases or {}

    def search_statutes(self, query_text: str, *, limit: int) -> list[StatuteHit]:
        return self._statutes[:limit]

    def statutes_for_case(self, case_id: str, *, limit: int) -> list[StatuteHit]:
        return self._case_links.get(case_id, [])[:limit]

    def cases_for_statute(self, statute_id: str, *, limit: int) -> list[CaseLinkHit]:
        return self._statute_cases.get(statute_id, [])[:limit]


def _good_statute(sid: str = "fake_art_1", art: str = "1") -> StatuteHit:
    return StatuteHit(
        statute_id=sid,
        law_name="假法名",
        text_id=f"fake::{sid}",
        article_no=art,
        article_text=None,
        source_corpus="fake_catalog",
        effective_status="unverified",
    )


def _anchorless_statute() -> StatuteHit:
    # text_id 为空 -> _safe_statute_ref 应丢弃（缺锚点不展示）。
    return StatuteHit(statute_id="fake_no_anchor", law_name="假法名", text_id="")


# --- search_statutes -------------------------------------------------------------

def test_search_statutes_returns_anchored_statute_refs():
    svc = StatuteSearchService(corpus=FakeCorpus(statutes=[_good_statute(), _good_statute("fake_art_2", "2")]))
    result = svc.search_statutes("假 查询", query_session_id="qs1")

    assert len(result.statute_refs) == 2
    assert result.degraded is False
    for ref in result.statute_refs:
        assert isinstance(ref, StatuteRef)
        # 每条带非空 statute_anchors，且 text_id 非空。
        assert ref.statute_anchors
        for anchor in ref.statute_anchors:
            assert anchor.text_id and anchor.text_id.strip()
        # 字段严格白名单、无裁判正文型键。
        dumped = ref.model_dump()
        assert set(dumped.keys()) <= EXPECTED_STATUTE_REF_FIELDS
        for k in FORBIDDEN_BODY_KEYS:
            assert k not in dumped
        # article_text 不由服务生成（fake 为 None）。
        assert ref.article_text is None


def test_search_statutes_accepts_search_profile():
    svc = StatuteSearchService(corpus=FakeCorpus(statutes=[_good_statute()]))
    profile = SearchProfile(query_text="假 查询", case_cause="假案由")
    result = svc.search_statutes(profile)
    assert len(result.statute_refs) == 1


def test_search_statutes_drops_anchorless_and_records_reason():
    svc = StatuteSearchService(
        corpus=FakeCorpus(statutes=[_good_statute(), _anchorless_statute()])
    )
    result = svc.search_statutes("假 查询")
    # 缺锚点候选被丢弃，只剩 1 条。
    assert len(result.statute_refs) == 1
    assert result.degraded is True
    assert STATUTE_REF_DROPPED_NO_ANCHOR in result.degraded_reasons


def test_search_statutes_empty_query_no_crash():
    svc = StatuteSearchService(corpus=FakeCorpus(statutes=[]))
    result = svc.search_statutes("")
    assert result.statute_refs == []
    assert result.degraded is False


# --- statutes_by_case（类案→法条互跳）-------------------------------------------

def test_statutes_by_case_with_candidate_ref():
    case_ref = CandidateRef(
        case_id="case-1",
        source_anchors=[{"case_id": "case-1", "source_chunk_id": "case-1::c0"}],
    )
    svc = StatuteSearchService(
        corpus=FakeCorpus(case_links={"case-1": [_good_statute(), _good_statute("fake_art_3", "3")]})
    )
    result = svc.statutes_by_case(case_ref)
    assert len(result.statute_refs) == 2
    for ref in result.statute_refs:
        dumped = ref.model_dump()
        assert set(dumped.keys()) <= EXPECTED_STATUTE_REF_FIELDS
        for k in FORBIDDEN_BODY_KEYS:
            assert k not in dumped


def test_statutes_by_case_with_case_id_string():
    svc = StatuteSearchService(corpus=FakeCorpus(case_links={"case-9": [_good_statute()]}))
    result = svc.statutes_by_case("case-9")
    assert len(result.statute_refs) == 1


def test_statutes_by_case_unknown_case_returns_empty():
    svc = StatuteSearchService(corpus=FakeCorpus(case_links={}))
    result = svc.statutes_by_case("nope")
    assert result.statute_refs == []


# --- cases_by_statute（法条→类案互跳）-------------------------------------------

def test_cases_by_statute_returns_whitelisted_candidate_refs():
    links = {
        "fake_art_1": [
            CaseLinkHit(case_id="case-a", source_chunk_ids=("case-a::c0",)),
            CaseLinkHit(case_id="case-b", source_chunk_ids=("case-b::c0",)),
        ]
    }
    svc = StatuteSearchService(corpus=FakeCorpus(statute_cases=links))
    result = svc.cases_by_statute("fake_art_1")
    assert len(result.candidate_refs) == 2
    for ref in result.candidate_refs:
        assert isinstance(ref, CandidateRef)
        dumped = ref.model_dump()
        assert set(dumped.keys()) <= EXPECTED_CANDIDATE_REF_FIELDS
        # 100% source_anchors（非空，含 case_id + source_chunk_id）。
        assert ref.source_anchors
        for anchor in ref.source_anchors:
            assert anchor.case_id and anchor.source_chunk_id
        for k in FORBIDDEN_BODY_KEYS:
            assert k not in dumped


def test_cases_by_statute_accepts_statute_ref():
    statute_ref = StatuteRef(
        statute_id="fake_art_1",
        law_name="假法名",
        statute_anchors=[{"text_id": "fake::fake_art_1"}],
    )
    links = {"fake_art_1": [CaseLinkHit(case_id="case-a", source_chunk_ids=("case-a::c0",))]}
    svc = StatuteSearchService(corpus=FakeCorpus(statute_cases=links))
    result = svc.cases_by_statute(statute_ref)
    assert len(result.candidate_refs) == 1


def test_cases_by_statute_drops_anchorless_case():
    # 无 source_chunk_id 的类案 -> CandidateRef fail-closed 丢弃并记原因码。
    links = {
        "fake_art_1": [
            CaseLinkHit(case_id="case-a", source_chunk_ids=("case-a::c0",)),
            CaseLinkHit(case_id="case-noanchor", source_chunk_ids=()),
        ]
    }
    svc = StatuteSearchService(corpus=FakeCorpus(statute_cases=links))
    result = svc.cases_by_statute("fake_art_1")
    assert len(result.candidate_refs) == 1
    assert result.degraded is True
    assert STATUTE_CASE_REF_DROPPED_NO_ANCHOR in result.degraded_reasons


# --- 静态边界断言（不 import fastapi/app.api/app.schemas/产品包；不回引聚合 __init__）---

def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


def test_service_does_not_import_forbidden_modules():
    mods = _imported_modules(SERVICE_MODULE)
    for m in mods:
        assert not m.startswith("fastapi"), f"service 不得 import fastapi: {m}"
        assert not m.startswith("app.api"), f"service 不得 import app.api: {m}"
        assert not m.startswith("app.schemas"), f"service 不得 import app.schemas: {m}"
        # 不得 import 任何产品包。
        for pkg in ("app.intake", "app.statute", "app.drafting", "app.casebook"):
            assert not m.startswith(pkg), f"service 不得 import 产品包: {m}"


def test_service_does_not_backref_aggregate_init():
    # 不得从聚合 app.kernel.rag / app.kernel 回引（循环导入规避）；只走子包路径。
    mods = _imported_modules(SERVICE_MODULE)
    assert "app.kernel.rag" not in mods
    assert "app.kernel" not in mods
    # 应经子包 / 护栏公开面消费。
    assert "app.kernel.rag.internal_search_contracts" in mods
    assert "app.kernel.rag.query_processing" in mods
    assert "app.kernel.guardrails.contracts" in mods


def test_public_face_identity_preserved():
    import app.kernel as kernel
    import app.kernel.rag as rag

    assert rag.StatuteSearchService is kernel.StatuteSearchService
    assert rag.STATUTE_REF_DROPPED_NO_ANCHOR is kernel.STATUTE_REF_DROPPED_NO_ANCHOR
    assert rag.STATUTE_CASE_REF_DROPPED_NO_ANCHOR is kernel.STATUTE_CASE_REF_DROPPED_NO_ANCHOR
    # 与服务模块真身同一对象（re-export 身份保持）。
    from app.kernel.rag import statute_search_service as mod
    assert rag.StatuteSearchService is mod.StatuteSearchService


def test_search_statutes_rejects_bad_input_type():
    svc = StatuteSearchService(corpus=FakeCorpus())
    with pytest.raises(TypeError):
        svc.search_statutes(123)  # type: ignore[arg-type]
