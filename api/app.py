"""UniPulse 后端 HTTP 接口（只读）。

职责边界：
    - 只做「认证 → 调 services/queries → 返回 JSON」，不写 SQL、不写业务规则
    - 所有用户数据接口都必须经过 auth.require_identity，user_id 只从 Token 里取
    - 统一错误格式：{"error": {"code": "...", "message": "...", "details": {...}}}

启动：
    python -m uvicorn api.app:app --reload --port 8000
文档：
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api import auth, compat
from database.connection import DB_PATH, init_db
from services import clock, preview, queries

API_VERSION = "0.3.0"

_TOKEN_IN_QUERY = re.compile(r"(token=)[^&\s\"']+")


class RedactTokenFilter(logging.Filter):
    """把访问日志里的 ?token=xxx 换成 ?token=<redacted>。

    uvicorn 默认会把完整 URL 打进日志，看板链接的 Token 就泄露在日志文件里了。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            fixed = list(args)
            fixed[2] = _TOKEN_IN_QUERY.sub(r"\1<redacted>", fixed[2])
            record.args = tuple(fixed)
        return True


def install_log_redaction() -> None:
    """给 uvicorn 的访问日志挂上脱敏过滤器（重复调用不会重复挂）。"""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactTokenFilter) for f in logger.filters):
        logger.addFilter(RedactTokenFilter())


