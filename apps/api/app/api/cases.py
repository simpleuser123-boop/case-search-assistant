"""Case detail API skeleton."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from fastapi import APIRouter, Request, status

from app.api.errors import api_error_response
from app.case_store.jsonl_store import CaseStoreNotReadyError, get_case_detail
from app.core.config import settings
from app.core.logging import logger
from app.core.timing import TimingRecorder
from app.schemas import (
    CaseDetailResponse,
    ErrorResponse,
    FactAlignmentRequest,
    FactAlignmentResponse,
)
from app.summary import (
    FACT_ALIGNMENT_FAILED,
    FACT_ALIGNMENT_TIMEOUT,
    FactAlignmentService,
    HOLDING_MODEL_FAILED,
    SummaryService,
)
from app.summary.highlights import build_similarity_highlights, summarize_highlights

router = APIRouter(prefix="/api", tags=["cases"])
summary_service = SummaryService()
fact_alignment_service = FactAlignmentService()
_fact_alignment_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fact-align")


@router.get(
    "/cases/{case_id}",
    response_model=CaseDetailResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def get_case(case_id: str, request: Request):
    query_session_id = str(request.state.query_session_id)
    recorder = TimingRecorder()
    try:
        detail = get_case_detail(case_id)
    except CaseStoreNotReadyError:
        timings = recorder.finish()
        logger.warning(
            "case_detail_failed query_session_id=%s case_id=%s reason=case_store_not_ready timings=%s",
            query_session_id,
            case_id,
            timings.__dict__,
        )
        return api_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="CASE_STORE_NOT_READY",
            message="案例详情数据读取能力尚未就绪。",
            query_session_id=query_session_id,
        )

    if detail is None:
        timings = recorder.finish()
        logger.info(
            "case_detail_not_found query_session_id=%s case_id=%s timings=%s",
            query_session_id,
            case_id,
            timings.__dict__,
        )
        return api_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="CASE_NOT_FOUND",
            message="未找到指定案例。",
            query_session_id=query_session_id,
        )

    holding_summary = _build_holding_summary(detail)
    reading_navigation = _build_reading_navigation(detail)
    similarity_highlights = _build_similarity_highlights(
        case_id=case_id,
        holding_summary=holding_summary,
        reading_navigation=reading_navigation,
        detail=detail,
    )
    highlight_summary = summarize_highlights(similarity_highlights)
    timings = recorder.finish()
    logger.info(
        "case_detail_loaded query_session_id=%s case_id=%s chunk_count=%s "
        "holding_summary_status=%s holding_summary_item_count=%s "
        "holding_summary_reason=%s "
        "issue_focus_status=%s issue_focus_item_count=%s issue_focus_categories=%s "
        "issue_focus_reason=%s key_elements_status=%s key_elements_item_count=%s "
        "key_elements_categories=%s key_elements_reason=%s "
        "highlight_count=%s highlight_by_module=%s highlight_by_status=%s "
        "highlight_by_reason=%s timings=%s",
        query_session_id,
        case_id,
        len(detail.get("chunks", [])),
        holding_summary.get("generation_status"),
        len(holding_summary.get("summary_items", [])),
        holding_summary.get("degrade_reason") or "",
        reading_navigation["issue_focus"].get("generation_status"),
        len(reading_navigation["issue_focus"].get("items", [])),
        _reading_categories(reading_navigation["issue_focus"]),
        reading_navigation["issue_focus"].get("degrade_reason") or "",
        reading_navigation["key_elements"].get("generation_status"),
        len(reading_navigation["key_elements"].get("items", [])),
        _reading_categories(reading_navigation["key_elements"]),
        reading_navigation["key_elements"].get("degrade_reason") or "",
        highlight_summary["count"],
        highlight_summary["by_module"],
        highlight_summary["by_status"],
        highlight_summary["by_reason"],
        timings.__dict__,
    )
    return CaseDetailResponse(
        **detail,
        holding_summary=holding_summary,
        issue_focus=reading_navigation["issue_focus"],
        key_elements=reading_navigation["key_elements"],
        similarity_highlights=similarity_highlights,
        query_session_id=query_session_id,
        timings=timings,
    )


def _build_holding_summary(detail: dict):
    try:
        return summary_service.build_holding_summary(
            case_id=str(detail.get("case_id") or ""),
            case_cause_hint=str(detail.get("case_cause") or ""),
            chunks=list(detail.get("chunks") or []),
        )
    except Exception as exc:  # noqa: BLE001 - reading assist must never break detail
        logger.warning(
            "case_detail_holding_summary_failed case_id=%s error_type=%s reason=%s",
            detail.get("case_id") or "",
            exc.__class__.__name__,
            HOLDING_MODEL_FAILED,
        )
        return {
            "summary_items": [],
            "source_anchors": [],
            "confidence": "low",
            "generation_status": "degraded",
            "degrade_reason": HOLDING_MODEL_FAILED,
        }


def _build_reading_navigation(detail: dict):
    try:
        return summary_service.build_issue_focus_and_key_elements(
            case_id=str(detail.get("case_id") or ""),
            chunks=list(detail.get("chunks") or []),
        )
    except Exception as exc:  # noqa: BLE001 - reading assist must never break detail
        logger.warning(
            "case_detail_reading_navigation_failed error_type=%s reason=%s",
            exc.__class__.__name__,
            HOLDING_MODEL_FAILED,
        )
        degraded = {
            "items": [],
            "source_anchors": [],
            "generation_status": "degraded",
            "degrade_reason": HOLDING_MODEL_FAILED,
        }
        return {"issue_focus": degraded, "key_elements": degraded}


def _build_similarity_highlights(
    *,
    case_id: str,
    holding_summary: dict,
    reading_navigation: dict,
    detail: dict,
) -> list[dict]:
    """Derive M3-5 highlight anchors; never break detail on failure."""

    try:
        return build_similarity_highlights(
            case_id=str(detail.get("case_id") or case_id),
            holding_summary=holding_summary,
            issue_focus=reading_navigation.get("issue_focus"),
            key_elements=reading_navigation.get("key_elements"),
            chunks=list(detail.get("chunks") or []),
        )
    except Exception as exc:  # noqa: BLE001 - highlight is a reading aid; never break detail
        logger.warning(
            "case_detail_similarity_highlights_failed case_id=%s error_type=%s",
            detail.get("case_id") or "",
            exc.__class__.__name__,
        )
        return []


def _reading_categories(section: dict) -> str:
    categories = sorted(
        {
            str(item.get("category"))
            for item in section.get("items", [])
            if item.get("category")
        }
    )
    return ",".join(categories)


@router.post(
    "/cases/{case_id}/fact-alignment",
    response_model=FactAlignmentResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def post_fact_alignment(case_id: str, payload: FactAlignmentRequest, request: Request):
    """Lazy-loaded similar-fact alignment.

    The request body carries the user query only for in-request abstraction.
    We never persist it; logs record sanitized counts/status/reason only.
    """

    query_session_id = str(request.state.query_session_id)
    recorder = TimingRecorder()
    try:
        detail = get_case_detail(case_id)
    except CaseStoreNotReadyError:
        timings = recorder.finish()
        logger.warning(
            "fact_alignment_failed query_session_id=%s case_id=%s reason=case_store_not_ready",
            query_session_id,
            case_id,
        )
        return api_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="CASE_STORE_NOT_READY",
            message="案例详情数据读取能力尚未就绪。",
            query_session_id=query_session_id,
        )

    if detail is None:
        timings = recorder.finish()
        logger.info(
            "fact_alignment_not_found query_session_id=%s case_id=%s",
            query_session_id,
            case_id,
        )
        return api_error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="CASE_NOT_FOUND",
            message="未找到指定案例。",
            query_session_id=query_session_id,
        )

    alignment = _build_fact_alignment(detail=detail, query_signal=payload.query_signal)
    timings = recorder.finish()
    logger.info(
        "fact_alignment_built query_session_id=%s case_id=%s status=%s item_count=%s "
        "match_type_count=%s query_signal_present=%s reason=%s",
        query_session_id,
        case_id,
        alignment.get("generation_status"),
        len(alignment.get("items", [])),
        _match_type_count(alignment),
        bool(alignment.get("query_signal_present")),
        alignment.get("degrade_reason") or "",
    )
    return FactAlignmentResponse(
        case_id=str(detail.get("case_id") or case_id),
        items=alignment.get("items", []),
        generation_status=alignment.get("generation_status", "degraded"),
        degrade_reason=alignment.get("degrade_reason"),
        query_signal_present=bool(alignment.get("query_signal_present")),
        query_session_id=query_session_id,
        timings=timings,
    )


def _build_fact_alignment(*, detail: dict, query_signal: str):
    """Run the alignment with a defensive timeout; degrade on any failure."""

    timeout_seconds = max(0.2, float(settings.FACT_ALIGNMENT_TIMEOUT_SECONDS))
    future = _fact_alignment_executor.submit(
        fact_alignment_service.build_fact_alignment,
        case_id=str(detail.get("case_id") or ""),
        query_signal_text=query_signal,
        chunks=list(detail.get("chunks") or []),
    )
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError:
        future.cancel()
        logger.warning(
            "fact_alignment_timeout case_id=%s reason=%s",
            detail.get("case_id") or "",
            FACT_ALIGNMENT_TIMEOUT,
        )
        return _degraded_fact_alignment(FACT_ALIGNMENT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - reading assist must never break detail
        logger.warning(
            "fact_alignment_error case_id=%s error_type=%s reason=%s",
            detail.get("case_id") or "",
            exc.__class__.__name__,
            FACT_ALIGNMENT_FAILED,
        )
        return _degraded_fact_alignment(FACT_ALIGNMENT_FAILED)


def _degraded_fact_alignment(reason: str) -> dict:
    return {
        "items": [],
        "generation_status": "degraded",
        "degrade_reason": reason,
        "query_signal_present": False,
    }


def _match_type_count(alignment: dict) -> str:
    counts: dict[str, int] = {}
    for item in alignment.get("items", []):
        match_type = str(item.get("match_type") or "")
        if match_type:
            counts[match_type] = counts.get(match_type, 0) + 1
    return ",".join(f"{key}:{value}" for key, value in sorted(counts.items()))
