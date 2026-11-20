"""只读查询服务（Agent 的上下文 / 网页看板的数据源）。

本模块只做 SELECT：
    get_context(user_id)   给 A 的 Agent 用：课表、考试、任务、目标、正式计划、偏好
    get_dashboard(user_id) 给 C 的看板用：7 天视图、下一节课、最近截止、待确认提示

三条硬规则：
    1. 每个查询都带 user_id 条件；plan_items 没有 user_id，必须经 plans 联表校验归属
    2. 只读正式数据：计划只取 status='confirmed' 的那一版；预览(previews)单独返回，
       绝不当作正式计划
    3. 只读不改：本模块不执行任何 INSERT / UPDATE / DELETE

空数据不是错误：查不到就返回空数组 / null，并在 missing 里说明原因。
用户不存在是错误：抛 UserNotFoundError，避免把“ID 写错”伪装成“这个人没数据”。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from database.connection import get_connection
from services import clock

# 默认查询 / 展示范围（天）
DEFAULT_RANGE_DAYS = 7
DASHBOARD_DAYS = 7

# 看板顶部「通知来源」最多显示几条
RECENT_NOTICE_LIMIT = 5

WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class QueryError(Exception):
    """查询参数或数据状态有误。"""

    code = "QUERY_ERROR"


class UserNotFoundError(QueryError):
    """用户不存在（通常是 user_id 传错或用户还没注册）。"""

    code = "USER_NOT_FOUND"

    def __init__(self, user_id: str):
        super().__init__(f"用户 {user_id!r} 不存在")
        self.user_id = user_id


# ---------------------------------------------------------------- 连接与工具
@contextmanager
def _connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """复用外部传入的连接（测试用），否则自己开一个用完关掉。"""
    if conn is not None:
        yield conn
        return
    own = get_connection()
    try:
        yield own
    finally:
        own.close()


def _load_user(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    """取用户；不存在直接报错，避免静默返回空数据。"""
    row = conn.execute(
        "SELECT id, name, timezone, wecom_userid FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        raise UserNotFoundError(user_id)
    return row


def _parse_date(value: str, field: str) -> date:
    """把 YYYY-MM-DD 解析成 date，格式错误时给出明确提示。"""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise QueryError(
            f"{field}={value!r} 不是合法日期，正确写法如 2026-11-30"
        ) from exc


def _parse_iso(text: str | None) -> datetime | None:
    """解析数据库里的 ISO 时间；不带时区的按系统时区补上。"""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=clock.get_timezone())
    return parsed


def _combine(day: str, clock_time: str | None) -> datetime | None:
    """把 '2026-11-30' + '10:00' 合成带时区的 datetime。"""
    if not clock_time:
        return None
    try:
        return datetime.fromisoformat(f"{day}T{clock_time}").replace(
            tzinfo=clock.get_timezone()
        )
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _normalize_time(text: str | None) -> str | None:
    """把 SQLite 的 '2026-11-20 20:50:44' 统一成 '2026-11-20T20:50:44+08:00'。

    数据库里 created_at/updated_at 用 datetime('now','localtime') 生成，不带时区；
    对外一律给 ISO 8601 带时区的形式，C 的页面和 A 的模型都更好处理。
    """
    if not text:
        return None
    parsed = _parse_iso(text)
    return parsed.isoformat() if parsed else text


def _plan_summary(plan: sqlite3.Row | None) -> dict | None:
    """计划摘要（对外统一格式）。"""
    if plan is None:
        return None
    return {
        "id": plan["id"],
        "goal_id": plan["goal_id"],
        "status": plan["status"],
        "version": plan["version"],
        "note": plan["note"],
        "updated_at": _normalize_time(plan["updated_at"]),
    }


def _json_value(text: str) -> Any:
    """preferences.value_json 是 JSON 字符串，解析失败时原样返回。"""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _weekday_name(day: date) -> str:
    return WEEKDAY_NAMES[day.weekday()]


# ---------------------------------------------------------------- 数据片段
def _preferences(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT key, value_json, scope, confirmed_at
        FROM user_preferences
        WHERE user_id = ?
        ORDER BY scope, key
        """,
        (user_id,),
    ).fetchall()
    return [
        {
            "key": row["key"],
            "value": _json_value(row["value_json"]),
            "scope": row["scope"],
            "confirmed_at": _normalize_time(row["confirmed_at"]),
        }
        for row in rows
    ]


