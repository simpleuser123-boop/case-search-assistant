"""Short-lived in-memory cache for query embeddings.

The cache stores only vectors and sanitized metadata. It never persists or
stores raw query text.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.core.config import settings


@dataclass(frozen=True)
class QueryEmbeddingCacheEntry:
    vectors: list[list[float]]
    metadata: dict[str, Any]
    expires_at: float


class QueryEmbeddingCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        max_entries: int = 256,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._clock = clock
        self._entries: OrderedDict[tuple[str, str], QueryEmbeddingCacheEntry] = OrderedDict()

    def get(
        self,
        *,
        input_hash: str,
        model_version: str,
        input_fingerprint: str,
    ) -> list[list[float]] | None:
        self._prune_expired()
        key = _key(input_hash, model_version)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.metadata.get("input_fingerprint") != input_fingerprint:
            return None
        self._entries.move_to_end(key)
        return _copy_vectors(entry.vectors)

    def set(
        self,
        *,
        input_hash: str,
        model_version: str,
        input_fingerprint: str,
        vectors: list[list[float]],
        metadata: dict[str, Any],
    ) -> None:
        self._prune_expired()
        key = _key(input_hash, model_version)
        sanitized_metadata = {
            **metadata,
            "input_hash": input_hash,
            "model_version": model_version,
            "input_fingerprint": input_fingerprint,
        }
        self._entries[key] = QueryEmbeddingCacheEntry(
            vectors=_copy_vectors(vectors),
            metadata=sanitized_metadata,
            expires_at=self._clock() + self.ttl_seconds,
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def _prune_expired(self) -> None:
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


def get_default_embedding_cache() -> QueryEmbeddingCache:
    return _DEFAULT_CACHE


def _key(input_hash: str, model_version: str) -> tuple[str, str]:
    return input_hash, model_version


def _copy_vectors(vectors: list[list[float]]) -> list[list[float]]:
    return [list(vector) for vector in vectors]


_DEFAULT_CACHE = QueryEmbeddingCache(
    ttl_seconds=settings.EMBEDDING_CACHE_TTL_SECONDS,
    max_entries=settings.EMBEDDING_CACHE_MAX_ENTRIES,
)
