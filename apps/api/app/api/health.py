"""健康检查路由（Day0 §4.2）。

GET /health 返回服务存活、密钥存在性（仅布尔）、Chroma 目录可写、
DB 连接可用。绝不返回密钥值。
"""
from __future__ import annotations

import os
import json
import urllib.error
import urllib.request

from fastapi import APIRouter
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy import text

from app.core.config import settings, check_secrets_present
from app.core.db import engine
from app.core.runtime import runtime_metadata
from app.retrieval.chroma_adapter import ChromaCollectionAdapter

router = APIRouter()


def _chroma_dir_writable() -> bool:
    path = settings.CHROMA_PERSIST_DIR
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:  # noqa: BLE001 - health must not fail the request
        return False


def _db_reachable() -> bool:
    reachable, _reason = _probe_db_connection()
    return reachable


def _probe_db_connection() -> tuple[bool, str | None]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - 健康检查只关心连得上与否
        return False, _db_degraded_reason(exc)


def _db_degraded_reason(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "connection_timeout"
    if isinstance(exc, OperationalError):
        return "connection_failed"
    if isinstance(exc, SQLAlchemyError):
        return "sqlalchemy_error"
    return "connection_failed"


def _db_target() -> dict:
    url = engine.url
    return {
        "driver": getattr(url, "drivername", None) or getattr(engine.dialect, "name", "unknown"),
        "dialect": getattr(engine.dialect, "name", "unknown"),
        "host": getattr(url, "host", None),
        "database": getattr(url, "database", None),
    }


def _db_status() -> dict:
    reachable, degraded_reason = _probe_db_connection()
    return {
        **_db_target(),
        "reachable": reachable,
        "degraded_reason": degraded_reason,
    }


def _ollama_status() -> dict:
    url = settings.OLLAMA_BASE_URL.rstrip("/") + "/api/show"
    payload = json.dumps({"model": settings.EMBEDDING_MODEL}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.EMBEDDING_TIMEOUT_SECONDS) as resp:
            json.loads(resp.read().decode("utf-8"))
        return {
            "reachable": True,
            "model": settings.EMBEDDING_MODEL,
            "provider": settings.EMBEDDING_PROVIDER,
            "dimension": settings.EMBEDDING_DIMENSION,
            "distance_metric": settings.EMBEDDING_DISTANCE_METRIC,
            "model_available": True,
            "degraded_reason": None,
        }
    except urllib.error.HTTPError as exc:
        return {
            "reachable": True,
            "model": settings.EMBEDDING_MODEL,
            "provider": settings.EMBEDDING_PROVIDER,
            "dimension": settings.EMBEDDING_DIMENSION,
            "distance_metric": settings.EMBEDDING_DISTANCE_METRIC,
            "model_available": False,
            "degraded_reason": f"ollama_http_{exc.code}",
        }
    except Exception as exc:  # noqa: BLE001 - health must not fail the request
        return {
            "reachable": False,
            "model": settings.EMBEDDING_MODEL,
            "provider": settings.EMBEDDING_PROVIDER,
            "dimension": settings.EMBEDDING_DIMENSION,
            "distance_metric": settings.EMBEDDING_DISTANCE_METRIC,
            "model_available": False,
            "degraded_reason": exc.__class__.__name__,
        }


def _chroma_status(*, writable: bool | None = None) -> dict:
    probe = ChromaCollectionAdapter().probe()
    return {
        "reachable": probe.metadata_valid,
        "collection": probe.collection,
        "persist_dir": probe.persist_dir,
        "writable": _chroma_dir_writable() if writable is None else writable,
        "queryable": probe.queryable,
        "chunk_count": probe.chunk_count,
        "metadata_valid": probe.metadata_valid,
        "metadata": probe.metadata,
        "degraded_reason": probe.degraded_reason,
    }


@router.get("/health")
def health() -> dict:
    db = _db_status()
    ollama = _ollama_status()
    chroma = _chroma_status()
    chroma_dir_writable = bool(chroma.get("writable", False))
    return {
        "status": "ok",
        "secrets_present": check_secrets_present(),  # 仅 True/False，无值
        "runtime": runtime_metadata(settings),
        "chroma_dir_writable": chroma_dir_writable,
        "db_reachable": db["reachable"],
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_dimension": settings.EMBEDDING_DIMENSION,
        "embedding_distance_metric": settings.EMBEDDING_DISTANCE_METRIC,
        "chroma_collection": settings.CHROMA_COLLECTION,
        "feature_flags": {
            "ENABLE_QUERY_REWRITE": settings.ENABLE_QUERY_REWRITE,
            "ENABLE_WEIGHTED_RERANK": settings.ENABLE_WEIGHTED_RERANK,
            "ENABLE_SUMMARY": settings.ENABLE_SUMMARY,
            "ENABLE_EXPANDED_SEARCH": settings.ENABLE_EXPANDED_SEARCH,
        },
        "ollama_reachable": bool(ollama["reachable"] and ollama["model_available"]),
        "chroma_collection_queryable": bool(chroma["queryable"]),
        "chroma_chunk_count": chroma["chunk_count"],
        "dependencies": {
            "db": db,
            "ollama": ollama,
            "chroma": chroma,
        },
    }
