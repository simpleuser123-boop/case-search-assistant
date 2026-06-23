"""E7-2 casebook 案件协作工作台端点（flag-gated，归集而非起草 + 持久化元数据/引用/短字段）。

端点（均 prefix=/api/casebook，均需登录）：
- POST   /folders                   创建 CaseFolder（默认 visibility=private，出已收敛对象，零正文）。
- GET    /folders                   列出当前用户/团队可见的 CaseFolder（对象级鉴权 + 租户隔离 + visibility 过滤）。
- GET    /folders/{case_folder_id}  读取单个（越权取不到 -> 404）。
- PUT    /folders/{case_folder_id}  更新归集引用/短字段（仍只存元数据，经 sanitize；非 owner -> 404）。

门控：ENABLE_CASEBOOK=false（默认）-> 403 CASEBOOK_DISABLED（安全降级，与 intake /
statute / drafting 关闭语义一致）；ENABLE_CASEBOOK=true 才走持久化 / 读取。前端入口本步不接（E7-3）。

鉴权 / 租户隔离：所有端点需登录（复用 M5-2 会话）；持久层强制 owner 私有 + 租户过滤。
红线：
- 请求体 schema extra=forbid：裁判正文 / 起草正文 / 原始案情 / PII / 胜负结论型键在 pydantic
  层即 422（第一道闸）；service 层经 sanitize_case_folder fail-closed（第二道闸）。
- service 绝不生成任何案件综述正文 / 结论 / 胜负判断；持久层只存元数据/引用/短字段。
- 日志只写 user_id_hash / case_folder_id_hash / 计数 / note 元信息(长度+hash) / reason_code；
  绝不写裁判正文 / 起草正文 / 原始案情 / note 全文。
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
from app.casebook.models import (
    hash_case_folder_id,
    hash_user_id_for_log,
    note_log_meta,
)
from app.casebook.schemas import (
    CaseFolderCreateRequest,
    CaseFolderListResponse,
    CaseFolderShareRequest,
    CaseFolderUpdateRequest,
    CaseFolderView,
)
from app.casebook.service import CasebookService
from app.casebook.store import CaseFolderStore

router = APIRouter(prefix="/api/casebook", tags=["casebook"])

CASEBOOK_DISABLED_CODE = "CASEBOOK_DISABLED"
CASEBOOK_REJECTED_CODE = "CASE_FOLDER_REJECTED"
CASEBOOK_REQUIRES_LOGIN_CODE = "CASEBOOK_REQUIRES_LOGIN"
CASE_FOLDER_NOT_FOUND_CODE = "CASE_FOLDER_NOT_FOUND"

# 模块级服务实例（懒构造，供测试 set_casebook_service_for_test 替换）。
_casebook_service: CasebookService | None = None


def _get_service() -> CasebookService:
    global _casebook_service
    if _casebook_service is None:
        store = CaseFolderStore(engine)
        store.init_schema()
        _casebook_service = CasebookService(store=store)
    return _casebook_service


def set_casebook_service_for_test(service: CasebookService | None) -> None:
    """测试注入钩子：替换 / 复位模块级 casebook 服务实例。"""
    global _casebook_service
    _casebook_service = service


def _enabled() -> bool:
    return bool(getattr(settings, "ENABLE_CASEBOOK", False))


def _query_session_id(request: Request) -> str | None:
    return getattr(request.state, "query_session_id", None)


def _disabled_response(request: Request):
    logger.info(
        "casebook_disabled path=%s reason_code=%s",
        request.url.path,
        "ENABLE_CASEBOOK_false",
    )
    return api_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code=CASEBOOK_DISABLED_CODE,
        message="案件协作工作台未启用（ENABLE_CASEBOOK=false），当前为单产品末态。",
        query_session_id=_query_session_id(request),
    )


def _login_required_response(request: Request):
    return api_error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code=CASEBOOK_REQUIRES_LOGIN_CODE,
        message="案件协作工作台操作需先登录。",
        query_session_id=_query_session_id(request),
    )


def _not_found_response(request: Request):
    return api_error_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code=CASE_FOLDER_NOT_FOUND_CODE,
        message="协作夹不存在或无权访问。",
        query_session_id=_query_session_id(request),
    )


def _rejected_response(request: Request):
    logger.warning(
        "casebook_payload_rejected path=%s reason_code=%s",
        request.url.path,
        CASEBOOK_REJECTED_CODE,
    )
    return api_error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code=CASEBOOK_REJECTED_CODE,
        message="协作夹入参不符合契约（裁判 / 起草正文 / 原始案情 / PII / 胜负结论零承载，引用须带锚点）。",
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
    """单用户私有态：team_id=None（创建恒私有；共享走 /share 显式动作）。"""
    return TenantContext(owner_user_id=login.account.user_id, team_id=None)


def _resolve_team_service():
    """复用 M5 团队服务（同一实例 + 同一成员关系账本，不另起一套权限模型）。

    懒导入 app.api.team（非内核私有、非其它产品包；与 _resolve_login 懒导入 auth 同范式），
    取其模块级 TeamService 单例；测试经 app.api.team.set_team_service_for_test 注入临时引擎实例。
    """
    from app.api import team as team_api

    return team_api._get_service()  # noqa: SLF001


def _resolve_read_ctx(login: AuthResult, team_id: str | None) -> TenantContext:
    """读取态租户上下文：带 X-Team-Id 时经 M5 成员关系校验后进入团队态，否则单用户私有。

    复用 M5 TeamService.resolve_tenant：非活跃成员/团队不存在一律降级为单用户私有态
    （绝不越权串读他团队 team 行）；不绕过 _tenant_clause。
    """
    cleaned = (team_id or "").strip() or None
    if cleaned is None:
        return TenantContext(owner_user_id=login.account.user_id, team_id=None)
    resolution = _resolve_team_service().resolve_tenant(
        owner_user_id=login.account.user_id, team_id=cleaned
    )
    return resolution.ctx


# --- 契约对象 -> 响应视图（只搬运白名单字段与锚点，零正文）-----------------------

def _candidate_ref_view_dict(ref) -> dict:
    return {
        "case_id": ref.case_id,
        "case_number": ref.case_number,
        "court": ref.court,
        "trial_level": ref.trial_level,
        "case_cause": ref.case_cause,
        "judgment_date": ref.judgment_date,
        "source_anchors": [
            a if isinstance(a, dict) else a for a in ref.source_anchors
        ],
    }


def _draft_descriptor_view_dict(d) -> dict:
    return {
        "draft_id": getattr(d, "draft_id", None),
        "structure_skeleton": list(getattr(d, "structure_skeleton", []) or []),
        "candidate_refs": [
            _candidate_ref_view_dict(rc) for rc in getattr(d, "candidate_refs", []) or []
        ],
        "statute_refs": [
            {
                "statute_id": s.statute_id,
                "law_name": s.law_name,
                "article_no": s.article_no,
                "statute_anchors": [
                    a.model_dump(exclude_none=True) for a in s.statute_anchors
                ],
                "article_text": s.article_text,
                "source_corpus": s.source_corpus,
                "effective_status": s.effective_status,
                "related_case_refs": [
                    _candidate_ref_view_dict(rc) for rc in s.related_case_refs
                ],
            }
            for s in getattr(d, "statute_refs", []) or []
        ],
        "note": getattr(d, "note", None),
        "tag": getattr(d, "tag", None),
    }


def _folder_to_view(folder) -> CaseFolderView:
    return CaseFolderView(
        case_folder_id=folder.case_folder_id,
        owner_user_id=folder.owner_user_id or "",
        team_id=folder.team_id,
        visibility=folder.visibility or "private",
        search_profile_summary=folder.search_profile_summary,
        candidate_refs=[_candidate_ref_view_dict(ref) for ref in folder.candidate_refs],
        draft_descriptors=[
            _draft_descriptor_view_dict(d) for d in folder.draft_descriptors
        ],
        title=folder.title,
        note=folder.note,
        tag=folder.tag,
        status="active",
        created_at=str(folder.created_at) if folder.created_at else None,
        updated_at=str(folder.updated_at) if folder.updated_at else None,
    )


# --- 端点 ----------------------------------------------------------------------

_RESPONSES = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
}


def _create_payload_fields(payload: CaseFolderCreateRequest) -> dict:
    return {
        "search_profile_summary": payload.search_profile_summary,
        "candidate_refs": payload.candidate_refs,
        "draft_descriptors": payload.draft_descriptors,
        "title": payload.title,
        "note": payload.note,
        "tag": payload.tag,
    }


@router.post("/folders", response_model=CaseFolderView, responses=_RESPONSES)
def create_case_folder(
    payload: CaseFolderCreateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """创建 CaseFolder：归集(不起草) + 持久化(只存元数据/引用/短字段)。"""
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _tenant_ctx(login)
    try:
        folder = _get_service().create_case_folder(
            ctx=ctx, payload=_create_payload_fields(payload)
        )
    except ContractViolationError:
        return _rejected_response(request)

    logger.info(
        "casebook_create user_id_hash=%s case_folder_id_hash=%s candidate_count=%s "
        "draft_count=%s has_summary=%s note_meta=%s",
        hash_user_id_for_log(login.account.user_id),
        hash_case_folder_id(folder.case_folder_id),
        len(folder.candidate_refs),
        len(folder.draft_descriptors),
        folder.search_profile_summary is not None,
        note_log_meta(folder.note),
    )
    return _folder_to_view(folder)


@router.get("/folders", response_model=CaseFolderListResponse, responses=_RESPONSES)
def list_case_folders(
    request: Request,
    authorization: str | None = Header(default=None),
    x_team_id: str | None = Header(default=None),
):
    """列出当前用户/团队可见的 CaseFolder（对象级鉴权 + 租户隔离 + visibility 过滤）。

    带 X-Team-Id 时进入团队态（经 M5 成员关系校验）：可见 = 自己的私有行 + 本团队
    visibility=team 的共享行；非活跃成员降级为单用户私有态（绝不串读他团队）。
    """
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _resolve_read_ctx(login, x_team_id)
    folders = _get_service().list_case_folders(ctx=ctx)
    logger.info(
        "casebook_list user_id_hash=%s folder_count=%s",
        hash_user_id_for_log(login.account.user_id),
        len(folders),
    )
    views = [_folder_to_view(f) for f in folders]
    return CaseFolderListResponse(folders=views, folder_count=len(views))


@router.get(
    "/folders/{case_folder_id}", response_model=CaseFolderView, responses=_RESPONSES
)
def get_case_folder(
    case_folder_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_team_id: str | None = Header(default=None),
):
    """读取单个 CaseFolder（越权取不到 -> 404，不泄露他人协作夹）。

    带 X-Team-Id 时进入团队态（经 M5 成员关系校验）：同 team 成员可读 visibility=team 行；
    非成员 / 私有他人行 / 跨租户一律取不到 -> 404（不泄露存在性）。
    """
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _resolve_read_ctx(login, x_team_id)
    folder = _get_service().get_case_folder(ctx=ctx, case_folder_id=case_folder_id)
    if folder is None:
        return _not_found_response(request)
    return _folder_to_view(folder)


@router.put(
    "/folders/{case_folder_id}", response_model=CaseFolderView, responses=_RESPONSES
)
def update_case_folder(
    case_folder_id: str,
    payload: CaseFolderUpdateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """更新 owner 本人的 CaseFolder（仍只存元数据，经 sanitize；非 owner -> 404）。"""
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    ctx = _tenant_ctx(login)
    payload_fields = {
        "search_profile_summary": payload.search_profile_summary,
        "candidate_refs": payload.candidate_refs,
        "draft_descriptors": payload.draft_descriptors,
        "title": payload.title,
        "note": payload.note,
        "tag": payload.tag,
    }
    if payload.visibility is not None:
        payload_fields["visibility"] = payload.visibility
    try:
        folder = _get_service().update_case_folder(
            ctx=ctx, case_folder_id=case_folder_id, payload=payload_fields
        )
    except ContractViolationError:
        return _rejected_response(request)
    except ValueError:
        # 租户一致性失败（如单用户态请求 visibility=team）：归一为入参不合契约 400，
        # 不泄露内部细节。共享切换的正路径走 POST /folders/{id}/share。
        return _rejected_response(request)
    if folder is None:
        return _not_found_response(request)

    logger.info(
        "casebook_update user_id_hash=%s case_folder_id_hash=%s candidate_count=%s "
        "draft_count=%s has_summary=%s note_meta=%s",
        hash_user_id_for_log(login.account.user_id),
        hash_case_folder_id(folder.case_folder_id),
        len(folder.candidate_refs),
        len(folder.draft_descriptors),
        folder.search_profile_summary is not None,
        note_log_meta(folder.note),
    )
    return _folder_to_view(folder)


@router.post(
    "/folders/{case_folder_id}/share",
    response_model=CaseFolderView,
    responses=_RESPONSES,
)
def share_case_folder(
    case_folder_id: str,
    payload: CaseFolderShareRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """E7-4 共享切换：把 owner 本人 CaseFolder 的可见性在 private<->team 间显式切换。

    复用 M5 多租户/对象级鉴权（不另起权限模型）：
    - 仅 owner 可改（非 owner / 不存在 -> 404，不泄露他人协作夹存在性）。
    - 共享到 team：visibility=team 须给 team_id，且 owner 须为该 team 活跃成员
      （经 M5 TeamService 成员关系校验）；非成员 -> 404（不泄露 team / folder 存在性）。
    - 取消共享：visibility=private 时 team_id 一并清空（回 owner 私有）。
    - visibility 只 private|team（public 已在 schema Literal 层 422）。
    - 只改可见性元数据，零正文、引用仍只带锚点（出库经 sanitize 双保险）。
    """
    if not _enabled():
        return _disabled_response(request)
    login = _require_login(authorization)
    if login is None:
        return _login_required_response(request)

    target_team_id = (payload.team_id or "").strip() or None
    if payload.visibility == "team":
        # 共享到 team：必须给 team_id 且 owner 是该 team 活跃成员（M5 成员关系校验）。
        if target_team_id is None:
            return _rejected_response(request)
        resolution = _resolve_team_service().resolve_tenant(
            owner_user_id=login.account.user_id, team_id=target_team_id
        )
        if resolution.downgraded or resolution.ctx.team_id != target_team_id:
            # 非活跃成员 / 团队不存在：拒绝并 404（不泄露 team / folder 存在性）。
            return _not_found_response(request)
        ctx = resolution.ctx
    else:
        # 取消共享：回单用户私有态；team_id 由 store 一并清空。
        target_team_id = None
        ctx = TenantContext(owner_user_id=login.account.user_id, team_id=None)

    try:
        folder = _get_service().share_case_folder(
            ctx=ctx,
            case_folder_id=case_folder_id,
            visibility=payload.visibility,
            team_id=target_team_id,
        )
    except (ContractViolationError, ValueError):
        return _rejected_response(request)
    if folder is None:
        return _not_found_response(request)

    logger.info(
        "casebook_share user_id_hash=%s case_folder_id_hash=%s visibility=%s has_team=%s",
        hash_user_id_for_log(login.account.user_id),
        hash_case_folder_id(folder.case_folder_id),
        folder.visibility,
        folder.team_id is not None,
    )
    return _folder_to_view(folder)


__all__ = [
    "router",
    "set_casebook_service_for_test",
    "CASEBOOK_DISABLED_CODE",
    "CASEBOOK_REJECTED_CODE",
    "CASEBOOK_REQUIRES_LOGIN_CODE",
    "CASE_FOLDER_NOT_FOUND_CODE",
]
