from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api import search as search_api
from app.core.config import Settings
from app.main import app
from app.query_processing.client import DeepSeekTimeoutError
from app.query_processing.service import (
    LLM_INVALID_JSON,
    LLM_SCHEMA_INVALID,
    LLM_TIMEOUT,
    QUERY_REWRITE_DISABLED,
    QueryProcessingService,
    QueryValidationError,
    clean_query,
    input_hash_for_query,
)
from app.query_processing.models import QueryRewriteLLMOutput
from app.retrieval.models import VectorRetrievalResult

client = TestClient(app)


class MockRewriteClient:
    def __init__(self, output: str | Exception) -> None:
        self.output = output
        self.calls: list[str] = []

    def rewrite_query(self, cleaned_query: str) -> str:
        self.calls.append(cleaned_query)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class EchoRewriteClient:
    def rewrite_query(self, cleaned_query: str) -> str:
        return json.dumps(
            {
                "legal_elements": [cleaned_query[:12], "事实相似性"],
                "query_variants": [
                    f"{cleaned_query} 类案 相似事实",
                    f"{cleaned_query} 裁判文书 同类事实",
                ],
                "case_cause_hint": "",
                "confidence": 0.8,
                "notes": "保留原事实。",
            },
            ensure_ascii=False,
        )


class EmptyRetrievalService:
    def retrieve(self, query_plan, *, include_relaxed_recall: bool = False) -> VectorRetrievalResult:
        return VectorRetrievalResult(
            candidates=[],
            embedding_duration_ms=1,
            retrieval_duration_ms=2,
            degraded=False,
            degraded_reasons=[],
        )


def _settings(**overrides):
    values = {
        "DEEPSEEK_API_KEY": "test-key",
        "ENABLE_QUERY_REWRITE": True,
        "QUERY_MAX_LENGTH": 5000,
        "QUERY_MIN_SEMANTIC_LENGTH": 4,
    }
    values.update(overrides)
    return Settings(**values)


def _valid_llm_json() -> str:
    return json.dumps(
        {
            "legal_elements": ["盗窃", "夜间进入店铺", "金额5000元"],
            "query_variants": [
                "盗窃 夜间进入店铺 金额5000元 类案",
                "夜间进入店铺盗窃5000元 财物损失 相似事实",
            ],
            "case_cause_hint": "盗窃罪",
            "confidence": 0.86,
            "notes": "保留金额和行为事实。",
        },
        ensure_ascii=False,
    )


def test_clean_query_normalizes_whitespace_and_common_punctuation():
    raw = "  张三　2023年，借款（5000元）；到期未还：“微信转账”。  "

    cleaned = clean_query(raw)

    assert cleaned == '张三 2023年,借款(5000元);到期未还:"微信转账".'
    assert "张三" in cleaned
    assert "5000元" in cleaned
    assert "2023年" in cleaned


@pytest.mark.parametrize(
    ("raw_query", "code"),
    [
        ("   ", "QUERY_EMPTY"),
        ("，，！！；；", "QUERY_PUNCTUATION_ONLY"),
        ("甲", "QUERY_TOO_SHORT"),
    ],
)
def test_invalid_queries_are_rejected(raw_query, code):
    service = QueryProcessingService(config=_settings(ENABLE_QUERY_REWRITE=False))

    with pytest.raises(QueryValidationError) as exc_info:
        service.process(raw_query)

    assert exc_info.value.code == code


def test_overlong_query_is_rejected():
    service = QueryProcessingService(config=_settings(QUERY_MAX_LENGTH=8, ENABLE_QUERY_REWRITE=False))

    with pytest.raises(QueryValidationError) as exc_info:
        service.process("夜间进入店铺盗窃现金5000元")

    assert exc_info.value.code == "QUERY_TOO_LONG"
    assert exc_info.value.status_code == 413


def test_normal_query_generates_input_hash():
    service = QueryProcessingService(config=_settings(ENABLE_QUERY_REWRITE=False))

    plan = service.process("夜间进入店铺盗窃现金5000元")

    assert plan.input_hash == input_hash_for_query("夜间进入店铺盗窃现金5000元")
    assert len(plan.input_hash) == 64
    assert plan.queries == [plan.cleaned_query]


