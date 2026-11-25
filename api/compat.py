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
from services import classes, goals, notices, preview, scheduler, tasks, workspace
from services.notices import NoticeError
from services.classes import ClassError
from services.goals import GoalError
from services.tasks import TaskError
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
    "NOTICE_NOT_FOUND": 404,
    "EMPTY_NOTICE": 422,
    "NOTICE_TOO_LONG": 422,
    "TASK_NOT_FOUND": 404,
    "TASK_ALREADY_DONE": 409,
    "INVALID_STATUS": 422,
    "INVALID_REMAINING": 422,
    "INVALID_ACTUAL": 422,
    "INVALID_FEEDBACK": 422,
    "INVALID_ARGUMENT": 422,
    "INVALID_TITLE": 422,
    "INVALID_TEXT": 422,
    "INVALID_EVENT": 422,
    "INVALID_DAILY_LIMIT": 422,
    "INVALID_COURSE": 422,
    "INVALID_LOCATION": 422,
    "INVALID_TIME": 422,
    "CROSS_DAY_NOT_SUPPORTED": 422,
    "CLASS_TIME_CONFLICT": 409,
}


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, PreviewError):
        return _fail(_STATUS_BY_CODE.get(exc.code, 422), exc.code, exc.message)
    if isinstance(exc, NoticeError):
        return _fail(_STATUS_BY_CODE.get(exc.code, 422), exc.code, exc.message)
    if isinstance(exc, TaskError):
        return _fail(_STATUS_BY_CODE.get(exc.code, 422), exc.code, exc.message)
    if isinstance(exc, GoalError):
        return _fail(_STATUS_BY_CODE.get(exc.code, 422), exc.code, exc.message)
    if isinstance(exc, ClassError):
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


class NoticeCreateRequest(BaseModel):
    """C 的 `save_notice(text, expected_version)` 请求体。"""

    text: str = Field(description="通知原文（1—20000 字）")
    expected_version: int | None = Field(
        default=None,
        description="调用方看到的工作台版本；通知是追加写入，这里只记录不校验",
    )


class GoalSaveRequest(BaseModel):
    """C 的 save_goal 请求体。"""

    payload: dict = Field(description="title / event_id / daily_max_minutes / scope / availability")
    expected_version: int | None = Field(default=None, description="工作台版本，仅记录")


class ClassAddRequest(BaseModel):
    """C 的 add_class 请求体。"""

    payload: dict = Field(description="course / location / start_at / end_at")
    expected_version: int | None = Field(default=None, description="工作台版本，仅记录")


class TaskProgressPayload(BaseModel):
    status: str = Field(description="todo / doing / partial / done")
    remaining_minutes: int = Field(description="用户报告的剩余预估（分钟）")
    actual_minutes: int | None = Field(default=None, description="累计实际用时（分钟）")
    feedback: str | None = Field(default=None, description="进度说明")


class TaskProgressRequest(BaseModel):
    """C 的 record_progress 请求体。"""

    payload: TaskProgressPayload
    expected_version: int | None = Field(default=None, description="工作台版本，仅记录")


class TaskTimeRequest(BaseModel):
    """C 的 record_time 请求体（累计实际用时，覆盖式上报）。"""

    actual_minutes: int = Field(description="累计实际学习分钟数")
    expected_version: int | None = Field(default=None, description="工作台版本，仅记录")


class ConfirmRequest(BaseModel):
    confirm_code: str | None = Field(default=None, description="确认码，如 T03")
    expected_version: int | None = Field(default=None, description="正式计划版本")


@router.get("/workspace", summary="工作台数据（C 的页面主接口）")
def get_workspace(identity: auth.Identity = Depends(auth.require_identity)) -> dict:
    """一次返回 C 页面需要的全部数据。只读。"""
    return _ok(workspace.build_workspace(identity.user_id))


@router.post("/notices", summary="保存通知原文")
def create_notice_route(
    payload: NoticeCreateRequest,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """保存一条通知原文（只入库，不解析、不改考试、不生成预览）。

    成功返回 `{id, text, status, channel, received_at, published_at, message}`，
    之后 `GET /v1/workspace` 的 `notices` 里就能看到它。
    """
    try:
        saved = notices.save_notice(identity.user_id, payload.text)
    except NoticeError as exc:
        return _error_response(exc)
    return _ok(saved)


@router.post("/goals", summary="保存学习目标")
def save_goal_route(
    payload: GoalSaveRequest,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """保存/更新当前用户的学习目标（同一考试已存在则更新并 version+1）。

    只保存目标与约束，不生成或修改学习计划。
    """
    try:
        result = goals.save_goal(identity.user_id, payload.payload)
    except GoalError as exc:
        return _error_response(exc)
    return _ok(result)


@router.post("/classes", summary="录入一节具体日期课程")
def add_class_route(
    payload: ClassAddRequest,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """录入一次具体日期的上课记录（课程档案按名称复用）。"""
    try:
        result = classes.add_class(identity.user_id, payload.payload)
    except ClassError as exc:
        return _error_response(exc)
    return _ok(result)


@router.post("/tasks/{task_id}/progress", summary="记录任务进度")
def record_progress_route(
    task_id: str,
    payload: TaskProgressRequest,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """用户明确报告的进度：状态 + 剩余工作量（可选累计用时与说明）。

    - 只有 `status="done"` 才会把任务标记完成
    - 已完成任务不能被静默改回未完成（409）
    - 不修改正式计划与时间块
    """
    try:
        result = tasks.record_progress(
            identity.user_id,
            task_id,
            payload.payload.model_dump(exclude_none=True),
        )
    except TaskError as exc:
        return _error_response(exc)
    return _ok(result)


@router.post("/tasks/{task_id}/time", summary="记录累计实际用时")
def record_time_route(
    task_id: str,
    payload: TaskTimeRequest,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """只记录累计学习时长，不改剩余工作量、不改状态、不动计划。"""
    try:
        result = tasks.record_time(
            identity.user_id, task_id, payload.actual_minutes
        )
    except TaskError as exc:
        return _error_response(exc)
    return _ok(result)


@router.post("/previews", summary="按新考试时间生成改期预览")
def create_preview_route(
    payload: PreviewCreateRequest,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """验证考试归属，按拟议的新时间算排程，生成待确认预览（不改正式数据）。"""
    try:
        if payload.source_notice_ids:
            notices.assert_notices_owned(identity.user_id, payload.source_notice_ids)
        detail = preview.create_exam_change_preview(
            identity.user_id,
            payload.event_id,
            payload.new_start,
            expected_version=payload.expected_version,
            notice_id=payload.source_notice_ids[0] if payload.source_notice_ids else None,
        )
    except (PreviewError, NoticeError, scheduler.SchedulerError) as exc:
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
