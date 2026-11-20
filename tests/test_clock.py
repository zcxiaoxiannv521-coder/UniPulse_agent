"""services/clock.py 的测试。

覆盖：默认模式、real、demo、时区、演示时间换算、错误配置、跨天。
每个用例都用 monkeypatch 设置环境变量，结束后自动还原。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import clock


@pytest.fixture(autouse=True)
def clear_clock_env(monkeypatch):
    """每个用例开始前清掉时间相关环境变量。"""
    for name in (clock.ENV_TIME_MODE, clock.ENV_DEMO_NOW, clock.ENV_TIMEZONE):
        monkeypatch.delenv(name, raising=False)


def test_default_mode_is_real():
    """不配置任何变量时，默认是 real。"""
    assert clock.get_mode() == "real"
    assert clock.is_demo_mode() is False


def test_real_mode_returns_current_time():
    """real 模式返回的时间应该接近“现在”，且带时区。"""
    now = clock.get_now()
    reference = datetime.now(tz=timezone.utc)

    assert now.tzinfo is not None
    assert abs(now - reference) < timedelta(seconds=10)


def test_real_mode_explicit(monkeypatch):
    """显式写 UNIPULSE_TIME_MODE=real 也一样。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "real")

    assert clock.get_mode() == "real"
    assert clock.get_now().utcoffset() is not None


def test_demo_mode_returns_fixed_time(monkeypatch):
    """demo 模式返回配置里写死的时间。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    now = clock.get_now()
    expected = datetime(2026, 11, 30, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert now == expected
    assert now.isoformat() == "2026-11-30T09:00:00+08:00"


def test_demo_now_converted_to_configured_timezone(monkeypatch):
    """演示时间写成 UTC，也要换算成 Asia/Shanghai。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T01:00:00+00:00")

    now = clock.get_now()
    assert now.hour == 9
    assert now.utcoffset() == timedelta(hours=8)
    assert now.tzinfo.key == "Asia/Shanghai"     # type: ignore[union-attr]


def test_timezone_is_asia_shanghai():
    """默认时区固定为 Asia/Shanghai。"""
    assert clock.get_timezone_name() == "Asia/Shanghai"
    assert clock.get_now().utcoffset() == timedelta(hours=8)


def test_today_boundary_end_of_day(monkeypatch):
    """23:59:59 仍然算当天。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T23:59:59+08:00")

    assert clock.get_today() == date(2026, 11, 30)


def test_today_boundary_crosses_midnight(monkeypatch):
    """过了零点，日期必须变成下一天。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-12-01T00:00:01+08:00")

    assert clock.get_today() == date(2026, 12, 1)


def test_invalid_mode_raises(monkeypatch):
    """非法模式不能静默退回 real。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "testing")

    with pytest.raises(clock.ClockConfigError) as excinfo:
        clock.get_mode()
    assert "UNIPULSE_TIME_MODE" in str(excinfo.value)


def test_demo_mode_requires_demo_now(monkeypatch):
    """demo 模式缺 UNIPULSE_DEMO_NOW 必须报错。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")

    with pytest.raises(clock.ClockConfigError) as excinfo:
        clock.get_now()
    assert "UNIPULSE_DEMO_NOW" in str(excinfo.value)


def test_invalid_demo_now_format_raises(monkeypatch):
    """演示时间格式错误必须报错。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026/11/30 09:00")

    with pytest.raises(clock.ClockConfigError) as excinfo:
        clock.get_now()
    assert "ISO 8601" in str(excinfo.value)


def test_naive_demo_now_rejected(monkeypatch):
    """不带时区的时间必须拒绝，否则会被当成 UTC 或本地时间，算错 8 小时。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00")

    with pytest.raises(clock.ClockConfigError) as excinfo:
        clock.get_now()
    assert "缺少时区" in str(excinfo.value)


def test_invalid_timezone_raises(monkeypatch):
    """不存在的时区必须报错，不能悄悄用默认值。"""
    monkeypatch.setenv(clock.ENV_TIMEZONE, "Mars/Base")

    with pytest.raises(clock.ClockConfigError) as excinfo:
        clock.get_now()
    assert "UNIPULSE_TIMEZONE" in str(excinfo.value)


def test_describe_reports_mode_and_timezone(monkeypatch):
    """describe() 用于日志和自查。"""
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    info = clock.describe()
    assert info["mode"] == "demo"
    assert info["timezone"] == "Asia/Shanghai"
    assert info["now"] == "2026-11-30T09:00:00+08:00"
