"""访问 Token 的签发与校验（身份认证）。

设计要点：
    1. Token = base64url(payload) + "." + base64url(HMAC-SHA256(payload, secret))
    2. payload 里只有 user_id、签发时间 iat、过期时间 exp、模式 mode、签发者 iss
       绝不包含姓名、企微 ID 等个人信息
    3. 两种模式：
         demo       —— 本地开发/演示，用固定的演示密钥，方便直接发 Token
         production —— 正式，必须由 UNIPULSE_TOKEN_SECRET 提供 ≥32 位密钥，
                       缺密钥时服务启动即失败，不会退化成"谁都能进"
    4. Token 里带 mode，校验时要求与当前服务模式一致：
       演示模式签的 Token 不能用于正式模式（反之亦然）
    5. 校验失败一律抛 AuthError，由 API 层统一转成 401

时间说明：
    iat / exp 用真实 UTC 时间（time.time()），不受 services.clock 的演示时间影响。
    演示时间只影响业务数据（"今天"是哪天），不应该影响"凭据还能用多久"。

命令行（开发/演示用，不要在生产服务器上跑）：
    python -m api.auth --status
    python -m api.auth --user us_demo_a
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import Header, Query

ENV_AUTH_MODE = "UNIPULSE_AUTH_MODE"
ENV_TOKEN_SECRET = "UNIPULSE_TOKEN_SECRET"
ENV_TOKEN_TTL_MINUTES = "UNIPULSE_DASHBOARD_TOKEN_TTL_MINUTES"

MODE_DEMO = "demo"
MODE_PRODUCTION = "production"
VALID_MODES = (MODE_DEMO, MODE_PRODUCTION)

ISSUER = "unipulse"
DEFAULT_TTL_MINUTES = 120
PRODUCTION_SECRET_MIN_LENGTH = 32
CLOCK_SKEW_SECONDS = 300

# 仅演示模式使用。正式模式如果用它，启动直接报错（见 get_secret）。
_DEMO_SECRET = "unipulse-demo-only-not-for-production-8f3c1d"


class AuthError(Exception):
    """认证失败。API 层统一返回 401。"""

    def __init__(self, message: str, reason: str = "UNAUTHORIZED"):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.code = "UNAUTHORIZED"


class AuthConfigError(RuntimeError):
    """认证配置有问题（模式非法、正式模式缺密钥等）。启动阶段就该失败。"""


@dataclass(frozen=True)
class Identity:
    """校验通过后的身份。业务代码只能通过它拿 user_id。"""

    user_id: str
    mode: str
    issued_at: int
    expires_at: int


# ---------------------------------------------------------------- 配置
def get_auth_mode() -> str:
    raw = (os.environ.get(ENV_AUTH_MODE) or "").strip().lower()
    if not raw:
        return MODE_DEMO
    if raw not in VALID_MODES:
        raise AuthConfigError(
            f"{ENV_AUTH_MODE}={raw!r} 无效，只能是 {' 或 '.join(VALID_MODES)}"
        )
    return raw


def get_secret(mode: str | None = None) -> str:
    """返回当前模式使用的签名密钥。正式模式缺密钥会抛 AuthConfigError。"""
    mode = mode or get_auth_mode()

    if mode == MODE_DEMO:
        return _DEMO_SECRET

    secret = (os.environ.get(ENV_TOKEN_SECRET) or "").strip()
    if not secret:
        raise AuthConfigError(
            f"正式模式必须设置 {ENV_TOKEN_SECRET}（至少 {PRODUCTION_SECRET_MIN_LENGTH} 位随机字符）。"
            f"生成方式：python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    if secret == _DEMO_SECRET:
        raise AuthConfigError(
            f"{ENV_TOKEN_SECRET} 不能使用演示密钥。正式环境请换成随机生成的长密钥。"
        )
    if len(secret) < PRODUCTION_SECRET_MIN_LENGTH:
        raise AuthConfigError(
            f"{ENV_TOKEN_SECRET} 太短（{len(secret)} 位），至少需要 {PRODUCTION_SECRET_MIN_LENGTH} 位。"
        )
    return secret


def get_token_ttl_minutes() -> int:
    raw = (os.environ.get(ENV_TOKEN_TTL_MINUTES) or "").strip()
    if not raw:
        return DEFAULT_TTL_MINUTES
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise AuthConfigError(f"{ENV_TOKEN_TTL_MINUTES}={raw!r} 必须是整数") from exc
    if ttl <= 0:
        raise AuthConfigError(f"{ENV_TOKEN_TTL_MINUTES} 必须大于 0")
    return ttl


def ensure_configured() -> None:
    """服务启动时调用：配置不对就立刻失败，不要带着错误配置对外提供服务。"""
    mode = get_auth_mode()
    get_secret(mode)
    get_token_ttl_minutes()


def status() -> dict:
    """给 /health 和命令行看的认证状态（不含密钥）。"""
    mode = get_auth_mode()
    configured = True
    problem = None
    try:
        get_secret(mode)
    except AuthConfigError as exc:
        configured = False
        problem = str(exc)
    return {
        "mode": mode,
        "secret_configured": configured,
        "problem": problem,
        "ttl_minutes": get_token_ttl_minutes(),
    }


# ---------------------------------------------------------------- 编码工具
def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload_b64: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64encode(digest)


# ---------------------------------------------------------------- 签发
def create_token(
    user_id: str,
    ttl_minutes: int | None = None,
    *,
    mode: str | None = None,
    now: float | None = None,
) -> str:
    """给指定用户签发一个访问 Token。"""
    if not user_id or not isinstance(user_id, str):
        raise ValueError("user_id 不能为空")

    mode = mode or get_auth_mode()
    secret = get_secret(mode)
    ttl = ttl_minutes if ttl_minutes is not None else get_token_ttl_minutes()

    issued = int(now if now is not None else time.time())
    payload = {
        "uid": user_id,
        "iat": issued,
        "exp": issued + ttl * 60,
        "mode": mode,
        "iss": ISSUER,
        "jti": secrets.token_hex(8),
    }
    payload_b64 = _b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


# ---------------------------------------------------------------- 校验
def decode_token(
    token: str,
    *,
    mode: str | None = None,
    now: float | None = None,
) -> Identity:
    """校验并解析 Token。任何不合法都会抛 AuthError。"""
    if not token or not isinstance(token, str):
        raise AuthError("缺少访问凭据", "MISSING_TOKEN")

    mode = mode or get_auth_mode()
    secret = get_secret(mode)
    current = time.time() if now is None else now

    parts = token.split(".")
    if len(parts) != 2:
        raise AuthError("凭据格式不正确", "MALFORMED_TOKEN")

    payload_b64, signature = parts
    if not hmac.compare_digest(_sign(payload_b64, secret), signature):
        raise AuthError("凭据无效", "BAD_SIGNATURE")

    try:
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("凭据格式不正确", "MALFORMED_TOKEN") from exc

    if not isinstance(payload, dict):
        raise AuthError("凭据格式不正确", "MALFORMED_TOKEN")

    if payload.get("iss") != ISSUER:
        raise AuthError("凭据来源不可信", "BAD_ISSUER")

    if payload.get("mode") != mode:
        raise AuthError(
            "凭据与当前运行模式不匹配（演示凭据不能用于正式环境）", "WRONG_MODE"
        )

    user_id = payload.get("uid")
    issued = payload.get("iat")
    expires = payload.get("exp")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("凭据内容不完整", "MALFORMED_TOKEN")
    if not isinstance(issued, int) or not isinstance(expires, int):
        raise AuthError("凭据内容不完整", "MALFORMED_TOKEN")

    if expires <= current:
        raise AuthError("凭据已过期", "TOKEN_EXPIRED")
    if issued > current + CLOCK_SKEW_SECONDS:
        raise AuthError("凭据时间异常", "NOT_YET_VALID")

    return Identity(user_id=user_id, mode=mode, issued_at=issued, expires_at=expires)


# ---------------------------------------------------------------- FastAPI 依赖
def _extract_token(authorization: str | None, query_token: str | None) -> str:
    """优先 Authorization: Bearer，其次 ?token=（给看板链接用）。

    两种方式拿到的都是签名 Token，不是明文 user_id。
    """
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
        raise AuthError("Authorization 头格式应为 'Bearer <token>'", "MALFORMED_TOKEN")
    if query_token:
        return query_token
    raise AuthError("缺少访问凭据，请从企微重新获取计划表链接", "MISSING_TOKEN")


def require_identity(
    authorization: str | None = Header(default=None, description="Bearer <token>"),
    token: str | None = Query(
        default=None, description="看板链接里的 token（与 Authorization 二选一）"
    ),
) -> Identity:
    """FastAPI 依赖：校验通过后把 Identity 注入路由函数。"""
    raw = _extract_token(authorization, token)
    return decode_token(raw)


# ---------------------------------------------------------------- 命令行
def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m api.auth", description="签发演示 Token / 查看认证配置"
    )
    parser.add_argument("--status", action="store_true", help="显示当前认证配置")
    parser.add_argument("--user", help="要签发 Token 的用户 ID，如 us_demo_a")
    parser.add_argument("--ttl", type=int, default=None, help="有效期（分钟）")
    args = parser.parse_args(argv)

    if args.status or not args.user:
        info = status()
        print(f"认证模式：{info['mode']}")
        print(f"密钥配置：{'已配置' if info['secret_configured'] else '未配置'}")
        if info["problem"]:
            print(f"问题：    {info['problem']}")
        print(f"有效期：  {info['ttl_minutes']} 分钟")
        if not args.user:
            return 0

    try:
        token = create_token(args.user, ttl_minutes=args.ttl)
    except AuthConfigError as exc:
        print(f"❌ {exc}")
        return 1

    print(f"用户：{args.user}")
    print(f"Token：{token}")
    print()
    print("用法一（命令行）：")
    print(f"  curl -H 'Authorization: Bearer {token}' http://127.0.0.1:8000/api/v1/dashboard")
    print("用法二（看板链接）：")
    print(f"  http://127.0.0.1:8000/api/v1/dashboard?token={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