def test_raw_query_and_rewrite_text_do_not_appear_in_logs(caplog, monkeypatch):
    raw_query = "这是不能进日志的原始案情XYZ,夜间盗窃现金5000元"
    rewrite_text = "这是不能进日志的改写文本XYZ,夜间盗窃现金5000元"
    mock = MockRewriteClient(
        json.dumps(
            {
                "legal_elements": ["盗窃", "现金5000元"],
                "query_variants": [
                    rewrite_text,
                    "盗窃现金5000元 相似事实",
                ],
                "case_cause_hint": "盗窃罪",
                "confidence": 0.7,
                "notes": "短说明",
            },
            ensure_ascii=False,
        )
    )
    service = QueryProcessingService(config=_settings(), rewrite_client=mock)
    monkeypatch.setattr(search_api, "query_processing_service", service)
    monkeypatch.setattr(search_api, "retrieval_service", EmptyRetrievalService())
    caplog.set_level(logging.INFO, logger="case_search")

    resp = client.post("/api/search", json={"query": raw_query})

    assert resp.status_code == 200
    assert resp.json()["results"] == []
    assert raw_query not in caplog.text
    assert rewrite_text not in caplog.text
    assert "input_hash=" in caplog.text


def test_rewrite_disabled_does_not_call_deepseek():
    mock = MockRewriteClient(_valid_llm_json())
    service = QueryProcessingService(
        config=_settings(ENABLE_QUERY_REWRITE=False),
        rewrite_client=mock,
    )

    plan = service.process("夜间进入店铺盗窃现金5000元")

    assert mock.calls == []
    assert plan.rewrite_used is False
    assert plan.degraded is True
    assert plan.degraded_reasons == [QUERY_REWRITE_DISABLED]
    assert plan.queries == [plan.cleaned_query]


def test_high_confidence_local_mapping_runs_when_rewrite_disabled():
    service = QueryProcessingService(config=_settings(ENABLE_QUERY_REWRITE=False))

    plan = service.process("2019年网络赌博抽头渔利2万元")

    assert plan.rewrite_used is False
    assert plan.degraded_reasons == [QUERY_REWRITE_DISABLED]
    assert plan.local_mapping_used is True
    assert plan.mapping_version == "m1_2_query_understanding_v1"
    assert "casino_online_profit" in plan.high_confidence_mappings
    assert plan.low_confidence_mappings == []
    assert plan.case_cause_hint == ""
    assert plan.legal_elements == []
    assert plan.queries[0] == plan.cleaned_query
    assert len(plan.query_variants) == 1


def test_narrow_high_confidence_mapping_can_participate_in_weighting():
    service = QueryProcessingService(config=_settings(ENABLE_QUERY_REWRITE=False))

    plan = service.process("偷东西")

    assert plan.local_mapping_used is True
    assert plan.high_confidence_mappings == ["ordinary_theft_colloquial"]
    assert "盗窃" in plan.legal_elements
    assert plan.confidence == 0.9


def test_low_confidence_local_mapping_is_recall_only():
    service = QueryProcessingService(config=_settings(ENABLE_QUERY_REWRITE=False))

    plan = service.process("帮赌博平台收钱3万元")

    assert plan.local_mapping_used is True
    assert plan.high_confidence_mappings == []
    assert plan.low_confidence_mappings == ["casino_payment_help"]
    assert plan.case_cause_hint == ""
    assert plan.legal_elements == []
    assert plan.confidence is None
    assert any("开设赌场帮助行为" in variant for variant in plan.query_variants)


def test_experimental_local_mapping_is_not_in_default_path():
    service = QueryProcessingService(config=_settings(ENABLE_QUERY_REWRITE=False))

    plan = service.process("假冒别人身份办卡")

    assert plan.local_mapping_used is False
    assert plan.mapping_labels == []
    assert plan.queries == [plan.cleaned_query]


def test_local_mapping_variants_preserve_key_fact_constraints():
    service = QueryProcessingService(config=_settings(ENABLE_QUERY_REWRITE=False))

    plan = service.process("2020年提供场所供多人吸毒3次")

    assert plan.local_mapping_used is True
    assert "harbor_drug_use" in plan.high_confidence_mappings
    for variant in plan.query_variants:
        assert "2020年" in variant
        assert "3次" in variant
        assert "吸毒" in variant


def test_deepseek_valid_json_passes_schema_and_keeps_cleaned_query_first():
    mock = MockRewriteClient(_valid_llm_json())
    service = QueryProcessingService(config=_settings(), rewrite_client=mock)

    plan = service.process("夜间进入店铺盗窃现金5000元")

    assert mock.calls == ["夜间进入店铺盗窃现金5000元"]
    assert plan.rewrite_used is True
    assert plan.degraded is False
    assert plan.legal_elements == ["盗窃", "夜间进入店铺", "金额5000元"]
    assert len(plan.query_variants) == 2
    assert plan.case_cause_hint == "盗窃罪"
    assert plan.confidence == 0.86
    assert plan.queries[0] == plan.cleaned_query
    assert plan.cleaned_query in plan.queries


