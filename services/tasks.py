"""任务进度反馈与实际用时记录。

两条独立的语义，不要混：
    record_progress  用户明确报告"做到了哪一步"：状态 + 剩余工作量（+ 累计用时 + 备注）
    record_time      只报告"花了多少时间"：累计用时，不改剩余工作量、不改状态

为什么需要 task_logs 表：
    `tasks` 表只有 estimated_minutes / remaining_minutes，没有"实际用时"和"反馈"字段。
    把学习时长写进 remaining_minutes 会把"投入时间"当成"完成进度"，是错的；
    改 estimated_minutes 又会破坏原计划。所以新增一张只追加的 task_logs：
        - 累计用时 = SUM(minutes)
        - 每条只存"本次增量"，不覆盖历史
    本表由 `database/schema.sql` 的 CREATE TABLE IF NOT EXISTS 创建，老库启动时自动补上，
    不需要数据迁移。

与 C 的约定（web/client.py）：
    POST /v1/tasks/{id}/progress
        {"payload": {"status","remaining_minutes","actual_minutes","feedback"},
         "expected_version": N}
    POST /v1/tasks/{id}/time
        {"actual_minutes": N, "expected_version": N}

状态说明：
    C 会发 partial，但数据库 CHECK 只允许 todo/doing/done，
    因此 partial 落库为 doing（对外仍显示"进行中"），这是 schema 限制。
"""

from __future__ import annotations

import secrets
import sqlite3

from services import clock
from services.queries import _connection, _load_user, _normalize_time

MAX_MINUTES = 10080          # 与 C 的前端限制一致（一周分钟数）
MAX_FEEDBACK_LENGTH = 500

# C 可能发 partial；数据库只接受 todo/doing/done
INPUT_STATUSES = ("todo", "doing", "partial", "done")
STATUS_TO_DB = {"todo": "todo", "doing": "doing", "partial": "doing", "done": "done"}


class TaskError(Exception):
    """任务操作失败。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _load_task(db: sqlite3.Connection, user_id: str, task_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)
    ).fetchone()
    if row is None:
        raise TaskError("TASK_NOT_FOUND", f"任务 {task_id!r} 不存在或不属于当前用户")
    return row


def _actual_minutes(db: sqlite3.Connection, user_id: str, task_id: str) -> int:
    row = db.execute(
        "SELECT COALESCE(SUM(minutes), 0) AS total FROM task_logs "
        "WHERE user_id = ? AND task_id = ?",
        (user_id, task_id),
    ).fetchone()
    return int(row["total"] or 0)


def _require_minutes(value, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskError("INVALID_ARGUMENT", f"{field} 必须是整数分钟")
    if value < minimum or value > MAX_MINUTES:
        raise TaskError(
            "INVALID_ARGUMENT", f"{field} 必须在 {minimum}—{MAX_MINUTES} 之间"
        )
    return value


def _append_log(
    db: sqlite3.Connection,
    *,
    user_id: str,
    task_id: str,
    kind: str,
    minutes: int,
    feedback: str | None,
) -> None:
    db.execute(
        """
        INSERT INTO task_logs (id, user_id, task_id, kind, minutes, feedback)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (f"tl_{secrets.token_hex(6)}", user_id, task_id, kind, minutes, feedback),
    )


def _task_view(row: sqlite3.Row, actual_minutes: int, feedback: str | None) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "remaining_minutes": int(row["remaining_minutes"] or 0),
        "actual_minutes": actual_minutes,
        "feedback": feedback or "",
    }


