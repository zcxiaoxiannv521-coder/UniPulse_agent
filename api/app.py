"""UniPulse 后端最小可运行服务（v0.1 骨架）。

作用：证明环境搭好了，并能对外提供一个健康检查接口。
业务接口等 API 契约确定后再加。

启动（在 unipulse 目录下）：
    python -m uvicorn api.app:app --reload --port 8000
然后浏览器打开：
    http://127.0.0.1:8000/health
    http://127.0.0.1:8000/docs    （自动生成的接口文档，C 可以直接看）
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from database.connection import DB_PATH, init_db

API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务启动时自动建库建表，退出时不做额外处理。"""
    init_db()
    yield


app = FastAPI(
    title="UniPulse API",
    description="UniPulse 校园智能助手后端接口（成员 B 提供，供 A 与 C 调用）",
    version=API_VERSION,
    lifespan=lifespan,
)


@app.get("/health", tags=["系统"], summary="健康检查")
def health() -> dict:
    """返回服务状态，用来确认后端是否正常启动。"""
    return {
        "status": "ok",
        "version": API_VERSION,
        "database": str(DB_PATH),
        "database_exists": DB_PATH.exists(),
    }


@app.get("/", tags=["系统"], summary="根路径")
def root() -> dict:
    return {"name": "UniPulse API", "docs": "/docs", "health": "/health"}
