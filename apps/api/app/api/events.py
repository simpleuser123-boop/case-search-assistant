"""Privacy-safe analytics event endpoint."""
from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError, TimeoutError

from app.api.errors import api_error_response
from app.core.db import engine
from app.core.logging import logger
from app.core.privacy import find_sensitive_metadata_keys, metadata_keys_only
from app.core.timing import TimingRecorder
from app.schemas import AnalyticsEventRequest, AnalyticsEventResponse, ErrorResponse

router = APIRouter(prefix="/api", tags=["events"])

EVENT_DB_DEGRADED_REASON = "DB_UNREACHABLE_EVENT_NOT_PERSISTED"


def _event_db_status() -> tuple[bool, str | None]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - event fallback must never block callers
        if isinstance(exc, TimeoutError):
            return False, "connection_timeout"
        if isinstance(exc, OperationalError):
            return False, "connection_failed"
        if isinstance(exc, SQLAlchemyError):
            return False, "sqlalchemy_error"
        return False, "connection_failed"


@router.post(
    "/events",
    response_model=AnalyticsEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
    },
)
def create_event(payload: AnalyticsEventRequest):
    recorder = TimingRecorder()
    sensitive_keys = find_sensitive_metadata_keys(payload.metadata)
    timings = recorder.finish()
    if sensitive_keys:
        logger.warning(
            "analytics_event_rejected query_session_id=%s event_name=%s "
            "reason=sensitive_metadata_keys metadata_keys=%s timings=%s",
            payload.query_session_id,
            payload.event_name,
            metadata_keys_only(payload.metadata),
            timings.__dict__,
        )
        return api_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="SENSITIVE_EVENT_METADATA",
            message="埋点 metadata 包含疑似原始案情、原始 query、密钥或可识别个人信息字段，已拒绝接收。",
            query_session_id=payload.query_session_id,
        )

    db_reachable, db_reason = _event_db_status()
    if not db_reachable:
        logger.warning(
            "analytics_event_degraded query_session_id=%s event_name=%s "
            "reason=%s db_reason=%s metadata_keys=%s timings=%s",
            payload.query_session_id,
            payload.event_name,
            EVENT_DB_DEGRADED_REASON,
            db_reason,
            metadata_keys_only(payload.metadata),
            timings.__dict__,
        )
        return AnalyticsEventResponse(
            query_session_id=payload.query_session_id,
            accepted=True,
            degraded=True,
            degraded_reasons=[EVENT_DB_DEGRADED_REASON],
            timings=timings,
        )

    logger.info(
        "analytics_event_accepted query_session_id=%s event_name=%s "
        "metadata_keys=%s timings=%s",
        payload.query_session_id,
        payload.event_name,
        metadata_keys_only(payload.metadata),
        timings.__dict__,
    )
    return AnalyticsEventResponse(
        query_session_id=payload.query_session_id,
        accepted=True,
        timings=timings,
    )
