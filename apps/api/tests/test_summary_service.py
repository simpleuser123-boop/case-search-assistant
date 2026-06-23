from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.query_processing.models import QueryPlan
from app.retrieval.models import CaseCandidate
from app.summary import (
    SUMMARY_DISABLED,
    SUMMARY_LLM_INVALID_JSON,
    SUMMARY_LLM_SCHEMA_INVALID,
    SUMMARY_LLM_TIMEOUT,
    SUMMARY_SOURCE_MISSING,
    SummaryService,
)
from app.summary.client import SummaryLLMTimeoutError
from app.summary.models import SourceChunk


class FakeSummaryClient:
    def __init__(self, output: str | Exception) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    def summarize_chunk(
        self,
        *,
        chunk_excerpt: str,
        source_chunk_id: str,
        query_terms: list[str],
        case_cause_hint: str,
    ) -> str:
        self.calls.append(
            {
                "chunk_excerpt": chunk_excerpt,
                "source_chunk_id": source_chunk_id,
                "query_terms": query_terms,
                "case_cause_hint": case_cause_hint,
            }
        )
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def _settings(**overrides):
    values = {
        "DEEPSEEK_API_KEY": "test-key",
        "ENABLE_SUMMARY": False,
        "SUMMARY_TIMEOUT_SECONDS": 1,
    }
    values.update(overrides)
    return Settings(**values)


def _plan() -> QueryPlan:
    return QueryPlan(
        cleaned_query="夜间进入店铺盗窃现金5000元",
        input_hash="hash-for-test",
        queries=["夜间进入店铺盗窃现金5000元"],
        legal_elements=["夜间进入店铺", "盗窃现金", "5000元"],
        case_cause_hint="盗窃罪",
    )


def _candidate(
    *,
    case_id: str = "case-1",
    top_chunk_id: str = "case-1-c1",
    source_chunk_ids: list[str] | None = None,
    hit_chunk_ids: list[str] | None = None,
    matched_text: str = (
        "本院查明,被告人夜间进入店铺。被告人盗窃现金5000元。"
        "案发后被告人退赔被害人损失。另查明,被告人此前无犯罪记录。"
    ),
) -> CaseCandidate:
    return CaseCandidate(
        case_id=case_id,
        top_chunk_id=top_chunk_id,
        source_chunk_ids=[top_chunk_id] if source_chunk_ids is None else source_chunk_ids,
        hit_chunk_ids=[top_chunk_id] if hit_chunk_ids is None else hit_chunk_ids,
        retrieval_source=["chroma_vector"],
        metadata={"case_cause": "盗窃罪", "chunk_type": "facts"},
        matched_text=matched_text,
        source="unit-test",
        vector_score=0.8,
        top_chunk_score=0.8,
        retrieval_score=0.8,
    )


def _sentence_count(text: str) -> int:
    return sum(1 for part in text.replace("!", "。").replace("?", "。").split("。") if part.strip())


def test_matched_chunk_extracts_two_to_three_sentence_summary():
    fake = FakeSummaryClient(SummaryLLMTimeoutError("timeout"))
    service = SummaryService(config=_settings(ENABLE_SUMMARY=True), summary_client=fake)

    presentation = service.build_presentation(_plan(), _candidate())

    assert presentation.summary is not None
    assert 2 <= _sentence_count(presentation.summary.text) <= 3
    assert "夜间进入店铺" in presentation.summary.text
    assert "盗窃现金5000元" in presentation.summary.text
    assert presentation.summary.method == "extractive"


def test_summary_always_includes_source_chunk_id_and_case_id():
    service = SummaryService(config=_settings())

    presentation = service.build_presentation(_plan(), _candidate())

    assert presentation.summary is not None
    assert presentation.summary.source_chunk_id == "case-1-c1"
    assert presentation.summary.source_case_id == "case-1"


def test_missing_source_chunk_id_suppresses_summary_and_highlights():
    service = SummaryService(config=_settings())
    candidate = _candidate(top_chunk_id="", source_chunk_ids=[], hit_chunk_ids=[])

    presentation = service.build_presentation(_plan(), candidate)

    assert presentation.summary is None
    assert presentation.highlights == []
    assert presentation.degraded_reasons == [SUMMARY_SOURCE_MISSING]


def test_summary_falls_back_to_safe_chunk_snippet_when_sentence_extraction_fails():
    fake = FakeSummaryClient(SummaryLLMTimeoutError("timeout"))
    service = SummaryService(config=_settings(ENABLE_SUMMARY=True), summary_client=fake)
    candidate = _candidate(matched_text="甲乙丙丁戊己庚辛壬癸" * 30)

    presentation = service.build_presentation(_plan(), candidate)

    assert presentation.summary is not None
    assert presentation.summary.source_chunk_id == "case-1-c1"
    assert presentation.summary.method == "extractive"
    assert len(presentation.summary.text) <= 183
    assert presentation.summary.text.endswith("...")


