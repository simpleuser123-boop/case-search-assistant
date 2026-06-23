"""Privacy-safe result feedback endpoint."""
from __future__ import annotations

from fastapi import APIRouter, status

from app.core.feedback_events import FeedbackEventStore
from app.core.logging import logger
from app.core.timing import TimingRecorder
from app.schemas import FeedbackEventRequest, FeedbackEventResponse

router = APIRouter(prefix="/api", tags=["feedback"])

FEEDBACK_EVENT_STORAGE_UNAVAILABLE = "FEEDBACK_EVENT_STORAGE_UNAVAILABLE"

feedback_event_store = FeedbackEventStore()


@router.post(
    "/feedback",
    response_model=FeedbackEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_feedback_event(payload: FeedbackEventRequest):
    recorder = TimingRecorder()
    event = payload.model_dump()

    try:
        feedback_event_store.append(event)
    except Exception as exc:  # noqa: BLE001 - feedback must not interrupt search use
        timings = recorder.finish()
        logger.warning(
            "feedback_event_degraded event_type=%s session_hash=%s query_hash=%s "
            "case_id_hash=%s rank=%s feedback_value=%s search_mode=%s "
            "confidence_level=%s reason=%s error_type=%s timings=%s",
            payload.event_type,
            payload.session_hash,
            payload.query_hash,
            payload.case_id_hash,
            payload.rank,
            payload.feedback_value,
            payload.search_mode,
            payload.confidence_level,
            FEEDBACK_EVENT_STORAGE_UNAVAILABLE,
            exc.__class__.__name__,
            timings.__dict__,
        )
        return FeedbackEventResponse(
            accepted=True,
            stored=False,
            degraded=True,
            degraded_reasons=[FEEDBACK_EVENT_STORAGE_UNAVAILABLE],
            feedback_value=payload.feedback_value,
            timings=timings,
        )

    timings = recorder.finish()
    logger.info(
        "feedback_event_accepted event_type=%s session_hash=%s query_hash=%s "
        "case_id_hash=%s rank=%s feedback_value=%s search_mode=%s "
        "confidence_level=%s timings=%s",
        payload.event_type,
        payload.session_hash,
        payload.query_hash,
        payload.case_id_hash,
        payload.rank,
        payload.feedback_value,
        payload.search_mode,
        payload.confidence_level,
        timings.__dict__,
    )
    return FeedbackEventResponse(
        accepted=True,
        stored=True,
        feedback_value=payload.feedback_value,
        timings=timings,
    )
