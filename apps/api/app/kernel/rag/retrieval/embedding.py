"""Ollama bge-m3 query embedding client.

This client never logs or embeds query text in raised exceptions. Callers should
log only sanitized error codes and input_hash values.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.core.config import Settings, settings
from app.kernel.rag.retrieval.models import (
    EmbeddingResult,
    RetrievalConfigMismatchError,
    RetrievalDependencyError,
)

EXPECTED_PROVIDER = "ollama"
EXPECTED_MODEL = "bge-m3"
EXPECTED_DIMENSION = 1024
EXPECTED_DISTANCE_METRIC = "cosine"
EMBEDDING_WARMUP_TEXT = "类案检索向量预热"


UrlOpen = Callable[..., Any]


@dataclass(frozen=True)
class OllamaEmbeddingClient:
    config: Settings = field(default_factory=lambda: settings)
    urlopen: UrlOpen = urllib.request.urlopen

    def embed_query(self, text: str) -> EmbeddingResult:
        vectors = self.embed_queries([text])
        return EmbeddingResult(
            provider=self.config.EMBEDDING_PROVIDER,
            model=self.config.EMBEDDING_MODEL,
            dimension=len(vectors[0]),
            vector=vectors[0],
        )

    def embed_queries(self, texts: list[str], *, timeout_seconds: float | None = None) -> list[list[float]]:
        self._validate_embedding_config()
        if not texts:
            return []

        url = self.config.OLLAMA_BASE_URL.rstrip("/") + "/api/embed"
        timeout = max(float(timeout_seconds or self.config.EMBEDDING_TIMEOUT_SECONDS), 0.1)
        payload = json.dumps(
            {"model": self.config.EMBEDDING_MODEL, "input": texts},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (socket.timeout, TimeoutError) as exc:
            raise RetrievalDependencyError("EMBEDDING_TIMEOUT", "Ollama embedding timed out.") from exc
        except urllib.error.HTTPError as exc:
            raise RetrievalDependencyError(
                "EMBEDDING_HTTP_ERROR",
                f"Ollama embedding returned HTTP {exc.code}.",
            ) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise RetrievalDependencyError("EMBEDDING_TIMEOUT", "Ollama embedding timed out.") from exc
            raise RetrievalDependencyError("EMBEDDING_UNAVAILABLE", "Ollama embedding is unavailable.") from exc
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RetrievalDependencyError("EMBEDDING_BAD_RESPONSE", "Ollama embedding response is invalid.") from exc

        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RetrievalDependencyError("EMBEDDING_BAD_RESPONSE", "Ollama embedding count is invalid.")

        vectors: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list):
                raise RetrievalDependencyError("EMBEDDING_BAD_RESPONSE", "Ollama embedding vector is invalid.")
            if len(vector) != self.config.EMBEDDING_DIMENSION:
                raise RetrievalConfigMismatchError(
                    "EMBEDDING_DIMENSION_MISMATCH",
                    (
                        "Ollama embedding dimension does not match configured "
                        f"dimension {self.config.EMBEDDING_DIMENSION}."
                    ),
                )
            vectors.append([float(value) for value in vector])
        return vectors

    def _validate_embedding_config(self) -> None:
        provider = self.config.EMBEDDING_PROVIDER.strip().lower()
        model = self.config.EMBEDDING_MODEL.strip()
        distance_metric = self.config.EMBEDDING_DISTANCE_METRIC.strip().lower()
        dimension = int(self.config.EMBEDDING_DIMENSION)

        if provider != EXPECTED_PROVIDER:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_PROVIDER_MISMATCH",
                f"Expected embedding provider {EXPECTED_PROVIDER}, got {provider or '<empty>'}.",
            )
        if model != EXPECTED_MODEL:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_MODEL_MISMATCH",
                f"Expected embedding model {EXPECTED_MODEL}, got {model or '<empty>'}.",
            )
        if dimension != EXPECTED_DIMENSION:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DIMENSION_MISMATCH",
                f"Expected embedding dimension {EXPECTED_DIMENSION}, got {dimension}.",
            )
        if distance_metric != EXPECTED_DISTANCE_METRIC:
            raise RetrievalConfigMismatchError(
                "EMBEDDING_DISTANCE_MISMATCH",
                f"Expected embedding distance metric {EXPECTED_DISTANCE_METRIC}, got {distance_metric or '<empty>'}.",
            )


@dataclass(frozen=True)
class EmbeddingWarmupStatus:
    ok: bool
    duration_ms: int
    degraded_reason: str | None = None


def warmup_ollama_embedding(
    *,
    config: Settings = settings,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> EmbeddingWarmupStatus:
    started = perf_counter()
    client = OllamaEmbeddingClient(config=config, urlopen=urlopen)
    try:
        client.embed_queries(
            [EMBEDDING_WARMUP_TEXT],
            timeout_seconds=config.EMBEDDING_WARMUP_TIMEOUT_SECONDS,
        )
    except RetrievalConfigMismatchError as exc:
        return EmbeddingWarmupStatus(
            ok=False,
            duration_ms=_elapsed_ms(started),
            degraded_reason=exc.code,
        )
    except RetrievalDependencyError as exc:
        return EmbeddingWarmupStatus(
            ok=False,
            duration_ms=_elapsed_ms(started),
            degraded_reason=exc.code,
        )
    except Exception:  # noqa: BLE001 - startup warmup must never block API startup
        return EmbeddingWarmupStatus(
            ok=False,
            duration_ms=_elapsed_ms(started),
            degraded_reason="EMBEDDING_UNAVAILABLE",
        )
    return EmbeddingWarmupStatus(ok=True, duration_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