def _goals(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, exam_id, title, daily_limit_minutes, status, version
        FROM goals
        WHERE user_id = ? AND status = 'active'
        ORDER BY created_at
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _exams(conn: sqlite3.Connection, user_id: str, today: date) -> list[dict]:
    """今天及以后的考试，按时间正序。"""
    rows = conn.execute(
        """
        SELECT e.id, e.course_id, c.name AS course_name, e.name,
               e.exam_date, e.start_time, e.end_time, e.location,
               e.status, e.version
        FROM exams e
        LEFT JOIN courses c ON c.id = e.course_id AND c.user_id = e.user_id
        WHERE e.user_id = ? AND e.exam_date >= ?
        ORDER BY e.exam_date, COALESCE(e.start_time, '00:00')
        """,
        (user_id, today.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _sessions(
    conn: sqlite3.Connection, user_id: str, date_from: date, date_to: date
) -> list[dict]:
    """日期范围内的课表（含时间/地点）。"""
    rows = conn.execute(
        """
        SELECT s.id, s.course_id, c.name AS course_name, c.teacher,
               s.session_date, s.start_time, s.end_time, s.location,
               s.status, s.version
        FROM class_sessions s
        JOIN courses c ON c.id = s.course_id AND c.user_id = s.user_id
        WHERE s.user_id = ? AND s.session_date BETWEEN ? AND ?
        ORDER BY s.session_date, s.start_time
        """,
        (user_id, date_from.isoformat(), date_to.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _tasks(conn: sqlite3.Connection, user_id: str, include_done: bool = False) -> list[dict]:
    """待办：未完成的排在前面；截止时间空的放最后。"""
    sql = """
        SELECT t.id, t.goal_id, t.course_id, c.name AS course_name, t.title, t.detail,
               t.due_at, t.estimated_minutes, t.remaining_minutes, t.priority,
               t.is_optional, t.splittable, t.status, t.source
        FROM tasks t
        LEFT JOIN courses c ON c.id = t.course_id AND c.user_id = t.user_id
        WHERE t.user_id = ?
    """
    params: list[Any] = [user_id]
    if not include_done:
        sql += " AND t.status <> 'done'"
    sql += " ORDER BY t.due_at IS NULL, t.due_at, t.priority, t.title"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _latest_confirmed_plan(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    """同一目标只会有一条 confirmed，取版本最高的那条。"""
    return conn.execute(
        """
        SELECT id, goal_id, status, version, note, updated_at
        FROM plans
        WHERE user_id = ? AND status = 'confirmed'
        ORDER BY version DESC, updated_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()


def _plan_items(conn: sqlite3.Connection, user_id: str, plan_id: str) -> list[dict]:
    """计划的时间块。join plans 再校验 user_id，防止越权读到别人的计划。"""
    rows = conn.execute(
        """
        SELECT pi.id, pi.plan_id, pi.start_at, pi.end_at, pi.title, pi.item_type,
               pi.task_id, pi.session_id, pi.exam_id, pi.is_locked,
               t.status AS task_status
        FROM plan_items pi
        JOIN plans p ON p.id = pi.plan_id AND p.user_id = ?
        LEFT JOIN tasks t ON t.id = pi.task_id AND t.user_id = p.user_id
        WHERE pi.plan_id = ?
        ORDER BY pi.start_at
        """,
        (user_id, plan_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _pending_previews(conn: sqlite3.Connection, user_id: str, now: datetime) -> list[dict]:
    """未确认且未过期的草稿。它们只是提示，不是正式计划。"""
    rows = conn.execute(
        """
        SELECT id, confirm_code, kind, summary, expires_at
        FROM previews
        WHERE user_id = ? AND status = 'pending'
        ORDER BY created_at
        """,
        (user_id,),
    ).fetchall()

    result = []
    for row in rows:
        expires_at = _parse_iso(row["expires_at"])
        if expires_at is not None and expires_at < now:
            continue
        result.append(
            {
                "preview_id": row["id"],
                "confirm_code": row["confirm_code"],
                "kind": row["kind"],
                "summary": row["summary"],
                "expires_at": _normalize_time(row["expires_at"]),
            }
        )
    return result


def _recent_notices(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, received_at, status, raw_text
        FROM notifications
        WHERE user_id = ?
        ORDER BY received_at DESC
        LIMIT ?
        """,
        (user_id, RECENT_NOTICE_LIMIT),
    ).fetchall()
    notices = []
    for row in rows:
        text = (row["raw_text"] or "").replace("\n", " ").strip()
        notices.append(
            {
                "id": row["id"],
                "received_at": _normalize_time(row["received_at"]),
                "status": row["status"],
                "summary": text[:40] + ("…" if len(text) > 40 else ""),
            }
        )
    return notices


# ---------------------------------------------------------------- 对外接口
def get_context(
    user_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Agent 的上下文：一次拿到该用户的课表、考试、任务、目标与正式计划。

    参数：
        user_id    可信身份里的内部用户 ID（不要从消息文本推断）
        date_from  可选，YYYY-MM-DD，默认今天
        date_to    可选，YYYY-MM-DD，默认今天 + 6 天

    返回：
        永远包含 user / range / as_of / time_mode / data_version /
        goals / exams / sessions / tasks / preferences / plan / plan_items / missing
    """
    today = clock.get_today()
    start = _parse_date(date_from, "date_from") if date_from else today
    end = (
        _parse_date(date_to, "date_to")
        if date_to
        else start + timedelta(days=DEFAULT_RANGE_DAYS - 1)
    )
    if end < start:
        raise QueryError(f"date_to({end}) 不能早于 date_from({start})")

    now = clock.get_now()

    with _connection(conn) as db:
        user = _load_user(db, user_id)

        preferences = _preferences(db, user_id)
        goals = _goals(db, user_id)
        exams = _exams(db, user_id, today)
        sessions = _sessions(db, user_id, start, end)
        tasks = _tasks(db, user_id)
        plan = _latest_confirmed_plan(db, user_id)
        items = _plan_items(db, user_id, plan["id"]) if plan else []

        missing: list[dict] = []
        if not sessions:
            missing.append(
                {"field": "sessions", "reason": "NO_CLASS_DATA_IN_RANGE",
                 "detail": f"{start} ~ {end} 没有课表记录"}
            )
        if not exams:
            missing.append({"field": "exams", "reason": "NO_UPCOMING_EXAM"})
        if not tasks:
            missing.append({"field": "tasks", "reason": "NO_OPEN_TASK"})
        if plan is None:
            missing.append({"field": "plan", "reason": "NO_CONFIRMED_PLAN"})
        elif not items:
            missing.append({"field": "plan_items", "reason": "EMPTY_PLAN"})
        if not goals:
            missing.append({"field": "goals", "reason": "NO_ACTIVE_GOAL"})

        return {
            "user": {
                "id": user["id"],
                "name": user["name"],
                "timezone": user["timezone"],
            },
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "as_of": now.isoformat(),
            "time_mode": clock.get_mode(),
            "data_version": {
                "plan_id": plan["id"] if plan else None,
                "plan_version": plan["version"] if plan else None,
            },
            "goals": goals,
            "exams": exams,
            "sessions": sessions,
            "tasks": tasks,
            "preferences": preferences,
            "plan": _plan_summary(plan),
            "plan_items": items,
            "missing": missing,
        }


def get_dashboard(user_id: str, *, conn: sqlite3.Connection | None = None) -> dict:
    """网页看板数据：未来 7 天的时间轴 + 下一节课 + 最近截止 + 待确认提示。

    只读：不生成计划、不改任何记录。没有数据就是空数组 / null。
    """
    now = clock.get_now()
    today = now.date()
    end = today + timedelta(days=DASHBOARD_DAYS - 1)

    with _connection(conn) as db:
        user = _load_user(db, user_id)

        sessions = _sessions(db, user_id, today, end)
        exams = _exams(db, user_id, today)
        tasks = _tasks(db, user_id)
        plan = _latest_confirmed_plan(db, user_id)
        items = _plan_items(db, user_id, plan["id"]) if plan else []
        pending = _pending_previews(db, user_id, now)
        notices = _recent_notices(db, user_id)

    # ---- 组装 7 天时间轴 ----
    timeline: dict[str, list[dict]] = {
        (today + timedelta(days=i)).isoformat(): [] for i in range(DASHBOARD_DAYS)
    }

    def _add(day_key: str, item: dict) -> None:
        if day_key in timeline:
            timeline[day_key].append(item)

    # 1) 课程（已取消的不显示）
    rendered_sessions: set[str] = set()
    for session in sessions:
        if session["status"] == "cancelled":
            continue
        start_at = _combine(session["session_date"], session["start_time"])
        end_at = _combine(session["session_date"], session["end_time"])
        _add(
            session["session_date"],
            {
                "start_at": _iso(start_at),
                "end_at": _iso(end_at),
                "title": session["course_name"],
                "item_type": "course",
                "status": "planned",
                "source": "course",
                "location": session["location"],
                "ref_id": session["id"],
            },
        )
        rendered_sessions.add(session["id"])

    # 2) 考试
    for exam in exams:
        exam_start = _combine(exam["exam_date"], exam["start_time"])
        _add(
            exam["exam_date"],
            {
                "start_at": _iso(exam_start),
                "end_at": _iso(_combine(exam["exam_date"], exam["end_time"])),
                "title": exam["name"],
                "item_type": "exam",
                "status": "planned",
                "source": "exam",
                "location": exam["location"],
                "ref_id": exam["id"],
            },
        )

    # 3) 计划里的学习时间块（避免与课程/考试重复）
    for item in items:
        start_at = _parse_iso(item["start_at"])
        end_at = _parse_iso(item["end_at"])
        if start_at is None or end_at is None:
            continue
        if item["session_id"] and item["session_id"] in rendered_sessions:
            continue
        _add(
            start_at.date().isoformat(),
            {
                "start_at": item["start_at"],
                "end_at": item["end_at"],
                "title": item["title"],
                "item_type": item["item_type"],
                "status": "done" if item["task_status"] == "done" else (
                    "locked" if item["is_locked"] else "planned"
                ),
                "source": "plan",
                "location": None,
                "ref_id": item["id"],
            },
        )

    days = [
        {
            "date": day_key,
            "weekday": _weekday_name(date.fromisoformat(day_key)),
            "items": sorted(
                day_items, key=lambda x: x["start_at"] or day_key + "T00:00:00+08:00"
            ),
        }
        for day_key, day_items in sorted(timeline.items())
    ]

    # ---- 下一节课 ----
    next_class = None
    for session in sessions:
        if session["status"] == "cancelled":
            continue
        start_at = _combine(session["session_date"], session["start_time"])
        if start_at and start_at >= now:
            next_class = {
                "title": session["course_name"],
                "start_at": _iso(start_at),
                "end_at": _iso(_combine(session["session_date"], session["end_time"])),
                "location": session["location"],
                "session_id": session["id"],
            }
            break

    # ---- 最近截止 ----
    next_deadline = None
    for task in tasks:
        due_at = _parse_iso(task["due_at"])
        if due_at and due_at >= now:
            next_deadline = {
                "task_id": task["id"],
                "title": task["title"],
                "due_at": task["due_at"],
                "remaining_minutes": task["remaining_minutes"],
            }
            break

    return {
        "user": {"id": user["id"], "name": user["name"]},
        "generated_at": now.isoformat(),
        "today": today.isoformat(),
        "time_mode": clock.get_mode(),
        "plan": _plan_summary(plan),
        "next_class": next_class,
        "next_deadline": next_deadline,
        "pending_changes": pending,
        "days": days,
        "tasks": tasks,
        "exams": exams,
        "notices": notices,
    }
