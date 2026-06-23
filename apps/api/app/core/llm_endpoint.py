"""LLM endpoint URL helpers."""
from __future__ import annotations

from urllib.parse import urlparse


def chat_completions_url(base_url: str, path: str) -> str:
    """Build an OpenAI-compatible chat completions URL.

    OpenAI-compatible endpoints are often configured either as the host root
    (``https://api.deepseek.com``) or already at ``/v1``. Avoid duplicating the
    version path while keeping the actual endpoint configurable.
    """
    base = base_url.rstrip("/")
    endpoint_path = "/" + path.strip("/")
    parsed = urlparse(base)
    if parsed.path.rstrip("/") == "/v1" and endpoint_path.startswith("/v1/"):
        endpoint_path = endpoint_path.removeprefix("/v1")
    return base + endpoint_path
