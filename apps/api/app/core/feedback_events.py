"""Sanitized feedback event storage.

The storage boundary is deliberately narrow: only fields from
FEEDBACK_EVENT_FIELDS are ever persisted.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

from app.core.config import PROJECT_ROOT

FEEDBACK_EVENT_FIELDS = (
    "event_type",
    "session_hash",
    "query_hash",
    "case_id_hash",
    "rank",
    "feedback_value",
    "search_mode",
    "confidence_level",
)

FORBIDDEN_FEEDBACK_FIELDS = {
    "query",
    "raw_query",
    "raw_text",
    "content",
    "text",
    "case_text",
    "case_body",
    "candidate_body",
    "chunk_text",
    "chunk_body",
    "judgment_text",
    "reason",
    "free_text_reason",
    "案情",
    "案情全文",
    "正文",
}

DEFAULT_FEEDBACK_EVENT_PATH = PROJECT_ROOT / "data" / "logs" / "feedback-events.jsonl"


class FeedbackEventStore:
    def __init__(self, path: Path | None = DEFAULT_FEEDBACK_EVENT_PATH) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []
        self._lock = Lock()

    def append(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        record = {field: payload[field] for field in FEEDBACK_EVENT_FIELDS}

        with self._lock:
            self.records.append(dict(record))
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                    handle.write("\n")

        return record

    def clear(self) -> None:
        with self._lock:
            self.records.clear()
