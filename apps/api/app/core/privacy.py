"""Privacy utilities for logs and analytics events."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SENSITIVE_METADATA_KEYS = {
    "query",
    "raw_query",
    "raw_text",
    "content",
    "text",
    "case_text",
    "fact",
    "prompt",
    "api_key",
    "secret",
    "password",
    "token",
    "phone",
    "id_card",
    "identity_card",
}


def find_sensitive_metadata_keys(value: Any, prefix: str = "") -> list[str]:
    """Return sensitive key paths in a nested metadata object."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_str = str(key)
            path = f"{prefix}.{key_str}" if prefix else key_str
            if key_str.lower() in SENSITIVE_METADATA_KEYS:
                found.append(path)
            found.extend(find_sensitive_metadata_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_sensitive_metadata_keys(child, f"{prefix}[{index}]"))
    return found


def metadata_keys_only(metadata: Mapping[str, Any]) -> list[str]:
    """Safe log representation: only top-level keys, never values."""
    return sorted(str(key) for key in metadata.keys())