def _error(
    status_code: int, code: str, message: str, details: dict | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表并检查认证配置；配置不对就不对外提供服务。"""
    init_db()
    auth.ensure_configured()
    install_log_redaction()
    yield


app = FastAPI(
    title="UniPulse API",
    description=(
        "UniPulse 校园智能助手后端（只读接口）。\n\n"
        "所有 `/api/v1/*` 接口都需要访问 Token：\n"
        "- `Authorization: Bearer <token>`\n"
        "- 或看板链接里的 `?token=<token>`\n\n"
        "Token 由成员 B 的 `api/auth.py` 签发，演示环境可用 "
        "`python -m api.auth --user us_demo_a` 生成。"
    ),
    version=API_VERSION,
    lifespan=lifespan,
)


app.include_router(compat.router)


# ---------------------------------------------------------------- 统一错误处理
def _is_compat_path(request: Request) -> bool:
    """C 的 /v1/* 用 {ok:false,error} 信封；原有 /api/v1/* 保持裸错误格式。"""
    return request.url.path.startswith("/v1/")


def _compat_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"code": code, "message": message}},
    )


@app.exception_handler(auth.AuthError)
async def _handle_auth_error(request: Request, exc: auth.AuthError) -> JSONResponse:
    if _is_compat_path(request):
        return _compat_error(401, exc.code, exc.message)
    return _error(401, exc.code, exc.message, {"reason": exc.reason})


_PREVIEW_STATUS = {
    "PREVIEW_NOT_FOUND": 404,
    "USER_NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "PREVIEW_EXPIRED": 409,
    "PREVIEW_NOT_PENDING": 409,
}


@app.exception_handler(preview.PreviewError)
async def _handle_preview_error(
    request: Request, exc: preview.PreviewError
) -> JSONResponse:
    status_code = _PREVIEW_STATUS.get(exc.code, 422)
    return _error(status_code, exc.code, exc.message)


@app.exception_handler(queries.UserNotFoundError)
async def _handle_user_not_found(
    request: Request, exc: queries.UserNotFoundError
) -> JSONResponse:
    if _is_compat_path(request):
        return _compat_error(404, "USER_NOT_FOUND", "用户不存在或已失效")
    return _error(404, "NOT_FOUND", "用户不存在或已失效", {"reason": "USER_NOT_FOUND"})


@app.exception_handler(queries.QueryError)
async def _handle_query_error(
    request: Request, exc: queries.QueryError
) -> JSONResponse:
    if _is_compat_path(request):
        return _compat_error(422, "VALIDATION_ERROR", str(exc))
    return _error(422, "VALIDATION_ERROR", str(exc))


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 只返回字段位置和原因，不回显用户输入，避免把 Token 之类写进响应
    fields = [
        {"field": ".".join(str(p) for p in err.get("loc", [])), "reason": err.get("msg", "")}
        for err in exc.errors()
    ]
    if _is_compat_path(request):
        return _compat_error(422, "VALIDATION_ERROR", "请求参数不合法")
    return _error(422, "VALIDATION_ERROR", "请求参数不合法", {"fields": fields})


# ---------------------------------------------------------------- 系统
@app.get("/health", tags=["系统"], summary="健康检查")
def health() -> dict:
    """返回服务状态。不需要认证。"""
    return {
        "status": "ok",
        "version": API_VERSION,
        "database": str(DB_PATH),
        "database_exists": DB_PATH.exists(),
        "auth_mode": auth.get_auth_mode(),
        "time_mode": clock.get_mode(),
    }


@app.get("/", tags=["系统"], summary="根路径")
def root() -> dict:
    return {"name": "UniPulse API", "docs": "/docs", "health": "/health"}


# ---------------------------------------------------------------- 业务（只读）
@app.get("/api/v1/dashboard", tags=["看板"], summary="个人看板数据")
def dashboard(identity: auth.Identity = Depends(auth.require_identity)) -> dict:
    """C 的看板一次拿全：7 天时间轴、下一节课、最近截止、待确认提示。

    数据只属于 Token 对应的用户；改 URL 参数不会看到别人的数据。
    """
    return queries.get_dashboard(identity.user_id)


@app.get("/api/v1/courses", tags=["看板"], summary="课表")
def courses(
    date_from: str | None = Query(default=None, description="起始日期 YYYY-MM-DD"),
    date_to: str | None = Query(default=None, description="结束日期 YYYY-MM-DD"),
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """日期范围内的课表，默认今天起 7 天。"""
    return queries.get_courses(identity.user_id, date_from, date_to)


@app.get("/api/v1/exams", tags=["看板"], summary="考试列表")
def exams(identity: auth.Identity = Depends(auth.require_identity)) -> dict:
    """今天及以后的考试，按时间正序。"""
    return queries.get_exams(identity.user_id)


@app.get("/api/v1/tasks", tags=["看板"], summary="待办列表")
def tasks(
    status: str | None = Query(
        default=None, description="筛选状态：todo / doing / done，省略返回全部"
    ),
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """待办任务。未完成在前，没有截止时间的排最后。"""
    return queries.get_tasks(identity.user_id, status)


@app.get("/api/v1/plans/current", tags=["看板"], summary="当前正式计划")
def current_plan(identity: auth.Identity = Depends(auth.require_identity)) -> dict:
    """当前已确认的计划及其时间块；没有正式计划时 plan 为 null。"""
    return queries.get_current_plan(identity.user_id)


# ---------------------------------------------------------------- 预览与确认
class ConfirmRequest(BaseModel):
    """确认请求体。confirm_code 是预览里显示的编号，不能省略。"""

    confirm_code: str | None = Field(
        default=None, description="预览里显示的确认码，例如 T03"
    )
    expected_version: int | None = Field(
        default=None, description="调用方看到的正式计划版本，用于冲突检测"
    )


@app.get("/api/v1/previews/{preview_id}", tags=["预览"], summary="预览详情")
def get_preview_detail(
    preview_id: str,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """返回当前用户自己的预览详情（含新旧计划差异与缺口）。

    预览不存在或不属于当前用户时返回 404。
    """
    return preview.get_preview(identity.user_id, preview_id)


@app.post("/api/v1/previews/{preview_id}/confirm", tags=["预览"], summary="确认并提交预览")
def confirm_preview_route(
    preview_id: str,
    payload: ConfirmRequest | None = None,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """把预览提交为新的正式计划（单事务，幂等）。

    - 409 `VERSION_CONFLICT`：正式计划已变化，需要重新生成预览
    - 409 `PREVIEW_EXPIRED` / `PREVIEW_NOT_PENDING`：过期或已取消
    - 422 `CONFIRM_CODE_MISMATCH` / `MISSING_CONFIRM_CODE`：确认码不对或没给
    """
    return preview.confirm_preview(
        identity.user_id,
        preview_id,
        confirm_code=payload.confirm_code if payload else None,
        expected_version=payload.expected_version if payload else None,
    )


@app.post("/api/v1/previews/{preview_id}/cancel", tags=["预览"], summary="取消预览")
def cancel_preview_route(
    preview_id: str,
    identity: auth.Identity = Depends(auth.require_identity),
) -> dict:
    """取消一条待确认预览。只改预览状态，不动正式计划。"""
    return preview.cancel_preview(identity.user_id, preview_id)