def record_progress(
    user_id: str,
    task_id: str,
    payload: dict,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """记录用户明确报告的进度（状态 + 剩余工作量）。

    - 只有明确给 status='done' 才会把任务标记完成
    - 已完成的任务不能被静默改回未完成
    - 累计用时只能增加（历史记录不可抹掉）
    - 不触碰正式计划与时间块
    """
    if not isinstance(payload, dict):
        raise TaskError("INVALID_ARGUMENT", "payload 必须是对象")

    status = payload.get("status")
    if status not in INPUT_STATUSES:
        raise TaskError(
            "INVALID_STATUS", f"status 必须是 {'/'.join(INPUT_STATUSES)} 之一"
        )

    remaining = _require_minutes(payload.get("remaining_minutes"), "remaining_minutes")
    if status != "done" and remaining <= 0:
        raise TaskError(
            "INVALID_REMAINING", "未完成任务必须给出大于 0 的剩余估时；完成请选 done"
        )

    actual_value = payload.get("actual_minutes")
    actual = (
        None
        if actual_value is None
        else _require_minutes(actual_value, "actual_minutes")
    )

    feedback_value = payload.get("feedback")
    feedback = None
    if feedback_value is not None:
        if not isinstance(feedback_value, str):
            raise TaskError("INVALID_FEEDBACK", "feedback 必须是文本")
        feedback = feedback_value.strip()
        if len(feedback) > MAX_FEEDBACK_LENGTH:
            raise TaskError(
                "INVALID_FEEDBACK", f"反馈不能超过 {MAX_FEEDBACK_LENGTH} 字"
            )

    now = clock.get_now()
    db_status = STATUS_TO_DB[status]
    if status == "done":
        remaining = 0

    with _connection(conn) as db:
        _load_user(db, user_id)
        task = _load_task(db, user_id, task_id)
        current_actual = _actual_minutes(db, user_id, task_id)

        if task["status"] == "done" and status != "done":
            raise TaskError(
                "TASK_ALREADY_DONE",
                "任务已完成，不能通过进度反馈直接改回未完成",
            )

        delta = 0
        if actual is not None:
            delta = actual - current_actual
            if delta < 0:
                raise TaskError(
                    "INVALID_ACTUAL", "累计用时不能减少（历史记录不可抹掉）"
                )

        with db:
            if delta > 0 or feedback:
                _append_log(
                    db,
                    user_id=user_id,
                    task_id=task_id,
                    kind="progress",
                    minutes=delta if delta > 0 else 0,
                    feedback=feedback,
                )
            db.execute(
                "UPDATE tasks SET status = ?, remaining_minutes = ?, "
                "updated_at = datetime('now', 'localtime') "
                "WHERE id = ? AND user_id = ?",
                (db_status, remaining, task_id, user_id),
            )
            updated = _load_task(db, user_id, task_id)

        total_actual = current_actual + max(delta, 0)
        message = (
            "任务已完成，剩余估时已归零。"
            if db_status == "done"
            else "进度已保存，剩余工作量按你的报告更新。"
        )
        return {
            "task": _task_view(updated, total_actual, feedback),
            "actual_minutes": total_actual,
            "message": message,
        }


def record_time(
    user_id: str,
    task_id: str,
    actual_minutes: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """记录累计实际用时（覆盖式上报，内部按增量入库）。

    只记时间：不改任务状态，不改 remaining_minutes，也不动正式计划。
    """
    actual = _require_minutes(actual_minutes, "actual_minutes")

    with _connection(conn) as db:
        _load_user(db, user_id)
        task = _load_task(db, user_id, task_id)
        current_actual = _actual_minutes(db, user_id, task_id)
        delta = actual - current_actual
        if delta < 0:
            raise TaskError(
                "INVALID_ACTUAL", "累计用时不能减少（历史记录不可抹掉）"
            )

        with db:
            if delta > 0:
                _append_log(
                    db,
                    user_id=user_id,
                    task_id=task_id,
                    kind="time",
                    minutes=delta,
                    feedback=None,
                )
            db.execute(
                "UPDATE tasks SET updated_at = datetime('now', 'localtime') "
                "WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )

    if delta == 0:
        message = "累计用时没有变化。"
    else:
        message = f"已记录累计学习 {actual} 分钟；剩余工作量没有变化。"

    return {
        "task": _task_view(task, actual, None),
        "actual_minutes": actual,
        "message": message,
    }


def list_logs(
    user_id: str, task_id: str, *, conn: sqlite3.Connection | None = None
) -> list[dict]:
    """查看某任务的历史记录（测试与排查用）。"""
    with _connection(conn) as db:
        _load_task(db, user_id, task_id)
        rows = db.execute(
            # 同一秒内可能写入多条，用 rowid 保证"插入顺序"
            "SELECT * FROM task_logs WHERE user_id = ? AND task_id = ? "
            "ORDER BY created_at, rowid",
            (user_id, task_id),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "kind": row["kind"],
            "minutes": int(row["minutes"] or 0),
            "feedback": row["feedback"],
            "created_at": _normalize_time(row["created_at"]),
        }
        for row in rows
    ]
