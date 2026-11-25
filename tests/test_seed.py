"""demo/seed.py 的测试。

覆盖：首次初始化、重复执行幂等、关联正确、不覆盖已有数据、
事务回滚、--reset 重建、不提前写入改期结果。
"""

from __future__ import annotations

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module


@pytest.fixture()
def conn(tmp_path):
    """每个用例一个全新的空数据库。"""
    db_file = tmp_path / "demo.db"
    init_db(db_file)
    connection = get_connection(db_file)
    yield connection
    connection.close()


def test_seed_creates_expected_rows(conn):
    """首次初始化应写齐演示数据。"""
    counts = seed_module.seed(conn)

    assert counts["users"] == 1
    assert counts["courses"] == 1
    assert counts["class_sessions"] == 3
    assert counts["exams"] == 1
    assert counts["goals"] == 1
    assert counts["tasks"] == 3
    assert counts["plans"] == 1
    assert counts["plan_items"] == 3
    assert counts["preferences"] == 1


def test_seed_is_idempotent(conn):
    """重复执行不产生重复数据。"""
    first = seed_module.seed(conn)
    second = seed_module.seed(conn)

    assert first == second
    assert second["tasks"] == 3
    assert second["plan_items"] == 3


def test_seed_links_objects_correctly(conn):
    """用户、课程、考试、目标、任务、计划之间的外键与时间必须对得上。"""
    seed_module.seed(conn)

    exam = conn.execute(
        "SELECT * FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
    ).fetchone()
    assert exam["exam_date"] == "2026-12-04"
    assert (exam["start_time"], exam["end_time"]) == ("14:00", "15:30")
    assert exam["status"] == "scheduled"

    goal = conn.execute(
        "SELECT * FROM goals WHERE id = ?", (seed_module.GOAL_ID,)
    ).fetchone()
    assert goal["exam_id"] == seed_module.EXAM_ID
    assert goal["daily_limit_minutes"] == 90
    assert goal["status"] == "active"

    plan = conn.execute(
        "SELECT * FROM plans WHERE id = ?", (seed_module.PLAN_ID,)
    ).fetchone()
    assert plan["goal_id"] == seed_module.GOAL_ID
    assert plan["status"] == "confirmed"
    assert plan["version"] == 1

    items = conn.execute(
        """
        SELECT pi.start_at, pi.end_at, pi.task_id, t.remaining_minutes, t.is_optional
        FROM plan_items pi
        JOIN tasks t ON t.id = pi.task_id
        WHERE pi.plan_id = ?
        ORDER BY pi.start_at
        """,
        (seed_module.PLAN_ID,),
    ).fetchall()

    assert len(items) == 3
    assert items[0]["start_at"] == "2026-11-30T19:00:00+08:00"
    assert items[0]["end_at"] == "2026-11-30T20:00:00+08:00"
    assert items[2]["start_at"] == "2026-12-02T15:00:00+08:00"
    assert sum(row["remaining_minutes"] for row in items) == 230

    optional = {row["task_id"]: row["is_optional"] for row in items}
    assert optional[seed_module.TASK_IDS[0]] == 0
    assert optional[seed_module.TASK_IDS[2]] == 1


def test_seed_does_not_write_future_change(conn):
    """初始数据里不能提前出现“考试提前到 12-02”的结果。"""
    seed_module.seed(conn)

    exam = conn.execute(
        "SELECT exam_date FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
    ).fetchone()
    assert exam["exam_date"] == "2026-12-04"

    # 有一条"收到的原文"通知，但它不代表考试已改期
    notices = conn.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()
    assert notices["n"] == 1
    previews = conn.execute("SELECT COUNT(*) AS n FROM previews").fetchone()
    assert previews["n"] == 0


def test_seed_does_not_overwrite_user_changes(conn):
    """用户改过的正式数据，重复 seed 不能被覆盖回演示值。"""
    seed_module.seed(conn)
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-12-05' WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    seed_module.seed(conn)

    exam = conn.execute(
        "SELECT exam_date FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
    ).fetchone()
    assert exam["exam_date"] == "2026-12-05"


def test_seed_rolls_back_on_failure(conn, monkeypatch):
    """中途失败必须整体回滚，不能留下半套数据。"""

    def boom(_conn):
        raise RuntimeError("模拟写时间块时数据库出错")

    monkeypatch.setattr(seed_module, "_insert_plan_items", boom)

    with pytest.raises(RuntimeError, match="模拟写时间块"):
        seed_module.seed(conn)

    # 前面已经插入的用户、课程、任务等，都应该被回滚掉
    for table in ("users", "courses", "exams", "goals", "tasks", "plans", "plan_items"):
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        assert row["n"] == 0, f"{table} 没有被回滚"


def test_reset_rebuilds_demo_data(conn):
    """--reset 会先删演示数据再重建。"""
    seed_module.seed(conn)
    with conn:
        conn.execute(
            "UPDATE exams SET exam_date = '2026-12-05' WHERE id = ?",
            (seed_module.EXAM_ID,),
        )

    counts = seed_module.seed(conn, reset=True)

    assert counts["tasks"] == 3
    exam = conn.execute(
        "SELECT exam_date FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
    ).fetchone()
    assert exam["exam_date"] == "2026-12-04"


def test_seed_inserts_demo_notice(conn):
    """演示库里应有一条"收到的原文"通知。"""
    seed_module.seed(conn)

    row = conn.execute(
        "SELECT * FROM notifications WHERE id = ?", (seed_module.NOTICE_ID,)
    ).fetchone()

    assert row is not None
    assert row["user_id"] == seed_module.DEMO_USER_ID
    assert row["raw_text"] == seed_module.NOTICE_TEXT
    assert "2026年12月2日14:00" in row["raw_text"]
    # 原文只是数据，不代表考试已经改期
    exam = conn.execute(
        "SELECT exam_date FROM exams WHERE id = ?", (seed_module.EXAM_ID,)
    ).fetchone()
    assert exam["exam_date"] == "2026-12-04"


def test_seed_does_not_duplicate_notice(conn):
    seed_module.seed(conn)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ?",
        (seed_module.DEMO_USER_ID,),
    ).fetchone()["n"]
    assert count == 1


def test_seed_does_not_overwrite_notice(conn):
    seed_module.seed(conn)
    with conn:
        conn.execute(
            "UPDATE notifications SET raw_text = '用户自己改过的原文' WHERE id = ?",
            (seed_module.NOTICE_ID,),
        )

    seed_module.seed(conn)

    row = conn.execute(
        "SELECT raw_text FROM notifications WHERE id = ?", (seed_module.NOTICE_ID,)
    ).fetchone()
    assert row["raw_text"] == "用户自己改过的原文"
