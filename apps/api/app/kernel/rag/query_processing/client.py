"""DeepSeek client for query rewrite.

Only the cleaned query is sent to DeepSeek. The client never logs request
content, response content, or API keys.
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


class DeepSeekClientError(Exception):
    """Base error for DeepSeek rewrite calls."""


class DeepSeekTimeoutError(DeepSeekClientError):
    """DeepSeek call exceeded the configured timeout."""


class DeepSeekHTTPError(DeepSeekClientError):
    """DeepSeek returned an HTTP error."""


class DeepSeekUnavailableError(DeepSeekClientError):
    """DeepSeek was unreachable or returned an unexpected transport error."""


@dataclass(frozen=True)
class DeepSeekClient:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    chat_completions_path: str = "/v1/chat/completions"
    model: str = "deepseek-chat"
    timeout_seconds: int = 5
    urlopen: UrlOpen = field(default=urllib.request.urlopen)

    def rewrite_query(self, cleaned_query: str) -> str:
        """Return raw JSON text from the model response."""
        url = chat_completions_url(self.base_url, self.chat_completions_path)
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是法律类案检索的查询改写器。只输出一个严格 JSON 对象,"
                        "不要输出 markdown、解释、前后缀或代码块。"
                        "JSON 必须且只能包含以下字段:"
                        "legal_elements, query_variants, case_cause_hint, confidence, notes。"
                        "legal_elements 必须是字符串数组, 不是对象, 不要包含 key-value 子结构。"
                        "query_variants 必须是 2 到 3 条字符串数组, 每条围绕事实相似性检索。"
                        "case_cause_hint 必须是字符串。confidence 必须是 0 到 1 之间的 JSON 数字,"
                        "不能是字符串, 不能写百分比。notes 必须是短字符串或 null。"
                        "不得删除原始关键事实, 不得凭空添加事实, 不要写成法律咨询答案。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"请处理以下已清洗案情 query：{cleaned_query}",
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
            raise DeepSeekTimeoutError("deepseek timeout") from exc
        except urllib.error.HTTPError as exc:
            raise DeepSeekHTTPError(f"deepseek http {exc.code}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise DeepSeekTimeoutError("deepseek timeout") from exc
            raise DeepSeekUnavailableError("deepseek unavailable") from exc
        except Exception as exc:  # noqa: BLE001 - external client must degrade safely
            raise DeepSeekUnavailableError("deepseek unavailable") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepSeekUnavailableError("deepseek response missing content") from exc
        if not isinstance(content, str) or not content.strip():
            raise DeepSeekUnavailableError("deepseek response empty content")
        return content
