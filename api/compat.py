"""给成员 C 网页用的 /v1 兼容接口。

为什么单独一层：
    C 的页面已经按 `GET /v1/workspace` + `{ok: true, data: ...}` 信封写好，
    而 B 已有的 `/api/v1/*` 是裸对象。为了不破坏已有 API，也不让 C 重写页面，
    这里加一层**薄适配**：只做参数转换和字段映射，业务全部调用 services。

约定：
    成功  {"ok": true,  "data": {...}}
    失败  {"ok": false, "error": {"code": "...", "message": "..."}}
    认证沿用 Bearer token / ?token=，user_id 只从认证依赖取。

映射关系：
    C 的 event  = B 的 exam（语义一致：都是一场考试事件）
    C 的 action = B 的 preview（同一张 previews 表）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api import auth
from services import preview, scheduler, workspace
from services.preview import PreviewError

router = APIRouter(prefix="/v1", tags=["C 兼容接口"])


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _fail(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"code": code, "message": message}},
    )


_STATUS_BY_CODE = {
    "PREVIEW_NOT_FOUND": 404,
    "USER_NOT_FOUND": 404,
    "EXAM_NOT_FOUND": 404,
    "GOAL_NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "EXAM_VERSION_CONFLICT": 409,
    "PREVIEW_EXPIRED": 409,
    "PREVIEW_NOT_PENDING": 409,
    "EXAM_TIME_UNCHANGED": 409,
    "EXAM_IN_PAST": 409,
}


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, PreviewError):
        return _fail(_STATUS_BY_CODE.get(exc.code, 422), exc.code, exc.message)
    if isinstance(exc, scheduler.SchedulerError):
        return _fail(_STATUS_BY_CODE.get(exc.code, 422), exc.code, exc.message)
    raise exc


class PreviewCreateRequest(BaseModel):
    """C 的改期预览请求体。"""

    event_id: str = Field(description="考试事件 ID（等于 B 的 exam_id）")
    new_start: str = Field(description="拟议的新考试开始时间（ISO 8601，带时区）")
    expected_version: int | None = Field(
        default=None, description="调用方看到的正式计划版本"
    )
    source_notice_ids: list[str] = Field(
        default_factory=list, description="关联的通知 ID，取第一个作为来源"
    )


class ConfirmRequest(BaseModel):
    confirm_code: str | None = Field(default=None, description="确认码，如 T03")
    expected_version: int | None = Field(default=None, description="正式计划版本")


@router.get("/workspace", summary="工作台数据（C 的页面主接口）")
def get_workspace(identity: auth.Identity = Depends(auth.require_identity)) -> dict:
    """一次返回 C 页面需要的全部数据。只读。"""
    return _ok(workspace.build_workspace(identity.user_id))


@router.post("/previews", summary="按新考试时间生成改期预览")
def create_preview_route(
    payload: PreviewCreateRequest,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """验证考试归属，按拟议的新时间算排程，生成待确认预览（不改正式数据）。"""
    try:
        detail = preview.create_exam_change_preview(
            identity.user_id,
            payload.event_id,
            payload.new_start,
            expected_version=payload.expected_version,
            notice_id=payload.source_notice_ids[0] if payload.source_notice_ids else None,
        )
    except (PreviewError, scheduler.SchedulerError) as exc:
        return _error_response(exc)
    return _ok(workspace.to_client_preview(detail))


@router.get("/previews/{preview_id}", summary="预览详情")
def get_preview_route(
    preview_id: str,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    try:
        detail = preview.get_preview(identity.user_id, preview_id)
    except PreviewError as exc:
        return _error_response(exc)
    return _ok(workspace.to_client_preview(detail))


@router.post("/actions/{preview_id}/confirm", summary="确认并提交预览")
def confirm_action_route(
    preview_id: str,
    payload: ConfirmRequest | None = None,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """确认预览：同一事务内更新考试时间、计划与提醒。

    C 当前的 `confirm_action` 只发空对象；这里在没带 `confirm_code` 时读取
    预览自身的确认码。`/api/v1` 的严格版本仍然强制要求确认码。
    """
    try:
        code = payload.confirm_code if payload else None
        if not code:
            detail = preview.get_preview(identity.user_id, preview_id)
            code = detail.get("confirm_code")
        result = preview.confirm_preview(
            identity.user_id,
            preview_id,
            code,
            expected_version=payload.expected_version if payload else None,
        )
    except (PreviewError, scheduler.SchedulerError) as exc:
        return _error_response(exc)

    message = (
        "此预览已经确认过，没有重复修改。"
        if result.get("duplicate")
        else f"已确认，正式计划更新为第 {result['applied']['plan_version']} 版。"
    )
    return _ok({"message": message, **result})


@router.post("/actions/{preview_id}/cancel", summary="取消预览")
def cancel_action_route(
    preview_id: str,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """取消草稿，不改正式计划。"""
    try:
        detail = preview.cancel_preview(identity.user_id, preview_id)
    except PreviewError as exc:
        return _error_response(exc)
    return _ok(
        {
            "message": "已取消；考试、任务和计划都没有修改。",
            "preview": workspace.to_client_preview(detail),
        }
    )
