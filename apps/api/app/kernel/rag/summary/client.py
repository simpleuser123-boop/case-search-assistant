"""DeepSeek client for optional short result summaries.

Only bounded chunk excerpts and short query signals are sent. The client never
logs request content, response content, or API keys.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.llm_endpoint import chat_completions_url


UrlOpen = Callable[..., Any]


class SummaryLLMError(Exception):
    """Base error for optional summary calls."""


class SummaryLLMTimeoutError(SummaryLLMError):
    """Summary call exceeded the configured timeout."""


class SummaryLLMUnavailableError(SummaryLLMError):
    """Summary provider was unreachable or returned an unexpected response."""


@dataclass(frozen=True)
class DeepSeekSummaryClient:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    chat_completions_path: str = "/v1/chat/completions"
    model: str = "deepseek-chat"
    timeout_seconds: int = 5
    urlopen: UrlOpen = field(default=urllib.request.urlopen)

    def summarize_chunk(
        self,
        *,
        chunk_excerpt: str,
        source_chunk_id: str,
        query_terms: list[str],
        case_cause_hint: str,
    ) -> str:
        """Return raw JSON text from the model response."""

        url = chat_completions_url(self.base_url, self.chat_completions_path)
        user_payload = {
            "source_chunk_id": source_chunk_id,
            "case_cause_hint": case_cause_hint[:80],
            "query_terms": query_terms[:12],
            "chunk_excerpt": chunk_excerpt,
        }
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是类案检索结果摘要器。只输出严格 JSON, 不输出 markdown。"
                        "JSON 只能包含 text 字段。text 必须为 2 到 3 句事实摘要,"
                        "只能依据给定 chunk_excerpt, 不得添加片段中没有的事实。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (socket.timeout, TimeoutError) as exc:
            raise SummaryLLMTimeoutError("summary llm timeout") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise SummaryLLMTimeoutError("summary llm timeout") from exc
            raise SummaryLLMUnavailableError("summary llm unavailable") from exc
        except Exception as exc:  # noqa: BLE001 - external client must degrade safely
            raise SummaryLLMUnavailableError("summary llm unavailable") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SummaryLLMUnavailableError("summary llm response missing content") from exc
        if not isinstance(content, str) or not content.strip():
            raise SummaryLLMUnavailableError("summary llm response empty content")
        return content
