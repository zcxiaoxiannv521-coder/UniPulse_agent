"""services/goals.py 的测试：创建/更新、校验、隔离、事务。"""

from __future__ import annotations

import pytest

from database.connection import get_connection, init_db
from demo import seed as seed_module
from services import clock, goals


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv(clock.ENV_TIME_MODE, "demo")
    monkeypatch.setenv(clock.ENV_DEMO_NOW, "2026-11-30T09:00:00+08:00")

    path = tmp_path / "goals.db"
    init_db(path)
    connection = get_connection(path)
    seed_module.seed(connection)
    yield connection
    connection.close()


PAYLOAD = {
    "title": "准备机器学习期末考试",
    "scope": "第三章、第四章",
    "event_id": seed_module.EXAM_ID,
    "daily_max_minutes": 120,
    "availability": "每天 19:00 后",
}


def test_update_existing_goal(conn):
    """演示用户已有该考试的目标：保存是更新，不是重复创建。"""
    result = goals.save_goal(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)

    assert result["created"] is False
    assert result["goal"]["id"] == seed_module.GOAL_ID
    assert result["goal"]["daily_max_minutes"] == 120
    assert result["goal"]["version"] == 2      # 原 version 1 + 1
    assert result["goal"]["scope"] == "第三章、第四章"
    assert result["goal"]["availability"] == "每天 19:00 后"

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM goals WHERE user_id = ?",
        (seed_module.DEMO_USER_ID,),
    ).fetchone()["n"]
    assert count == 1


def test_create_new_goal_for_new_exam(conn):
    with conn:
        conn.execute(
            "INSERT INTO exams (id, user_id, name, exam_date) "
            "VALUES ('ex_new', ?, '新考试', '2026-12-10')",
            (seed_module.DEMO_USER_ID,),
        )

    result = goals.save_goal(
        seed_module.DEMO_USER_ID,
        {**PAYLOAD, "event_id": "ex_new"},
        conn=conn,
    )

    assert result["created"] is True
    assert result["goal"]["event_id"] == "ex_new"
    assert result["goal"]["version"] == 1


def test_invalid_inputs(conn):
    cases = [
        ({**PAYLOAD, "title": "   "}, "INVALID_TITLE"),
        ({**PAYLOAD, "title": "长" * 61}, "INVALID_TEXT"),
        ({**PAYLOAD, "daily_max_minutes": 10}, "INVALID_DAILY_LIMIT"),
        ({**PAYLOAD, "daily_max_minutes": 500}, "INVALID_DAILY_LIMIT"),
        ({**PAYLOAD, "daily_max_minutes": "90"}, "INVALID_DAILY_LIMIT"),
        ({**PAYLOAD, "event_id": ""}, "INVALID_EVENT"),
    ]
    for payload, code in cases:
        with pytest.raises(goals.GoalError) as excinfo:
            goals.save_goal(seed_module.DEMO_USER_ID, payload, conn=conn)
        assert excinfo.value.code == code


def test_exam_must_belong_to_user(conn):
    with conn:
        conn.execute("INSERT INTO users (id, name) VALUES ('us_other', '别人')")
        conn.execute(
            "INSERT INTO exams (id, user_id, name, exam_date) "
            "VALUES ('ex_other', 'us_other', '别人的考试', '2026-12-10')"
        )

    with pytest.raises(goals.GoalError) as excinfo:
        goals.save_goal(
            seed_module.DEMO_USER_ID,
            {**PAYLOAD, "event_id": "ex_other"},
            conn=conn,
        )
    assert excinfo.value.code == "EXAM_NOT_FOUND"


def test_preferences_are_scoped_to_goal(conn):
    goals.save_goal(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)

    rows = conn.execute(
        "SELECT key, scope FROM user_preferences WHERE user_id = ? AND key LIKE 'goal_%'",
        (seed_module.DEMO_USER_ID,),
    ).fetchall()

    assert {row["key"] for row in rows} == {"goal_scope", "goal_availability"}
    assert all(row["scope"] == f"goal:{seed_module.GOAL_ID}" for row in rows)


def test_goal_save_does_not_touch_plan(conn):
    def snapshot():
        return (
            [tuple(r) for r in conn.execute("SELECT * FROM plans")],
            [tuple(r) for r in conn.execute("SELECT * FROM plan_items")],
            [tuple(r) for r in conn.execute("SELECT * FROM exams")],
        )

    before = snapshot()
    goals.save_goal(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)
    assert snapshot() == before


def test_rollback_when_pref_write_fails(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("模拟写偏好失败")

    monkeypatch.setattr(goals, "_upsert_pref", boom)

    before = conn.execute(
        "SELECT title, version FROM goals WHERE id = ?", (seed_module.GOAL_ID,)
    ).fetchone()

    with pytest.raises(RuntimeError):
        goals.save_goal(seed_module.DEMO_USER_ID, PAYLOAD, conn=conn)

    after = conn.execute(
        "SELECT title, version FROM goals WHERE id = ?", (seed_module.GOAL_ID,)
    ).fetchone()
    assert tuple(after) == tuple(before)