def test_highlights_include_short_snippets_source_chunk_id_and_no_html():
    service = SummaryService(config=_settings())
    candidate = _candidate(matched_text="本院查明,<script>被告人夜间进入店铺盗窃现金5000元。</script>")

    presentation = service.build_presentation(_plan(), candidate)

    assert presentation.highlights
    first = presentation.highlights[0]
    assert first.source_chunk_id == "case-1-c1"
    assert len(first.text) <= 96
    assert "<script>" not in first.text
    assert "&lt;script&gt;" in first.text


def test_summary_disabled_does_not_call_llm():
    fake = FakeSummaryClient(json.dumps({"text": "LLM 摘要。"}, ensure_ascii=False))
    service = SummaryService(config=_settings(ENABLE_SUMMARY=False), summary_client=fake)

    presentation = service.build_presentation(_plan(), _candidate())

    assert fake.calls == []
    assert presentation.summary is not None
    assert presentation.summary.method == "source_snippet"
    assert presentation.summary.degraded_reason == SUMMARY_DISABLED
    assert presentation.degraded_reasons == [SUMMARY_DISABLED]


def test_llm_summary_success_passes_schema_and_keeps_source_anchor():
    fake = FakeSummaryClient(
        json.dumps({"text": "被告人夜间进入店铺。被告人盗窃现金5000元。"}, ensure_ascii=False)
    )
    service = SummaryService(config=_settings(ENABLE_SUMMARY=True), summary_client=fake)

    presentation = service.build_presentation(_plan(), _candidate())

    assert len(fake.calls) == 1
    assert fake.calls[0]["source_chunk_id"] == "case-1-c1"
    assert "夜间进入店铺盗窃现金5000元" not in fake.calls[0]["query_terms"]
    assert presentation.summary is not None
    assert presentation.summary.text == "被告人夜间进入店铺。被告人盗窃现金5000元。"
    assert presentation.summary.source_chunk_id == "case-1-c1"
    assert presentation.summary.method == "llm_deepseek"


def test_llm_timeout_falls_back_to_rule_summary():
    fake = FakeSummaryClient(SummaryLLMTimeoutError("timeout"))
    service = SummaryService(config=_settings(ENABLE_SUMMARY=True), summary_client=fake)

    presentation = service.build_presentation(_plan(), _candidate())

    assert presentation.summary is not None
    assert presentation.summary.method == "extractive"
    assert presentation.summary.degraded_reason == SUMMARY_LLM_TIMEOUT
    assert presentation.degraded_reasons == [SUMMARY_LLM_TIMEOUT]


def test_llm_non_json_falls_back_to_rule_summary():
    fake = FakeSummaryClient("不是 JSON")
    service = SummaryService(config=_settings(ENABLE_SUMMARY=True), summary_client=fake)

    presentation = service.build_presentation(_plan(), _candidate())

    assert presentation.summary is not None
    assert presentation.summary.method == "extractive"
    assert presentation.summary.degraded_reason == SUMMARY_LLM_INVALID_JSON
    assert presentation.degraded_reasons == [SUMMARY_LLM_INVALID_JSON]


def test_llm_schema_invalid_falls_back_to_rule_summary():
    fake = FakeSummaryClient(json.dumps({"text": ""}, ensure_ascii=False))
    service = SummaryService(config=_settings(ENABLE_SUMMARY=True), summary_client=fake)

    presentation = service.build_presentation(_plan(), _candidate())

    assert presentation.summary is not None
    assert presentation.summary.method == "extractive"
    assert presentation.summary.degraded_reason == SUMMARY_LLM_SCHEMA_INVALID
    assert presentation.degraded_reasons == [SUMMARY_LLM_SCHEMA_INVALID]


def test_chunk_resolver_reads_text_when_candidate_has_only_chunk_id():
    def resolver(chunk_id: str, *, case_id: str) -> SourceChunk:
        assert chunk_id == "case-1-c1"
        assert case_id == "case-1"
        return SourceChunk(
            case_id=case_id,
            chunk_id=chunk_id,
            text="经审理查明,被告人夜间进入店铺。被告人盗窃现金5000元。",
        )

    service = SummaryService(config=_settings(), chunk_resolver=resolver)
    presentation = service.build_presentation(_plan(), _candidate(matched_text=""))

    assert presentation.summary is not None
    assert "盗窃现金5000元" in presentation.summary.text
    assert presentation.summary.source_chunk_id == "case-1-c1"
