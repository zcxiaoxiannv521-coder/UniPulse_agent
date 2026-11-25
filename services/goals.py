"""学习目标录入（目标 + 复习范围/允许时段）。

背景：
    C 的 `save_goal(payload, expected_version)` 发：
        POST /v1/goals
        {"payload": {"title","scope","event_id","daily_max_minutes","availability"},
         "expected_version": 1}

`goals` 表只有 title / exam_id / daily_limit_minutes / status / version，
没有 scope 与 availability。这两项是用户**明确填写**的约束，不能静默丢弃，
所以复用已有的 `user_preferences` 表，按 `scope='goal:<id>'` 存：

    key='goal_scope'        value_json='"第三章、第四章"'
    key='goal_availability' value_json='"每天 19:00 后"'

这样不用改表，也能在 workspace 里原样回显。

更新策略：
    同一用户 + 同一考试已有 active 目标时**更新**它（version + 1），
    没有才新建。避免重复提交时堆出一堆重复目标。
"""

from __future__ import annotations

import json
import secrets
import sqlite3

from services.queries import _connection, _load_user

MAX_TITLE_LENGTH = 60
MAX_TEXT_LENGTH = 1000
MIN_DAILY_MINUTES = 15
MAX_DAILY_MINUTES = 480

PREF_KEYS = {"scope": "goal_scope", "availability": "goal_availability"}


class GoalError(Exception):
    """目标保存失败。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _text(payload: dict, key: str, *, max_length: int, required: bool) -> str | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise GoalError("INVALID_TITLE" if key == "title" else "INVALID_TEXT",
                            f"{key} 不能为空")
        return None
    if not isinstance(value, str):
        raise GoalError("INVALID_TEXT", f"{key} 必须是文本")
    text = value.strip()
    if required and not text:
        raise GoalError("INVALID_TITLE", "目标名称不能为空")
    if len(text) > max_length:
        raise GoalError("INVALID_TEXT", f"{key} 不能超过 {max_length} 字")
    return text


def _daily_limit(payload: dict) -> int:
    value = payload.get("daily_max_minutes")
    if isinstance(value, bool) or not isinstance(value, int):
        raise GoalError("INVALID_DAILY_LIMIT", "daily_max_minutes 必须是整数分钟")
    if not MIN_DAILY_MINUTES <= value <= MAX_DAILY_MINUTES:
        raise GoalError(
            "INVALID_DAILY_LIMIT",
            f"每日上限必须在 {MIN_DAILY_MINUTES}—{MAX_DAILY_MINUTES} 分钟之间",
        )
    return value


def _load_exam(db: sqlite3.Connection, user_id: str, exam_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT id, name FROM exams WHERE id = ? AND user_id = ?",
        (exam_id, user_id),
    ).fetchone()
    if row is None:
        raise GoalError("EXAM_NOT_FOUND", f"考试 {exam_id!r} 不存在或不属于当前用户")
    return row


def _upsert_pref(
    db: sqlite3.Connection, user_id: str, goal_id: str, key: str, value: str
) -> None:
    scope = f"goal:{goal_id}"
    db.execute(
        "DELETE FROM user_preferences WHERE user_id = ? AND key = ? AND scope = ?",
        (user_id, key, scope),
    )
    db.execute(
        "INSERT INTO user_preferences (id, user_id, key, value_json, scope) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"up_{secrets.token_hex(6)}", user_id, key,
         json.dumps(value, ensure_ascii=False), scope),
    )


def _goal_view(
    goal: sqlite3.Row, scope: str | None, availability: str | None
) -> dict:
    return {
        "id": goal["id"],
        "title": goal["title"],
        "event_id": goal["exam_id"],
        "daily_max_minutes": int(goal["daily_limit_minutes"]),
        "scope": scope or "",
        "availability": availability or "",
        "status": goal["status"],
        "version": int(goal["version"]),
    }


def _load_prefs(
    db: sqlite3.Connection, user_id: str, goal_id: str
) -> dict[str, str]:
    rows = db.execute(
        "SELECT key, value_json FROM user_preferences "
        "WHERE user_id = ? AND scope = ?",
        (user_id, f"goal:{goal_id}"),
    ).fetchall()
    result: dict[str, str] = {}
    for row in rows:
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            value = row["value_json"]
        result[row["key"]] = value if isinstance(value, str) else str(value)
    return result


def save_goal(
    user_id: str,
    payload: dict,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """保存/更新当前用户的学习目标，返回给 C 的目标结构。"""
    if not isinstance(payload, dict):
        raise GoalError("INVALID_ARGUMENT", "payload 必须是对象")

    title = _text(payload, "title", max_length=MAX_TITLE_LENGTH, required=True)
    exam_id = payload.get("event_id")
    if not isinstance(exam_id, str) or not exam_id.strip():
        raise GoalError("INVALID_EVENT", "event_id 不能为空")
    exam_id = exam_id.strip()

    daily = _daily_limit(payload)
    scope = _text(payload, "scope", max_length=MAX_TEXT_LENGTH, required=False)
    availability = _text(
        payload, "availability", max_length=MAX_TEXT_LENGTH, required=False
    )

    with _connection(conn) as db:
        try:
            _load_user(db, user_id)
        except Exception as exc:
            raise GoalError("USER_NOT_FOUND", f"用户 {user_id!r} 不存在") from exc

        _load_exam(db, user_id, exam_id)

        existing = db.execute(
            """
            SELECT * FROM goals
            WHERE user_id = ? AND exam_id = ? AND status = 'active'
            ORDER BY version DESC, created_at DESC LIMIT 1
            """,
            (user_id, exam_id),
        ).fetchone()

        with db:
            if existing is None:
                goal_id = f"gl_{secrets.token_hex(6)}"
                db.execute(
                    """
                    INSERT INTO goals (id, user_id, exam_id, title,
                                       daily_limit_minutes, status, version)
                    VALUES (?, ?, ?, ?, ?, 'active', 1)
                    """,
                    (goal_id, user_id, exam_id, title, daily),
                )
                action = "created"
            else:
                goal_id = existing["id"]
                db.execute(
                    """
                    UPDATE goals SET title = ?, daily_limit_minutes = ?,
                                     version = version + 1,
                                     updated_at = datetime('now', 'localtime')
                    WHERE id = ? AND user_id = ?
                    """,
                    (title, daily, goal_id, user_id),
                )
                action = "updated"

            if scope is not None:
                _upsert_pref(db, user_id, goal_id, PREF_KEYS["scope"], scope)
            if availability is not None:
                _upsert_pref(
                    db, user_id, goal_id, PREF_KEYS["availability"], availability
                )

            goal = db.execute(
                "SELECT * FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
            prefs = _load_prefs(db, user_id, goal_id)

    message = (
        "目标已保存；当前计划仍按旧目标，需要重新生成预览才会更新。"
        if action == "updated"
        else "目标已创建；可以基于它生成学习计划预览。"
    )
    return {
        "goal": _goal_view(
            goal,
            prefs.get(PREF_KEYS["scope"]),
            prefs.get(PREF_KEYS["availability"]),
        ),
        "created": action == "created",
        "message": message,
    }
