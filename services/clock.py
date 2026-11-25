"""统一时间入口（UniPulse 所有业务时间的唯一来源）。

为什么需要它：
    演示场景要求“今天”固定在 2026-11-30 上午。如果代码里直接写
    datetime.now()，到了 12 月 6 日答辩时“今天”就变了，缺口算出来全错。
    所以业务代码一律调用本模块，由环境变量决定用真实时间还是固定时间。

两种模式（由 UNIPULSE_TIME_MODE 控制，默认 real）：
    real —— 返回真实当前时间
    demo —— 返回 UNIPULSE_DEMO_NOW 指定的固定时间

用法：
    from services.clock import get_now, get_today, get_mode

    now = get_now()            # 带时区的 datetime，永远是 Asia/Shanghai
    today = get_today()        # date
    if get_mode() == "demo": ...

命令行自查：
    python -m services.clock

配置错误（模式写错、演示时间格式不对、时区不存在）一律抛
ClockConfigError，不会静默退回真实时间——静默回退会让演示数据悄悄算错。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 环境变量名（集中在这里，避免各处拼错字符串）
ENV_TIME_MODE = "UNIPULSE_TIME_MODE"
ENV_DEMO_NOW = "UNIPULSE_DEMO_NOW"
ENV_TIMEZONE = "UNIPULSE_TIMEZONE"

# 合法模式
MODE_REAL = "real"
MODE_DEMO = "demo"
_VALID_MODES = (MODE_REAL, MODE_DEMO)

# 全系统统一时区
DEFAULT_TIMEZONE = "Asia/Shanghai"

# 演示时间的标准写法，出错时给用户看
_DEMO_NOW_EXAMPLE = "2026-11-30T09:00:00+08:00"


class ClockConfigError(ValueError):
    """时间配置有误：模式非法、演示时间缺失/格式错误、时区无效。"""


def get_timezone_name() -> str:
    """返回配置的时区名（未配置时用 Asia/Shanghai）。"""
    name = (os.environ.get(ENV_TIMEZONE) or DEFAULT_TIMEZONE).strip()
    if not name:
        raise ClockConfigError(
            f"{ENV_TIMEZONE} 为空。请填写 IANA 时区名，例如 {DEFAULT_TIMEZONE}。"
        )
    return name


def get_timezone() -> ZoneInfo:
    """返回时区对象。时区名无效时抛 ClockConfigError，不自动退回默认值。"""
    name = get_timezone_name()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ClockConfigError(
            f"{ENV_TIMEZONE}={name!r} 不是有效的 IANA 时区名。"
            f"示例：{DEFAULT_TIMEZONE}、Asia/Tokyo、UTC。"
        ) from exc


def get_mode() -> str:
    """返回当前时间模式：'real' 或 'demo'。未设置时默认 real。"""
    raw = (os.environ.get(ENV_TIME_MODE) or "").strip().lower()
    if not raw:
        return MODE_REAL
    if raw not in _VALID_MODES:
        raise ClockConfigError(
            f"{ENV_TIME_MODE}={raw!r} 无效。只能是 {' 或 '.join(_VALID_MODES)}。"
        )
    return raw


def is_demo_mode() -> bool:
    """是否处于演示模式。"""
    return get_mode() == MODE_DEMO


def get_demo_now() -> datetime:
    """返回解析后的演示时间（已换算到配置时区）。仅 demo 模式使用。"""
    raw = (os.environ.get(ENV_DEMO_NOW) or "").strip()
    if not raw:
        raise ClockConfigError(
            f"demo 模式必须设置 {ENV_DEMO_NOW}，例如 {_DEMO_NOW_EXAMPLE}。"
        )

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ClockConfigError(
            f"{ENV_DEMO_NOW}={raw!r} 不是合法的 ISO 8601 时间。"
            f"正确写法示例：{_DEMO_NOW_EXAMPLE}。"
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClockConfigError(
            f"{ENV_DEMO_NOW}={raw!r} 缺少时区。必须写成带偏移量的形式，"
            f"例如 {_DEMO_NOW_EXAMPLE}。"
        )

    return parsed.astimezone(get_timezone())


def get_now() -> datetime:
    """返回“当前业务时间”，永远是带时区的 datetime。

    - real 模式：真实时间，换算到配置时区
    - demo 模式：UNIPULSE_DEMO_NOW 指定的固定时间

    业务代码请用它代替 datetime.now()。
    """
    if get_mode() == MODE_DEMO:
        return get_demo_now()
    return datetime.now(tz=get_timezone())


def get_today() -> date:
    """返回“今天的日期”（按配置时区计算）。"""
    return get_now().date()


def describe() -> dict:
    """返回当前时间配置的摘要，用于日志和自查。"""
    info: dict[str, object] = {
        "mode": get_mode(),
        "timezone": get_timezone_name(),
    }
    if info["mode"] == MODE_DEMO:
        info["now"] = get_now().isoformat()
    return info


if __name__ == "__main__":
    # 直接运行：python -m services.clock
    settings = describe()
    print("时间模式：", settings["mode"])
    print("时区：    ", settings["timezone"])
    print("当前时间：", get_now().isoformat())
    print("今天日期：", get_today().isoformat())
    if settings["mode"] == MODE_DEMO:
        print("说明：      演示模式，时间固定不变")
