"""课程/课表录入（具体日期的单次上课）。

C 的 `add_class(payload, expected_version)` 发：
    POST /v1/classes
    {"payload": {"course","location","start_at","end_at"}, "expected_version": 1}

按 V4「首版使用手动录入的具体日期课表」，这里**不生成每周重复课程**：
    courses         —— 课程档案（按用户 + 课程名复用）
    class_sessions  —— 这一次具体日期/时间段的上课记录

校验：
    课程名必填；结束必须晚于开始；必须同一天；时区必须明确；
    与已有课次时间重叠 → 409（和 C 的演示模式行为一致）。
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timedelta

from services import clock
from services.queries import _connection, _load_user

MAX_COURSE_LENGTH = 60
MAX_LOCATION_LENGTH = 120
MAX_DURATION_MINUTES = 12 * 60
MIN_YEAR = 2000
MAX_YEAR = 2100


class ClassError(Exception):
    """课表录入失败。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _parse_time(value, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ClassError("INVALID_TIME", f"{field} 不能为空")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ClassError(
            "INVALID_TIME", f"{field} 不是合法的 ISO 8601 时间"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClassError("INVALID_TIME", f"{field} 必须带时区，例如 +08:00")
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        raise ClassError("INVALID_TIME", f"{field} 的年份超出合理范围")
    return parsed.astimezone(clock.get_timezone())


def _session_view(session: sqlite3.Row, course_name: str) -> dict:
    start = datetime.fromisoformat(
        f"{session['session_date']}T{session['start_time']}"
    ).replace(tzinfo=clock.get_timezone())
    end = datetime.fromisoformat(
        f"{session['session_date']}T{session['end_time']}"
    ).replace(tzinfo=clock.get_timezone())
    return {
        "id": session["id"],
        "course": course_name,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "location": session["location"] or "",
    }


def _insert_session(
    db: sqlite3.Connection,
    *,
    user_id: str,
    course_id: str,
    session_date: str,
    start_time: str,
    end_time: str,
    location: str,
) -> sqlite3.Row:
    session_id = f"cs_{secrets.token_hex(6)}"
    db.execute(
        """
        INSERT INTO class_sessions (id, user_id, course_id, session_date,
                                    start_time, end_time, location, status, version)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'normal', 1)
        """,
        (session_id, user_id, course_id, session_date, start_time, end_time,
         location or None),
    )
    return db.execute(
        "SELECT * FROM class_sessions WHERE id = ?", (session_id,)
    ).fetchone()


def add_class(
    user_id: str,
    payload: dict,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """新增一次具体日期的课程，返回 C 需要的课程结构。"""
    if not isinstance(payload, dict):
        raise ClassError("INVALID_ARGUMENT", "payload 必须是对象")

    course_value = payload.get("course")
    if not isinstance(course_value, str) or not course_value.strip():
        raise ClassError("INVALID_COURSE", "课程名称不能为空")
    course_name = course_value.strip()
    if len(course_name) > MAX_COURSE_LENGTH:
        raise ClassError("INVALID_COURSE", f"课程名称不能超过 {MAX_COURSE_LENGTH} 字")

    location_value = payload.get("location", "")
    if location_value is None:
        location = ""
    elif not isinstance(location_value, str):
        raise ClassError("INVALID_LOCATION", "地点必须是文本")
    else:
        location = location_value.strip()
    if len(location) > MAX_LOCATION_LENGTH:
        raise ClassError("INVALID_LOCATION", f"地点不能超过 {MAX_LOCATION_LENGTH} 字")

    start = _parse_time(payload.get("start_at"), "start_at")
    end = _parse_time(payload.get("end_at"), "end_at")

    if end <= start:
        raise ClassError("INVALID_TIME", "结束时间必须晚于开始时间")
    if end.date() != start.date():
        raise ClassError(
            "CROSS_DAY_NOT_SUPPORTED",
            "当前课表按单日存储，跨天的课程请拆成两条录入",
        )
    if end - start > timedelta(minutes=MAX_DURATION_MINUTES):
        raise ClassError("INVALID_TIME", "单次课程时长不能超过 12 小时")

    session_date = start.date().isoformat()
    start_hm = start.strftime("%H:%M")
    end_hm = end.strftime("%H:%M")

    with _connection(conn) as db:
        try:
            _load_user(db, user_id)
        except Exception as exc:
            raise ClassError("USER_NOT_FOUND", f"用户 {user_id!r} 不存在") from exc

        conflict = db.execute(
            """
            SELECT s.start_time, s.end_time, c.name AS course_name
            FROM class_sessions s
            JOIN courses c ON c.id = s.course_id AND c.user_id = s.user_id
            WHERE s.user_id = ? AND s.status <> 'cancelled' AND s.session_date = ?
              AND s.start_time < ? AND ? < s.end_time
            LIMIT 1
            """,
            (user_id, session_date, end_hm, start_hm),
        ).fetchone()
        if conflict is not None:
            raise ClassError(
                "CLASS_TIME_CONFLICT",
                f"与已有课程「{conflict['course_name']}」"
                f"（{conflict['start_time']}–{conflict['end_time']}）时间冲突",
            )

        with db:
            course = db.execute(
                "SELECT id FROM courses WHERE user_id = ? AND name = ? LIMIT 1",
                (user_id, course_name),
            ).fetchone()
            if course is None:
                course_id = f"cr_{secrets.token_hex(6)}"
                db.execute(
                    "INSERT INTO courses (id, user_id, name, location) "
                    "VALUES (?, ?, ?, ?)",
                    (course_id, user_id, course_name, location or None),
                )
            else:
                course_id = course["id"]

            session = _insert_session(
                db,
                user_id=user_id,
                course_id=course_id,
                session_date=session_date,
                start_time=start_hm,
                end_time=end_hm,
                location=location,
            )

    return {
        "class": _session_view(session, course_name),
        "course_id": course_id,
        "session_id": session["id"],
        "message": f"已录入 {session_date} {start_hm}–{end_hm} 的「{course_name}」。",
    }
