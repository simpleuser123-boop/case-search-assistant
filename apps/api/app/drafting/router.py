"""E6-2 drafting 文书工作台端点（flag-gated，组装而非起草 + 持久化元数据/引用/短字段）。

端点（均 prefix=/api/drafting，均需登录）：
- POST   /drafts             创建 DraftDescriptor（出已收敛对象，零正文）。
- GET    /drafts             列出当前用户/团队可见的 DraftDescriptor（对象级鉴权 + 租户隔离）。
- GET    /drafts/{draft_id}  读取单个（越权取不到 -> 404）。
- PUT    /drafts/{draft_id}  更新骨架/引用/短字段（仍只存元数据，经 sanitize；非 owner -> 404）。

门控：ENABLE_DRAFTING=false（默认）-> 403 DRAFTING_DISABLED（安全降级，与 intake /
statute 关闭语义一致）；ENABLE_DRAFTING=true 才走持久化 / 读取。前端入口本步不接（E6-3）。

鉴权 / 租户隔离：所有端点需登录（复用 M5-2 会话）；持久层强制 owner 私有 + 租户过滤。
红线：
- 请求体 schema extra=forbid：起草正文 / 裁判正文 / PII / 胜负结论型键在 pydantic 层即 422
  （第一道闸）；service 层经 sanitize_draft_descriptor fail-closed（第二道闸）。
- service 绝不生成任何段落正文 / 结论 / 胜负判断；持久层只存元数据/引用/短字段。
- 日志只写 user_id_hash / draft_id_hash / 计数 / note 元信息(长度+hash) / reason_code；
  绝不写起草正文 / 裁判正文 / 原始案情 / note 全文。
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request, status

from app.api.errors import api_error_response
from app.core.config import settings
from app.core.db import engine
from app.core.logging import logger
from app.kernel.guardrails import ContractViolationError
from app.kernel.identity import AuthResult, TenantContext
from app.schemas import ErrorResponse
from app.drafting.models import hash_draft_id, hash_user_id_for_log, note_log_meta
from app.drafting.schemas import (
    DraftCreateRequest,
    DraftDescriptorView,
    DraftListResponse,
    DraftUpdateRequest,
)
from app.drafting.service import DraftingService
from app.drafting.store import DraftStore

router = APIRouter(prefix="/api/drafting", tags=["drafting"])

DRAFTING_DISABLED_CODE = "DRAFTING_DISABLED"
DRAFTING_REJECTED_CODE = "DRAFT_REJECTED"
DRAFTING_REQUIRES_LOGIN_CODE = "DRAFTING_REQUIRES_LOGIN"
DRAFT_NOT_FOUND_CODE = "DRAFT_NOT_FOUND"

# 模块级服务实例（懒构造，供测试 set_drafting_service_for_test 替换）。
_drafting_service: DraftingService | None = None


def _get_service() -> DraftingService:
    global _drafting_service
    if _drafting_service is None:
        store = DraftStore(engine)
        store.init_schema()
        _drafting_service = DraftingService(store=store)
    return _drafting_service


def set_drafting_service_for_test(service: DraftingService | None) -> None:
    """测试注入钩子：替换 / 复位模块级 drafting 服务实例。"""
    global _drafting_service
    _drafting_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_DRAFTING", False))


def _query_session_id(request: Request) -> str | None:
    return getattr(request.state, "query_session_id", None)


def _disabled_response(request: Request):
    logger.info(
        "drafting_disabled path=%s reason_code=%s",
        request.url.path,
        "ENABLE_DRAFTING_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=DRAFTING_DISABLED_CODE,
        message="文书工作台未启用（ENABLE_DRAFTING=false），当前为单产品末态。",
        query_session_id=_query_session_id(request),
    )


def _login_required_response(request: Request):
    return api_error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code=DRAFTING_REQUIRES_LOGIN_CODE,
        message="文书工作台操作需先登录。",
        query_session_id=_query_session_id(request),
    )


def _not_found_response(request: Request):
    return api_error_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code=DRAFT_NOT_FOUND_CODE,
        message="草稿不存在或无权访问。",
        query_session_id=_query_session_id(request),
    )


def _rejected_response(request: Request):
    logger.warning(
        "drafting_payload_rejected path=%s reason_code=%s",
        request.url.path,
        DRAFTING_REJECTED_CODE,
    )
    return api_error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=DRAFTING_REJECTED_CODE,
        message="草稿入参不符合契约（字段越界或引用缺少锚点）。",
        query_session_id=_query_session_id(request),
    )


def _resolve_login(authorization: str | None) -> AuthResult:
    from app.api import auth as auth_api

    token = None
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not token:
        return AuthResult(ok=False)
    if not getattr(settings, "ENABLE_ACCOUNT_SYSTEM", False):
        return AuthResult(ok=False)
    try:
        return auth_api._get_service().resolve_session(session_token=token)  # noqa: SLF001
    except Exception:  # noqa: BLE001 - 校验失败一律视为未登录，不泄露细节
        return AuthResult(ok=False)


def _require_login(authorization: str | None) -> AuthResult | None:
    result = _resolve_login(authorization)
    if not result.ok or result.account is None:
        return None
    return result


def _tenant_ctx(login: AuthResult) -> TenantContext:
    """E6-2 默认单用户私有态：team_id=None（团队放权留待后续步骤）。"""
    return TenantContext(owner_user_id=login.account.user_id, team_id=None)


# --- 契约对象 -> 响应视图（只搬运白名单字段与锚点，零正文）-----------------------

def _descriptor_to_view(descriptor) -> DraftDescriptorView:
    return DraftDescriptorView(
        draft_id=descriptor.draft_id,
        structure_skeleton=list(descriptor.structure_skeleton),
        candidate_refs=[
            {
                "case_id": ref.case_id,
                "case_number": ref.case_number,
                "court": ref.court,
                "trial_level": ref.trial_level,
                "case_cause": ref.case_cause,
                "judgment_date": ref.judgment_date,
                "source_anchors": ref.source_anchors,
            }
            for ref in descriptor.candidate_refs
        ],
        statute_refs=[
            {
                "statute_id": ref.statute_id,
                "law_name": ref.law_name,
                "article_no": ref.article_no,
                "statute_anchors": [a.model_dump(exclude_none=True) for a in ref.statute_anchors],
                "article_text": ref.article_text,
                "source_corpus": ref.source_corpus,
                "effective_status": ref.effective_status,
                "related_case_refs": [
                    {
                        "case_id": rc.case_id,
                        "case_number": rc.case_number,
                        "court": rc.court,
                        "trial_level": rc.trial_level,
                        "case_cause": rc.case_cause,
                        "judgment_date": rc.judgment_date,
                        "source_anchors": rc.source_anchors,
                    }
                    for rc in ref.related_case_refs
                ],
            }
            for ref in descriptor.statute_refs
        ],
        note=descriptor.note,
        tag=descriptor.tag,
        owner_user_id=descriptor.owner_user_id or "",
        team_id=descriptor.team_id,
        visibility=descriptor.visibility or "private",
        status="active",
        created_at=str(descriptor.created_at) if descriptor.created_at else None,
        updated_at=str(descriptor.updated_at) if descriptor.updated_at else None,
    )


# --- 端点 ----------------------------------------------------------------------

_RESPONSES = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
}


@router.post("/drafts", response_model=DraftDescriptorView, responses=_RESPONSES)
def create_draft(
    payload: DraftCreateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """创建 DraftDescriptor：组装(不起草) + 持久化(只存元数据/引用/短字段)。"""
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _tenant_ctx(login)
    payload_fields = {
        "structure_skeleton": payload.structure_skeleton,
        "candidate_refs": payload.candidate_refs,
        "statute_refs": payload.statute_refs,
        "note": payload.note,
        "tag": payload.tag,
    }
    try:
        descriptor = _get_service().create_draft(ctx=ctx, payload=payload_fields)
    except ContractViolationError:
        return _rejected_response(request)

    logger.info(
        "drafting_create user_id_hash=%s draft_id_hash=%s skeleton_count=%s "
        "candidate_count=%s statute_count=%s note_meta=%s",
        hash_user_id_for_log(login.account.user_id),
        hash_draft_id(descriptor.draft_id),
        len(descriptor.structure_skeleton),
        len(descriptor.candidate_refs),
        len(descriptor.statute_refs),
        note_log_meta(descriptor.note),
    )
    return _descriptor_to_view(descriptor)


@router.get("/drafts", response_model=DraftListResponse, responses=_RESPONSES)
def list_drafts(request: Request, authorization: str | None = Header(default=None)):
    """列出当前用户/团队可见的 DraftDescriptor（对象级鉴权 + 租户隔离）。"""
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _tenant_ctx(login)
    descriptors = _get_service().list_drafts(ctx=ctx)
    logger.info(
        "drafting_list user_id_hash=%s draft_count=%s",
        hash_user_id_for_log(login.account.user_id),
        len(descriptors),
    )
    views = [_descriptor_to_view(d) for d in descriptors]
    return DraftListResponse(drafts=views, draft_count=len(views))


@router.get("/drafts/{draft_id}", response_model=DraftDescriptorView, responses=_RESPONSES)
def get_draft(
    draft_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """读取单个 DraftDescriptor（越权取不到 -> 404，不泄露他人草稿）。"""
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _tenant_ctx(login)
    descriptor = _get_service().get_draft(ctx=ctx, draft_id=draft_id)
    if descriptor is None:
        return _not_found_response(request)
    return _descriptor_to_view(descriptor)


@router.put("/drafts/{draft_id}", response_model=DraftDescriptorView, responses=_RESPONSES)
def update_draft(
    draft_id: str,
    payload: DraftUpdateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """更新 owner 本人的 DraftDescriptor（仍只存元数据，经 sanitize；非 owner -> 404）。"""
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _tenant_ctx(login)
    payload_fields = {
        "structure_skeleton": payload.structure_skeleton,
        "candidate_refs": payload.candidate_refs,
        "statute_refs": payload.statute_refs,
        "note": payload.note,
        "tag": payload.tag,
    }
    try:
        descriptor = _get_service().update_draft(
            ctx=ctx, draft_id=draft_id, payload=payload_fields
        )
    except ContractViolationError:
        return _rejected_response(request)
    if descriptor is None:
        return _not_found_response(request)

    logger.info(
        "drafting_update user_id_hash=%s draft_id_hash=%s skeleton_count=%s "
        "candidate_count=%s statute_count=%s note_meta=%s",
        hash_user_id_for_log(login.account.user_id),
        hash_draft_id(descriptor.draft_id),
        len(descriptor.structure_skeleton),
        len(descriptor.candidate_refs),
        len(descriptor.statute_refs),
        note_log_meta(descriptor.note),
    )
    return _descriptor_to_view(descriptor)


__all__ = [
    "router",
    "set_drafting_service_for_test",
    "DRAFTING_DISABLED_CODE",
    "DRAFTING_REJECTED_CODE",
    "DRAFTING_REQUIRES_LOGIN_CODE",
    "DRAFT_NOT_FOUND_CODE",
]
