"""Read-only JSONL case store backed by Day 0 processed files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT

CASES_PATH = PROJECT_ROOT / "data" / "processed" / "cases.jsonl"
CHUNKS_PATH = PROJECT_ROOT / "data" / "processed" / "chunks.jsonl"
MAX_CHUNKS_PER_CASE = 20
MAX_CHUNK_TEXT_CHARS = 1200
MAX_RESOLVED_CHUNK_TEXT_CHARS = 1200


class CaseStoreNotReadyError(RuntimeError):
    pass


def _iter_jsonl(path: Path):
    if not path.is_file():
        raise CaseStoreNotReadyError(f"missing file: {path.name}")
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                yield json.loads(line)


def get_case_detail(case_id: str) -> dict[str, Any] | None:
    case: dict[str, Any] | None = None
    for row in _iter_jsonl(CASES_PATH):
        if row.get("case_id") == case_id:
            case = row
            break
    if case is None:
        return None

    chunks: list[dict[str, Any]] = []
    for row in _iter_jsonl(CHUNKS_PATH):
        if row.get("case_id") != case_id:
            continue
        text = row.get("text")
        if isinstance(text, str) and len(text) > MAX_CHUNK_TEXT_CHARS:
            text = text[:MAX_CHUNK_TEXT_CHARS]
        chunks.append(
            {
                "chunk_id": row.get("chunk_id", ""),
                "chunk_type": row.get("chunk_type", ""),
                "source_anchors": _source_anchors_for_chunk(
                    case_id=case_id,
                    chunk_id=row.get("chunk_id"),
                    chunk_type=row.get("chunk_type"),
                    source_url=case.get("source_url"),
                    source_ref=case.get("source_name"),
                    anchor_type="detail_chunk",
                ),
                "start_offset": row.get("start_offset"),
                "end_offset": row.get("end_offset"),
                "text": text,
            }
        )
        if len(chunks) >= MAX_CHUNKS_PER_CASE:
            break

    return {**case, "chunks": chunks}


def get_chunk_by_id(chunk_id: str, *, case_id: str | None = None) -> dict[str, Any] | None:
    """Return one processed chunk without reading or exposing full judgments."""

    if not chunk_id:
        return None
    for row in _iter_jsonl(CHUNKS_PATH):
        if row.get("chunk_id") != chunk_id:
            continue
        if case_id and row.get("case_id") != case_id:
            continue
        text = row.get("text")
        if isinstance(text, str) and len(text) > MAX_RESOLVED_CHUNK_TEXT_CHARS:
            text = text[:MAX_RESOLVED_CHUNK_TEXT_CHARS]
        return {
            "case_id": row.get("case_id", ""),
            "chunk_id": row.get("chunk_id", ""),
            "chunk_type": row.get("chunk_type", ""),
            "start_offset": row.get("start_offset"),
            "end_offset": row.get("end_offset"),
            "text": text,
        }
    return None


def _source_anchors_for_chunk(
    *,
    case_id: str | None,
    chunk_id: Any,
    chunk_type: Any,
    source_url: Any,
    source_ref: Any,
    anchor_type: str,
) -> list[dict[str, str | None]]:
    clean_case_id = str(case_id or "").strip()
    clean_chunk_id = str(chunk_id or "").strip()
    if not clean_case_id or not clean_chunk_id:
        return []
    return [
        {
            "case_id": clean_case_id,
            "source_chunk_id": clean_chunk_id,
            "chunk_type": _clean_optional(chunk_type),
            "anchor_type": anchor_type,
            "source_url": _clean_optional(source_url),
            "source_ref": _clean_optional(source_ref) or "local_case_store",
        }
    ]


def _clean_optional(value: Any) -> str | None:
    clean = str(value or "").strip()
    return clean or None
