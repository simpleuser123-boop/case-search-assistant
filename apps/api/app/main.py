"""FastAPI 应用入口（Day0 §4.2）。

启动期：校验密钥存在性（缺失只 warning，不打印值，不中断 Day0 骨架启动）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.auth import router as auth_router
from app.api.cases import router as cases_router
from app.api.errors import api_error_response
from app.api.events import router as events_router
from app.api.feedback import router as feedback_router
from app.api.health import router as health_router
from app.api.search import router as search_router
from app.api.team import router as team_router
from app.api.permission import router as permission_router
from app.api.sharing import router as sharing_router
from app.api.bulk_import import router as bulk_import_router
from app.api.billing import router as billing_router
# E4-3：案情录入端 intake 产品包（E 系列第一个产品包，flag-gated，默认 false 降级）。
from app.intake import router as intake_router
# E5-4：法条检索端 statute 产品包（E 系列第二个产品包，flag-gated，默认 false 降级）。
from app.statute import router as statute_router
# E6-2：文书工作台 drafting 产品包（E 系列第三个产品包，flag-gated，默认 false 降级）。
from app.drafting.router import router as drafting_router
# E7-2：案件协作工作台 casebook 产品包（E 系列第四个产品包，flag-gated，默认 false 降级）。
from app.casebook.router import router as casebook_router
from app.core.config import missing_secrets
from app.core.logging import logger
from app.core.session import generate_query_session_id
from app.retrieval.bm25_fallback import warmup_bm25_fallback
from app.retrieval.embedding import warmup_ollama_embedding


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = missing_secrets()
    if missing:
        # 只报“哪个 key 缺”，绝不打印任何 key 的值。
        logger.warning("缺少必需密钥(仅名称): %s", ", ".join(missing))
    else:
        logger.info("必需密钥均已配置")
    warmup = warmup_ollama_embedding()
    if warmup.ok:
        logger.info("embedding_warmup_completed duration_ms=%s", warmup.duration_ms)
    else:
        logger.warning(
            "embedding_warmup_degraded degraded_reason=%s duration_ms=%s",
            warmup.degraded_reason,
            warmup.duration_ms,
        )
    bm25_warmup = warmup_bm25_fallback()
    if bm25_warmup.ok:
        logger.info(
            "bm25_warmup_completed duration_ms=%s document_count=%s",
            bm25_warmup.duration_ms,
            bm25_warmup.document_count,
        )
    else:
        logger.warning(
            "bm25_warmup_degraded degraded_reason=%s duration_ms=%s",
            bm25_warmup.degraded_reason,
            bm25_warmup.duration_ms,
        )
    yield


app = FastAPI(title="类案检索助手 API", version="0.0.0", lifespan=lifespan)


@app.middleware("http")
async def attach_query_session_id(request: Request, call_next):
    request.state.query_session_id = generate_query_session_id()
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # 不把 exc.errors() 直接回传，避免把原始 query/content 反射到响应或日志里。
    query_session_id = getattr(request.state, "query_session_id", None)
    logger.warning(
        "request_validation_failed path=%s query_session_id=%s error_count=%s",
        request.url.path,
        query_session_id,
        len(exc.errors()),
    )
    return api_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="VALIDATION_ERROR",
        message="请求参数不符合接口契约。",
        query_session_id=query_session_id,
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    query_session_id = getattr(request.state, "query_session_id", None)
    return api_error_response(
        status_code=exc.status_code,
        code="HTTP_ERROR",
        message=str(exc.detail),
        query_session_id=query_session_id,
    )


@app.get("/")
async def root():
    return {"service": "case-search-api"}


app.include_router(health_router)
app.include_router(search_router)
app.include_router(cases_router)
app.include_router(events_router)
app.include_router(feedback_router)
app.include_router(auth_router)
app.include_router(team_router)
app.include_router(permission_router)
app.include_router(sharing_router)
app.include_router(bulk_import_router)
app.include_router(billing_router)
app.include_router(intake_router)
app.include_router(statute_router)
app.include_router(drafting_router)
app.include_router(casebook_router)
