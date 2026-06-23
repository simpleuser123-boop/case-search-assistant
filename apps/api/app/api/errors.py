"""Unified API error responses."""
from __future__ import annotations

from fastapi.responses import JSONResponse

from app.schemas import ErrorResponse


def api_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    query_session_id: str | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error={
            "code": code,
            "message": message,
            "query_session_id": query_session_id,
        }
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())
