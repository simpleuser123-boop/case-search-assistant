"""M3-5: similarity highlight anchors for detail-page source navigation.

This module derives a minimal, metadata-only ``similarity_highlights`` list from
already-anchored reading-assist content (holding summary, issue focus, key
elements). It NEVER stores chunk body text, raw query, or any judgment text.

Boundaries (M3-5):
- highlights only point to a source chunk; they never rewrite or echo body text.
- highlights are a reading aid; they emit no legal conclusion.
- highlights never use offline-eval fields (qrels/label/relevance/query id).
- highlights never special-case a case id.
- highlights never influence main result ranking.
"""
from __future__ import annotations

from typing import Any

# anchor_type values reused from the existing source-anchor contract.
HIGHLIGHT_ANCHOR_TYPE = "detail_chunk"

# related_module identifiers (which reading-assist module a highlight serves).
MODULE_HOLDING_SUMMARY = "holding_summary"
MODULE_ISSUE_FOCUS = "issue_focus"
MODULE_KEY_ELEMENTS = "key_elements"

# display_status values.
STATUS_AVAILABLE = "available"
STATUS_DEGRADED = "degraded"

# degrade_reason codes (sanitized; no body text).
REASON_MISSING_SOURCE_ANCHOR = "missing_source_anchor"
REASON_SOURCE_CHUNK_UNAVAILABLE = "source_chunk_unavailable"
REASON_HIGHLIGHT_TARGET_MISSING = "highlight_target_missing"
REASON_NAVIGATION_FAILED = "navigation_failed"

HIGHLIGHT_REASON_CODES = (
    REASON_MISSING_SOURCE_ANCHOR,
    REASON_SOURCE_CHUNK_UNAVAILABLE,
    REASON_HIGHLIGHT_TARGET_MISSING,
    REASON_NAVIGATION_FAILED,
)

MAX_HIGHLIGHTS = 12


def build_similarity_highlights(
    *,
    case_id: str,
    holding_summary: dict[str, Any] | None,
    issue_focus: dict[str, Any] | None,
    key_elements: dict[str, Any] | None,
    chunks: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Derive similarity highlights from anchored reading-assist content.

    Returns a list of dicts shaped like the ``SimilarityHighlight`` schema. Every
    highlight carries ``case_id`` + ``source_chunk_id`` (the minimum anchor) and a
    ``display_status``. When the referenced chunk cannot be resolved to a navigable
    source excerpt, the highlight is kept but marked degraded with a reason code so
    the frontend can render a safe state instead of a broken jump.
    """

    clean_case_id = _clean(case_id)
    if not clean_case_id:
        return []

    navigable_chunk_ids = _navigable_chunk_ids(case_id=clean_case_id, chunks=chunks or [])

    highlights: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    module_sources = (
        (MODULE_HOLDING_SUMMARY, _iter_anchors_from_items(holding_summary, "summary_items")),
        (MODULE_ISSUE_FOCUS, _iter_anchors_from_items(issue_focus, "items")),
        (MODULE_KEY_ELEMENTS, _iter_anchors_from_items(key_elements, "items")),
    )

    for related_module, anchors in module_sources:
        for anchor in anchors:
            if len(highlights) >= MAX_HIGHLIGHTS:
                return highlights

            anchor_case_id = _clean(anchor.get("case_id"))
            source_chunk_id = _clean(anchor.get("source_chunk_id"))
            anchor_type = _clean(anchor.get("anchor_type")) or HIGHLIGHT_ANCHOR_TYPE

            # Minimum anchor contract: must have case_id + source_chunk_id and
            # must belong to this case. Otherwise it is not a usable highlight.
            if not anchor_case_id or not source_chunk_id:
                continue
            if anchor_case_id != clean_case_id:
                continue

            dedupe_key = (related_module, source_chunk_id, anchor_type)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            if source_chunk_id in navigable_chunk_ids:
                display_status = STATUS_AVAILABLE
                degrade_reason = None
            else:
                display_status = STATUS_DEGRADED
                degrade_reason = REASON_SOURCE_CHUNK_UNAVAILABLE

            highlights.append(
                {
                    "highlight_id": _highlight_id(
                        case_id=clean_case_id,
                        related_module=related_module,
                        source_chunk_id=source_chunk_id,
                        index=len(highlights),
                    ),
                    "case_id": clean_case_id,
                    "source_chunk_id": source_chunk_id,
                    "anchor_type": anchor_type,
                    "related_module": related_module,
                    "display_status": display_status,
                    "degrade_reason": degrade_reason,
                }
            )

    return highlights


def summarize_highlights(highlights: list[dict[str, Any]]) -> dict[str, Any]:
    """Build sanitized log/report counters (no body text, no case-id specials)."""

    by_module: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for highlight in highlights:
        module = str(highlight.get("related_module") or "")
        status = str(highlight.get("display_status") or "")
        reason = str(highlight.get("degrade_reason") or "")
        if module:
            by_module[module] = by_module.get(module, 0) + 1
        if status:
            by_status[status] = by_status.get(status, 0) + 1
        if reason:
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "count": len(highlights),
        "by_module": by_module,
        "by_status": by_status,
        "by_reason": by_reason,
    }


def _navigable_chunk_ids(*, case_id: str, chunks: list[dict[str, Any]]) -> set[str]:
    """A chunk is navigable when it has body text and a valid detail_chunk anchor."""

    navigable: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_id = _clean(chunk.get("chunk_id"))
        if not chunk_id:
            continue
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        if _has_detail_chunk_anchor(case_id=case_id, chunk_id=chunk_id, chunk=chunk):
            navigable.add(chunk_id)
    return navigable


def _has_detail_chunk_anchor(*, case_id: str, chunk_id: str, chunk: dict[str, Any]) -> bool:
    anchors = chunk.get("source_anchors")
    if not isinstance(anchors, list):
        return False
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        if (
            _clean(anchor.get("case_id")) == case_id
            and _clean(anchor.get("source_chunk_id")) == chunk_id
            and _clean(anchor.get("anchor_type")) == HIGHLIGHT_ANCHOR_TYPE
        ):
            return True
    return False


def _iter_anchors_from_items(
    section: dict[str, Any] | None,
    items_key: str,
) -> list[dict[str, Any]]:
    """Collect anchors only from a *generated* reading-assist section."""

    if not isinstance(section, dict):
        return []
    if str(section.get("generation_status") or "") != "generated":
        return []

    anchors: list[dict[str, Any]] = []
    for item in section.get(items_key) or []:
        if not isinstance(item, dict):
            continue
        for anchor in item.get("source_anchors") or []:
            if isinstance(anchor, dict):
                anchors.append(anchor)
    return anchors


def _highlight_id(
    *,
    case_id: str,
    related_module: str,
    source_chunk_id: str,
    index: int,
) -> str:
    return f"hl_{related_module}_{source_chunk_id}_{index}"


def _clean(value: Any) -> str:
    return str(value or "").strip()
