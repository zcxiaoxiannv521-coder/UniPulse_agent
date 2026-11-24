"""演示数据初始化（V4 第 7 节的固定场景）。

本文件只准备**改期通知发生之前**的初始状态：
    机器学习期末考试  -> 2026-12-04 14:00
    复习第三章        -> 60 分钟（必做）
    复习第四章        -> 90 分钟（必做）
    模拟练习          -> 80 分钟（选做）
    每天最多学        -> 90 分钟

注意：**不会**写入“考试提前到 12-02”的结果，那是后续演示中由预览/确认流程
产生的，提前写进去会毁掉整个演示。

数据标识固定（us_demo_a / ex_demo_ml_final / ...），重复执行不会重复插入，
也不会覆盖你手工改过的数据。

用法：
    python -m demo.seed            # 首次或重复执行，幂等
    python -m demo.seed --reset    # 先删掉演示数据再重建（只在需要重来时用）
    python -m demo.seed --db /tmp/x.db   # 指定数据库文件（测试用）
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from database.connection import DB_PATH, get_connection, init_db

# ---------- 固定标识（不要改，测试和演示都依赖它们） ----------
DEMO_USER_ID = "us_demo_a"
DEMO_USER_NAME = "林小满"

COURSE_ID = "cr_demo_ml"
COURSE_NAME = "机器学习"

SESSION_IDS = ("cs_demo_01", "cs_demo_02", "cs_demo_03")

EXAM_ID = "ex_demo_ml_final"
EXAM_NAME = "机器学习期末考试"
EXAM_DATE = "2026-12-04"          # 原定考试日；演示中会被“提前到 12-02”
EXAM_START = "14:00"
EXAM_END = "15:30"

GOAL_ID = "gl_demo_ml"
DAILY_LIMIT_MINUTES = 90

TASK_IDS = ("tk_demo_ch3", "tk_demo_ch4", "tk_demo_mock")

PLAN_ID = "pl_demo_01"
PLAN_ITEM_IDS = ("pi_demo_01", "pi_demo_02", "pi_demo_03")

PREF_ID = "up_demo_daily_limit"

# 演示通知：只是"学生收到的原文"，不代表考试已经改期
NOTICE_ID = "nt_demo_exam_change"
NOTICE_TEXT = (
    "教务通知：原定于2026年12月4日14:00举行的机器学习期末考试调整至"
    "2026年12月2日14:00，请同学及时调整复习安排。"
)
NOTICE_RECEIVED_AT = "2026-11-30T08:55:00+08:00"


# ---------- 插入工具 ----------
def _insert_rows(
    conn: sqlite3.Connection, table: str, rows: list[dict[str, object]]
) -> None:
    """逐行 INSERT OR IGNORE。

    - 表名来自本文件常量，不是外部输入
    - OR IGNORE 保证重复执行不报错、不重复插入
    - 已存在的数据保持原样（不 UPDATE），因此不会覆盖用户改过的正式数据
    """
    for row in rows:
        columns = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )


# ---------- 各表数据 ----------
def _insert_users(conn: sqlite3.Connection) -> None:
    _insert_rows(
        conn,
        "users",
        [
            {
                "id": DEMO_USER_ID,
                "name": DEMO_USER_NAME,
                "timezone": "Asia/Shanghai",
            }
        ],
    )


def _insert_preferences(conn: sqlite3.Connection) -> None:
    # 每日学习上限属于“这个目标”，所以 scope 写成 goal:<id>
    _insert_rows(
        conn,
        "user_preferences",
        [
            {
                "id": PREF_ID,
                "user_id": DEMO_USER_ID,
                "key": "daily_limit_minutes",
                "value_json": str(DAILY_LIMIT_MINUTES),
                "scope": f"goal:{GOAL_ID}",
            }
        ],
    )


def _insert_courses(conn: sqlite3.Connection) -> None:
    _insert_rows(
        conn,
        "courses",
        [
            {
                "id": COURSE_ID,
                "user_id": DEMO_USER_ID,
                "name": COURSE_NAME,
                "teacher": "王老师",
                "location": "教三 302",
            }
        ],
    )


def _insert_notifications(conn: sqlite3.Connection) -> None:
    """一条脱敏通知，仅作为"收到的原文"，不改变考试时间。"""
    _insert_rows(
        conn,
        "notifications",
        [
            {
                "id": NOTICE_ID,
                "user_id": DEMO_USER_ID,
                "wecom_msg_id": None,
                "raw_text": NOTICE_TEXT,
                "received_at": NOTICE_RECEIVED_AT,
                "published_at": None,
                "parse_json": None,
                "status": "parsed",
            }
        ],
    )


def _insert_class_sessions(conn: sqlite3.Connection) -> None:
    # 三次课都安排在上午，不与三个学习时段（晚上/下午）冲突
    sessions = [
        ("cs_demo_01", "2026-11-30", "10:00", "11:30"),
        ("cs_demo_02", "2026-12-01", "10:00", "11:30"),
        ("cs_demo_03", "2026-12-03", "10:00", "11:30"),
    ]
    _insert_rows(
        conn,
        "class_sessions",
        [
            {
                "id": sid,
                "user_id": DEMO_USER_ID,
                "course_id": COURSE_ID,
                "session_date": day,
                "start_time": start,
                "end_time": end,
                "location": "教三 302",
                "status": "normal",
            }
            for sid, day, start, end in sessions
        ],
    )


def _insert_exams(conn: sqlite3.Connection) -> None:
    _insert_rows(
        conn,
        "exams",
        [
            {
                "id": EXAM_ID,
                "user_id": DEMO_USER_ID,
                "course_id": COURSE_ID,
                "name": EXAM_NAME,
                "exam_date": EXAM_DATE,
                "start_time": EXAM_START,
                "end_time": EXAM_END,
                "location": "教三 302",
                "status": "scheduled",
            }
        ],
    )


def _insert_goals(conn: sqlite3.Connection) -> None:
    _insert_rows(
        conn,
        "goals",
        [
            {
                "id": GOAL_ID,
                "user_id": DEMO_USER_ID,
                "exam_id": EXAM_ID,
                "title": "准备机器学习期末考试",
                "daily_limit_minutes": DAILY_LIMIT_MINUTES,
                "status": "active",
            }
        ],
    )


def _insert_tasks(conn: sqlite3.Connection) -> None:
    tasks = [
        {
            "id": "tk_demo_ch3",
            "title": "复习第三章",
            "detail": "教材第 3 章 + 课后题",
            "minutes": 60,
            "priority": 1,
            "is_optional": 0,
            "splittable": 0,
        },
        {
            "id": "tk_demo_ch4",
            "title": "复习第四章",
            "detail": "教材第 4 章 + 课后题",
            "minutes": 90,
            "priority": 1,
            "is_optional": 0,
            "splittable": 0,
        },
        {
            "id": "tk_demo_mock",
            "title": "模拟练习",
            "detail": "选做：完整模拟卷一套",
            "minutes": 80,
            "priority": 2,
            "is_optional": 1,
            "splittable": 1,
        },
    ]
    _insert_rows(
        conn,
        "tasks",
        [
            {
                "id": t["id"],
                "user_id": DEMO_USER_ID,
                "goal_id": GOAL_ID,
                "course_id": COURSE_ID,
                "title": t["title"],
                "detail": t["detail"],
                "estimated_minutes": t["minutes"],
                "remaining_minutes": t["minutes"],
                "priority": t["priority"],
                "is_optional": t["is_optional"],
                "splittable": t["splittable"],
                "status": "todo",
                "source": "manual",
            }
            for t in tasks
        ],
    )


def _insert_plans(conn: sqlite3.Connection) -> None:
    _insert_rows(
        conn,
        "plans",
        [
            {
                "id": PLAN_ID,
                "user_id": DEMO_USER_ID,
                "goal_id": GOAL_ID,
                "status": "confirmed",   # 初始计划已经过用户确认
                "version": 1,
                "note": None,
            }
        ],
    )


def _insert_plan_items(conn: sqlite3.Connection) -> None:
    items = [
        ("pi_demo_01", "tk_demo_ch3", "复习第三章", "2026-11-30T19:00:00+08:00", "2026-11-30T20:00:00+08:00"),
        ("pi_demo_02", "tk_demo_ch4", "复习第四章", "2026-12-01T19:00:00+08:00", "2026-12-01T20:30:00+08:00"),
        ("pi_demo_03", "tk_demo_mock", "模拟练习", "2026-12-02T15:00:00+08:00", "2026-12-02T16:20:00+08:00"),
    ]
    _insert_rows(
        conn,
        "plan_items",
        [
            {
                "id": item_id,
                "plan_id": PLAN_ID,
                "start_at": start,
                "end_at": end,
                "title": title,
                "item_type": "task",
                "task_id": task_id,
                "is_locked": 0,
            }
            for item_id, task_id, title, start, end in items
        ],
    )


# ---------- 删除（仅 --reset 使用） ----------
def _delete_demo(conn: sqlite3.Connection) -> None:
    """按外键顺序删除演示数据。只删本文件创建的固定 ID，不碰别人的数据。"""
    conn.execute("DELETE FROM plan_items WHERE plan_id = ?", (PLAN_ID,))
    conn.execute("DELETE FROM plans WHERE id = ?", (PLAN_ID,))
    placeholders = ", ".join("?" for _ in TASK_IDS)
    conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", TASK_IDS)
    conn.execute("DELETE FROM goals WHERE id = ?", (GOAL_ID,))
    conn.execute("DELETE FROM exams WHERE id = ?", (EXAM_ID,))
    placeholders = ", ".join("?" for _ in SESSION_IDS)
    conn.execute(f"DELETE FROM class_sessions WHERE id IN ({placeholders})", SESSION_IDS)
    conn.execute("DELETE FROM notifications WHERE id = ?", (NOTICE_ID,))
    conn.execute("DELETE FROM courses WHERE id = ?", (COURSE_ID,))
    conn.execute("DELETE FROM user_preferences WHERE id = ?", (PREF_ID,))
    conn.execute("DELETE FROM users WHERE id = ?", (DEMO_USER_ID,))


# ---------- 汇总 ----------
def _count(conn: sqlite3.Connection, table: str, where: str, params: tuple) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()
    return int(row["n"])


def summarize(conn: sqlite3.Connection) -> dict[str, int]:
    """返回演示用户当前的数据条数，用于命令行输出和测试断言。"""
    task_ph = ", ".join("?" for _ in TASK_IDS)
    session_ph = ", ".join("?" for _ in SESSION_IDS)
    return {
        "users": _count(conn, "users", "id = ?", (DEMO_USER_ID,)),
        "preferences": _count(conn, "user_preferences", "user_id = ?", (DEMO_USER_ID,)),
        "courses": _count(conn, "courses", "user_id = ?", (DEMO_USER_ID,)),
        "class_sessions": _count(
            conn, "class_sessions", f"id IN ({session_ph})", SESSION_IDS
        ),
        "exams": _count(conn, "exams", "user_id = ?", (DEMO_USER_ID,)),
        "goals": _count(conn, "goals", "user_id = ?", (DEMO_USER_ID,)),
        "tasks": _count(conn, "tasks", f"id IN ({task_ph})", TASK_IDS),
        "plans": _count(conn, "plans", "user_id = ?", (DEMO_USER_ID,)),
        "plan_items": _count(
            conn, "plan_items", "plan_id = ?", (PLAN_ID,)
        ),
        "notifications": _count(
            conn, "notifications", "user_id = ?", (DEMO_USER_ID,)
        ),
    }


def seed(conn: sqlite3.Connection, reset: bool = False) -> dict[str, int]:
    """写入演示数据，返回各表条数。

    整个写入在一个事务里：任何一步抛异常，全部回滚，不会留下半套数据。
    """
    with conn:                      # 成功自动 COMMIT，异常自动 ROLLBACK
        if reset:
            _delete_demo(conn)
        _insert_users(conn)
        _insert_preferences(conn)
        _insert_courses(conn)
        _insert_notifications(conn)
        _insert_class_sessions(conn)
        _insert_exams(conn)
        _insert_goals(conn)
        _insert_tasks(conn)
        _insert_plans(conn)
        _insert_plan_items(conn)
    return summarize(conn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo.seed",
        description="初始化 UniPulse 演示数据（幂等，可重复执行）",
    )
    parser.add_argument(
        "--reset", action="store_true", help="先删除演示数据再重建（会丢弃对演示数据的修改）"
    )
    parser.add_argument(
        "--db", default=str(DB_PATH), help=f"数据库文件路径，默认 {DB_PATH}"
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        counts = seed(conn, reset=args.reset)
    finally:
        conn.close()

    print(f"✅ 演示数据已写入：{db_path}")
    if args.reset:
        print("   （已按 --reset 删除旧演示数据后重建）")
    print(f"   用户 {DEMO_USER_ID}（{DEMO_USER_NAME}）")
    print(f"   考试：{EXAM_NAME} {EXAM_DATE} {EXAM_START}")
    print(f"   任务 {counts['tasks']} 条，计划 {counts['plans']} 份，时间块 {counts['plan_items']} 个")
    print(f"   通知 {counts['notifications']} 条（改期通知尚未发生，属正常）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
