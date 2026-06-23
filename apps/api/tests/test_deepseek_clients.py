from __future__ import annotations

import json

from app.core.llm_endpoint import chat_completions_url
from app.query_processing.client import DeepSeekClient
from app.summary.client import DeepSeekSummaryClient


class FakeResponse:
    def __init__(self, body: dict):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._body, ensure_ascii=False).encode("utf-8")


def test_chat_completions_url_uses_v1_path_for_official_root():
    assert (
        chat_completions_url("https://api.deepseek.com/", "/v1/chat/completions")
        == "https://api.deepseek.com/v1/chat/completions"
    )


def test_chat_completions_url_does_not_duplicate_v1_when_base_url_contains_v1():
    assert (
        chat_completions_url("https://api.deepseek.com/v1", "/v1/chat/completions")
        == "https://api.deepseek.com/v1/chat/completions"
    )


def test_query_rewrite_client_posts_to_configured_v1_chat_completions_path():
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "legal_elements": ["盗窃", "现金5000元"],
                                    "query_variants": ["盗窃现金5000元 类案", "夜间入店盗窃 相似事实"],
                                    "case_cause_hint": "盗窃罪",
                                    "confidence": 0.8,
                                    "notes": "保留事实。",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

    client = DeepSeekClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/",
        chat_completions_path="/v1/chat/completions",
        urlopen=fake_urlopen,
        timeout_seconds=7,
    )

    raw = client.rewrite_query("夜间进入店铺盗窃现金5000元")

    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert seen["timeout"] == 7
    assert seen["payload"]["temperature"] == 0.0
    system_prompt = seen["payload"]["messages"][0]["content"]
    assert "legal_elements 必须是字符串数组" in system_prompt
    assert "confidence 必须是 0 到 1 之间的 JSON 数字" in system_prompt
    assert json.loads(raw)["case_cause_hint"] == "盗窃罪"


def test_summary_client_posts_to_configured_v1_chat_completions_path():
    seen: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse({"choices": [{"message": {"content": json.dumps({"text": "规则摘要。"})}}]})

    client = DeepSeekSummaryClient(
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
        chat_completions_path="/v1/chat/completions",
        urlopen=fake_urlopen,
        timeout_seconds=3,
    )

    raw = client.summarize_chunk(
        chunk_excerpt="本院查明,被告人夜间进入店铺盗窃现金5000元。",
        source_chunk_id="chunk-1",
        query_terms=["盗窃"],
        case_cause_hint="盗窃罪",
    )

    assert seen == {"url": "https://api.deepseek.com/v1/chat/completions", "timeout": 3}
    assert json.loads(raw)["text"] == "规则摘要。"