def test_query_rewrite_schema_accepts_two_to_three_variants_and_confidence_range():
    two_variants = QueryRewriteLLMOutput.model_validate(
        {
            "legal_elements": ["盗窃", "现金5000元"],
            "query_variants": ["盗窃现金5000元 相似事实", "夜间入店盗窃 类案"],
            "case_cause_hint": "盗窃罪",
            "confidence": 0,
        }
    )
    three_variants = QueryRewriteLLMOutput.model_validate(
        {
            "legal_elements": ["盗窃", "现金5000元"],
            "query_variants": ["盗窃现金5000元", "入店盗窃", "盗窃罪类案"],
            "case_cause_hint": "盗窃罪",
            "confidence": 1,
        }
    )

    assert len(two_variants.query_variants) == 2
    assert len(three_variants.query_variants) == 3
    assert two_variants.confidence == 0
    assert three_variants.confidence == 1


@pytest.mark.parametrize(
    "payload",
    [
        {
            "legal_elements": ["盗窃"],
            "query_variants": ["只有一条"],
            "case_cause_hint": "盗窃罪",
            "confidence": 0.8,
        },
        {
            "legal_elements": ["盗窃"],
            "query_variants": ["盗窃5000元", "店铺盗窃5000元", "入店盗窃", "盗窃罪"],
            "case_cause_hint": "盗窃罪",
            "confidence": 0.8,
        },
        {
            "legal_elements": ["盗窃"],
            "query_variants": ["盗窃5000元", "店铺盗窃5000元"],
            "case_cause_hint": "盗窃罪",
            "confidence": -0.1,
        },
        {
            "legal_elements": ["盗窃"],
            "query_variants": ["盗窃5000元", "店铺盗窃5000元"],
            "case_cause_hint": "盗窃罪",
            "confidence": 1.1,
        },
        {
            "legal_elements": ["盗窃"],
            "query_variants": ["盗窃5000元", "店铺盗窃5000元"],
            "case_cause_hint": "盗窃罪",
            "confidence": "0.8",
        },
    ],
)
def test_query_rewrite_schema_rejects_variant_count_and_confidence_violations(payload):
    with pytest.raises(ValidationError):
        QueryRewriteLLMOutput.model_validate(payload)


def test_deepseek_timeout_degrades_without_interrupting_request():
    mock = MockRewriteClient(DeepSeekTimeoutError("timeout"))
    service = QueryProcessingService(config=_settings(), rewrite_client=mock)

    plan = service.process("夜间进入店铺盗窃现金5000元")

    assert plan.degraded is True
    assert plan.degraded_reasons == [LLM_TIMEOUT]
    assert plan.queries == [plan.cleaned_query]


def test_deepseek_non_json_degrades():
    mock = MockRewriteClient("不是 JSON")
    service = QueryProcessingService(config=_settings(), rewrite_client=mock)

    plan = service.process("夜间进入店铺盗窃现金5000元")

    assert plan.degraded is True
    assert plan.degraded_reasons == [LLM_INVALID_JSON]
    assert plan.queries == [plan.cleaned_query]


@pytest.mark.parametrize(
    "payload",
    [
        {"legal_elements": ["盗窃"], "query_variants": ["只有一条"], "case_cause_hint": "盗窃罪", "confidence": 0.8},
        {"query_variants": ["盗窃5000元", "店铺盗窃5000元"], "case_cause_hint": "盗窃罪", "confidence": 0.8},
        {"legal_elements": ["盗窃"], "query_variants": ["盗窃5000元", "店铺盗窃5000元"], "case_cause_hint": "盗窃罪", "confidence": 1.5},
        {"legal_elements": ["盗窃"], "query_variants": ["完全无关案情", "合同解除争议"], "case_cause_hint": "盗窃罪", "confidence": 0.8},
    ],
)
def test_deepseek_schema_invalid_degrades(payload):
    mock = MockRewriteClient(json.dumps(payload, ensure_ascii=False))
    service = QueryProcessingService(config=_settings(), rewrite_client=mock)

    plan = service.process("夜间进入店铺盗窃现金5000元")

    assert plan.degraded is True
    assert plan.degraded_reasons == [LLM_SCHEMA_INVALID]
    assert plan.queries == [plan.cleaned_query]


def test_typical_queries_can_produce_legal_elements_with_mock_deepseek():
    queries = [
        "夜间进入店铺盗窃现金5000元",
        "醉酒驾驶发生追尾事故造成一人轻伤",
        "借款10万元到期不还有微信转账记录",
        "冒充客服诈骗被害人3万元",
        "故意伤害致人轻伤二级后赔偿谅解",
        "职务侵占公司货款8万元",
        "聚众赌博抽头渔利2万元",
        "抢劫便利店持刀威胁店员",
        "交通事故逃逸造成财产损失",
        "贩卖毒品甲基苯丙胺20克",
    ]
    service = QueryProcessingService(
        config=_settings(),
        rewrite_client=EchoRewriteClient(),
    )

    plans = [service.process(query) for query in queries]

    assert sum(1 for plan in plans if plan.legal_elements) >= 8
